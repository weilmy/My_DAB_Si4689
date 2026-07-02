#!/usr/bin/env python3
"""
Audio Codec – HifiBerry DAC+ADC Pro  (PCM5122 + PCM1863)
Version: 1.1  –  Mai 2026

Hardware:  Raspberry Pi 5 + HifiBerry DAC+ADC Pro
Chip:      PCM5122 (Playback/DAC)  +  PCM1863 (Capture/ADC)
Si4689:    I2S-Audio → PCM1863 ADC-Eingang  →  ALSA-Capture

Signalweg:
    arecord (Source01_DAB / PCM1863)
        │  arecord | aplay Pipeline (-t raw → kein 2 GB-Limit)
        ▼
    aplay (hifiberry / PCM5122)

Features:
  - Haupt-Pipeline: arecord | aplay  (permanent, ohne tee)
  - Raw-Capture:    separater arecord für dab.raw (nur bei Bedarf)
  - Auto-Restart:   periodischer Neustart hält Raw-Datei klein
  - Health-Check:   automatischer Neustart bei Pipeline-Absturz
  - ADC Mute:       PCM1863 bei Stop/Init stummschalten (GPIO20-Konflikt)
  - Format:         S32_LE / 2ch / 48000 Hz  (nativ PCM5122 + PCM1863)

Changelog:
  v1.1  ADC Mute/Unmute Methoden (_mute_adc / _unmute_adc)
        _mute_adc() in __init__ und stop_audio_codec()
        _unmute_adc() in start_audio_codec() und _restart_pipeline()
"""

import json
import os
import re
import signal
import subprocess
import time
import threading
from dataclasses import dataclass
from typing import Optional

try:
    import smbus2 as _smbus2
    _SMBUS2_AVAILABLE = True
except ImportError:
    _SMBUS2_AVAILABLE = False


# =========================================================================
# KONFIGURATION
# =========================================================================

@dataclass
class AudioConfig:
    # --- ALSA-Device-Namen (müssen mit asound.conf übereinstimmen) ---
    capture_device:     str = "Source01_DAB"        # PCM1863 ADC (Si4689 I2S)
    capture_device_raw: str = "Source02_matplotlib"  # PCM1863 ADC (Raw / Spektrum)
    playback_device:    str = "hifiberry"            # PCM5122 DAC (via SoftMaster + EQ)

    # --- Audio-Format (PCM5122 + PCM1863 nativ) ---
    format:   str = "S32_LE"  # HifiBerry DAC+ADC Pro nativ → kein Resampling
    channels: int = 2
    rate:     int = 48000

    # --- ALSA-Puffer (konservativ; bei Glitches period_time halbieren) ---
    buffer_time: int = 250000  # µs  (250 ms)
    period_time: int = 62500   # µs  (62.5 ms = buffer_time / 4)

    # --- Health-Check ---
    enable_health_check:   bool  = True
    health_check_interval: float = 5.0
    max_restart_attempts:  int   = 3

    # --- Raw-Capture Auto-Restart ---
    raw_restart_interval: int   = 60    # Sekunden zwischen Neustarts
    raw_max_size_mb:      float = 10.0  # Neustart wenn Datei grösser

    # --- Sonstiges ---
    verbose:          bool = False
    alsa_state_path:  str  = ""  # Pfad zu asound.state für alsactl restore
    alsa_config_path: str  = ""  # Pfad zu alsa_config.json (Playback-Controls)


# =========================================================================
# HAUPT-KLASSE
# =========================================================================

class Audio_Codec:
    """
    ALSA-Pipeline für HifiBerry DAC+ADC Pro mit separatem Raw-Capture.

    Architektur:
      - Haupt-Pipeline läuft permanent (kein dmix/dsnoop Resource-Leak)
      - Raw-Capture ist separat und wird nur bei Bedarf gestartet
      - PCM1863 ADC: analog stummgeschaltet + DOUT Hi-Z (GPIO20-Konflikt)
      - Volume-Steuerung: SoftMaster → Digital → Analogue → Master
    """

    # PCM1863 I2C: Adresse + Power-Down Register (DOUT → Hi-Z wenn PWRDN=1)
    _PCM186X_I2C_BUS  = 1
    _PCM186X_I2C_ADDR = 0x4A
    _PCM186X_PWRDN_REG = 0x70
    _PCM186X_PWRDN_ON  = 0x01
    _PCM186X_PWRDN_OFF = 0x00

    def __init__(self, config: Optional[AudioConfig] = None):
        self.config  = config or AudioConfig()
        self.proc:             Optional[subprocess.Popen] = None
        self.raw_capture_proc: Optional[subprocess.Popen] = None

        self.mixer_card = self._find_hifiberry_card()
        self.write_raw  = False

        self._lock        = threading.RLock()
        self._last_volume = 50

        self._pcm186x_i2c_ok: bool = _SMBUS2_AVAILABLE

        # --- Health-Check Haupt-Pipeline ---
        self._health_check_thread: Optional[threading.Thread] = None
        self._health_check_stop   = threading.Event()
        self._restart_count       = 0
        self._last_start_time:    Optional[float] = None

        # --- Auto-Restart Raw-Capture ---
        self._raw_restart_thread: Optional[threading.Thread] = None
        self._raw_restart_stop   = threading.Event()
        self._raw_restarting     = False

        # Definierter Ausgangszustand: ADC stummschalten + DOUT Hi-Z
        self._mute_adc()

    # =========================================================================
    # KARTEN-ERKENNUNG + PCM186x DEBUGFS
    # =========================================================================

    @staticmethod
    def _find_hifiberry_card() -> str:
        """
        Findet die HifiBerry DAC+ADC Pro Kartennummer.
        Fallback: '0'  (Pi 5 hat meist nur eine aktive Soundkarte).
        """
        search_terms = (
            "sndrpihifiberry",
            "hifiberry",
            "dacplusadcpro",
            "dacplusadc",
        )
        try:
            out = subprocess.check_output(
                ["aplay", "-l"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if any(t in line.lower() for t in search_terms):
                    m = re.search(r'card\s+(\d+)|Karte\s+(\d+)', line, re.IGNORECASE)
                    if m:
                        return m.group(1) or m.group(2)
        except (subprocess.SubprocessError, OSError):
            pass
        return "0"

    def _pcm186x_dout_disable(self) -> bool:
        """
        PCM1863 DOUT → Hi-Z via I2C Register 0x70=0x01 (Power-Down).
        smbus2 force=True umgeht den Kernel-I2C-Lock (UU-Device).
        Si4689 DOUT treibt GPIO20 danach unangefochten.
        """
        return self._pcm186x_write_reg(self._PCM186X_PWRDN_REG, self._PCM186X_PWRDN_ON)

    def _pcm186x_write_reg(self, reg: int, val: int) -> bool:
        if not self._pcm186x_i2c_ok:
            return False
        try:
            # force=True nutzt I2C_SLAVE_FORCE ioctl statt I2C_SLAVE – einziger Weg,
            # direkt auf einen UU-gebundenen I2C-Chip zu schreiben ohne Treiber zu entladen. 
            # Der Versuch, mit i2cset direkt in das Power-Down-Register des PCM1863 zu schreiben, 
            # schlägt fehl, da der Kernel-Treiber eine exklusive Sperre für das I2C-Gerät hält (in i2cdetect als UU angezeigt).
            
            bus = _smbus2.SMBus(self._PCM186X_I2C_BUS, force=True)
            bus.write_byte_data(self._PCM186X_I2C_ADDR, reg, val)
            bus.close()
            if self.config.verbose:
                print(f"PCM1863 I2C reg 0x{reg:02x} = 0x{val:02x}")
            return True
        except OSError:
            self._pcm186x_i2c_ok = False
            return False

    def pcm186x_read_dout_state(self) -> str:
        """Liest Register 0x70 – zur Diagnose ob DOUT aktiv oder Hi-Z."""
        if not _SMBUS2_AVAILABLE:
            return "smbus2 nicht verfügbar"
        try:
            bus = _smbus2.SMBus(self._PCM186X_I2C_BUS, force=True)
            val = bus.read_byte_data(self._PCM186X_I2C_ADDR, self._PCM186X_PWRDN_REG)
            bus.close()
            state = "Hi-Z (PWRDN)" if (val & 0x01) else "aktiv (DOUT treibt GPIO20)"
            return f"reg 0x70 = 0x{val:02x} → {state}"
        except OSError as e:
            return f"Lesefehler: {e}"

    # =========================================================================
    # ADC MUTE / UNMUTE  (PCM1863 – GPIO20-Konflikt eliminieren)
    # =========================================================================

    def _mute_adc(self):
        """
        PCM1863 vollständig deaktivieren:
          1. Analog: Input/Gain/Mixer via ALSA-Controls auf No Select / 0
          2. I2S DOUT: Register 0x70=0x01 (Power-Down → DOUT Hi-Z)
        Aufgerufen bei: __init__, stop_audio_codec(), Fehler in start/restart.
        """

        commands = [
            ["amixer","-c",self.mixer_card,
            "sset","ADC Left Input","No Select"],

            ["amixer","-c",self.mixer_card,
            "sset","ADC Right Input","No Select"],

            ["amixer","-c",self.mixer_card,
            "sset","ADC Left Capture Source","No Select"],

            ["amixer","-c",self.mixer_card,
            "sset","ADC Right Capture Source","No Select"],

            ["amixer","-c",self.mixer_card,
            "sset","ADC Capture Volume","0"],

            ["amixer","-c",self.mixer_card,
            "sset","ADC Mic Bias","Mic Bias off"],
        ]

        for cmd in commands:
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
            except Exception:
                pass

        # DOUT Hi-Z: PCM1863 hört auf GPIO20 zu treiben
        self._pcm186x_dout_disable()

        if self.config.verbose:
            print("🔇 PCM1863 ADC stummgeschaltet + DOUT Hi-Z")

    def _unmute_adc(self):
        """
        PCM1863 ADC aktivieren.
        Aufgerufen bei: start_audio_codec(), _restart_pipeline().
        PGA Gain wird bewusst NICHT gesetzt → zuletzt gespeicherter Wert
        aus alsactl bleibt erhalten.
        """
        commands = [
            ["amixer", "-c", self.mixer_card, "sset", "ADC Left Input",  "on"],
            ["amixer", "-c", self.mixer_card, "sset", "ADC Right Input", "on"],
        ]
        for cmd in commands:
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
            except (subprocess.SubprocessError, OSError):
                pass
        if self.config.verbose:
            print("🔊 PCM1863 ADC aktiviert")

    # =========================================================================
    # KOMMANDO-BUILDER
    # =========================================================================

    def _build_pipeline_cmd(self) -> str:
        """
        Baut das arecord | aplay Pipeline-Kommando.
        -t raw:  kein WAV-Header → kein 2 GB-Limit
        S32_LE:  PCM5122/PCM1863 nativ → kein ALSA-Resampling
        """
        record = (
            f"arecord -D {self.config.capture_device} "
            f"-f {self.config.format} -c{self.config.channels} -r {self.config.rate} "
            f"--buffer-time={self.config.buffer_time} "
            f"--period-time={self.config.period_time} "
            f"-t raw"
        )
        playback = (
            f"aplay -D {self.config.playback_device} "
            f"-f {self.config.format} -c{self.config.channels} -r {self.config.rate} "
            f"--buffer-time={self.config.buffer_time} "
            f"--period-time={self.config.period_time} "
            f"-t raw"
        )
        return (
            f"{record} 2>>/tmp/arecord_hifiberry.log | "
            f"{playback} 2>>/tmp/aplay_hifiberry.log"
        )

    def _build_raw_capture_cmd(self, output_path: str) -> str:
        """Baut das Raw-Capture Kommando (für Spektrum-Analyse / matplotlib)"""
        return (
            f"arecord -D {self.config.capture_device_raw} "
            f"-f {self.config.format} -c{self.config.channels} -r {self.config.rate} "
            f"--buffer-time={self.config.buffer_time} "
            f"--period-time={self.config.period_time} "
            f"-t raw {output_path}"
        )

    # =========================================================================
    # PROZESS-HILFSMETHODEN
    # =========================================================================

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen, use_sigkill: bool = False):
        """Beendet Prozess-Gruppe (arecord + aplay im Pipe-Prozess)"""
        sig = signal.SIGKILL if use_sigkill else signal.SIGTERM
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _get_audio_pids() -> list[int]:
        """Findet aktive arecord/aplay PIDs (nicht bash-Wrapper)"""
        pids = []
        for program in ("arecord", "aplay"):
            try:
                result = subprocess.run(
                    ["pgrep", "-a", program],
                    capture_output=True, text=True, timeout=2.0,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        parts = line.split(None, 1)
                        if len(parts) >= 2 and parts[1].strip().startswith(program):
                            pids.append(int(parts[0]))
            except (subprocess.SubprocessError, ValueError):
                pass
        return pids

    # =========================================================================
    # STATUS
    # =========================================================================

    def is_running(self) -> bool:
        """Prüft ob Haupt-Pipeline läuft"""
        return self.proc is not None and self.proc.poll() is None

    def is_raw_capture_running(self) -> bool:
        """Prüft ob Raw-Capture läuft"""
        return self.raw_capture_proc is not None and self.raw_capture_proc.poll() is None

    def is_raw_restarting(self) -> bool:
        """True während Raw-Capture Restart (für Thread-Koordination)"""
        return self._raw_restarting

    def get_status(self) -> dict:
        """Gibt detaillierten Status zurück"""
        uptime = None
        if self._last_start_time and self.is_running():
            uptime = round(time.time() - self._last_start_time, 1)

        raw_size_mb = 0.0
        raw_path = os.path.expanduser("~/dab.raw")
        try:
            if os.path.exists(raw_path):
                raw_size_mb = os.path.getsize(raw_path) / (1024 * 1024)
        except OSError:
            pass

        return {
            "running":             self.is_running(),
            "raw_capture_running": self.is_raw_capture_running(),
            "raw_restarting":      self._raw_restarting,
            "write_raw":           self.write_raw,
            "raw_file_size_mb":    round(raw_size_mb, 2),
            "restart_count":       self._restart_count,
            "uptime_seconds":      uptime,
            "health_check_active": bool(
                self._health_check_thread and self._health_check_thread.is_alive()
            ),
            "raw_restart_active":  bool(
                self._raw_restart_thread and self._raw_restart_thread.is_alive()
            ),
            "last_volume":     self._last_volume,
            "mixer_card":      self.mixer_card,
            "capture_device":  self.config.capture_device,
            "playback_device": self.config.playback_device,
            "format":          self.config.format,
        }

    # =========================================================================
    # HAUPT-PIPELINE: START / STOP
    # =========================================================================

    def start_audio_codec(self, write_raw: bool = False) -> bool:
        """
        Startet die Haupt-Pipeline (arecord | aplay).
        write_raw=True: startet zusätzlich separaten Raw-Capture-Prozess.
        """
        with self._lock:
            # Raw-Capture separat steuern (unabhängig von Haupt-Pipeline)
            if write_raw and not self.is_raw_capture_running():
                self._start_raw_capture()
            elif not write_raw and self.is_raw_capture_running():
                self._stop_raw_capture()

            # Haupt-Pipeline nur starten wenn noch nicht aktiv
            if self.is_running():
                return False

            self._mute_adc()            # PCM1863 stumm halten → GPIO20-Buskonflikt minimieren
            self._ensure_clean_state()
            self._last_start_time = time.time()
            self._restart_count   = 0

            cmd = self._build_pipeline_cmd()
            print(
                f"👉 ALSA-Pipeline: arecord ({self.config.capture_device} / PCM1863) "
                f"→ aplay ({self.config.playback_device} / PCM5122)"
            )
            if self.config.verbose:
                print(f"   CMD: {cmd}")

            try:
                self.proc = subprocess.Popen(
                    ["/bin/bash", "-lc", cmd],
                    preexec_fn=os.setsid,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
                time.sleep(0.3)

                if not self.is_running():
                    print("❌ Pipeline beendet sich sofort nach Start!")
                    print(
                        "   → Logs: /tmp/arecord_hifiberry.log  "
                        "/tmp/aplay_hifiberry.log"
                    )
                    self._mute_adc()    # ADC wieder stummschalten bei Fehler
                    return False

                if self.config.enable_health_check:
                    self._start_health_check()

                self._init_hardware_volume()
                # DAPM hat PCM1863 beim Stream-Open hochgefahren → DOUT wieder Hi-Z
                time.sleep(0.5)
                self._pcm186x_dout_disable()
                print(f"✅ Audio-Pipeline läuft (PID: {self.proc.pid})")
                return True

            except OSError as e:
                print(f"❌ Pipeline Start fehlgeschlagen: {e}")
                self._mute_adc()
                return False

    def stop_audio_codec(self) -> bool:
        """Beendet Haupt-Pipeline UND Raw-Capture, schaltet ADC stumm."""
        with self._lock:
            self._stop_raw_capture()
            self._stop_health_check()

            if not self.is_running():
                self.proc = None
                self._mute_adc()
                return False

            print("🛑 Stoppe Audio-Pipeline...")
            time.sleep(0.05)

            self._kill_process_group(self.proc)

            for _ in range(20):
                if not self.is_running():
                    break
                time.sleep(0.1)

            if self.is_running():
                self._kill_process_group(self.proc, use_sigkill=True)
                time.sleep(0.2)

            self.proc = None
            self._mute_adc()            # PCM1863 stummschalten nach Stop
            print("✅ Audio-Pipeline gestoppt")
            return True

    def _restart_pipeline(self):
        """Interner Pipeline-Neustart (für Health-Check)"""

        # Optional: ALSA-State wiederherstellen
        if self.config.alsa_state_path:
            try:
                subprocess.run(
                    ["alsactl", "--file", self.config.alsa_state_path, "restore"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                )
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] [Health-Check] 🎛️ ALSA-State wiederhergestellt")
            except (subprocess.SubprocessError, OSError) as e:
                print(f"[Health-Check] alsactl restore Fehler: {e}")

        self._mute_adc()                # PCM1863 stumm halten → GPIO20-Buskonflikt minimieren

        cmd = self._build_pipeline_cmd()
        try:
            self.proc = subprocess.Popen(
                ["/bin/bash", "-lc", cmd],
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            time.sleep(1.0)             # ALSA-Init HifiBerry braucht etwas Zeit

            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            if self.is_running():
                print(
                    f"[{ts}] [Health-Check] ✅ Pipeline neu gestartet "
                    f"(PID: {self.proc.pid})"
                )
                self._last_start_time = time.time()
                # DAPM hat PCM1863 beim Neustart hochgefahren → DOUT wieder Hi-Z
                time.sleep(0.5)
                self._pcm186x_dout_disable()
                if self._last_volume > 0:
                    self.set_volume_amixer(self._last_volume)
            else:
                print(f"[{ts}] [Health-Check] ❌ Pipeline startet nicht (sofort beendet)")
                self._mute_adc()

        except OSError as e:
            print(f"❌ Neustart-Fehler: {e}")
            self._mute_adc()

    # =========================================================================
    # CLEANUP
    # =========================================================================

    def _ensure_clean_state(self):
        """
        Stellt sicher dass keine verwaisten arecord/aplay-Prozesse laufen.
        Wichtig vor jedem Pipeline-Start (dmix/dsnoop ALSA Shared-Memory).
        """
        for _ in range(3):
            pids = self._get_audio_pids()
            if not pids:
                break
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            time.sleep(0.3)

        # ALSA-Flush: Kernel-Shared-Memory-Bereinigung (dmix/dsnoop)
        try:
            subprocess.run(
                ["aplay", "-l"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3.0,
            )
        except (subprocess.SubprocessError, OSError):
            pass

        # amixer-Aufruf: vollständigere ALSA-Reinitialisierung
        try:
            subprocess.run(
                ["amixer", "-c", self.mixer_card, "scontrols"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3.0,
            )
        except (subprocess.SubprocessError, OSError):
            pass

        time.sleep(1.5)     # Wartezeit nach SIGKILL (ALSA-Treiber HifiBerry)

    # =========================================================================
    # ALSA-CONFIG: LADEN (read-only – alsa_config.json wird nie überschrieben)
    # =========================================================================

    def _apply_alsa_config(self, path: str) -> bool:
        """Liest alsa_config.json und wendet jeden Eintrag via amixer sset an."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return False
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ALSA-Config] Lesefehler {path}: {e}")
            return False

        controls = data.get("controls", [])
        if not controls:
            return False

        applied = 0
        for entry in controls:
            name  = entry.get("name", "")
            value = entry.get("value", "")
            if not name or not value:
                continue
            try:
                subprocess.run(
                    ["amixer", "-c", self.mixer_card, "sset", name, value],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                applied += 1
            except (subprocess.SubprocessError, OSError):
                if self.config.verbose:
                    print(f"[ALSA-Config] sset '{name}' = '{value}' fehlgeschlagen")

        if self.config.verbose:
            print(f"[ALSA-Config] {applied}/{len(controls)} Controls angewendet")
        return applied > 0

    # =========================================================================
    # LAUTSTÄRKE
    # =========================================================================

    def _init_hardware_volume(self) -> None:
        """
        Setzt PCM5122-Controls beim Pipeline-Start.
        Priorität: alsa_config.json → Fallback Digital 80%.
        SoftMaster wird immer auf 100% gesetzt; der GUI-Slider überschreibt das
        kurz danach mit dem zuletzt gespeicherten Benutzerwert.
        """
        # 1. Playback-Controls aus JSON (ohne SoftMaster)
        loaded = False
        if self.config.alsa_config_path:
            loaded = self._apply_alsa_config(self.config.alsa_config_path)
            if loaded:
                print(f"🎛️  ALSA-Config geladen: {self.config.alsa_config_path}")

        if not loaded:
            # Fallback: Digital auf konservativen Startwert
            try:
                subprocess.run(
                    ["amixer", "-c", self.mixer_card, "sset", "Digital", "80%"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                if self.config.verbose:
                    print("🔊 Fallback: Digital=80%")
            except (subprocess.SubprocessError, OSError):
                pass

        # 2. SoftMaster: immer 100% bis GUI-Slider greift
        try:
            subprocess.run(
                ["amixer", "-c", self.mixer_card, "sset", "SoftMaster", "100%"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
        except (subprocess.SubprocessError, OSError):
            pass

    def set_volume_amixer(self, volume: int | None):
        """
        Setzt Benutzer-Lautstärke via amixer.

        SoftMaster (Software-Gain in asound.conf) ist der primäre Regler.
        Digital (PCM5122 Hardware-DAC) wird einmalig in _init_hardware_volume()
        auf 80% gesetzt und hier nicht mehr verändert.
        Fallback auf Digital oder Master wenn SoftMaster fehlt.
        """
        if volume is None:
            return

        vol = max(0, min(100, int(volume)))
        if vol > 0:
            self._last_volume = vol

        for control in ("SoftMaster", "Digital", "Master"):
            try:
                subprocess.run(
                    ["amixer", "-c", self.mixer_card, "sset", control,
                     f"{vol}%", "unmute"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                if self.config.verbose:
                    print(f"🔊 Volume={vol}% via amixer control '{control}'")
                return
            except (subprocess.SubprocessError, OSError):
                continue

        if self.config.verbose:
            print(
                f"⚠️ set_volume_amixer: kein gültiger Control gefunden "
                f"(Karte {self.mixer_card})"
            )

    # =========================================================================
    # RAW-CAPTURE (öffentliche API)
    # =========================================================================

    def start_raw_capture(self):
        """Öffentliche Methode: Raw-Capture starten"""
        with self._lock:
            self._start_raw_capture()

    def stop_raw_capture(self):
        """Öffentliche Methode: Raw-Capture stoppen"""
        with self._lock:
            self._stop_raw_capture()

    # =========================================================================
    # RAW-CAPTURE (intern)
    # =========================================================================

    def _start_raw_capture(self):
        """Startet Raw-Capture mit Auto-Restart Timer"""
        if self.is_raw_capture_running():
            return
        self._start_raw_capture_internal()
        if self.is_raw_capture_running():
            self._start_raw_restart_timer()

    def _start_raw_capture_internal(self):
        """Interne Start-Methode ohne Timer"""
        raw_path = os.path.expanduser("~/dab.raw")
        try:
            os.remove(raw_path)
        except FileNotFoundError:
            pass

        cmd = self._build_raw_capture_cmd(raw_path)
        try:
            self.raw_capture_proc = subprocess.Popen(
                ["/bin/bash", "-lc", cmd],
                preexec_fn=os.setsid,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            self.write_raw = True
            if self.config.verbose:
                print(f"📼 Raw-Capture gestartet → {raw_path}")
        except OSError as e:
            print(f"❌ Raw-Capture Start fehlgeschlagen: {e}")

    def _stop_raw_capture(self):
        """Stoppt Raw-Capture, Timer und löscht dab.raw"""
        self._stop_raw_restart_timer()

        if not self.is_raw_capture_running():
            self.raw_capture_proc = None
            self.write_raw = False
            self._delete_raw_file()
            return

        self._stop_raw_capture_internal()
        self._delete_raw_file()
        print("🗑️ dab.raw gelöscht")

    def _stop_raw_capture_internal(self):
        """Interne Stop-Methode"""
        if not self.is_raw_capture_running():
            self.raw_capture_proc = None
            self.write_raw = False
            return

        self._kill_process_group(self.raw_capture_proc)
        for _ in range(5):
            if not self.is_raw_capture_running():
                break
            time.sleep(0.05)

        if self.is_raw_capture_running():
            self._kill_process_group(self.raw_capture_proc, use_sigkill=True)
            time.sleep(0.05)

        self.raw_capture_proc = None
        self.write_raw = False

    @staticmethod
    def _delete_raw_file():
        """Löscht ~/dab.raw"""
        try:
            os.remove(os.path.expanduser("~/dab.raw"))
        except FileNotFoundError:
            pass

    # =========================================================================
    # RAW-CAPTURE AUTO-RESTART
    # =========================================================================

    def _start_raw_restart_timer(self):
        """Startet Thread für periodischen Raw-Capture Neustart"""
        if self._raw_restart_thread and self._raw_restart_thread.is_alive():
            return
        self._raw_restart_stop.clear()
        self._raw_restart_thread = threading.Thread(
            target=self._raw_restart_loop, daemon=True,
        )
        self._raw_restart_thread.start()

    def _stop_raw_restart_timer(self):
        """Stoppt Restart-Timer"""
        if not self._raw_restart_thread:
            return
        self._raw_restart_stop.set()
        self._raw_restart_thread.join(timeout=2.0)
        self._raw_restart_thread = None

    def _raw_restart_loop(self):
        """Periodischer Raw-Capture Neustart bei Dateigrössenüberschreitung"""
        while not self._raw_restart_stop.wait(timeout=self.config.raw_restart_interval):
            with self._lock:
                if not self.is_raw_capture_running():
                    break
                try:
                    size_mb = (
                        os.path.getsize(os.path.expanduser("~/dab.raw")) / (1024 * 1024)
                    )
                except OSError:
                    size_mb = 0

                if size_mb > self.config.raw_max_size_mb:
                    print(f"🔄 Raw-Capture Neustart (dab.raw: {size_mb:.1f} MB → 0)")
                    self._raw_restarting = True
                    try:
                        self._stop_raw_capture_internal()
                        time.sleep(0.15)
                        self._start_raw_capture_internal()
                        time.sleep(0.1)
                    finally:
                        self._raw_restarting = False

    # =========================================================================
    # HEALTH-CHECK
    # =========================================================================

    def _start_health_check(self):
        """Startet Health-Check Thread"""
        if self._health_check_thread and self._health_check_thread.is_alive():
            return
        self._health_check_stop.clear()
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop, daemon=True,
        )
        self._health_check_thread.start()

    def _stop_health_check(self):
        """Stoppt Health-Check Thread"""
        if not self._health_check_thread:
            return
        self._health_check_stop.set()
        self._health_check_thread.join(timeout=2.0)
        self._health_check_thread = None

    def _health_check_loop(self):
        """
        Health-Check Loop:
          - Prüfintervall:  alle 5s  (health_check_interval)
          - Aufgabe:        Pipeline abgestürzt? → Neustart (max. 3x)
          - Hinweis:        Praeventiver 2GB-Restart entfaellt (-t raw hat kein Limit)
        """
        while not self._health_check_stop.wait(timeout=self.config.health_check_interval):
            with self._lock:
                uptime = (
                    time.time() - self._last_start_time
                    if self._last_start_time else 0
                )
                if (
                    not self.is_running()
                    and self._restart_count < self.config.max_restart_attempts
                ):
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    exit_code = self.proc.poll() if self.proc else None
                    print(
                        f"[{ts}] [Health-Check] Pipeline abgestuerzt "
                        f"nach {uptime:.1f}s (Exit-Code: {exit_code})"
                    )
                    self._restart_count += 1
                    print(
                        f"[{ts}] [Health-Check] Neustart "
                        f"{self._restart_count}/{self.config.max_restart_attempts}..."
                    )
                    self._ensure_clean_state()
                    time.sleep(0.5)
                    self._restart_pipeline()


# =========================================================================
# FACTORY FUNCTION
# =========================================================================

def create_audio_codec(
    verbose: bool = False,
    alsa_state_path: str = "",
) -> Audio_Codec:
    """Erzeugt Audio_Codec mit Standard-Konfiguration fuer HifiBerry DAC+ADC Pro."""
    return Audio_Codec(AudioConfig(
        verbose=verbose,
        alsa_state_path=alsa_state_path,
    ))


__all__ = ["Audio_Codec", "AudioConfig", "create_audio_codec"]


# =========================================================================
# SELF-TEST
# =========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Audio Codec Test - HifiBerry DAC+ADC Pro (PCM5122 + PCM1863)")
    print("=" * 60)

    codec = create_audio_codec(verbose=True)

    print(f"\nErkannte HifiBerry Karte: {codec.mixer_card}")
    print(f"   Capture-Device:  {codec.config.capture_device}")
    print(f"   Playback-Device: {codec.config.playback_device}")
    print(f"   Format:          {codec.config.format} / {codec.config.channels}ch / {codec.config.rate} Hz")

    print("\n1  Starte Pipeline...")
    ok = codec.start_audio_codec()
    if not ok:
        print("Pipeline konnte nicht gestartet werden.")
        print("   Logs: /tmp/arecord_hifiberry.log  /tmp/aplay_hifiberry.log")
    else:
        time.sleep(2)

        print("\n2  Status:")
        for k, v in codec.get_status().items():
            print(f"   {k}: {v}")

        print("\n3  Lautstaerke -> 70 %")
        codec.set_volume_amixer(70)
        time.sleep(1)

        print("\n4  Stop...")
        codec.stop_audio_codec()

    print("\nTest beendet")