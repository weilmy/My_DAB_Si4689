#!/usr/bin/env python3
# ('my_venv_314':venv)

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

"""
page_04.py  –  DAB Sender-Scan, Datenbank, Datenkomplettierung
==============================================================
Bereich 1: Sender-Scannen (dieser Abschnitt)
    - Autoscan via btn_autoscan
    - Läuft im Hintergrund-Thread (GUI bleibt responsiv)
    - Host-Load Boot-Sequenz (Firmware aus Dateien)
    - Ergebnis → SQLite + Treeview

Bereich 2: Si4689-interne Datenbank       (TODO)
Bereich 3: Datenkomplettierung/Ensembles  (TODO)
"""

import os
import threading
import traceback
import sqlite3
import datetime
from typing import Optional, List, Dict, Any, Tuple, Callable
import tkinter as tk
from tkinter import ttk, messagebox
import time
from pathlib import Path

from .base_page import BasePage


# ===========================================================================
#  Hardware-Konstanten  (RaspiAudio DAB HAT)
# ===========================================================================

_PIN_RESET   = 25          # GPIO BCM  → Reset Si4689
_PIN_INT     = 23          # GPIO BCM  → /INT Si4689 (aktiv LOW, optional)
_PIN_AMP     = 17          # GPIO BCM  → Verstärker Enable (aktiv HIGH)

_SPI_BUS     = 0
_SPI_DEVICE  = 0
_SPI_SPEED   = 10_000_000  # 10 MHz

# Firmware-Dateien (Host-Load)
_FIRMWARE_DIR  = Path("/home/weilmy/My_DAB_Si4689/hardware/firmwares")
_FW_PATCH      = _FIRMWARE_DIR / "rom00_patch.016.bin"
_FW_DAB        = _FIRMWARE_DIR / "dab_radio_6_0_9.bin"

# ===========================================================================
#  DAB Band III Frequenzliste
# ===========================================================================

_DAB_BAND_III: List[Tuple[str, int]] = [
    ("5A",  174_928), ("5B",  176_640), ("5C",  178_352), ("5D",  180_064),
    ("6A",  181_936), ("6B",  183_648), ("6C",  185_360), ("6D",  187_072),
    ("7A",  188_928), ("7B",  190_640), ("7C",  192_352), ("7D",  194_064),
    ("8A",  195_936), ("8B",  197_648), ("8C",  199_360), ("8D",  201_072),
    ("9A",  202_928), ("9B",  204_640), ("9C",  206_352), ("9D",  208_064),
    ("10A", 209_936), ("10B", 211_648), ("10C", 213_360), ("10D", 215_072),
    ("10N", 210_096),
    ("11A", 216_928), ("11B", 218_640), ("11C", 220_352), ("11D", 222_064),
    ("11N", 217_088),
    ("12A", 223_936), ("12B", 225_648), ("12C", 227_360), ("12D", 229_072),
    ("12N", 224_096),
    ("13A", 230_784), ("13B", 232_496), ("13C", 234_208), ("13D", 235_776),
    ("13E", 237_488), ("13F", 239_200),
]

# ===========================================================================
#  PTY-Tabelle  (Programme Type Codes, Monkeyboard-Doku)
# ===========================================================================

PTY_MAP: dict[int, str] = {
    0:  "<Prg Type N/A>",   1: "News",              2: "Current Affairs",
    3:  "Information",      4: "Sport",              5: "Education",
    6:  "Drama",            7: "Arts",               8: "Science",
    9:  "Talk",            10: "Pop Music",          11: "Rock Music",
    12: "Easy Listening",  13: "Light Classical",    14: "Classical Music",
    15: "Other Music",     16: "Weather",            17: "Finance",
    18: "Children's",      19: "Factual",            20: "Religion",
    21: "Phone In",        22: "Travel",             23: "Leisure",
    24: "Jazz and Blues",  25: "Country Music",      26: "National Music",
    27: "Oldies Music",    28: "Folk Music",         29: "Documentary",
    30: "<Undefined>",     31: "<Undefined>",
}


# ===========================================================================
#  Si4689 Befehlskonstanten
# ===========================================================================

_CMD_POWER_UP                 = 0x01
_CMD_HOST_LOAD                = 0x04
_CMD_LOAD_INIT                = 0x06
_CMD_BOOT                     = 0x07
_CMD_SET_PROPERTY             = 0x13
_CMD_DAB_TUNE_FREQ            = 0xB0
_CMD_DAB_DIGRAD_STATUS        = 0xB2
_CMD_DAB_GET_EVENT_STATUS     = 0xB3
_CMD_DAB_SET_FREQ_LIST        = 0xB8
_CMD_GET_DIGITAL_SERVICE_LIST = 0x80

_PROP_DAB_TUNE_FE_VARM           = 0x1710
_PROP_DAB_TUNE_FE_VARB           = 0x1711
_PROP_DAB_TUNE_FE_CFG            = 0x1712
_PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
_PROP_DAB_VALID_RSSI_THRESHOLD   = 0xB201


def _signed_byte(v: int) -> int:
    return v - 256 if v & 0x80 else v


# ===========================================================================
#  _Si4689Radio  –  Minimaler SPI-Treiber (nur Scan-relevante Methoden)
#  Entspricht Si4689Scanner aus scan_test.py, bereinigt für page_04.
# ===========================================================================

class _Si4689Radio:
    """
    Low-level SPI-Treiber für den Si4689.
    Wird ausschliesslich vom DabScanner verwendet.
    Nicht direkt von der GUI ansprechen.
    """

    def __init__(self) -> None:
        try:
            import spidev          # type: ignore
            import RPi.GPIO as GPIO  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                f"Fehlende Hardware-Bibliothek: {exc}\n"
                "Bitte auf dem Raspberry Pi ausführen und\n"
                "  pip install spidev RPi.GPIO"
            ) from exc

        self._spidev = spidev
        self._GPIO   = GPIO

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(_PIN_RESET, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(_PIN_INT,   GPIO.IN)
        GPIO.setup(_PIN_AMP,   GPIO.OUT, initial=GPIO.LOW)

        self._spi = spidev.SpiDev()
        self._spi.open(_SPI_BUS, _SPI_DEVICE)
        self._spi.max_speed_hz  = _SPI_SPEED
        self._spi.mode          = 0
        self._spi.bits_per_word = 8

    # ------------------------------------------------------------------
    # SPI Low-Level
    # ------------------------------------------------------------------

    def _send(self, data: List[int]) -> None:
        self._spi.xfer2(data)

    def _recv(self, length: int) -> List[int]:
        return self._spi.xfer2([0x00] + [0x00] * length)[1:]

    def _wait_cts(self, timeout: float = 2.0, allow_error: bool = False) -> None:
        """
        Wartet auf CTS-Bit (Bit 7).
        allow_error=True: ERR-Bit (Bit 6) wird toleriert –
        notwendig nach Flash-BOOT und vor jedem neuen Befehl.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._recv(1)[0]
            if status & 0x80:
                if (status & 0x40) and not allow_error:
                    raise RuntimeError(
                        f"Si4689 Befehlsfehler (STATUS=0x{status:02X})"
                    )
                return
            time.sleep(0.001)
        raise TimeoutError("CTS-Timeout – Si4689 antwortet nicht")

    def _cmd(self, data: List[int], timeout: float = 2.0,
             allow_error_after: bool = False) -> None:
        """
        Sendet einen Befehl: CTS abwarten → senden → CTS abwarten.
        Das CTS vor dem Senden ist immer tolerant (allow_error=True),
        um einen evtl. Pending-Error nicht zu blockieren.
        """
        self._wait_cts(timeout=timeout, allow_error=True)
        self._send(data)
        self._wait_cts(timeout=timeout, allow_error=allow_error_after)

    # ------------------------------------------------------------------
    # Boot-Sequenz (Host-Load)
    # ------------------------------------------------------------------

    def reset(self) -> None:
        GPIO = self._GPIO
        GPIO.output(_PIN_RESET, GPIO.LOW)
        time.sleep(0.010)
        GPIO.output(_PIN_RESET, GPIO.HIGH)
        time.sleep(0.200)

    def amp_enable(self, on: bool) -> None:
        GPIO = self._GPIO
        GPIO.output(_PIN_AMP, GPIO.HIGH if on else GPIO.LOW)

    def power_up(self, xtal_freq: int = 19_200_000, ctun: int = 0x07) -> None:
        cmd    = [0x00] * 16
        cmd[0] = _CMD_POWER_UP
        cmd[1] = 0x00
        cmd[2] = (1 & 0x03) << 4 | (0x07 & 0x0F)   # CLK_MODE=1, TR_SIZE=7
        cmd[3] = 0x28                                 # IBIAS
        cmd[4:8] = list(xtal_freq.to_bytes(4, "little"))
        cmd[8]   = ctun & 0x3F
        cmd[9]   = 0x10                               # ROM00
        cmd[13]  = 0x18                               # IBIAS_RUN
        self._send(cmd)
        self._wait_cts(timeout=2.0)

    def host_load_and_boot(self, patch_path: Path, fw_path: Path) -> None:
        """
        Lädt Patch + DAB-Firmware von Dateien (Host-Load) und bootet.
        Ablauf: LOAD_INIT → HOST_LOAD(Patch) → LOAD_INIT → HOST_LOAD(FW) → BOOT
        """
        def _load_file(path: Path) -> None:
            self._cmd([_CMD_LOAD_INIT, 0x00])
            with path.open("rb") as f:
                while True:
                    chunk = f.read(32)
                    if not chunk:
                        break
                    self._cmd([_CMD_HOST_LOAD, 0x00, 0x00, 0x00] + list(chunk))

        _load_file(patch_path)
        time.sleep(0.004)
        _load_file(fw_path)
        self._cmd([_CMD_BOOT, 0x00])

    # ------------------------------------------------------------------
    # Konfiguration
    # ------------------------------------------------------------------

    def set_property(self, prop_id: int, value: int) -> None:
        self._cmd([
            _CMD_SET_PROPERTY, 0x00,
            prop_id & 0xFF, (prop_id >> 8) & 0xFF,
            value   & 0xFF, (value   >> 8) & 0xFF,
        ])

    def configure_dab_frontend(self) -> None:
        """
        Setzt DAB Frontend-Properties.
        Werte: RaspiAudio Referenzcode (Platform_F380_Module).
        """
        self.set_property(_PROP_DAB_TUNE_FE_VARM,           0xFD12)
        self.set_property(_PROP_DAB_TUNE_FE_VARB,           0x009B)
        self.set_property(_PROP_DAB_TUNE_FE_CFG,            0x0000)
        self.set_property(_PROP_DAB_EVENT_INTERRUPT_SOURCE, 0x00C1)
        self.set_property(_PROP_DAB_VALID_RSSI_THRESHOLD,   6)

    def set_freq_list(self, freqs_khz: List[int]) -> None:
        """DAB_SET_FREQ_LIST: überträgt Kanalliste an den Chip."""
        num = len(freqs_khz)
        cmd = [_CMD_DAB_SET_FREQ_LIST, num & 0xFF, 0x00, 0x00]
        for f in freqs_khz:
            cmd.extend(list(int(f).to_bytes(4, "little")))
        self._cmd(cmd)

    # ------------------------------------------------------------------
    # Scan-Operationen
    # ------------------------------------------------------------------

    def tune(self, freq_index: int, antcap: int = 0) -> None:
        """DAB_TUNE_FREQ: Tune auf freq_index aus der Frequenzliste."""
        self._cmd([
            _CMD_DAB_TUNE_FREQ,
            0x00,               # Injection: automatisch
            freq_index & 0xFF,
            0x00,
            antcap & 0xFF,
            (antcap >> 8) & 0xFF,
        ])

    def digrad_status(self, stc_ack: bool = False) -> Dict:
        """
        DAB_DIGRAD_STATUS (0xB2): Signalqualität abfragen.

        stc_ack=True: quittiert den STCINT-Interrupt (Seek/Tune Complete).
        Muss nach bestätigtem Tune-Lock aufgerufen werden, bevor
        get_ensemble_info() oder andere Befehle gesendet werden.
        Ohne Quittierung bleibt STCINT gesetzt → Si4689 antwortet auf
        nachfolgende Befehle mit STATUS=0xC1 (CTS+ERR+STCINT).
        """
        arg1 = 0x01 if stc_ack else 0x00   # Bit 0 = STCACK
        self._cmd([_CMD_DAB_DIGRAD_STATUS, arg1])
        r = self._recv(0x28)
        return {
            "valid":       bool(r[5] & 0x01),
            "acq":         bool(r[5] & 0x04),
            "rssi":        _signed_byte(r[6]),
            "snr":         r[7],
            "fic_quality": r[8],
            "cnr":         r[9],
        }

    def wait_for_lock(self, timeout_s: float = 5.0) -> Optional[Dict]:
        """
        Wartet auf Ensemble-Einrastung (valid AND acq AND Qualität > 0).

        Nach bestätigtem Lock wird STCINT explizit quittiert (stc_ack=True).
        Ohne diese Quittierung bleibt das STC-Interrupt-Flag gesetzt, und
        nachfolgende Befehle (z.B. DAB_GET_ENSEMBLE_INFO) scheitern mit
        STATUS=0xC1 (CTS + ERR_CMD + STCINT).

        Gibt None zurück wenn kein Signal innerhalb des Timeouts.
        """
        deadline = time.time() + timeout_s
        last: Optional[Dict] = None
        while time.time() < deadline:
            st = self.digrad_status(stc_ack=False)   # während Polling: kein ACK
            last = st
            if st["valid"] and st["acq"] and (
                st["fic_quality"] > 0 or st["snr"] > 0 or st["cnr"] > 0
            ):
                # Lock bestätigt → STCINT quittieren bevor weitere Befehle
                self.digrad_status(stc_ack=True)
                return st
            time.sleep(0.05)
        if last and last["valid"] and last["acq"]:
            self.digrad_status(stc_ack=True)   # auch im Fallback quittieren
            return last
        return None

    def get_event_status(self, ack: bool = False) -> Dict:
        """DAB_GET_EVENT_STATUS: prüft ob neue Service-Liste bereit."""
        self._cmd([_CMD_DAB_GET_EVENT_STATUS, 0x01 if ack else 0x00])
        r = self._recv(9)
        return {"svrlist": bool(r[5] & 0x01)}

    def wait_for_service_list(self, timeout_s: float = 4.0) -> bool:
        """Wartet auf svrlist-Ereignis. True = Liste bereit, False = Timeout."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.get_event_status(ack=False)["svrlist"]:
                self.get_event_status(ack=True)
                return True
            time.sleep(0.1)
        return False

    def get_audio_services(self) -> List[Dict]:
        """
        GET_DIGITAL_SERVICE_LIST: liest alle unverschlüsselten Audio-Dienste.
        Gibt Liste von Dicts mit service_id, component_id, label zurück.
        """
        self._cmd([_CMD_GET_DIGITAL_SERVICE_LIST, 0x00])
        header     = self._recv(6)
        total_size = int.from_bytes(header[4:6], "little")
        if total_size == 0:
            return []
        full    = self._recv(6 + total_size)
        payload = bytes(full[6:])
        return self._parse_service_list(payload)

    @staticmethod
    def _parse_service_list(payload: bytes) -> List[Dict]:
        """Parst binäre Service-Liste (Si468x API-Format)."""
        services: List[Dict] = []
        if len(payload) < 6:
            return services
        service_count = int.from_bytes(payload[2:4], "little")
        offset = 6

        for _ in range(service_count):
            if offset + 24 > len(payload):
                break
            sid       = int.from_bytes(payload[offset:offset + 4], "little")
            info1     = payload[offset + 4]
            info2     = payload[offset + 5]
            label_raw = payload[offset + 8:offset + 24]
            label     = label_raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore").strip()
            num_comp  = info2 & 0x0F
            pty_idx   = (info1 >> 1) & 0x1F   # Service Info 1, Bits[5:1]
            offset   += 24
            for _ in range(num_comp):
                if offset + 4 > len(payload):
                    break
                comp_id   = int.from_bytes(payload[offset:offset + 2], "little")
                comp_info = payload[offset + 2]
                tmid      = (comp_id >> 14) & 0x03
                ca_flag   = comp_info & 0x01
                if tmid == 0 and ca_flag == 0 and (info1 & 0x01) == 0:
                    services.append({
                        "service_id":   sid,
                        "component_id": comp_id,
                        "label":        label or f"SID:0x{sid:08X}",
                        "pty_idx":      pty_idx,   # ← NEU
                    })
                offset += 4
                
        return services

    # ------------------------------------------------------------------
    # Ensemble-Info
    # ------------------------------------------------------------------

    def get_ensemble_info(self) -> Dict:
        """
        DAB_GET_ENSEMBLE_INFO (0xB4): Liefert Ensemble-ID und Ensemble-Label.

        Antwortstruktur gemäss AN649, Command 0xB4:
            RESP4-5  : EID[15:0]        – 16-Bit Ensemble-ID
            RESP6-21 : LABEL[0..15]     – Ensemble-Name (max. 16 Zeichen)
            RESP22   : ENSEMBLE_ECC     – Extended Country Code
            RESP23   : CHARSET          – Zeichensatz (0 = EBU Latin)

        Muss NACH vollständiger FIC-Dekodierung aufgerufen werden,
        d.h. nach wait_for_service_list().

        Rückgabe:
            eid     – Ensemble-ID (int)
            label   – Ensemble-Name (str, z.B. "SMC BEFR", "SRG SSR")
            ecc     – Extended Country Code (int)
            charset – Zeichensatz-Code (int)
        """
        self._cmd([0xB4, 0x00])            # CMD_DAB_GET_ENSEMBLE_INFO
        r = self._recv(26)                  # 4 Status + 22 Daten-Bytes
        eid       = int.from_bytes(r[4:6], "little")
        label_raw = bytes(r[6:22])
        label     = label_raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore").strip()
        ecc       = r[22]
        charset   = r[23]
        return {
            "eid":     eid,
            "label":   label,
            "ecc":     ecc,
            "charset": charset,
        }

    # ------------------------------------------------------------------
    # Ressourcen freigeben
    # ------------------------------------------------------------------

    def close(self) -> None:
        """
        Gibt den SPI-Bus frei.

        WICHTIG: Kein GPIO.cleanup() und kein amp_enable(False) hier!
        GPIO.cleanup() ohne Argumente würde ALLE GPIO-Pins der gesamten App
        zurücksetzen (inkl. PIN_AMP und PIN_RESET der Haupt-App) und damit
        dauerhaft den Ton abschalten und die DAB Empfangs-Steuerung zerstören.
        Die Haupt-App verwaltet GPIO und Verstärker selbst.
        """
        try:
            self._spi.close()
        except Exception:
            pass


# ===========================================================================
#  DabScanner  –  Orchestriert Boot + Scan-Schleife
#  Läuft vollständig im Hintergrund-Thread.
# ===========================================================================

class DabScanner:
    """
    Orchestriert den vollständigen DAB Band-Scan:
        1. Hardware initialisieren (_Si4689Radio)
        2. Verstärker einschalten
        3. Reset → POWER_UP → Host-Load (Patch + DAB-FW) → BOOT
        4. DAB Frontend konfigurieren
        5. DAB_SET_FREQ_LIST
        6. Für jeden Kanal: TUNE → Warten auf Lock → Service-Liste lesen
        7. Ergebnisse als Liste zurückgeben

    Kommuniziert mit der GUI ausschliesslich über Callbacks:
        progress_cb(msg: str)           – Statusmeldung pro Schritt
        done_cb(result: List[Dict] | Exception)  – Abschluss oder Fehler
    """

    def __init__(
        self,
        patch_path:  Path = _FW_PATCH,
        fw_path:     Path = _FW_DAB,
        lock_timeout: float = 5.0,
        antcap:       int   = 0,
    ) -> None:
        self.patch_path   = patch_path
        self.fw_path      = fw_path
        self.lock_timeout = lock_timeout
        self.antcap       = antcap
        self._stop        = threading.Event()

    def abort(self) -> None:
        """Bricht den laufenden Scan ab (Thread-sicher)."""
        self._stop.set()

    def run(
        self,
        progress_cb: "Callable[[str], None]",
        done_cb:     "Callable[[Any], None]",
        channels:    Optional[List[Tuple[int, str, int]]] = None,
    ) -> None:
        """
        Führt den Scan durch. Aufzurufen in einem Hintergrund-Thread.

        channels: Liste von (freq_index, label, freq_khz).
                  None → alle 41 Band-III-Kanäle.
        """
        if channels is None:
            channels = [
                (idx, label, khz)
                for idx, (label, khz) in enumerate(_DAB_BAND_III)
            ]

        radio: Optional[_Si4689Radio] = None
        try:
            # --- 1. Hardware initialisieren ---
            progress_cb("Initialisiere SPI-Verbindung …")
            radio = _Si4689Radio()
            radio.amp_enable(True)

            # --- 2. Reset + Boot ---
            progress_cb("Hardware-Reset …")
            radio.reset()

            progress_cb("POWER_UP …")
            radio.power_up()

            # Firmware-Dateien prüfen
            for label, path in [("Patch", self.patch_path), ("DAB-FW", self.fw_path)]:
                if not path.exists():
                    raise FileNotFoundError(
                        f"{label}-Datei nicht gefunden:\n{path}"
                    )

            progress_cb(f"Lade Patch ({self.patch_path.stat().st_size // 1024} KB) …")
            radio.host_load_and_boot(self.patch_path, self.fw_path)
            progress_cb("Firmware geladen – BOOT OK")

            # --- 3. DAB konfigurieren ---
            progress_cb("DAB Frontend konfigurieren …")
            radio.configure_dab_frontend()

            # Frequenzliste an Chip senden
            all_freqs = [khz for _, khz in _DAB_BAND_III]
            radio.set_freq_list(all_freqs)
            progress_cb(f"Frequenzliste gesetzt ({len(all_freqs)} Kanäle)")

            # --- 4. Scan-Schleife ---
            total     = len(channels)
            all_svcs: List[Dict] = []   # alle gefundenen Dienste

            for step, (freq_index, ch_label, freq_khz) in enumerate(channels, start=1):
                if self._stop.is_set():
                    progress_cb("Scan abgebrochen.")
                    done_cb(all_svcs)
                    return

                freq_mhz = freq_khz / 1000.0
                progress_cb(
                    f"[{step:2d}/{total}]  {ch_label:4s}  {freq_mhz:.3f} MHz  –  "
                    f"warte auf Signal …"
                )

                try:
                    radio.tune(freq_index, antcap=self.antcap)
                except Exception:
                    continue

                status = radio.wait_for_lock(timeout_s=self.lock_timeout)
                if status is None:
                    progress_cb(
                        f"[{step:2d}/{total}]  {ch_label:4s}  {freq_mhz:.3f} MHz  –  "
                        f"kein Signal"
                    )
                    continue

                # Signal gefunden
                progress_cb(
                    f"[{step:2d}/{total}]  {ch_label:4s}  {freq_mhz:.3f} MHz  –  "
                    f"Signal OK  RSSI={status['rssi']:+d} dBuV  "
                    f"FIC={status['fic_quality']}%  –  lese Service-Liste …"
                )

                radio.wait_for_service_list(timeout_s=4.0)

                # --- Ensemble-Name via DAB_GET_ENSEMBLE_INFO (0xB4) ---
                # Aufruf nach wait_for_service_list(): FIC ist jetzt
                # vollständig dekodiert, Ensemble-Label ist verfügbar.
                ensemble_label = ch_label   # Fallback: Kanalname ("7D")
                try:
                    ens_info = radio.get_ensemble_info()
                    raw_label = ens_info.get("label", "").strip()
                    if raw_label:
                        ensemble_label = raw_label
                        progress_cb(
                            f"[{step:2d}/{total}]  {ch_label:4s}  "
                            f"Ensemble: '{ensemble_label}'"
                        )
                except Exception as ens_exc:
                    progress_cb(
                        f"[{step:2d}/{total}]  {ch_label:4s}  "
                        f"Ensemble-Info nicht verfügbar ({ens_exc})"
                    )

                try:
                    services = radio.get_audio_services()
                except Exception:
                    services = []

                if not services:
                    progress_cb(
                        f"[{step:2d}/{total}]  {ch_label:4s}  "
                        f"Signal vorhanden, keine Audio-Dienste"
                    )
                    continue

                progress_cb(
                    f"[{step:2d}/{total}]  {ch_label:4s}  "
                    f"['{ensemble_label}']  {len(services)} Dienst(e)"
                )

                for svc in services:
                    all_svcs.append({
                        "label":        svc["label"],
                        "channel":      ch_label,
                        "ensemble":     ensemble_label,
                        "mhz":          freq_mhz,
                        "service_id":   svc["service_id"],
                        "component_id": svc["component_id"],
                        "freq_index":   freq_index,
                        "freq_khz":     freq_khz,
                        "pty_idx":      svc.get("pty_idx", 0),   # ← NEU
                    })

            # --- 5. Scan abgeschlossen ---
            progress_cb(
                f"Scan abgeschlossen: {len(all_svcs)} Dienste in "
                f"{len({s['channel'] for s in all_svcs})} Ensembles gefunden."
            )
            done_cb(all_svcs)

        except Exception as exc:
            done_cb(exc)
        finally:
            if radio is not None:
                try:
                    radio.close()
                except Exception:
                    pass


# ===========================================================================
#  Page04  –  Tkinter-Seite
# ===========================================================================

class Page04(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app        = controller

        self.configure(bg="#3D5B0C")

        self._scan_thread:   Optional[threading.Thread] = None
        self._scanner:       Optional[DabScanner]       = None
        self._scan_running:  bool                       = False

        self._build_gui()

        self.sqlite_controller = SQLite_Controller(self)
        self.gui_controller    = GUI_Controller(self, self.sqlite_controller)

        # Gespeicherte Einträge beim Start laden
        self._load_from_db()

    # ------------------------------------------------------------------
    # GUI aufbauen
    # ------------------------------------------------------------------

    def _build_gui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, minsize=25, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)

        # ---- Titelzeile ----
        self.label = tk.Label(
            self,
            text="DAB-Sender Bern/Mittelland",
            font=("Helvetica", 25),
            background="#0C560C",
            foreground="#C8CDF7",
        )
        self.label.grid(row=0, column=0, sticky=tk.NSEW)

        # ---- Toolbar ----
        self.top_frame = tk.Frame(self, bg="#3D5B0C")
        self.top_frame.grid(row=1, column=0, sticky=tk.NSEW, padx=8, pady=(6, 4))
        self.top_frame.grid_columnconfigure(1, weight=1)

        self.btn_autoscan = ttk.Button(
            self.top_frame,
            text="Autoscan starten",
            command=self._on_autoscan_clicked,
        )
        self.btn_autoscan.grid(row=0, column=0, padx=(10, 6), sticky=tk.W)

        self.btn_abort = ttk.Button(
            self.top_frame,
            text="Abbrechen",
            command=self._on_abort_clicked,
            state=tk.DISABLED,
        )
        self.btn_abort.grid(row=0, column=1, padx=(0, 6), sticky=tk.W)

        # Fortschrittsbalken (erscheint nur während Scan)
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progressbar = ttk.Progressbar(
            self.top_frame,
            variable=self.progress_var,
            maximum=len(_DAB_BAND_III),
            mode="determinate",
            length=300,
        )
        self.progressbar.grid(row=0, column=2, padx=(6, 10), sticky=tk.EW)
        self.progressbar.grid_remove()   # zunächst versteckt

        # ---- Tabelle ----
        self.bottom_frame = tk.Frame(self, bg="#3D5B0C")
        self.bottom_frame.grid(row=2, column=0, sticky=tk.NSEW, padx=8, pady=(4, 8))
        self.bottom_frame.grid_columnconfigure(0, weight=1)
        self.bottom_frame.grid_rowconfigure(0, weight=1)
        self._build_table(self.bottom_frame)

    def _build_table(self, parent: tk.Frame) -> None:
        """
        Treeview-Spalten:
            nr          – laufende Nummer (1..N)
            name        – Service-Name
            pty         – PTY-Text (aus Datenkomplettierung, Bereich 3)
            channels    – DAB-Channel (z.B. "7D")
            ensemble    – Ensemble-Name (aus Datenkomplettierung, Bereich 3)
            mhz         – Frequenz in MHz
            si4689_idx  – Sequenzindex für Tuning
        """
        cols = ("nr", "name", "pty", "channels", "ensemble", "mhz", "si4689_idx")
        self.tree = ttk.Treeview(parent, height=20, columns=cols, show="headings")

        self.tree.heading("nr",         text="Nr.")
        self.tree.heading("name",       text="Name")
        self.tree.heading("pty",        text="PTY")
        self.tree.heading("channels",   text="Channel")
        self.tree.heading("ensemble",   text="Ensemble")
        self.tree.heading("mhz",        text="MHz")
        self.tree.heading("si4689_idx", text="Si4689-Idx")

        self.tree.column("nr",         width=50,  anchor="center")
        self.tree.column("name",       width=180, anchor="w")
        self.tree.column("pty",        width=100, anchor="w")
        self.tree.column("channels",   width=80,  anchor="center")
        self.tree.column("ensemble",   width=160, anchor="w")
        self.tree.column("mhz",        width=90,  anchor="center")
        self.tree.column("si4689_idx", width=90,  anchor="center")

        vsb = ttk.Scrollbar(parent, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        hsb.grid(row=1, column=0, sticky=tk.EW)

        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Statuszeile
        parent.grid_rowconfigure(2, weight=0)
        self.status_frame = tk.Frame(parent, bg="#2E4309")
        self.status_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(4, 0))
        self.status_frame.grid_columnconfigure(0, weight=1)

        self.comment_var = tk.StringVar(value="Bereit.")
        self.comment_label = tk.Label(
            self.status_frame,
            textvariable=self.comment_var,
            anchor="w",
            padx=6, pady=3,
            bg="#2E4309", fg="#E8F0FF",
            relief="sunken",
        )
        self.comment_label.grid(row=0, column=0, sticky=tk.EW)

    # ------------------------------------------------------------------
    # Öffentliche Hilfsmethode
    # ------------------------------------------------------------------

    def set_comment(self, text: str) -> None:
        """Statuszeile aktualisieren (GUI-Thread-sicher via after())."""
        if hasattr(self, "comment_var"):
            self.comment_var.set(text or "")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_page_hide(self) -> None:
        pass  # Scan läuft ggf. weiter im Hintergrund

    # ------------------------------------------------------------------
    # Autoscan
    # ------------------------------------------------------------------

    def _on_autoscan_clicked(self) -> None:
        """
        Startet den Scan-Prozess.

        Ablauf (SPI-Kollision vermeiden):
            1. Firmware-Dateien prüfen
            2. Haupt-App DAB Empfang pausieren (is_ready=False)
               → stoppt Status-Polling, DLS, Tune-Calls
            3. 800 ms warten → laufende SPI-Transaktionen abschliessen
            4. Scan-Thread starten (exklusiver SPI-Zugriff)
        """
        if self._scan_running:
            return

        # Firmware-Dateien prüfen
        for label, path in [("Patch", _FW_PATCH), ("DAB-Firmware", _FW_DAB)]:
            if not path.exists():
                messagebox.showerror(
                    "Datei fehlt",
                    f"{label}-Datei nicht gefunden:\n{path}\n\n"
                    "Bitte Pfad in den Konstanten am Dateianfang anpassen.",
                    parent=self,
                )
                return

        self._scan_running = True
        self.btn_autoscan.configure(state=tk.DISABLED, text="Scan läuft …")
        self.btn_abort.configure(state=tk.NORMAL)
        self.progress_var.set(0)
        self.progressbar.grid()

        # --- Haupt-DAB Empfang pausieren: _initialized = False ---
        # Si4689Manager-Methoden prüfen is_ready vor jedem SPI-Zugriff.
        # Mit _initialized=False geben sie sofort False/{}  zurück.
        # si._radio bleibt OFFEN → kein Neustart nötig, kein Spam.
        # Der SPI-Bus ist exklusiv für _Si4689Radio während des Scans.
        self._set_radio_ready(False)
        self.set_comment("DAB Empfang pausiert – Scan startet in 800 ms …")
        print("[Page04] DAB Empfang pausiert (_initialized=False)")

        # 800 ms warten → laufende SPI-Transaktionen abschliessen
        self.after(800, self._start_scan_thread)

    # ------------------------------------------------------------------
    # DAB-Empfangs-Pause / -Resume für SPI-Exklusivität während Scan
    # ------------------------------------------------------------------

    def _set_radio_ready(self, ready: bool) -> bool:
        """
        Setzt den is_ready-Zustand des Si4689Manager.

        is_ready ist eine read-only Property → das Backing-Attribut wird
        direkt über si.__dict__ gesetzt (unterstützt _is_ready, _initialized,
        _ready, _booted). Funktioniert unabhängig vom Attribut-Namen.

        Rückgabe: True wenn erfolgreich gesetzt, False sonst.
        """
        si = getattr(self.app, "si4689", None)
        if si is None:
            return False
        # Bekannte Backing-Attribut-Namen (Reihenfolge nach Häufigkeit)
        candidates = ("_initialized", "_is_ready", "_ready", "_booted")  # _initialized ist korrekt für Si4689Manager
        inst = vars(si)                     # si.__dict__ – direkt bearbeitbar
        for attr in candidates:
            if attr in inst:
                inst[attr] = ready
                print(f"[Page04] si.{attr} = {ready}")
                return True
        # Fallback: Property direkt (falls doch ein Setter existiert)
        try:
            si.is_ready = ready
            return True
        except AttributeError:
            pass
        print(f"[Page04] is_ready konnte nicht auf {ready} gesetzt werden")
        return False

    def _start_scan_thread(self) -> None:
        """Startet den Scan-Thread nach der SPI-Freigabe-Pause."""
        self.set_comment("Scan gestartet – Boot-Sequenz …")
        self._scanner = DabScanner(
            patch_path=_FW_PATCH,
            fw_path=_FW_DAB,
            lock_timeout=5.0,
            antcap=0,
        )
        self._scan_thread = threading.Thread(
            target=self._scanner.run,
            kwargs={
                "progress_cb": self._on_scan_progress,
                "done_cb":     self._on_scan_done,
            },
            daemon=True,
            name="DabScanThread",
        )
        self._scan_thread.start()

    def _on_abort_clicked(self) -> None:
        """Sendet Abbruch-Signal an den Scanner."""
        if self._scanner is not None:
            self._scanner.abort()
        self.set_comment("Scan wird abgebrochen …")
        self.btn_abort.configure(state=tk.DISABLED)

    def _on_scan_progress(self, msg: str) -> None:
        """
        Callback vom Scan-Thread → muss in den GUI-Thread weitergeleitet werden.
        Aktualisiert Statuszeile und Fortschrittsbalken.
        """
        self.after(0, self._apply_progress, msg)

    def _apply_progress(self, msg: str) -> None:
        """Wird im GUI-Thread ausgeführt (via after())."""
        self.set_comment(msg)
        # Kanalfortschritt aus Meldung extrahieren [X/41]
        import re
        match = re.search(r"\[(\d+)/(\d+)\]", msg)
        if match:
            self.progress_var.set(int(match.group(1)))

    def _on_scan_done(self, result: Any) -> None:
        """
        Callback vom Scan-Thread wenn Scan abgeschlossen (oder Fehler).
        result: Liste[Dict] bei Erfolg, Exception bei Fehler.
        """
        self.after(0, self._apply_scan_done, result)

    def _apply_scan_done(self, result: Any) -> None:
        """Wird im GUI-Thread ausgeführt."""
        self._scan_running = False
        self.btn_autoscan.configure(state=tk.NORMAL, text="Autoscan starten")
        self.btn_abort.configure(state=tk.DISABLED)
        self.progressbar.grid_remove()          # Fortschrittsbalken ausblenden

        if isinstance(result, Exception):
            err_msg = f"{type(result).__name__}: {result}"
            self.set_comment(f"Scan-Fehler: {err_msg}")
            messagebox.showerror(
                "Scan fehlgeschlagen",
                f"Der DAB-Scan wurde mit einem Fehler beendet:\n\n{err_msg}\n\n"
                "Bitte Verkabelung und Firmware-Pfade überprüfen.",
                parent=self,
            )
            return

        # Ergebnisse sind eine Liste von Service-Dicts
        services: List[Dict] = result
        if not services:
            self.set_comment("Scan abgeschlossen – keine Sender gefunden.")
            messagebox.showinfo(
                "Scan abgeschlossen",
                "Es wurden keine DAB-Sender gefunden.\n\n"
                "Mögliche Ursachen:\n"
                "• Antenne nicht angeschlossen\n"
                "• Falscher Antennentyp oder schlechter Empfang",
                parent=self,
            )
            return

        # --- Sequenzindizes zuweisen ---
        db_rows = []
        for idx, svc in enumerate(services):
            pty_idx = svc.get("pty_idx", 0)
            pty_txt = PTY_MAP.get(pty_idx, f"<PTY {pty_idx}>")
            db_rows.append({
                "si4689_idx":   idx,
                "name":         svc["label"],
                "channel":      svc["channel"],
                "ensemble":     svc["ensemble"],
                "mhz":          svc["mhz"],
                "freq_index":   svc["freq_index"],
                "service_id":   svc["service_id"],
                "component_id": svc["component_id"],
                "pty_idx":      pty_idx,
                "pty_txt":      pty_txt,
            })

        # --- In SQLite speichern ---
        try:
            self.sqlite_controller.replace_si4689_rows(db_rows)
        except Exception as exc:
            self.set_comment(f"DB-Fehler: {exc}")
            traceback.print_exc()

        # --- Treeview aktualisieren ---
        self.gui_controller.populate_tree(db_rows)

        n_ensemble = len({svc["ensemble"] for svc in services})
        ens_names  = ", ".join(sorted({svc["ensemble"] for svc in services}))
        self.set_comment(
            f"Scan abgeschlossen: {len(services)} Sender in "
            f"{n_ensemble} Ensemble(s) ({ens_names}) – "
            f"Audio wird neu gestartet …"
        )

        # --- Nach dem Scan: Haupt-App DAB Empfang wieder neu initialisieren -----------
        # Der Scan hat den Si4689 zurückgesetzt und neu gebootet.
        # Die Haupt-App weiss davon nichts → initialize() stellt den
        # definierten Ausgangszustand wieder her (Boot + Frequenzliste).
        # Danach wird der zuletzt gespielte Sender automatisch wieder
        # eingestellt.
        self.after(300, self._reinit_main_radio, len(services))

    def _reinit_main_radio(self, n_services: int = 0) -> None:
        """
        Stellt Audio nach dem Scan wieder her.

        Hintergrund: Der Scan hat den Si4689 zurückgesetzt und korrekt neu
        gebootet (Host-Load + Boot + Freq-List). Der Chip ist danach im
        richtigen DAB-Modus. si.initialize() schlägt deshalb fehl (0xC0 –
        Chip ist schon gebootet). Stattdessen:
            1. is_ready = True  → App weiss: Chip ist betriebsbereit
            2. _current_channel = None  → erzwingt Kanal-Retune
            3. tune_service(letzter Index)  → Ton wieder einschalten
        """
        si = getattr(self.app, "si4689", None)
        if si is None:
            return

        def _worker():
            try:
                # 1. Radio reaktivieren (_initialized=True)
                self._set_radio_ready(True)
                print("[Page04] DAB Empfang wieder reaktiviert (_initialized=True)")

                # 2. I2S-Output re-konfigurieren
                # ─────────────────────────────────────────────────────────
                # Ursache des fehlenden Tons nach Scan:
                # _Si4689Radio.host_load_and_boot() setzt den Chip zurück,
                # konfiguriert aber NUR configure_dab_frontend() – NICHT
                # configure_i2s(). Ohne I2S-Konfiguration gibt der Si4689
                # keinen Audio-Stream aus (kein BCLK/LRCLK/DATA).
                # DLS funktioniert trotzdem (aus FIC-Daten, kein I2S nötig).
                radio = getattr(si, "_radio", None)
                if radio is not None and hasattr(radio, "configure_i2s"):
                    try:
                        radio.configure_i2s(master=False)
                        print("[Page04] configure_i2s(master=False) OK")
                    except Exception as i2s_exc:
                        print(f"[Page04] configure_i2s Fehler: {i2s_exc}")

                # 3. Kanal-Cache zurücksetzen → dab_tune() beim nächsten
                #    tune_service()-Aufruf neu tunen
                if hasattr(self.app, "_current_channel"):
                    self.app._current_channel = None

                # 4. Letzten Sender wieder einstimmen
                state = getattr(self.app, "state", None)
                last_idx = 0
                if state is not None:
                    try:
                        last_idx = int(getattr(state, "AktuelleSenderId", 0))
                    except Exception:
                        last_idx = 0

                self.after(
                    600,
                    lambda i=last_idx: self.app.tune_service(
                        index=i, volume=None, record_history=False,
                    )
                )
                n = n_services
                self.after(0, lambda: self.set_comment(
                    f"Scan abgeschlossen: {n} Sender gespeichert."
                ))

            except Exception as exc:
                print(f"[Page04] Audio-Wiederherstellung fehlgeschlagen: {exc}")
                self.after(0, lambda e=str(exc): self.set_comment(
                    f"Fehler nach Scan: {e}"
                ))

        threading.Thread(target=_worker, daemon=True,
                         name="RadioReinit").start()

    def _load_from_db(self) -> None:
        """Lädt vorhandene DB-Einträge beim Start in den Treeview."""
        try:
            rows = self.sqlite_controller.fetch_si4689_rows()
            if rows:
                self.gui_controller.populate_tree(rows)
                self.set_comment(
                    f"{len(rows)} Sender aus gespeichertem Scan geladen."
                )
        except Exception:
            pass   # Leere DB ist kein Fehler

    # ------------------------------------------------------------------
    # Treeview-Interaktion
    # ------------------------------------------------------------------

    def _on_tree_double_click(self, _evt) -> None:
        """Doppelklick → Sender tunen (via app.tune_service)."""
        info = self._get_selected_info()
        if not info:
            return
        si4689_idx = info["si4689_idx"]
        if si4689_idx is None:
            return
        self.set_comment(
            f"Wähle '{info['name']}' (Index {si4689_idx}) → hörbar auf Seite Home"
        )
        tune_fn = getattr(self.app, "tune_service", None)
        if callable(tune_fn):
            try:
                tune_fn(index=int(si4689_idx), volume=None, record_history=True)
            except Exception as exc:
                traceback.print_exc()
                messagebox.showerror(
                    "Tune-Fehler",
                    f"Tunen fehlgeschlagen (Index {si4689_idx}):\n"
                    f"{type(exc).__name__}: {exc}",
                    parent=self,
                )
        else:
            print(f"[Page04] Tune-Anfrage: si4689_idx={si4689_idx}")

    def _get_selected_info(self) -> Optional[Dict[str, Any]]:
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        if not vals or len(vals) < 7:
            return None
        try:
            si4689_idx = int(vals[6]) if str(vals[6]).strip() != "" else None
        except Exception:
            si4689_idx = None
        return {"name": str(vals[1]), "si4689_idx": si4689_idx}


# ===========================================================================
#  GUI_Controller  –  Treeview Hilfsmethoden
# ===========================================================================

class GUI_Controller:
    def __init__(self, page: "Page04", sqlite_controller: "SQLite_Controller"):
        self.page   = page
        self.app    = page.app
        self.sqlite = sqlite_controller
        self.tree   = page.tree

    def populate_tree(self, rows: List[Dict[str, Any]]) -> None:
        """
        Füllt den Treeview mit den übergebenen Zeilen.
        Optimiert: Tree temporär versteckt → kein Flackern.

        rows: Dicts mit Keys:
            si4689_idx, name, pty_txt, channel, ensemble, mhz
        """
        if not rows:
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            return

        original_height = int(self.tree.cget("height"))
        self.tree.configure(height=0)   # Redraws stoppen
        try:
            for iid in self.tree.get_children():
                self.tree.delete(iid)

            for nr, r in enumerate(rows, start=1):
                si4689_idx = r.get("si4689_idx", "")
                name       = r.get("name") or ""
                pty        = r.get("pty_txt") or ""
                channel    = r.get("channel") or ""
                ensemble   = r.get("ensemble") or ""
                mhz        = r.get("mhz")
                mhz_txt    = f"{mhz:.3f}" if isinstance(mhz, (int, float)) else ""

                self.tree.insert(
                    "", "end",
                    values=(nr, name, pty, channel, ensemble, mhz_txt, si4689_idx),
                )
        finally:
            self.tree.configure(height=original_height)   # Ein Redraw


# ===========================================================================
#  SQLite_Controller  –  Datenbank für Scan-Ergebnisse
# ===========================================================================

class SQLite_Controller:
    """
    Verwaltet die SQLite-Datenbank für Si4689-Scan-Ergebnisse.

    Tabelle: si4689_datenbank
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        si4689_idx      INTEGER NOT NULL UNIQUE  – Sequenzindex (0-basiert)
        name            TEXT    NOT NULL          – Service-Name
        channel         TEXT                      – DAB-Kanal (z.B. "7D")
        ensemble        TEXT                      – Ensemble-Name
        mhz             REAL                      – Frequenz in MHz
        service_id      INTEGER                   – 32-Bit SID
        pty_idx         INTEGER                   – PTY-Index (Bereich 3)
        pty_txt         TEXT                      – PTY-Text  (Bereich 3)
        created_at_utc  TEXT
        updated_at_utc  TEXT
        bbox_x_x        INTEGER                   - Bbox Koordinaten oben links, x-Wert, der Senderlogos auf CH-Karte z.B. RADIO24: 478
        bbox_x_y        INTEGER                   - Bbox Koordinaten unten rechts, y-Wert,  der Senderlogos auf CH-Karte z.B. RADIO24: 133 
        bbox_y_x        INTEGER                   - Bbox Koordinaten oben links, x-Wert,  der Senderlogos auf CH-Karte z.B. RADIO24: 500
        bbox_y_y        INTEGER                   - Bbox Koordinaten unten rechts, y-Wert,  der Senderlogos auf CH-Karte z.B. RADIO24: 151       
    """

    def __init__(self, page: "Page04") -> None:
        self.page     = page
        self.app      = page.app
        self.db_path  = self._resolve_db_path()
        self.ensure_ready()

    # --- Pfad ---

    def _resolve_db_path(self) -> str:
        cfg = getattr(self.app, "config_data", {}) or {}
        rel = cfg.get("dab_scan_db", "assets/DB/dab_scans.sqlite")
        if os.path.isabs(rel):
            path = rel
        else:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            path = os.path.abspath(os.path.join(root, rel))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )

    # --- Schema ---

    def ensure_ready(self) -> None:
        """Erstellt Tabelle wenn nicht vorhanden, ergänzt fehlende Spalten."""
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS si4689_datenbank (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    si4689_idx      INTEGER NOT NULL UNIQUE,
                    name            TEXT    NOT NULL,
                    channel         TEXT,
                    ensemble        TEXT,
                    mhz             REAL,
                    freq_index      INTEGER,
                    service_id      INTEGER,
                    component_id    INTEGER,
                    pty_idx         INTEGER,
                    pty_txt         TEXT,
                    created_at_utc  TEXT,
                    updated_at_utc  TEXT,
                    bbox_x_x        INTEGER,
                    bbox_x_y        INTEGER,
                    bbox_y_x        INTEGER,
                    bbox_y_y        INTEGER
                );
            """)
            # Migration: fehlende Spalten ergänzen
            cur.execute("PRAGMA table_info(si4689_datenbank)")
            existing = {row[1] for row in cur.fetchall()}
            for col, typ in [
                ("pty_idx",      "INTEGER"),
                ("pty_txt",      "TEXT"),
                ("service_id",   "INTEGER"),
                ("component_id", "INTEGER"),   # für dab_start_service(sid, cid)
                ("freq_index",   "INTEGER"),   # für dab_tune(channel)
                ("bbox_x_x",     "INTEGER"),   # Koordinaten x Logo auf CH-Karte
                ("bbox_x_y",     "INTEGER"),   # Koordinaten y Logo auf CH-Karte
                ("bbox_y_x",     "INTEGER"),   # Koordinaten x Logo auf CH-Karte
                ("bbox_y_y",     "INTEGER"),   # Koordinaten y Logo auf CH-Karte
            ]:
                if col not in existing:
                    cur.execute(f"ALTER TABLE si4689_datenbank ADD COLUMN {col} {typ};")
            # Migration: veraltete TA_-Spalten entfernen (SQLite >= 3.35)
            for obsolete in ("TA_Typ", "TA_Ensemble", "TA_Service"):
                if obsolete in existing:
                    cur.execute(f"ALTER TABLE si4689_datenbank DROP COLUMN {obsolete};")
            con.commit()
        finally:
            con.close()

    # --- Schreiben ---

    @staticmethod
    def _utcnow() -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def replace_si4689_rows(self, rows: List[Dict[str, Any]]) -> None:
        """
        Ersetzt alle Einträge in si4689_datenbank durch die übergebenen Zeilen.
        Transaktion: alles oder nichts.
        """
        if not isinstance(rows, list):
            return
        con = self._connect()
        try:
            now = self._utcnow()
            cur = con.cursor()
            cur.execute("BEGIN;")
            # bbox-Koordinaten sichern, damit ein Rescan manuell gesetzte Werte nicht löscht
            cur.execute(
                "SELECT si4689_idx, bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y "
                "FROM si4689_datenbank "
                "WHERE bbox_x_x IS NOT NULL OR bbox_x_y IS NOT NULL "
                "   OR bbox_y_x IS NOT NULL OR bbox_y_y IS NOT NULL;"
            )
            bbox_backup = {
                row[0]: (row[1], row[2], row[3], row[4]) for row in cur.fetchall()
            }
            cur.execute("DELETE FROM si4689_datenbank;")

            params = []
            for r in rows:
                try:
                    idx = int(r["si4689_idx"])
                except (KeyError, TypeError, ValueError):
                    continue
                params.append((
                    idx,
                    (r.get("name") or "").strip() or "(ohne Name)",
                    (r.get("channel")  or "").strip() or None,
                    (r.get("ensemble") or "").strip() or None,
                    r.get("mhz"),
                    r.get("freq_index"),
                    r.get("service_id"),
                    r.get("component_id"),
                    r.get("pty_idx"),
                    (r.get("pty_txt") or "").strip() or None,
                    now, now,   # created_at_utc, updated_at_utc
                    None, None, None, None,   # bbox_x_x/y, bbox_y_x/y → werden aus Backup wiederhergestellt
                ))

            cur.executemany("""
                INSERT INTO si4689_datenbank
                    (si4689_idx, name, channel, ensemble, mhz,
                     freq_index, service_id, component_id,
                     pty_idx, pty_txt, created_at_utc, updated_at_utc, bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, params)
            # Gesicherte bbox-Koordinaten wiederherstellen
            if bbox_backup:
                cur.executemany(
                    "UPDATE si4689_datenbank "
                    "SET bbox_x_x=?, bbox_x_y=?, bbox_y_x=?, bbox_y_y=? "
                    "WHERE si4689_idx=?;",
                    [(bxx, bxy, byx, byy, idx) for idx, (bxx, bxy, byx, byy) in bbox_backup.items()]
                )
            con.commit()
            print(f"[DB]  si4689_datenbank: {len(params)} Einträge → {self.db_path}")
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass
            raise
        finally:
            con.close()

    # --- Lesen ---

    def fetch_si4689_rows(self) -> List[Dict[str, Any]]:
        """Liest alle Einträge sortiert nach si4689_idx."""
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute("""
                SELECT si4689_idx, name, channel, ensemble, mhz,
                       freq_index, service_id, component_id,
                       pty_idx, pty_txt, bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y
                FROM si4689_datenbank
                ORDER BY si4689_idx ASC;
            """)
            out = []
            for row in cur.fetchall():
                (si4689_idx, name, channel, ensemble, mhz, freq_index,
                 service_id, component_id, pty_idx, pty_txt,
                 bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y) = row
                out.append({
                    "si4689_idx":   si4689_idx,
                    "name":         name,
                    "freq_index":   freq_index,
                    "service_id":   service_id,
                    "component_id": component_id,
                    "channel":      channel,
                    "ensemble":     ensemble,
                    "mhz":          mhz,
                    "pty_idx":      pty_idx,
                    "pty_txt":      pty_txt,
                    "bbox_x_x":     bbox_x_x,
                    "bbox_x_y":     bbox_x_y,
                    "bbox_y_x":     bbox_y_x,
                    "bbox_y_y":     bbox_y_y,
                })
            return out
        finally:
            con.close()


__all__ = ["Page04"]