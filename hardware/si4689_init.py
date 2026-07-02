#!/usr/bin/env python3
# ('my_venv_314':venv)
"""
si4689_init.py
Initialisierung und Konfiguration des Si4689 DAB/FM-Controllers.

Firmware-Dateien (RaspiAudio HAT):
  rom00_patch.016.bin    – ROM-Patch (immer gleich, für DAB und FM)
  dab_radio_6_0_9.bin    – DAB/DAB+-Firmware
  fmhd_radio_5_3_3.bin   – FM/FMHD-Firmware

Moduswechsel DAB ↔ FM erfordert vollständigen Firmware-Reload (~10–15 s):
  reset → power_up → load_patch+firmware → boot → configure

Initialisierungsreihenfolge DAB (gemäss AN649 / RaspiAudio HAT):
  1. open()
  2. reset()
  3. power_up()
  4. load_firmware(patch, dab_firmware) → BOOT
  5. configure_i2s()
  6. configure_dab_frontend()
  7. set_dab_freq_list()
  8. set_volume()
  9. amp_enable(False)

Initialisierungsreihenfolge FM:
  1. reset()
  2. power_up()
  3. load_firmware(patch, fm_firmware) → BOOT
  4. configure_i2s()
  5. set_property(0x1710/0x1711/0x1712) ← Varactor (gleiche Werte wie DAB)
  6. fm_tune(freq_khz)
  7. amp_enable(True)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from hardware.si4689_driver import Si4689

# ---------------------------------------------------------------------------
# Firmware-Pfade
# ---------------------------------------------------------------------------
FIRMWARE_DIR   = Path("/home/weilmy/My_DAB_Si4689/hardware/firmwares")
PATCH_FILE     = FIRMWARE_DIR / "rom00_patch.016.bin"
DAB_FIRMWARE   = FIRMWARE_DIR / "dab_radio_6_0_9.bin"
FM_FIRMWARE    = FIRMWARE_DIR / "fmhd_radio_5_3_3.bin"

# FM-Varactor-Kalibrierung (gleiche Werte wie DAB, RaspiAudio-Board)
_FM_VARM = 0xFD12
_FM_VARB = 0x009B
_FM_CFG  = 0x0000


class Si4689Manager:
    """
    Verwaltet den Si4689 für die Hauptapplikation.

    Unterstützt DAB- und FM-Modus mit vollständigem Firmware-Switching.
    Alle öffentlichen Methoden sind threadsicher (RLock).

    Modus-Wechsel:
        # DAB → FM (Firmware-Reload ~10–15 s):
        self.si4689.switch_to_fm(freq_mhz=101.7, progress_cb=my_callback)

        # FM → DAB (Firmware-Reload ~10–15 s):
        self.si4689.switch_to_dab()

    Typischer Startup (DAB):
        self.si4689 = Si4689Manager()
        if not self.si4689.initialize():
            print("Si4689 nicht bereit")
    """

    def __init__(
        self,
        firmware_dir: Path = FIRMWARE_DIR,
        patch_file: Path = PATCH_FILE,
        dab_firmware: Path = DAB_FIRMWARE,
        fm_firmware: Path = FM_FIRMWARE,
        verbose: bool = False,
    ) -> None:
        self.firmware_dir  = Path(firmware_dir)
        self.patch_file    = Path(patch_file)
        self.dab_firmware  = Path(dab_firmware)
        self.fm_firmware   = Path(fm_firmware)
        self.verbose       = verbose

        self._radio: Optional[Si4689] = None
        self._initialized: bool = False
        self._current_mode: str = "none"   # "dab" | "fm" | "none"
        self._post_reload: bool = False    # True nach jedem Firmware-Reload (cold start)
        self._lock = threading.RLock()

        # RDS-Puffer (werden bei jedem FM-Moduswechsel zurückgesetzt)
        self._rds_ps_curr: list  = [" "] * 8     # aktueller PS-Scan (wird befüllt)
        self._rds_ps_mask: int   = 0             # Bit-Maske empfangener Segmente (0x0F = komplett)
        self._rds_ps_stable: str = ""            # letzter vollständiger PS-String
        self._rds_ps_scroll: list[str] = []      # Dynamic PS: laufend akkumulierte Frames (aktueller Zyklus)
        self._rds_ps_prev_cycle: list[str] = []  # Dynamic PS: Frames des letzten abgeschlossenen Zyklus
        self._rds_ps_scroll_display: str = ""    # Dynamic PS: letzter bestätigter Anzeigetext (nur an Zyklusgrenzen)
        self._rds_ps_pending: str = ""           # Stability-Gate: aktuell geprüfter Frame
        self._rds_ps_pending_count: int = 0      # Stability-Gate: wie oft bisher hintereinander gesehen
        self._rds_ps_warmup_until: float = 0.0   # Startup-Filter: PS vor diesem Zeitstempel ignorieren
        self._rds_rt: list      = ['\x00'] * 64  # RadioText; '\x00'=leer, '\x0D'=Ende
        self._rds_rt_prev: list = ['\x00'] * 64  # RT-Vorwerte pro Segment (Konflikt-Erkennung)
        self._rds_ab: object = None              # A/B-Flag für RT-Reset
        self._rds_supported: bool = False        # True erst nach erfolgreichem Aktivieren

    # =======================================================================
    # DAB-Startup (wie bisher)
    # =======================================================================

    def initialize(self) -> bool:
        """
        Vollständige DAB-Initialisierungssequenz.
        Gibt True zurück wenn bereit, False bei Fehler.
        """
        with self._lock:
            try:
                self._radio = Si4689(
                    firmware_dir=self.firmware_dir,
                    verbose=self.verbose,
                )
                self._radio.open()
                print("  [Si4689] open() ✓")

                self._radio.reset()
                print("  [Si4689] reset() ✓")

                self._radio.power_up()
                print("  [Si4689] power_up() ✓")

                self._radio.load_firmware(
                    patch_path=self.patch_file,
                    firmware_path=self.dab_firmware,
                )
                print("  [Si4689] load_firmware(DAB) ✓")

                self._radio.configure_i2s(master=False)
                print("  [Si4689] configure_i2s() ✓")

                self._radio.configure_dab_frontend()
                print("  [Si4689] configure_dab_frontend() ✓")

                self._radio.set_dab_freq_list()
                print("  [Si4689] set_dab_freq_list() ✓")

                self._radio.set_volume(45)
                print("  [Si4689] set_volume(45) ✓")

                self._radio.amp_enable(False)
                print("  [Si4689] amp_enable(False) ✓")

                self._initialized = True
                self._current_mode = "dab"
                self._post_reload = True   # Erster Tune braucht extended FIC-Timeout
                print("✅ Si4689 initialisiert (DAB-Modus).")
                return True

            except Exception as exc:
                print(f"❌ Si4689 Initialisierung fehlgeschlagen: {exc}")
                self._safe_close()
                return False

    # =======================================================================
    # Modus-Wechsel
    # =======================================================================

    def switch_to_fm(
        self,
        freq_mhz: float = 101.7,
        progress_cb=None,
    ) -> bool:
        """
        Chip von DAB auf FM umschalten (vollständiger Firmware-Reload).

        Dauer: ~10–15 Sekunden (Firmware-Load).
        Wird im Dispatcher-Thread aufgerufen – NICHT im GUI-Thread!

        Parameter
        ---------
        freq_mhz : float
            Erste FM-Frequenz nach dem Boot (z.B. 101.7 für SRF 1 Bern).
        progress_cb : callable | None
            Optionaler Callback f(text: str) für Fortschrittsmeldungen.
            Wird im Dispatcher-Thread aufgerufen (NICHT GUI-sicher!).
            Beispiel: lambda t: app.gui_batcher.schedule_update(lambda: ...)

        Rückgabe
        --------
        True wenn erfolgreich, False bei Fehler.
        """
        with self._lock:
            if self._current_mode == "fm":
                # Schon im FM-Modus → nur tunen
                return self._fm_tune_only(freq_mhz)

            def _progress(text: str) -> None:
                print(f"[FM-Switch] {text}")
                if progress_cb:
                    try:
                        progress_cb(text)
                    except Exception:
                        pass

            _progress("FM-Firmware laden …")
            try:
                self._reload_chip(
                    firmware_path=self.fm_firmware,
                    mode_label="FM",
                    progress_cb=_progress,
                )
            except Exception as exc:
                print(f"[FM-Switch] Fehler: {exc}")
                self._initialized = False
                self._current_mode = "none"
                return False

            # FM-Varactor setzen (gleiche Werte wie DAB-Frontend)
            _progress("FM-Frontend konfigurieren …")
            try:
                self._radio.set_property(0x1710, _FM_VARM)
                self._radio.set_property(0x1711, _FM_VARB)
                self._radio.set_property(0x1712, _FM_CFG)
            except Exception as exc:
                # Varactor ist optional – FM kann auch ohne kalibriert werden
                print(f"[FM-Switch] Varactor-Warnung (nicht kritisch): {exc}")

            # FM-Audio-Eigenschaften (AN649 Table 4)
            try:
                # FM_AUDIO_DE_EMPHASIS (0x3900): 0=75µs (USA), 1=50µs (Europa/CH)
                self._radio.set_property(0x3900, 0x0001)
                # FM_SOFTMUTE_SNR_ATTENUATION (0x3501): 0 = Softmute deaktiviert
                # (Standard 8 dB würde FM gegenüber DAB leiser machen)
                self._radio.set_property(0x3501, 0x0000)
                print("[FM-Switch] FM-Audio: 50µs De-Emphasis, Softmute deaktiviert")
            except Exception as exc:
                print(f"[FM-Switch] FM-Audio-Warnung: {exc}")

            # RDS aktivieren (AN649, FMHD-Firmware)
            try:
                # INT_CTL_ENABLE (0x0000): RDSINT-Bit (Bit2) freischalten
                self._radio.set_property(0x0000, 0x0004)
                # FM_RDS_INTERRUPT_SOURCE (0x3C00): RDSRECV=1
                self._radio.set_property(0x3C00, 0x0001)
                # FM_RDS_INTERRUPT_FIFO_COUNT (0x3C01): Interrupt bei jeder Gruppe
                self._radio.set_property(0x3C01, 0x0001)
                # FM_RDS_CONFIG (0x3C02): RDSEN=1 (RDS-Empfänger einschalten)
                self._radio.set_property(0x3C02, 0x0001)
                self._rds_supported = True
                print("[FM-Switch] RDS aktiviert")
            except Exception as exc:
                self._rds_supported = False
                print(f"[FM-Switch] RDS nicht verfügbar: {exc}")

            # Erste Frequenz einstimmen
            _progress(f" ")
            try:
                freq_khz = int(round(freq_mhz * 1000))
                self._radio.fm_tune(freq_khz)
                time.sleep(0.4)
            except Exception as exc:
                print(f"[FM-Switch] fm_tune Fehler: {exc}")
                return False

            self._current_mode = "fm"
            self._reset_rds_buffers()
            return True

    def switch_to_dab(self, progress_cb=None) -> bool:
        """
        Chip von FM auf DAB umschalten (vollständiger Firmware-Reload).

        Dauer: ~10–15 Sekunden.
        Wird im Dispatcher-Thread aufgerufen.

        Parameter
        ---------
        progress_cb : callable | None
            Optionaler Fortschritts-Callback f(text: str).
        """
        with self._lock:
            if self._current_mode == "dab":
                return True   # Schon DAB, nichts zu tun

            def _progress(text: str) -> None:
                print(f"[DAB-Switch] {text}")
                if progress_cb:
                    try:
                        progress_cb(text)
                    except Exception:
                        pass

            _progress("DAB-Firmware laden …")
            try:
                self._reload_chip(
                    firmware_path=self.dab_firmware,
                    mode_label="DAB",
                    progress_cb=_progress,
                )
            except Exception as exc:
                print(f"[DAB-Switch] Fehler: {exc}")
                self._initialized = False
                self._current_mode = "none"
                return False

            # DAB-Frontend konfigurieren + Frequenzliste laden
            _progress("DAB-Frontend konfigurieren …")
            try:
                self._radio.configure_dab_frontend()
                self._radio.set_dab_freq_list()
            except Exception as exc:
                print(f"[DAB-Switch] DAB-Konfiguration Fehler: {exc}")
                return False

            self._current_mode = "dab"
            self._post_reload = True   # Cold start: nächster Tune braucht extended FIC-Timeout
            _progress("✅ DAB aktiv")
            return True

    # =======================================================================
    # Interner Reload
    # =======================================================================

    def _reload_chip(
        self,
        firmware_path: Path,
        mode_label: str,
        progress_cb=None,
    ) -> None:
        """
        Vollständiger Reset + Firmware-Reload des Chips.
        KEIN Neuöffnen von SPI/GPIO – nur Reset+Power-Cycle+Firmware.

        Wirft Exception bei Fehler.
        """
        def _p(t):
            if progress_cb:
                progress_cb(t)

        # SPI-Verbindung muss offen sein
        if self._radio is None or not self._radio._opened:
            raise RuntimeError("Si4689 nicht geöffnet – reload_chip() nicht möglich")

        if not Path(firmware_path).exists():
            raise FileNotFoundError(f"Firmware nicht gefunden: {firmware_path}")

        _p(f"Reset …")
        self._radio.reset()

        _p(f"POWER_UP …")
        self._radio.power_up()

        fw_name = Path(firmware_path).name
        fw_kb = Path(firmware_path).stat().st_size // 1024
        _p(f"Lade {mode_label}-Firmware ({fw_name}, {fw_kb} KB) …")
        self._radio.load_firmware(
            patch_path=self.patch_file,
            firmware_path=firmware_path,
        )
        _p(f"{mode_label}-Firmware gestartet.")

        _p("I2S konfigurieren …")
        self._radio.configure_i2s(master=False)
        time.sleep(0.15)   # PIN_CONFIG_ENABLE braucht etwas Zeit

        self._initialized = True

    def _fm_tune_only(self, freq_mhz: float) -> bool:
        """Nur tunen, wenn Chip schon im FM-Modus ist."""
        try:
            freq_khz = int(round(freq_mhz * 1000))
            self._radio.fm_tune(freq_khz)
            time.sleep(0.3)
            return True
        except Exception as exc:
            print(f"[Si4689] fm_tune_only Fehler: {exc}")
            return False

    # =======================================================================
    # Cleanup
    # =======================================================================

    def close(self) -> None:
        with self._lock:
            self._safe_close()
            print("[Si4689] Geschlossen.")

    def _safe_close(self) -> None:
        if self._radio is not None:
            try:
                self._radio.amp_enable(False)
            except Exception:
                pass
            try:
                self._radio.close()
            except Exception:
                pass
            self._radio = None
        self._initialized = False
        self._current_mode = "none"

    # =======================================================================
    # Status
    # =======================================================================

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._radio is not None

    @property
    def current_mode(self) -> str:
        """Aktueller Modus: 'dab' | 'fm' | 'none'"""
        return self._current_mode

    def get_sys_state(self) -> dict:
        with self._lock:
            if not self.is_ready:
                return {}
            try:
                return self._radio.get_sys_state()
            except Exception:
                return {}

    # =======================================================================
    # DAB-Steuerung
    # =======================================================================

    def dab_tune(self, channel: str) -> bool:
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return False
            try:
                self._radio.dab_tune(channel)
                # Auf STCINT (Tune Complete, bit0 von STATUS0) warten statt fixer 1s-Pause.
                # Nach Firmware-Reload dauert der VCO-Lock länger als 1s.
                spi = self._radio._spi
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if spi.xfer2([0x00, 0x00])[1] & 0x01:   # STCINT gesetzt
                        break
                    time.sleep(0.05)
                return True
            except Exception as exc:
                print(f"[Si4689] dab_tune({channel}) Fehler: {exc}")
                return False


    def dab_start_service(self, service_id: int, component_id: int) -> bool:
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return False

            # 1) STCINT quittieren
            try:
                self._radio.dab_digrad_status(stc_ack=True, attune=False)
            except Exception:
                pass

            # 2) Warten auf VALID + ACQ + FIC ≥ 90% (Service-Liste vollständig).
            # Nach Firmware-Reload (cold start) braucht der Chip 10–15s für
            # OFDM-Sync → Ensemble-Acquisition → FIC 90%; normales Kanalwechseln
            # (Chip bereits synchronisiert) benötigt nur 3–8s.
            fic_timeout = 15.0 if self._post_reload else 8.0
            self._post_reload = False   # Flag nach erstem Gebrauch löschen
            valid_achieved = False
            deadline = time.monotonic() + fic_timeout
            while time.monotonic() < deadline:
                try:
                    st = self._radio.dab_digrad_status(stc_ack=False, attune=False)
                    if (st.get("valid") and st.get("acq")
                            and st.get("fic_quality", 0) >= 90):
                        valid_achieved = True
                        break
                except Exception:
                    pass
                time.sleep(0.05)

            if not valid_achieved:
                print(f"[Si4689] dab_start_service: Kein FIC/Signal – FIC nicht bereit nach {fic_timeout:.0f}s - "
                    f"SID=0x{service_id:08X} nicht erreichbar")
                return False

            # 3) STCINT nochmals quittieren (kann während FIC-Poll neu gesetzt werden)
            try:
                self._radio.dab_digrad_status(stc_ack=True, attune=False)
            except Exception:
                pass

            # 3b) Auf SRVLST-Event warten: Service-Liste kann nach FIC=90% noch
            # 1–3s unvollständig sein. START_DIGITAL_SERVICE liefert 0xC0 (ERR)
            # wenn der Service-Eintrag (MCI) noch nicht dekodiert wurde.
            srvlist_deadline = time.monotonic() + 5.0
            while time.monotonic() < srvlist_deadline:
                try:
                    ev = self._radio.dab_get_event_status(ack=False)
                    if ev.get("srvlist"):
                        self._radio.dab_get_event_status(ack=True)
                        break
                except Exception:
                    pass
                time.sleep(0.1)

            # 4) Service starten – Retry bei 0xC0 (Service-Liste noch nicht vollständig,
            # obwohl SRVLST bereits gesetzt war oder Timeout erreicht wurde).
            for attempt in range(4):
                try:
                    self._radio.dab_start_service(service_id, component_id)
                    return True
                except RuntimeError as exc:
                    if "Kommandofehler" in str(exc) and attempt < 3:
                        time.sleep(0.5)
                        continue
                    print(f"[Si4689] dab_start_service Fehler: {exc}")
                    return False
                except Exception as exc:
                    print(f"[Si4689] dab_start_service Fehler: {exc}")
                    return False
            return False

    def dab_stop_service(self, service_id: int, component_id: int) -> bool:
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return False
            try:
                self._radio.dab_stop_service(service_id, component_id)
                return True
            except Exception as exc:
                            print(f"[Si4689] dab_stop_service Fehler: {exc}")
                            return False

    def read_anno(self) -> bool:
        """True, wenn aktuell eine DAB-Announcement aktiv ist (ANNO-Flag).

        Liest DAB_GET_EVENT_STATUS ohne Quittierung. Das ist der einzige
        verlässliche TA-Trigger (siehe ta_controller). Thread-sicher.
        """
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return False
            try:
                ev = self._radio.dab_get_event_status(ack=False)
                return bool(ev.get("anno"))
            except Exception:
                return False

    def enable_announcements(self, types_mask: int = 0x07FF) -> bool:
        """DAB-Announcements aktivieren – EINMAL nach dem DAB-Tune aufrufen.

        Setzt DAB_ANNOUNCEMENT_ENABLE (0xB700) auf die gewünschten
        Announcement-Typen und aktiviert die ANNO-Interrupt-Quelle
        (DAB_EVENT_INTERRUPT_SOURCE 0xB300, Bit4). Für SRG genügt 0x07FF
        (alle 11 Typen), da kein TRAFFIC-Bit gesetzt wird.
        """
        PROP_ANNOUNCEMENT_ENABLE    = 0xB700
        PROP_EVENT_INTERRUPT_SOURCE = 0xB300
        ANNO_INT_BIT                = 0x0010
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return False
            try:
                self._radio.set_property(PROP_ANNOUNCEMENT_ENABLE, types_mask)
                self._radio.set_property(PROP_EVENT_INTERRUPT_SOURCE, ANNO_INT_BIT)
                return True
            except Exception as exc:
                print(f"[Si4689] enable_announcements Fehler: {exc}")
                return False

    def dab_digrad_status(self) -> dict:
        with self._lock:
            if not self.is_ready or self._current_mode != "dab":
                return {}
            try:
                return self._radio.dab_digrad_status(stc_ack=False, attune=False)
            except Exception:
                return {}

    # =======================================================================
    # FM-Steuerung
    # =======================================================================

    def _reset_rds_buffers(self) -> None:
        """RDS-Puffer leeren – bei jedem Frequenzwechsel aufrufen."""
        self._rds_ps_curr   = [" "] * 8
        self._rds_ps_mask   = 0
        self._rds_ps_stable = ""
        self._rds_ps_scroll = []
        self._rds_ps_prev_cycle = []
        self._rds_ps_scroll_display = ""
        self._rds_ps_pending = ""
        self._rds_ps_pending_count = 0
        self._rds_ps_warmup_until = time.monotonic() + 2.0
        self._rds_rt      = ['\x00'] * 64
        self._rds_rt_prev = ['\x00'] * 64
        self._rds_ab  = None

    def fm_tune(self, freq_mhz: float) -> bool:
        """FM-Frequenz in MHz einstimmen. Chip muss im FM-Modus sein."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return False
            self._reset_rds_buffers()
            return self._fm_tune_only(freq_mhz)

    def fm_tune_for_scan(self, freq_khz: int) -> bool:
        """FM-Frequenz einstimmen und auf STCINT (Tune Complete) warten.
        Pollt STCINT statt fixer 60ms-Pause – verhindert 0xC0 an Bandgrenzen,
        wo der Chip mehr als 60ms zum VCO-Lock braucht."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return False
            try:
                self._radio.fm_tune(freq_khz)
                # Aktiv auf STCINT=bit0 warten statt fixer Pause
                # CTS (bit7) = Befehl akzeptiert, STCINT = Tune tatsächlich abgeschlossen
                spi = self._radio._spi
                deadline = time.monotonic() + 0.5   # max 500ms
                while time.monotonic() < deadline:
                    if spi.xfer2([0x00, 0x00])[1] & 0x01:   # STCINT gesetzt
                        break
                    time.sleep(0.005)
                return True
            except Exception as exc:
                print(f"[Si4689] fm_tune_for_scan Fehler: {exc}")
                return False

    def fm_tune_and_check(self, freq_mhz: float) -> dict:
        """Tune to freq_mhz, wait for STCINT, return RSQ dict.
        Per-step helper for the manual scan loop.
        Lock held for the full sequence (~70–500 ms). Returns {} on failure."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return {}
            try:
                freq_khz = int(round(freq_mhz * 1000))
                spi = self._radio._spi

                # 1. Clear any leftover STCINT + RDS-Daten vom vorherigen Schritt
                self._reset_rds_buffers()
                self._radio.fm_rsq_status(stcack=True)

                # 2. Send FM_TUNE_FREQ (waits for CTS, not for STCINT)
                self._radio.fm_tune(freq_khz)

                # 3. Poll STCINT (tune complete) – 10 ms intervals, 500 ms max
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    if spi.xfer2([0x00, 0x00])[1] & 0x01:   # STCINT = bit 0
                        break
                    time.sleep(0.01)

                # 4. Read RSQ status + clear STCINT for the next step
                return self._radio.fm_rsq_status(stcack=True)

            except Exception as exc:
                print(f"[Si4689] fm_tune_and_check Fehler: {exc}")
                return {}

    def fm_rsq_status(self, stcack: bool = False) -> dict:
        """FM_RSQ_STATUS – Empfangspegel lesen."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return {}
            try:
                return self._radio.fm_rsq_status(stcack=stcack)
            except Exception:
                return {}

    def fm_seek_start(self, seekup: bool = True, wrap: bool = True) -> bool:
        """FM_SEEK_START (0x31) – Sendersuche starten."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return False
            try:
                from hardware.si4689_driver import CMD_FM_SEEK_START
                arg1 = (0x01 if seekup else 0x00) | (0x04 if wrap else 0x00)  # WRAP=Bit2
                self._radio._write_command([CMD_FM_SEEK_START, arg1])
                return True
            except Exception as exc:
                print(f"[Si4689] fm_seek_start Fehler: {exc}")
                return False

    def fm_wait_stc(self, timeout: float = 8.0) -> bool:
        """Auf Seek/Tune Complete warten (nur für fm_seek_start)."""
        # KEIN serial_lock hier – fm_wait_stc pollt lange
        # Aufrufer muss sicherstellen dass keine anderen SPI-Zugriffe laufen
        if not self.is_ready or self._current_mode != "fm":
            return False
        try:
            return self._radio.fm_wait_stc(timeout=timeout)
        except AttributeError:
            # fm_wait_stc noch nicht im Driver → Fallback-Implementierung
            deadline = time.monotonic() + timeout
            spi = self._radio._spi
            if spi is None:
                return False
            while time.monotonic() < deadline:
                status = spi.xfer2([0x00, 0x00])[1]
                if status & 0x01:
                    return True
                time.sleep(0.1)
            return False
        except Exception:
            return False

    def _rt_string(self) -> str:
        """RT-Anzeige-String aus _rds_rt bauen.

        Nur anzeigen wenn Segment 0 gefüllt ist (kein Fragment aus der Mitte).
        Zeichenkette endet am ersten CR (0x0D) oder Null (0x00)."""
        if self._rds_rt[0] in ('\x00', '\x0D'):
            return ""
        chars = []
        for c in self._rds_rt:
            if c in ('\x00', '\x0D'):
                break
            chars.append(c)
        return "".join(chars).strip()

    def _ps_scroll_text(self) -> str:
        """Dynamic PS: letzten bestätigten Zyklus-Text zurückgeben.

        Wird nur an Zyklusgrenzen aktualisiert (wenn ein Frame wiederholt erscheint).
        Transition-Artefakte werden durch Vergleich mit dem Vorzyklus herausgefiltert."""
        return self._rds_ps_scroll_display

    def fm_rds_poll(self, max_groups: int = 16) -> dict:
        """Liest bis zu max_groups RDS-Gruppen und aktualisiert PS/RT-Puffer.

        PS: Anzeige sobald alle 4 Segmente (0-3) eines Scans empfangen wurden.
        Puffer wird bei Segment 0 zurückgesetzt → verhindert Segment-Mischung.
        Funktioniert auch für Dynamic PS (scrollende Stationen).
        RT: Kontinuierlich befüllt, Anzeige sobald nicht leer.

        Rückgabe: {"ps": str, "rt": str} oder {} bei Fehler/inaktiv."""
        with self._lock:
            if not self.is_ready or self._current_mode != "fm":
                return {}
            if not self._rds_supported:
                return {}
            try:
                spi = self._radio._spi
                if spi is None:
                    return {}

                status = spi.xfer2([0x00, 0x00])[1]
                if not (status & 0x04):
                    # Kein neues Daten – gecachte Werte zurückgeben
                    return {
                        "ps":        self._rds_ps_stable,
                        "rt":        self._rt_string(),
                        "ps_scroll": self._ps_scroll_text(),
                    }

                def _parse(grp: dict) -> None:
                    b_b = grp["block_b"]
                    b_c = grp["block_c"]
                    b_d = grp["block_d"]
                    rds_group = b_b >> 11   # 5 Bit: (Gruppentyp << 1) | Version

                    if rds_group in (0, 1):    # Gruppe 0A / 0B: PS-Name
                        seg = b_b & 0x03
                        if seg == 0:
                            # Neuer Scan-Zyklus beginnt: Puffer und Maske zurücksetzen.
                            # Verhindert Mischung aus alten und neuen Segmenten.
                            self._rds_ps_curr = [" "] * 8
                            self._rds_ps_mask = 0
                        for i, byte in enumerate([(b_d >> 8) & 0xFF, b_d & 0xFF]):
                            if 0x20 <= byte < 0x80:
                                self._rds_ps_curr[seg * 2 + i] = chr(byte)
                        self._rds_ps_mask |= (1 << seg)
                        if seg == 3 and self._rds_ps_mask == 0x0F:
                            # Alle 4 Segmente vollständig – sofort anzeigen.
                            # Kein Doppelpuffer-Vergleich: funktioniert auch für
                            # Dynamic PS (Stationen, die Songtitel durch PS scrollen).
                            self._rds_ps_stable = "".join(self._rds_ps_curr).strip()
                            self._rds_ps_mask = 0
                            # Dynamic PS Akkumulation mit Stability-Gate.
                            #
                            # Warmup-Phase (2 s nach Tune): alle PS-Frames ignorieren,
                            # da der Chip kurz nach dem Tunen noch nicht stabil ist und
                            # Rauschen als gültige Gruppen liefern kann.
                            #
                            # Stability-Gate: ein Frame wird erst akzeptiert, wenn er
                            # mindestens 3 Mal hintereinander empfangen wurde. Das filtert
                            # Einzel-/Doppeltreffer (Startup-Artefakte, kurze Transitions),
                            # ohne echte Frames (die ~2 s lang = 5–10 Treffer erscheinen) zu
                            # verwerfen.
                            #
                            # Zyklusende: ein früher gesehener Frame erscheint wieder (nach
                            # mindestens einem anderen Frame dazwischen) → Anzeigetext wird
                            # aktualisiert, kein wachsender Zwischenstand sichtbar.
                            new_ps = self._rds_ps_stable
                            if new_ps and len(new_ps.replace(" ", "")) >= 2:
                                if new_ps == self._rds_ps_pending:
                                    self._rds_ps_pending_count += 1
                                else:
                                    self._rds_ps_pending = new_ps
                                    self._rds_ps_pending_count = 1

                                if (self._rds_ps_pending_count >= 3
                                        and time.monotonic() >= self._rds_ps_warmup_until):
                                    last = (self._rds_ps_scroll[-1]
                                            if self._rds_ps_scroll else None)
                                    if new_ps in self._rds_ps_scroll and last != new_ps:
                                        # Zyklus abgeschlossen
                                        curr = self._rds_ps_scroll
                                        if len(curr) >= 2:
                                            self._rds_ps_scroll_display = "  ".join(curr)
                                        self._rds_ps_scroll = [new_ps]
                                    elif last != new_ps:
                                        # Neuer stabiler Frame
                                        self._rds_ps_scroll.append(new_ps)
                                        if len(self._rds_ps_scroll) > 12:
                                            self._rds_ps_scroll.pop(0)

                    elif rds_group in (4, 5):  # Gruppe 2A / 2B: RadioText
                        ab = (b_b >> 4) & 0x01
                        if self._rds_ab is not None and ab != self._rds_ab:
                            # A/B-Wechsel = neuer RT → beide Puffer leeren
                            self._rds_rt      = ['\x00'] * 64
                            self._rds_rt_prev = ['\x00'] * 64
                        self._rds_ab = ab
                        if rds_group == 4:     # 2A: 4 Zeichen aus BLOCKC + BLOCKD
                            seg = b_b & 0x0F
                            pos = seg * 4
                            raws = [(b_c >> 8) & 0xFF, b_c & 0xFF,
                                    (b_d >> 8) & 0xFF, b_d & 0xFF]
                            buf_limit = 64
                        else:                  # 2B: 2 Zeichen nur aus BLOCKD (BLOCKC = PI)
                            seg = b_b & 0x0F
                            pos = seg * 2
                            raws = [(b_d >> 8) & 0xFF, b_d & 0xFF]
                            buf_limit = 32

                        new_ch = []
                        for raw in raws:
                            if 0x20 <= raw < 0x80:
                                new_ch.append(chr(raw))
                            elif raw == 0x0D:
                                new_ch.append('\x0D')
                            else:
                                new_ch.append('\x00')
                        # Konflikt-Erkennung (DABShield-Prinzip):
                        # Vorwert war gültig und weicht vom neuen Wert ab
                        # → veraltete FIFO-Daten oder Senderwechsel → alles leeren
                        n = min(len(raws), buf_limit - pos)
                        if n > 0:
                            conflict = any(
                                self._rds_rt_prev[pos+i] not in ('\x00', '\x0D', new_ch[i])
                                and new_ch[i] not in ('\x00', '\x0D')
                                for i in range(n)
                            )
                            if conflict:
                                self._rds_rt      = ['\x00'] * 64
                                self._rds_rt_prev = ['\x00'] * 64
                            # Vorherige Werte sichern, neue schreiben
                            for i in range(n):
                                self._rds_rt_prev[pos+i] = self._rds_rt[pos+i]
                                self._rds_rt[pos+i] = new_ch[i]
                                if new_ch[i] == '\x0D':
                                    for j in range(pos+i+1, 64):
                                        self._rds_rt[j] = '\x00'
                                    break

                # FIFO leeren: RDSFIFOUSED jeder Antwort steuert die Schleife.
                # Spec (AN649 S.37): RDSFIFOUSED = Anzahl Einträge inkl. aktuellem.
                # → 0 = kein gültiger Eintrag; ≤ 1 = letzter Eintrag, danach fertig.
                # Fehler innerhalb der Schleife (z.B. leerer FIFO nach RDSSYNCINT)
                # werden still abgefangen – kein Log-Spam.
                intack = True
                for _ in range(max_groups):
                    try:
                        grp = self._radio.fm_rds_status(intack=intack)
                    except Exception:
                        break   # transient → stop, nächster Poll versucht erneut
                    intack = False
                    fifo_n = grp.get("rdsfifoused", 0)
                    if (fifo_n > 0
                            and grp.get("rdssync")
                            and grp.get("bleb", 3) < 3):
                        _parse(grp)
                    if fifo_n <= 1:
                        break

                return {
                    "ps":        self._rds_ps_stable,
                    "rt":        self._rt_string(),
                    "ps_scroll": self._ps_scroll_text(),
                }

            except Exception as exc:
                print(f"[Si4689] fm_rds_poll temporärer Fehler: {exc}")
                return {
                    "ps":        self._rds_ps_stable,
                    "rt":        self._rt_string(),
                    "ps_scroll": self._ps_scroll_text(),
                }

    # =======================================================================
    # Audio-Steuerung
    # =======================================================================

    def set_volume(self, level: int) -> int:
        with self._lock:
            if not self.is_ready:
                return 0
            try:
                return self._radio.set_volume(level)
            except Exception as exc:
                print(f"[Si4689] set_volume Fehler: {exc}")
                return 0

    def amp_enable(self, enable: bool) -> None:
        with self._lock:
            if not self.is_ready:
                return
            try:
                self._radio.amp_enable(enable)
            except Exception as exc:
                print(f"[Si4689] amp_enable Fehler: {exc}")