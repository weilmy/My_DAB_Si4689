#!/usr/bin/env python3
"""
si4689.py – Hardware-Abstraktionsklasse für den Silicon Labs Si4689
DAB/FM/AM/HD-Radio-Chip auf dem RaspiAudio DAB HAT (Raspberry Pi 5).

Pinbelegung (RaspiAudio HAT, BCM-Nummern):
  SPI   : GPIO 8 (CE0) / 9 (MISO) / 10 (MOSI) / 11 (SCLK)  → spidev 0.0
  INT   : GPIO 23
  RESET : GPIO 25
  AMP   : GPIO 17  (Verstärker-Enable, active HIGH)

Referenz:
  - Si468x Programming Guide (AN649)
  - RaspiAudio GitHub (Referenz-Implementierung)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optionale Imports – werden erst beim Öffnen der Hardware geprüft
# ---------------------------------------------------------------------------
try:
    import spidev          # type: ignore
    _HAS_SPIDEV = True
except ImportError:
    spidev = None
    _HAS_SPIDEV = False

try:
    import RPi.GPIO as GPIO  # type: ignore
    _HAS_GPIO = True
except ImportError:
    GPIO = None
    _HAS_GPIO = False

# ---------------------------------------------------------------------------
# Si468x Befehlskonstanten (AN649)
# ---------------------------------------------------------------------------
CMD_POWER_UP               = 0x01  # Chip einschalten und Takt konfigurieren
CMD_GET_PART_INFO          = 0x08  # Partnummer und Revision lesen/AN649: GET_PART_INFO (war fälschlich 0x02)
CMD_GET_SYS_STATE          = 0x09  # Aktuellen Systemzustand abfragen
CMD_HOST_LOAD              = 0x04  # Firmware-Block über SPI laden
CMD_FLASH_LOAD             = 0x05  # Firmware aus externem NVM-Flash laden
CMD_LOAD_INIT              = 0x06  # Ladevorgang initialisieren
CMD_BOOT                   = 0x07  # Geladene Firmware starten

CMD_SET_PROPERTY           = 0x13  # Property schreiben
CMD_GET_PROPERTY           = 0x14  # Property lesen

CMD_GET_DIGITAL_SERVICE_DATA = 0x84

CMD_DAB_SET_FREQ_LIST      = 0xB8  # DAB-Frequenzliste setzen
CMD_DAB_TUNE_FREQ          = 0xB0  # DAB-Kanal einstimmen
CMD_DAB_DIGRAD_STATUS      = 0xB2  # DAB Empfangspegel / Qualität
CMD_DAB_GET_EVENT_STATUS   = 0xB3  # DAB-Ereignisse abfragen

CMD_GET_DIGITAL_SERVICE_LIST = 0x80  # DAB-Dienstliste lesen
CMD_START_DIGITAL_SERVICE  = 0x81  # DAB-Audiodienst starten
CMD_STOP_DIGITAL_SERVICE   = 0x82  # DAB-Audiodienst stoppen
CMD_DAB_GET_AUDIO_INFO     = 0xBD  # DAB Audio-Modus und Bitrate abfragen

CMD_FM_TUNE_FREQ           = 0x30  # FM-Frequenz einstimmen
CMD_FM_SEEK_START          = 0x31  # FM-Sendersuche (vorwärts / rückwärts)
CMD_FM_RSQ_STATUS          = 0x32  # FM Empfangsstatus
CMD_FM_RDS_STATUS          = 0x34  # FM RDS-Gruppen lesen (FMHD-Firmware)

# ---------------------------------------------------------------------------
# Property-IDs (Auswahl)
# ---------------------------------------------------------------------------
PROP_PIN_CONFIG_ENABLE            = 0x0800  # Ausgangs-Pins aktivieren
PROP_DIGITAL_IO_OUTPUT_SELECT     = 0x0200  # I2S-Master/Slave
PROP_DIGITAL_IO_OUTPUT_SAMPLE_RATE = 0x0201 # I2S-Abtastrate
PROP_DIGITAL_IO_OUTPUT_FORMAT     = 0x0202  # I2S-Format / Wortbreite
PROP_AUDIO_ANALOG_VOLUME          = 0x0300  # Analog-Lautstärke (0–63)
PROP_DAB_TUNE_FE_VARM             = 0x1710  # Frontend-Kalibrierung VARM
PROP_DAB_TUNE_FE_VARB             = 0x1711  # Frontend-Kalibrierung VARB
PROP_DAB_TUNE_FE_CFG              = 0x1712  # Frontend-Konfiguration
PROP_INT_CTL_ENABLE               = 0x0000  # Globale Interrupt-Freigabe: Bit4=DSRVIEN, Bit2=RDSINT, Bit0=STCINT
PROP_DIGITAL_SERVICE_INT_SOURCE   = 0x8100  # Digital-Service-Interrupt: Bit0=DSRVPCKTINT
PROP_DAB_EVENT_INTERRUPT_SOURCE   = 0xB300  # DAB-Interrupt-Quellen
PROP_DAB_VALID_RSSI_THRESHOLD     = 0xB201  # Minimaler RSSI für gültiges Signal
PROP_DAB_XPAD_ENABLE              = 0xB400  # X-PAD-Dienste: Bit0=DLS, Bit2=MOT

# ---------------------------------------------------------------------------
# GET_SYS_STATE Rückgabewerte (Byte 5)
# ---------------------------------------------------------------------------
SYS_STATE_POWER_UP = 0  # Chip bereit, Firmware noch nicht gestartet

# ---------------------------------------------------------------------------
# GET_SYS_STATE IMAGE-Werte (AN649, Kap. 0x09) – KORRIGIERT
# ---------------------------------------------------------------------------
SYS_STATE_BOOTLOADER = 0  # Bootloader aktiv (noch keine Firmware)
SYS_STATE_FM         = 1  # FMHD aktiv
SYS_STATE_DAB        = 2  # DAB aktiv          ← war fälschlich 1
SYS_STATE_TDMB       = 3  # TDMB / Data-DAB
SYS_STATE_AMHD       = 5  # AMHD aktiv

# ---------------------------------------------------------------------------
# Standard-Pinbelegung RaspiAudio HAT (BCM)
# ---------------------------------------------------------------------------
DEFAULT_RST_PIN  = 25
DEFAULT_INT_PIN  = 23
DEFAULT_AMP_PIN  = 17
DEFAULT_SPI_BUS  = 0
DEFAULT_SPI_DEV  = 0
DEFAULT_SPI_SPEED_HZ = 2_000_000  # 2 MHz – sicher für alle Si468x-Revisionen

# ---------------------------------------------------------------------------
# Standard-Firmware-Verzeichnis
# ---------------------------------------------------------------------------
DEFAULT_FIRMWARE_DIR = Path("/home/weilmy/My_DAB_Si4689/hardware/firmwares")

# ---------------------------------------------------------------------------
# DAB Band-III Frequenzliste (Kanalname → Frequenz in kHz)
# ---------------------------------------------------------------------------
DAB_BAND_III: List[Tuple[str, int]] = [
    ("5A", 174_928), ("5B", 176_640), ("5C", 178_352), ("5D", 180_064),
    ("6A", 181_936), ("6B", 183_648), ("6C", 185_360), ("6D", 187_072),
    ("7A", 188_928), ("7B", 190_640), ("7C", 192_352), ("7D", 194_064),
    ("8A", 195_936), ("8B", 197_648), ("8C", 199_360), ("8D", 201_072),
    ("9A", 202_928), ("9B", 204_640), ("9C", 206_352), ("9D", 208_064),
    ("10A", 209_936), ("10B", 211_648), ("10C", 213_360), ("10D", 215_072),
    ("10N", 210_096),
    ("11A", 216_928), ("11B", 218_640), ("11C", 220_352), ("11D", 222_064),
    ("11N", 217_088),
    ("12A", 223_936), ("12B", 225_648), ("12C", 227_360), ("12D", 229_072),
    ("12N", 224_096),
    ("13A", 230_784), ("13B", 232_496), ("13C", 234_208), ("13D", 235_776),
    ("13E", 237_488), ("13F", 239_200),
]
# Index-Lookup: Kanalname → Listenindex (für CMD_DAB_TUNE_FREQ)
DAB_CHANNEL_INDEX: Dict[str, int] = {
    ch: idx for idx, (ch, _) in enumerate(DAB_BAND_III)
}



# ===========================================================================
class Si4689:
    """
    Hardware-Abstraktionsklasse für den Si4689 DAB/FM/AM-Chip.

    Die Klasse kapselt die gesamte SPI-Kommunikation, den Boot-Ablauf,
    das Firmware-Laden sowie die wichtigsten Tuner-Funktionen.

    Typischer Ablauf::

        radio = Si4689()
        radio.open()          # SPI + GPIO initialisieren
        radio.reset()         # Hardware-Reset
        radio.power_up()      # POWER_UP-Kommando senden
        radio.load_firmware(  # Patch + DAB-Firmware laden und booten
            patch_path=...,
            firmware_path=...
        )
        radio.configure_i2s()          # I2S-Ausgang konfigurieren
        radio.set_dab_freq_list()      # Frequenzliste setzen
        radio.dab_tune("12D")          # Kanal einstimmen
        radio.set_volume(30)           # Lautstärke setzen
        ...
        radio.close()

    Parameter
    ---------
    rst_pin : int
        BCM-GPIO-Nummer des RESET-Pins (Standard: 25).
    int_pin : int
        BCM-GPIO-Nummer des INT-Pins (Standard: 23).
    amp_pin : int
        BCM-GPIO-Nummer des Verstärker-Enable-Pins (Standard: 17).
    spi_bus, spi_dev : int
        SPI-Bus und Gerät (Standard: 0, 0 → /dev/spidev0.0).
    spi_speed_hz : int
        SPI-Taktfrequenz in Hz (Standard: 2 000 000).
    firmware_dir : Path
        Verzeichnis mit den Firmware-Binärdateien.
    verbose : bool
        Wenn True, werden Diagnose-Ausgaben auf stdout geschrieben.
    """

    # -----------------------------------------------------------------------
    def __init__(
        self,
        rst_pin: int = DEFAULT_RST_PIN,
        int_pin: int = DEFAULT_INT_PIN,
        amp_pin: int = DEFAULT_AMP_PIN,
        spi_bus: int = DEFAULT_SPI_BUS,
        spi_dev: int = DEFAULT_SPI_DEV,
        spi_speed_hz: int = DEFAULT_SPI_SPEED_HZ,
        firmware_dir: Path = DEFAULT_FIRMWARE_DIR,
        verbose: bool = True,
    ) -> None:
        self.rst_pin      = rst_pin
        self.int_pin      = int_pin
        self.amp_pin      = amp_pin
        self.spi_bus      = spi_bus
        self.spi_dev      = spi_dev
        self.spi_speed_hz = spi_speed_hz
        self.firmware_dir = Path(firmware_dir)
        self.verbose      = verbose

        self._spi: Optional[object] = None   # spidev.SpiDev-Instanz
        self._opened: bool = False

    # =======================================================================
    # SPI-Bus- und GPIO- Methoden
    # =======================================================================

    def open(self) -> None:
        """
        SPI-Bus und GPIO-Pins initialisieren.

        Muss vor allen anderen Methoden aufgerufen werden.
        Wirft RuntimeError, wenn spidev oder RPi.GPIO fehlen.
        """
        if not _HAS_SPIDEV:
            raise RuntimeError(
                "spidev nicht gefunden – bitte 'pip install spidev' ausführen."
            )
        if not _HAS_GPIO:
            raise RuntimeError(
                "RPi.GPIO nicht gefunden – bitte 'pip install RPi.GPIO' ausführen."
            )

        # --- SPI konfigurieren ---
        self._spi = spidev.SpiDev()
        self._spi.open(self.spi_bus, self.spi_dev)
        self._spi.max_speed_hz = self.spi_speed_hz
        self._spi.mode = 0          # CPOL=0, CPHA=0 (Si468x Anforderung)
        self._spi.bits_per_word = 8

        # --- GPIO konfigurieren ---
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.rst_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.int_pin, GPIO.IN)
        GPIO.setup(self.amp_pin, GPIO.OUT, initial=GPIO.LOW)

        self._opened = True
        self._log(
            f"Si4689 geöffnet: SPI={self.spi_bus}.{self.spi_dev} "
            f"@ {self.spi_speed_hz // 1000} kHz, "
            f"RST=GPIO{self.rst_pin}, INT=GPIO{self.int_pin}, "
            f"AMP=GPIO{self.amp_pin}"
        )

    def close(self) -> None:
        """
        SPI-Bus schließen und GPIO-Ressourcen freigeben.
        Verstärker wird dabei deaktiviert.
        """
        try:
            self.amp_enable(False)
        except Exception:
            pass
        try:
            if self._spi is not None:
                self._spi.close()
                self._spi = None
        except Exception:
            pass
        try:
            GPIO.cleanup()
        except Exception:
            pass
        self._opened = False
        self._log("Si4689 geschlossen.")

    def __enter__(self) -> "Si4689":
        """Kontextmanager-Unterstützung (with-Statement)."""
        self.open()
        return self

    def __exit__(self, *_) -> None:
        """Kontextmanager-Unterstützung – schließt automatisch."""
        self.close()

    # =======================================================================
    # Hardware-Steuerung
    # =======================================================================

    def reset(self, hold_ms: int = 10, settle_ms: int = 200) -> None:
        """
        Hardware-Reset des Si4689 durchführen.

        Der RESET-Pin wird für *hold_ms* Millisekunden auf LOW gezogen,
        anschließend auf HIGH gesetzt. Nach *settle_ms* Millisekunden
        ist der Chip bereit für das POWER_UP-Kommando.

        Parameter
        ---------
        hold_ms : int
            Dauer des LOW-Pulses in ms (Standard: 10 ms).
        settle_ms : int
            Wartezeit nach dem HIGH-Setzen in ms (Standard: 200 ms).
        """
        self._check_open()
        GPIO.output(self.rst_pin, GPIO.LOW)
        time.sleep(hold_ms / 1000.0)
        GPIO.output(self.rst_pin, GPIO.HIGH)
        time.sleep(settle_ms / 1000.0)
        self._log("Hardware-Reset durchgeführt.")

    def amp_enable(self, enable: bool = True) -> None:
        """
        Onboard-Verstärker (GPIO 17) ein- oder ausschalten.

        Parameter
        ---------
        enable : bool
            True = Verstärker AN, False = Verstärker AUS.
        """
        self._check_open()
        GPIO.output(self.amp_pin, GPIO.HIGH if enable else GPIO.LOW)
        self._log(f"Verstärker {'EIN' if enable else 'AUS'}.")

    # =======================================================================
    # Boot-Sequenz
    # =======================================================================

    def power_up(
        self,
        xtal_freq: int = 19_200_000,
        clk_mode: int = 1,
        tr_size: int = 0x07,
        ibias: int = 0x28,
        ctun: int = 0x07,
        ibias_run: int = 0x18,
    ) -> None:
        """
        POWER_UP-Kommando (0x01) senden.

        Versetzt den Chip in den Betriebszustand und konfiguriert den
        Referenztakt. Nach diesem Kommando ist der Chip bereit,
        Firmware zu empfangen (LOAD_INIT → HOST_LOAD → BOOT).

        Parameter
        ---------
        xtal_freq : int
            Quarzfrequenz in Hz. RaspiAudio HAT: 19 200 000 Hz.
        clk_mode : int
            Taktmodus: 1 = Crystal (Standard).
        tr_size : int
            Transistorgröße für den Oszillator (Standard: 0x07).
        ibias : int
            Biasrom-Stromwert während des Starts (Standard: 0x28).
        ctun : int
            Crystal-Tuning (Standard: 0x07).
        ibias_run : int
            Biasrom-Stromwert im Normalbetrieb (Standard: 0x18).
        """
        cmd = [0x00] * 16
        cmd[0]  = CMD_POWER_UP
        cmd[1]  = 0x00                          # CTSIEN deaktiviert
        cmd[2]  = ((clk_mode & 0x03) << 4) | (tr_size & 0x0F)
        cmd[3]  = ibias & 0x7F
        cmd[4:8] = list(xtal_freq.to_bytes(4, "little"))
        cmd[8]  = ctun & 0x3F
        cmd[9]  = 0x10                          # Pflichtfeld bei ROM00-Chips
        cmd[13] = ibias_run & 0x7F
        self._write_command(cmd)
        self._log("POWER_UP gesendet.")

    def load_firmware(
        self,
        patch_path: Path,
        firmware_path: Path,
        chunk_size: int = 32,
    ) -> None:
        """
        ROM-Patch und DAB/FM-Firmware laden, dann BOOT ausführen.

        Reihenfolge (gemäß AN649):
        1. LOAD_INIT
        2. HOST_LOAD (Patch, *chunk_size* Bytes pro Kommando)
        3. LOAD_INIT
        4. HOST_LOAD (Firmware)
        5. BOOT

        Parameter
        ---------
        patch_path : Path
            Pfad zur Patch-Datei (z.B. ``rom00_patch.016.bin``).
        firmware_path : Path
            Pfad zur Firmware-Datei (z.B. ``dab_radio_5_0_5.bin``).
        chunk_size : int
            Bytes pro HOST_LOAD-Kommando (Standard: 32, max: 4092).
        """
        patch_path    = Path(patch_path)
        firmware_path = Path(firmware_path)

        if not patch_path.exists():
            raise FileNotFoundError(f"Patch nicht gefunden: {patch_path}")
        if not firmware_path.exists():
            raise FileNotFoundError(f"Firmware nicht gefunden: {firmware_path}")

        self._log(f"Lade Patch ({patch_path.name}) …")
        self._send_load_init()
        self._host_load_file(patch_path, chunk_size)
        time.sleep(0.004)   # kurze Pause nach Patch (Empfehlung Silicon Labs)

        self._log(f"Lade Firmware ({firmware_path.name}) …")
        self._send_load_init()
        self._host_load_file(firmware_path, chunk_size)

        self._log("BOOT …")
        self._send_boot()
        self._log("Firmware gestartet.")

    def load_firmware_auto(
        self,
        mode: str = "dab",
        patch_glob: str = "rom00_patch*.bin",
        firmware_glob_dab: str = "dab*.bin",
        firmware_glob_fm:  str = "fm*.bin",
    ) -> None:
        """
        Firmware automatisch aus *firmware_dir* laden (Glob-Suche).

        Nützlich für schnelle Tests, wenn die genauen Dateinamen
        noch nicht bekannt sind. Bei mehreren Treffern wird die
        erste Datei (alphabetisch) verwendet.

        Parameter
        ---------
        mode : str
            ``"dab"`` oder ``"fm"`` – bestimmt, welche Firmware-Datei
            gesucht wird.
        patch_glob : str
            Glob-Muster für die Patch-Datei.
        firmware_glob_dab : str
            Glob-Muster für die DAB-Firmware.
        firmware_glob_fm : str
            Glob-Muster für die FM-Firmware.
        """
        patches = sorted(self.firmware_dir.glob(patch_glob))
        if not patches:
            raise FileNotFoundError(
                f"Kein Patch gefunden in {self.firmware_dir} ({patch_glob})"
            )
        patch = patches[0]

        fw_glob = firmware_glob_dab if mode.lower() == "dab" else firmware_glob_fm
        firmwares = sorted(self.firmware_dir.glob(fw_glob))
        if not firmwares:
            raise FileNotFoundError(
                f"Keine Firmware gefunden in {self.firmware_dir} ({fw_glob})"
            )
        firmware = firmwares[0]

        self.load_firmware(patch, firmware)

    # =======================================================================
    # Systemstatus
    # =======================================================================

    def get_sys_state(self) -> Dict[str, object]:
        """
        GET_SYS_STATE (0x09) – Systemzustand und Chip-Gesundheit abfragen.

        Rückgabe-Dictionary
        -------------------
        ``image``     : int   – Aktiver Firmware-Image-Code (0=Bootloader, 1=FM, 2=DAB …)
        ``mode``      : str   – Lesbare Modusbezeichnung
        ``cts``       : bool  – Clear To Send (Chip bereit für nächsten Befehl)
        ``err``       : bool  – Fehler-Flag (Alias für err_cmd, Rückwärtskompatibilität)
        ``err_cmd``   : bool  – Letzter Befehl fehlgeschlagen (STATUS0 Bit 6)
        ``pup_state`` : int   – Power-Up-Zustand: 0=Reset, 2=Bootloader, 3=App läuft
        ``fatal``     : bool  – Fataler Fehler (REPOFERR|CMDOFERR|ARBERR|ERRNR gesetzt)
        ``fatal_raw`` : int   – STATUS3 Bits[3:0] als Rohwert für Diagnose
        ``color``     : str   – Ampelfarbe: "green" | "yellow" | "red"
        ``label``     : str   – Lesbarer Zustandstext für Tooltip/Log
        """
        self._write_command([CMD_GET_SYS_STATE, 0x00])
        reply = self._read_reply(5)   # STATUS0..STATUS3 + RESP4(IMAGE)

        status0 = reply[0]
        status3 = reply[3]
        image   = reply[4]            # RESP4 = IMAGE[7:0]

        mode_map = {
            SYS_STATE_BOOTLOADER: "BOOTLOADER",
            SYS_STATE_FM:         "FM",
            SYS_STATE_DAB:        "DAB",
            SYS_STATE_TDMB:       "TDMB",
            SYS_STATE_AMHD:       "AM_HD",
        }

        # --- STATUS0 auswerten ---
        cts     = bool(status0 & 0x80)
        err_cmd = bool(status0 & 0x40)

        # --- STATUS3 auswerten ---
        pup_state = (status3 >> 6) & 0x03     # Bits [7:6]
        fatal_raw = status3 & 0x0F            # Bits [3:0]: REPOFERR|CMDOFERR|ARBERR|ERRNR
        fatal     = bool(fatal_raw)

        # --- Ampelfarbe und Label bestimmen ---
        if err_cmd or fatal:
            color = "red"
            label = f"Fehler – ERR_CMD={err_cmd}, FATAL=0x{fatal_raw:02X}"
        elif pup_state == 3 and cts:
            color = "green"
            label = f"Bereit – {mode_map.get(image, f'Image 0x{image:02X}')} läuft"
        elif pup_state == 2:
            color = "yellow"
            label = "Bootloader aktiv"
        else:
            color = "yellow"
            label = f"Warte / Reset (PUP={pup_state}, CTS={cts})"

        return {
            "image":     image,
            "mode":      mode_map.get(image, f"UNBEKANNT (0x{image:02X})"),
            "cts":       cts,
            "err":       err_cmd,        # Alias – Rückwärtskompatibilität
            "err_cmd":   err_cmd,
            "pup_state": pup_state,
            "fatal":     fatal,
            "fatal_raw": fatal_raw,
            "color":     color,
            "label":     label,
        }

    def get_part_info(self) -> Dict[str, object]:
        """
        GET_PART_INFO (0x02) – Chip-Partnummer und Revision lesen.

        Rückgabe-Dictionary
        -------------------
        ``chiprev`` : int
            Chip-Revision.
        ``romid`` : int
            ROM-ID (0x00 = ROM00).
        ``part`` : int
            Partnummer (z.B. 0x4689 für Si4689).
        ``part_str`` : str
            Formatierte Partnummer als String (z.B. ``"Si4689"``).
        """
        self._write_command([CMD_GET_PART_INFO, 0x00])
        reply = self._read_reply(10)            # bis RESP9 (= reply[9]) lesen
        chiprev = reply[4]                       # RESP4: Chip-Revision
        romid   = reply[5]                       # RESP5: ROM-ID
        part    = reply[8] | (reply[9] << 8)     # RESP8/9: PART[15:0], dezimal
        return {
            "chiprev":  chiprev,
            "romid":    romid,
            "part":     part,
            "part_str": f"Si{part}",             # dezimal, nicht hex
        }   

    # =======================================================================
    # Property-Zugriff
    # =======================================================================

    def set_property(self, prop_id: int, value: int) -> None:
        """
        SET_PROPERTY (0x13) – eine Chip-Property schreiben.

        Parameter
        ---------
        prop_id : int
            Property-ID (16 Bit, Little-Endian im Kommando).
        value : int
            Zu setzender Wert (16 Bit).
        """
        cmd = [
            CMD_SET_PROPERTY,
            0x00,
            prop_id & 0xFF,
            (prop_id >> 8) & 0xFF,
            value & 0xFF,
            (value >> 8) & 0xFF,
        ]
        self._write_command(cmd)

    def get_property(self, prop_id: int) -> int:
        """
        GET_PROPERTY (0x14) – eine Chip-Property lesen.

        Parameter
        ---------
        prop_id : int
            Property-ID.

        Rückgabe
        --------
        int : aktueller Wert der Property (16 Bit).
        """
        cmd = [
            CMD_GET_PROPERTY,
            0x00,
            prop_id & 0xFF,
            (prop_id >> 8) & 0xFF,
        ]
        self._write_command(cmd)
        reply = self._read_reply(4)
        return reply[2] | (reply[3] << 8)

    # =======================================================================
    # Audio-Konfiguration
    # =======================================================================


    def configure_i2s(
        self,
        sample_rate: int = 48_000,
        sample_size: int = 16,
        master: bool = False,
    ) -> None:
        """
        I2S-Ausgang aktivieren und konfigurieren.

        Setzt die Properties:
        - PIN_CONFIG_ENABLE  → I2S-Ausgang aktivieren
        - DIGITAL_IO_OUTPUT_SELECT → Master/Slave
        - DIGITAL_IO_OUTPUT_SAMPLE_RATE → Abtastrate
        - DIGITAL_IO_OUTPUT_FORMAT → Wortbreite / Format

        Parameter
        ---------
        sample_rate : int
            I2S-Abtastrate in Hz (Standard: 48 000).
        sample_size : int
            Wortbreite in Bits (Standard: 16).
        master : bool
            True = Si4689 ist I2S-Master (Standard).
        """
        # Bit1 = I2SOUTEN, Bit15 = INTB-Enable (immer gesetzt)
        self.set_property(PROP_PIN_CONFIG_ENABLE, 0x8002)
        output_select = 0x8000 if master else 0x0000
        self.set_property(PROP_DIGITAL_IO_OUTPUT_SELECT, output_select)
        self.set_property(PROP_DIGITAL_IO_OUTPUT_SAMPLE_RATE, sample_rate)
        # Bits [13:8] = Wortbreite, Bits [1:0] = Framing (0 = I2S Standard)
        fmt = ((sample_size & 0x3F) << 8)
        self.set_property(PROP_DIGITAL_IO_OUTPUT_FORMAT, fmt)
        self._log(
            f"I2S konfiguriert: {sample_rate} Hz / {sample_size} Bit / "
            f"{'Master' if master else 'Slave'}."
        )

    def set_volume(self, level: int) -> int:
        """
        Analog-Lautstärke setzen (Property 0x0300).

        Parameter
        ---------
        level : int
            Lautstärkepegel 0 (stumm) bis 63 (Maximum).
            Werte außerhalb des Bereichs werden begrenzt.

        Rückgabe
        --------
        int : tatsächlich gesetzter Pegel (nach Begrenzung).
        """
        level = max(0, min(63, int(level)))
        self.set_property(PROP_AUDIO_ANALOG_VOLUME, level)
        self._log(f"Lautstärke: {level}/63.")
        return level

    # =======================================================================
    # DAB-Funktionen
    # =======================================================================

    def configure_dab_frontend(self) -> None:
        """
        DAB-Frontend-Properties konfigurieren.

        Setzt die Kalibrierungswerte für den HF-Frontend des RaspiAudio HAT
        (VARM, VARB, CFG) sowie die Interrupt-Quellen und den
        RSSI-Schwellwert für ein gültiges Signal.
        """
        self.set_property(PROP_DAB_TUNE_FE_VARM, 0xFD12)
        self.set_property(PROP_DAB_TUNE_FE_VARB, 0x009B)
        self.set_property(PROP_DAB_TUNE_FE_CFG,  0x0000)
        # Keine DAB-Event-Interrupts auf GPIO 23 – SRVLSTINT/RECFGINT werden nie
        # quittiert und würden den INT-Pin permanent LOW halten (DSRVPCKTINT würde
        # dann keine falling edge mehr erzeugen). Alles wird gepollt.
        self.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, 0x0000)
        self.set_property(PROP_DAB_VALID_RSSI_THRESHOLD, 6)
        self.set_property(PROP_DAB_XPAD_ENABLE, 0x0005)   # DLS (Bit0) + PAD (Bit2)
        self._log("DAB-Frontend konfiguriert.")

    def set_dab_freq_list(
        self,
        freqs_khz: Optional[List[int]] = None,
        extend_range: bool = False,
    ) -> None:
        """
        DAB_SET_FREQ_LIST (0xB8) – Frequenzliste in den Chip laden.

        Muss vor dem ersten dab_tune() aufgerufen werden.
        Standard: vollständige Band-III-Liste (5A … 13F).

        Parameter
        ---------
        freqs_khz : list of int, optional
            Liste der Frequenzen in kHz. Wenn None, wird die
            vollständige Band-III-Liste aus DAB_BAND_III verwendet.
        extend_range : bool
            True = erweiterter Tuning-Bereich erlaubt (Bit0 im Kommando).
        """
        if freqs_khz is None:
            freqs_khz = [f for _, f in DAB_BAND_III]

        num = len(freqs_khz)
        if num == 0:
            raise ValueError("Frequenzliste ist leer.")
        if num > 75:
            raise ValueError(f"Frequenzliste zu lang ({num} > 75 Einträge).")

        cmd = [
            CMD_DAB_SET_FREQ_LIST,
            num & 0xFF,
            0x01 if extend_range else 0x00,
            0x00,
        ]
        for f in freqs_khz:
            cmd.extend(list(int(f).to_bytes(4, "little")))
        self._write_command(cmd)
        self._log(f"DAB-Frequenzliste geladen: {num} Einträge.")

    def dab_tune(
        self,
        channel: str | int,
        antcap: int = 0,
    ) -> None:
        """
        DAB_TUNE_FREQ (0xB0) – DAB-Kanal einstimmen.

        Parameter
        ---------
        channel : str oder int
            Kanalname (z.B. ``"12D"``) oder direkter Listen-Index (0-basiert).
            Der Index bezieht sich auf die zuletzt mit set_dab_freq_list()
            geladene Frequenzliste.
        antcap : int
            Antennen-Kapazitäts-Override (0 = automatisch, Bereich 1–4095).

        Beispiele::

            radio.dab_tune("12D")      # Kanal per Name
            radio.dab_tune(33)         # Kanal per Index
        """
        if isinstance(channel, str):
            ch_upper = channel.upper()
            if ch_upper not in DAB_CHANNEL_INDEX:
                raise ValueError(
                    f"Unbekannter DAB-Kanal '{channel}'. "
                    f"Gültige Kanäle: {list(DAB_CHANNEL_INDEX.keys())}"
                )
            freq_index = DAB_CHANNEL_INDEX[ch_upper]
            ch_label = ch_upper
        else:
            freq_index = int(channel)
            ch_label = str(freq_index)

        cmd = [
            CMD_DAB_TUNE_FREQ,
            0x00,            # Injection: automatisch
            freq_index & 0xFF,
            0x00,
            antcap & 0xFF,
            (antcap >> 8) & 0xFF,
        ]
        self._write_command(cmd)
        self._log(f"DAB-Tune: Kanal {ch_label} (Index {freq_index}).")

    def dab_digrad_status(
        self,
        stc_ack: bool = True,    # STC-Flag quittieren
        attune: bool = True,     # RSSI-Snapshot zum Tune-Zeitpunkt liefern
        digrad_ack: bool = False,
    ) -> Dict[str, object]:
        """DAB_DIGRAD_STATUS (0xB2)."""
        arg1 = (
            (0x08 if digrad_ack else 0x00) |
            (0x02 if attune   else 0x00) |
            (0x01 if stc_ack  else 0x00)
        )
        self._write_command([CMD_DAB_DIGRAD_STATUS, arg1])
        reply = self._read_reply(0x17)   # 23 Bytes reichen (bis TUNE_INDEX)
        return {
            "valid":       bool(reply[5] & 0x01),
            "acq":         bool(reply[5] & 0x04),
            "fic_error":   bool(reply[5] & 0x08),
            "rssi":        self._signed_byte(reply[6]),
            "snr":         reply[7],           # 0–20 dB, kein Vorzeichen
            "fic_quality": reply[8],           # 0–100 %
            "cnr":         reply[9],           # 0–54 dB
            "tune_freq_khz": int.from_bytes(reply[12:16], "little"),
            "tune_index":  reply[16],
        }

    def get_dab_signal_strength(self) -> Dict[str, object]:
        """
        DAB-Signalstärke des laufenden Senders abfragen.

        Sendet DAB_DIGRAD_STATUS (Cmd 0xB2) ohne Interrupt-Quittierung
        und gibt die wesentlichen Empfangs-Metriken als kompaktes
        Dictionary zurück.  Geeignet für regelmäßiges Polling aus der GUI.

        Rückgabe-Dictionary
        -------------------
        ``rssi``        : int
            Empfangspegel in dBm.  Bereich: −128 … +63.
            Typische Richtwerte für guten DAB-Empfang: > 15 dBm.
        ``snr``         : int
            Signal-/Rauschverhältnis in dB.  Bereich: 0–20.
            Guter Empfang: SNR > 10.
        ``fic_quality`` : int
            FIC-Kanal-Qualität in %.  Bereich: 0–100.
            Störungsfreier Empfang: > 80 %.
        ``cnr``         : int
            Träger-/Rauschverhältnis in dB.  Bereich: 0–54.
        ``valid``       : bool
            True wenn das Signal den eingestellten RSSI-Schwellwert
            überschreitet (DAB_VALID_RSSI_THRESHOLD).
        ``acq``         : bool
            True wenn das Ensemble synchronisiert ist (ACQ-Bit).

        Im Fehlerfall (z. B. SPI nicht geöffnet) werden Fallback-Werte
        zurückgegeben (rssi=-128, alle bools=False) und ein Log-Eintrag
        geschrieben, damit der Aufrufer keinen Exception-Handler benötigt.

        Quellen: AN649 Rev. 2.0, Cmd 0xB2 DAB_DIGRAD_STATUS,
                 Response-Bytes RESP4–RESP9.
        """
        try:
            status = self.dab_digrad_status(
                stc_ack=False,    # kein STC-Acknowledge – nur Lese-Abfrage
                attune=False,     # aktuellen Live-Pegel liefern (kein Tune-Snapshot)
                digrad_ack=False, # keine Interrupt-Flags quittieren
            )
        except Exception as exc:
            self._log(f"get_dab_signal_strength Fehler: {exc}")
            return {
                "rssi":        -128,
                "snr":         0,
                "fic_quality": 0,
                "cnr":         0,
                "valid":       False,
                "acq":         False,
            }
        return {
            "rssi":        status["rssi"],        # int, −128…+63 dBm
            "snr":         status["snr"],          # int, 0–20 dB
            "fic_quality": status["fic_quality"],  # int, 0–100 %
            "cnr":         status["cnr"],          # int, 0–54 dB
            "valid":       status["valid"],        # bool
            "acq":         status["acq"],          # bool
        }

    def dab_get_event_status(self, ack: bool = False) -> Dict[str, bool]:
        """
        DAB_GET_EVENT_STATUS (0xB3) – DAB-Ereignisse abfragen.

        Parameter
        ---------
        ack : bool
            True = Ereignis-Flags nach dem Lesen quittieren.

        Rückgabe-Dictionary
        -------------------
        ``srvlist`` : bool  – neue Dienstliste verfügbar.
        ``freqinfo`` : bool – Frequenzinfo aktualisiert.
        ``audio``    : bool – Audio-Ereignis.
        ``mute``     : bool – Mute aktiv.
        ``blk_error``: bool – Block-Fehler.
        ``blk_loss`` : bool – Block-Verlust.
        ``anno``     : bool – Announcement AKTIV (RESP5 Bit4) – TA-Trigger.
        ``annoint``  : bool – Announcement-Ereignis (RESP4 Bit4, sticky).
        """
        flags = 0x01 if ack else 0x00
        self._write_command([CMD_DAB_GET_EVENT_STATUS, flags])
        reply = self._read_reply(9)
        return {
            "srvlist":   bool(reply[5] & 0x01),
            "freqinfo":  bool(reply[5] & 0x02),
            "audio":     bool(reply[5] & 0x20),
            "anno":      bool(reply[5] & 0x10),   # Announcement aktiv (TA-Trigger)
            "annoint":   bool(reply[4] & 0x10),   # Announcement-Ereignis (sticky)
            "mute":      bool(reply[8] & 0x08),
            "blk_error": bool(reply[8] & 0x02),
            "blk_loss":  bool(reply[8] & 0x01),
        }

    def get_announcement_support_info(self, sid: int, src: int = 0) -> Dict[str, object]:
        """
        GET_ANNOUNCEMENT_SUPPORT_INFO (0xB5) – Announcement-Links einer Service-ID.

        Parameter
        ---------
        sid : int   Service-ID (32-Bit LE).
        src : int   0 = aktuelles Ensemble, 1 = OE (andere Ensembles).

        Rückgabe
        --------
        ``num_ids`` : int   Anzahl verlinkter Ensembles (0 → keine OE-Announcements).
        ``asu``     : int   Announcement Support Word (Bitfeld der Durchsagetypen).
        ``eids``    : list  Fremde Ensemble-IDs (je 16-Bit LE).
        """
        cmd = [0xB5, src & 0xFF, 0x00, 0x00] + list(sid.to_bytes(4, "little"))
        self._write_command(cmd)
        reply = self._read_reply(24)
        num_ids = reply[4]
        asu     = reply[6] | (reply[7] << 8)
        eids    = [
            reply[8 + i * 2] | (reply[9 + i * 2] << 8)
            for i in range(min(num_ids, 8))
            if 9 + i * 2 < len(reply)
        ]
        # cluster_ids: Cluster-IDs aus FIG 0/18 (SRC=0); für SRC=1 sind dies OE-EIDs.
        # Spec: jede ID ist 16-Bit LE; für SRC=0 steht der 8-Bit Cluster-ID im Low-Byte.
        cluster_ids = [e & 0xFF for e in eids]
        return {"num_ids": num_ids, "asu": asu, "eids": eids, "cluster_ids": cluster_ids}

    def get_oe_services_info(self, sid: int) -> Dict[str, object]:
        """
        GET_OE_SERVICES (0xC1) – OE-Service-Liste für eine Service-ID.

        Parameter
        ---------
        sid : int   Service-ID (32-Bit LE).

        Rückgabe
        --------
        ``size``     : int   Gesamtgrösse des Antwortblocks.
        ``num_eids`` : int   Anzahl verlinkter Ensembles (0 → keine OE-Services).
        ``eids``     : list  Ensemble-IDs (je 16-Bit LE).
        """
        cmd = [0xC1, 0x00, 0x00, 0x00] + list(sid.to_bytes(4, "little"))
        self._write_command(cmd)
        reply = self._read_reply(24)
        size     = reply[4] | (reply[5] << 8)
        num_eids = reply[6]
        eids     = [
            reply[8 + i * 2] | (reply[9 + i * 2] << 8)
            for i in range(min(num_eids, 8))
            if 9 + i * 2 < len(reply)
        ]
        return {"size": size, "num_eids": num_eids, "eids": eids}

    def dab_get_ensemble_info(self) -> Dict[str, object]:
        """
        DAB_GET_ENSEMBLE_INFO (0xB4) – Ensemble-ID und Ensemble-Label abfragen.

        Liefert den offiziellen Namen des aktuell eingestellten Ensembles,
        z.B. "SMC BEFR" für Kanal 8B oder "SRG SSR" für Kanal 12C.

        Muss NACH vollständiger Synchronisation aufgerufen werden (FIC dekodiert),
        d.h. nach wait_for_lock() / get_dab_signal_strength() mit acq=True.

        Antwortstruktur (AN649, Cmd 0xB4):
            RESP4-5  : EID[15:0]       – 16-Bit Ensemble-ID
            RESP6-21 : LABEL[0..15]    – Ensemble-Name (max. 16 Zeichen, Latin-1)
            RESP22   : ENSEMBLE_ECC    – Extended Country Code
            RESP23   : CHARSET         – Zeichensatz (0 = EBU Latin)

        Rückgabe
        --------
        ``eid``     : int  – Ensemble-ID
        ``label``   : str  – Ensemble-Name (z.B. "SMC BEFR")
        ``ecc``     : int  – Extended Country Code
        ``charset`` : int  – Zeichensatz-Code
        """
        self._write_command([0xB4, 0x00])    # CMD_DAB_GET_ENSEMBLE_INFO
        r = self._read_reply(26)              # 4 Status + 22 Daten-Bytes
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

    def get_dab_audio_info(self) -> Dict[str, object]:
        """
        DAB_GET_AUDIO_INFO (0xBD) – Audio-Modus des laufenden DAB-Dienstes abfragen.
 
        Liefert den Stereo/Mono-Modus, die Bitrate und die Abtastrate des
        momentan dekodierenden DAB/DAB+-Dienstes.  Der Befehl darf nur im
        Betriebszustand (PUP_STATE = 3, App läuft) gesendet werden.
 
        Rückgabe-Dictionary
        -------------------
        ``audio_mode``  : int
            Roher Modus-Code aus RESP8 Bits[1:0]:
            0 = Dual (zweisprachig)
            1 = Mono
            2 = Stereo
            3 = Joint Stereo
        ``mode_str``    : str
            Lesbarer Modus-String für die GUI (z.B. "Stereo", "Mono").
        ``sbr``         : bool
            Spectral Band Replication aktiv (nur DAB+, sonst False).
        ``ps``          : bool
            Parametric Stereo aktiv (nur DAB+, sonst False).
        ``bit_rate``    : int
            Audio-Bitrate des Dienstes in kbps.
        ``sample_rate`` : int
            Abtastrate des Audio-Decoders in Hz (typisch 48 000).
        ``drc_gain``    : int
            DRC-Gain 0–63, entspricht 0–15,75 dB (Schrittweite 0,25 dB).
 
        Quelle: AN649 Rev. 2.0, Cmd 0xBD DAB_GET_AUDIO_INFO, RESP4–RESP9.
        """
        CMD_DAB_GET_AUDIO_INFO = 0xBD       # lokal definiert; besser global als Konstante anlegen
        _MODE_MAP = {
            0: "Dual",
            1: "Mono",
            2: "Stereo",
            3: "Joint Stereo",
        }
 
        try:
            self._write_command([CMD_DAB_GET_AUDIO_INFO, 0x00])
            # 10 Bytes: STATUS0–STATUS3 (reply[0..3]) + RESP4–RESP9 (reply[4..9])
            reply = self._read_reply(10)
 
            # RESP8: [7:4]=XXXX  [3]=PS_FLAG  [2]=SBR_FLAG  [1:0]=AUDIO_MODE
            resp8       = reply[8]
            audio_mode  = resp8 & 0x03
            sbr_flag    = bool(resp8 & 0x04)
            ps_flag     = bool(resp8 & 0x08)
 
            bit_rate    = int.from_bytes(reply[4:6], "little")   # RESP4+RESP5
            sample_rate = int.from_bytes(reply[6:8], "little")   # RESP6+RESP7
            drc_gain    = reply[9]                                # RESP9
 
            mode_str = _MODE_MAP.get(audio_mode, f"Modus {audio_mode}")
 
            self._log(
                f"DAB Audio-Info: {mode_str} | "
                f"{bit_rate} kbps | {sample_rate} Hz | "
                f"SBR={sbr_flag} PS={ps_flag} DRC={drc_gain}"
            )
 
            return {
                "audio_mode":  audio_mode,
                "mode_str":    mode_str,
                "sbr":         sbr_flag,
                "ps":          ps_flag,
                "bit_rate":    bit_rate,
                "sample_rate": sample_rate,
                "drc_gain":    drc_gain,
            }
 
        except Exception as exc:
            self._log(f"get_dab_audio_info Fehler: {exc}")
            return {
                "audio_mode":  -1,
                "mode_str":    "",
                "sbr":         False,
                "ps":          False,
                "bit_rate":    0,
                "sample_rate": 0,
                "drc_gain":    0,
            }
 


    def dab_start_service(self, service_id: int, component_id: int) -> None:
        """
        START_DIGITAL_SERVICE (0x81) – DAB-Audiodienst starten.

        Parameter
        ---------
        service_id : int
            Service-ID (SID) aus der Dienstliste.
        component_id : int
            Komponenten-ID (SCIdS + SCId) aus der Dienstliste.
        """
        cmd = [
            CMD_START_DIGITAL_SERVICE,
            0x00, 0x00, 0x00,
            *list(service_id.to_bytes(4, "little")),
            *list(component_id.to_bytes(4, "little")),
        ]
        self._write_command(cmd)
        self._log(
            f"DAB-Dienst gestartet: SID=0x{service_id:08X}, "
            f"CID=0x{component_id:04X}."
        )

    def dab_stop_service(self, service_id: int, component_id: int) -> None:
        """
        STOP_DIGITAL_SERVICE (0x82) – DAB-Audiodienst stoppen.

        Parameter
        ---------
        service_id : int
            Service-ID des laufenden Dienstes.
        component_id : int
            Komponenten-ID des laufenden Dienstes.
        """
        cmd = [
            CMD_STOP_DIGITAL_SERVICE,
            0x00, 0x00, 0x00,
            *list(service_id.to_bytes(4, "little")),
            *list(component_id.to_bytes(4, "little")),
        ]
        self._write_command(cmd)
        self._log("DAB-Dienst gestoppt.")

    def get_digital_service_data(self, status_only: bool = False, ack: bool = True) -> Dict[str, object]:
        flags = (0x10 if status_only else 0x00) | (0x01 if ack else 0x00)
        self._write_command([CMD_GET_DIGITAL_SERVICE_DATA, flags])
        header = self._read_reply(24)
        if len(header) < 24:
            raise RuntimeError("Short GET_DIGITAL_SERVICE_DATA header reply")
        byte_count = int.from_bytes(bytes(header[18:20]), "little")
        reply = header
        if not status_only and byte_count > 0:
            # spidev xfer2 limit: 4096 bytes (1 Dummy + max 4095 Nutzbytes).
            # Pakete > 4071 Bytes (z.B. MOT/SLS-Segmente) können nicht in einem
            # Transfer gelesen werden. Das Paket wurde bereits durch ACK im Befehl
            # dequeued; das Payload-Read wird übersprungen (CS-Deassert nach
            # _read_reply(24) hat den SPI-State des Chips bereits zurückgesetzt).
            if byte_count <= 4071:
                reply = self._read_reply(24 + byte_count)
        payload = bytes(reply[24 : 24 + byte_count]) if len(reply) >= 24 else b""
        return {
            "overflow": bool(header[4] & 0x02),
            "packet_ready": bool(header[4] & 0x01),
            "buffer_count": header[5],
            "service_state": header[6],
            "data_src": (header[7] >> 6) & 0x03,
            "dscty": header[7] & 0x3F,
            "service_id": int.from_bytes(bytes(header[8:12]), "little"),
            "component_id": int.from_bytes(bytes(header[12:16]), "little"),
            "uatype": int.from_bytes(bytes(header[16:18]), "little"),
            "byte_count": byte_count,
            "seg_num": int.from_bytes(bytes(header[20:22]), "little"),
            "num_segs": int.from_bytes(bytes(header[22:24]), "little"),
            "payload": payload,
        }


    # =======================================================================
    # FM-Funktionen
    # =======================================================================

    def fm_tune(
        self,
        freq_khz: int,
        antcap: int = 0,
        injection: int = 0,
    ) -> None:
        """
        FM_TUNE_FREQ (0x30) – FM-Frequenz einstimmen.

        Parameter
        ---------
        freq_khz : int
            Frequenz in kHz (z.B. 99_400 für 99.4 MHz).
            Erlaubter Bereich: 64 000 – 108 000 kHz.
        antcap : int
            Antennen-Kapazitäts-Override (0 = automatisch).
        injection : int
            Mischer-Injection: 0 = automatisch, 1 = Low-Side, 2 = High-Side.

        Beispiel::

            radio.fm_tune(99_400)    # SRF 1 Bern (99.4 MHz)
        """
        freq_10khz = int(round(freq_khz / 10))
        arg1 = injection & 0x03
        cmd = [
            CMD_FM_TUNE_FREQ,
            arg1,
            freq_10khz & 0xFF,
            (freq_10khz >> 8) & 0xFF,
            antcap & 0xFF,
            (antcap >> 8) & 0xFF,
            0x00,
        ]
        self._write_command(cmd)
        self._log(f"FM-Tune: {freq_khz / 1000:.1f} MHz.")

    def fm_rsq_status(self, stcack: bool = False) -> Dict[str, object]:
        """
        FM_RSQ_STATUS (0x32) – FM-Empfangsstatus lesen.

        Parameter
        ---------
        stcack : bool
            True = STC-Flag nach dem Lesen quittieren.

        Rückgabe-Dictionary
        -------------------
        ``valid`` : bool
            True = Signal gültig.
        ``rssi`` : int
            Empfangspegel in dBm.
        ``snr`` : int
            Signal-Rausch-Abstand in dB.
        ``freqoff`` : int
            Frequenzabweichung in kHz (vorzeichenbehaftet).
        ``freq_khz`` : int
            Tatsächlich empfangene Frequenz in kHz.
        """
        flags = (0x04 | (0x01 if stcack else 0x00))
        self._write_command([CMD_FM_RSQ_STATUS, flags])
        reply = self._read_reply(23)
        freq_10khz = int.from_bytes(reply[6:8], "little")
        return {
            "valid":     bool(reply[5] & 0x01),
            "rssi":      self._signed_byte(reply[9]),
            "snr":       self._signed_byte(reply[10]),
            "freqoff":   self._signed_byte(reply[8]),
            "freq_khz":  freq_10khz * 10,
            "pilot":     bool(reply[12] & 0x80),  # Stereo-Pilot (RESP12 Bit7)
            "multipath": reply[11],               # 0–100: Multipfad-Indikator
        }

    def fm_seek_start(
        self,
        seekup: bool = True,
        wrap: bool = True,
    ) -> None:
        """
        FM_SEEK_START (0x31) – Automatische FM-Sendersuche starten.

        Läuft asynchron im Chip (FM-Firmware: fmhd_radio_*.bin).
        Nach dem Aufruf mit fm_wait_stc() auf Abschluss warten,
        dann fm_rsq_status(stcack=True) für Frequenz und Signal aufrufen.

        Parameter
        ---------
        seekup : bool
            True = vorwärts (höhere Frequenz), False = rückwärts.
        wrap : bool
            True = am Bandende umkehren (Standard-Suchverhalten).
        """
        arg1 = (0x01 if seekup else 0x00) | (0x04 if wrap else 0x00)  # WRAP=Bit2, SEEKUP=Bit0
        self._write_command([CMD_FM_SEEK_START, arg1])
        self._log(
            f"FM_SEEK_START: {'↑ vorwärts' if seekup else '↓ rückwärts'}, "
            f"wrap={'ja' if wrap else 'nein'}."
        )

    def fm_wait_stc(
        self,
        timeout: float = 8.0,
        poll_interval: float = 0.1,
    ) -> bool:
        """
        Warten auf STC (Seek/Tune Complete) nach fm_seek_start().

        Pollt STATUS0 über SPI (Bit0 = STCINT).
        Gibt True zurück wenn STC erkannt, False bei Timeout.

        Hinweis: Nach fm_tune() NICHT nötig – _write_command() wartet
        bereits intern auf CTS. Nur nach fm_seek_start() verwenden.

        Parameter
        ---------
        timeout : float
            Maximale Wartezeit in Sekunden (Standard: 8.0).
        poll_interval : float
            Polling-Intervall in Sekunden (Standard: 0.1).
        """
        if self._spi is None:
            raise RuntimeError("SPI nicht initialisiert.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._spi.xfer2([0x00, 0x00])[1]
            if status & 0x01:   # STCINT = Bit0
                self._log("STC (Seek/Tune Complete) erkannt.")
                return True
            time.sleep(poll_interval)
        self._log(f"fm_wait_stc: Timeout nach {timeout:.1f} s.")
        return False

    def fm_rds_status(self, intack: bool = True) -> Dict[str, object]:
        """
        FM_RDS_STATUS (0x34) – Eine RDS-Gruppe lesen (FMHD-Firmware).

        Response layout (AN649, 20 bytes):
          reply[5]   RESP5: bit1=RDSSYNC, bit0=RDSFIFOLOST
          reply[10]  RDSFIFOUSED[7:0] – Gruppen noch im FIFO (inkl. aktuelle)
          reply[11]  BLEA[7:6] BLEB[5:4] BLEC[3:2] BLED[1:0] – Block-Fehlerraten
                     Wert 3 = nicht korrigierbar
          reply[12..13] Block A (little-endian)
          reply[14..15] Block B
          reply[16..17] Block C
          reply[18..19] Block D
        """
        arg1 = 0x01 if intack else 0x00
        self._write_command([CMD_FM_RDS_STATUS, arg1])
        reply = self._read_reply(20)
        block_a = reply[12] | (reply[13] << 8)
        block_b = reply[14] | (reply[15] << 8)
        block_c = reply[16] | (reply[17] << 8)
        block_d = reply[18] | (reply[19] << 8)
        return {
            "block_a":     block_a,
            "block_b":     block_b,
            "block_c":     block_c,
            "block_d":     block_d,
            "rdsfifoused": reply[10],
            "rdssync":     bool(reply[5] & 0x02),
            "rdsfifolost": bool(reply[5] & 0x01),
            "blea":        (reply[11] >> 6) & 0x03,
            "bleb":        (reply[11] >> 4) & 0x03,
            "blec":        (reply[11] >> 2) & 0x03,
            "bled":        reply[11] & 0x03,
        }

    # =======================================================================
    # Private Hilfsmethoden
    # =======================================================================

    def _check_open(self) -> None:
        """Wirft RuntimeError, wenn open() noch nicht aufgerufen wurde."""
        if not self._opened:
            raise RuntimeError(
                "Si4689 ist nicht geöffnet – bitte zuerst open() aufrufen."
            )

    def _read_reply(self, length: int) -> List[int]:
        """
        *length* Bytes vom Si4689 über SPI lesen.

        Gemäß Si468x SPI-Protokoll: erstes Byte ist ein Dummy-Byte (0x00),
        gefolgt von *length* Nutzbytes. Die Methode gibt nur die Nutzbytes
        zurück (ohne das Dummy-Byte).

        Parameter
        ---------
        length : int
            Anzahl der zu lesenden Nutzbytes.

        Rückgabe
        --------
        list of int : Empfangene Bytes (ohne Dummy-Byte).
        """
        if self._spi is None:
            raise RuntimeError("SPI nicht initialisiert.")
        resp = self._spi.xfer2([0x00] * (length + 1))
        return resp[1:]

    def _wait_cts(self, timeout: float = 2.0, check_err: bool = True) -> None:
        """
        Warten bis CTS (Clear-To-Send, Bit 7 des Status-Bytes) gesetzt ist.

        Parameter
        ---------
        timeout : float
            Maximale Wartezeit in Sekunden (Standard: 2.0 s).
        check_err : bool
            True  = RuntimeError wenn ERR-Bit gesetzt (Standard, post-send).
            False = ERR-Bit ignorieren (pre-send: ein altes ERR vom vorigen
                    Befehl darf den nächsten Befehl nicht blockieren).
        """
        if self._spi is None:
            raise RuntimeError("SPI nicht initialisiert.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # [Dummy, STATUS0, STATUS1, STATUS2, STATUS3]
            resp = self._spi.xfer2([0x00, 0x00, 0x00, 0x00, 0x00])
            status0 = resp[1]
            status3 = resp[4]
            if status0 & 0x80:                       # CTS gesetzt
                if check_err and (status0 & 0x40):   # ERR nur post-send prüfen
                    raise RuntimeError(
                        f"Si4689 meldet Kommandofehler (Status=0x{status0:02X})."
                    )
                # REPOFERR(0x08)/CMDOFERR(0x04): Daten verloren -> SPI-Takt zu hoch.
                if check_err and (status3 & 0x0C):
                    raise RuntimeError(
                        f"Si4689 SPI-Überlauf (STATUS3=0x{status3:02X}) – "
                        f"SPI-Takt zu hoch (REPOFERR/CMDOFERR)."
                    )
                return
            time.sleep(0.001)
        raise TimeoutError(
            f"CTS-Timeout: Si4689 antwortet nicht (timeout={timeout:.1f} s)."
        )

    def _write_command(self, data: List[int], timeout: float = 2.0) -> None:
        """Ein Kommando über SPI an den Si4689 senden."""
        if self._spi is None:
            raise RuntimeError("SPI nicht initialisiert.")
        self._wait_cts(timeout=timeout, check_err=False)  # pre-send:  altes ERR ignorieren
        self._spi.xfer2(data)
        self._wait_cts(timeout=timeout, check_err=True)   # post-send: ERR dieses Befehls fangen

    def _send_load_init(self) -> None:
        """LOAD_INIT (0x06) senden – startet einen neuen Ladevorgang."""
        self._write_command([CMD_LOAD_INIT, 0x00])

    def _send_boot(self) -> None:
        """BOOT (0x07) senden. CTS signalisiert, dass die Firmware startbereit ist."""
        self._write_command([CMD_BOOT, 0x00], timeout=5.0)
        time.sleep(0.3)   # Firmware-Initialisierung abwarten

    def _host_load_file(self, path: Path, chunk_size: int = 32) -> None:
        """
        Binärdatei blockweise mit HOST_LOAD (0x04) über SPI laden.

        Parameter
        ---------
        path : Path
            Pfad zur Binärdatei.
        chunk_size : int
            Bytes pro HOST_LOAD-Kommando (empfohlen: 32, max: 4092).
        """
        total = 0
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                payload = [CMD_HOST_LOAD, 0x00, 0x00, 0x00] + list(chunk)
                self._write_command(payload)
                total += len(chunk)
        self._log(f"  HOST_LOAD: {total} Bytes aus '{path.name}' geladen.")

    @staticmethod
    def _signed_byte(value: int) -> int:
        """Vorzeichenloses Byte in vorzeichenbehafteten Integer umwandeln."""
        return value - 256 if value & 0x80 else value

    def _log(self, message: str) -> None:
        """Diagnose-Ausgabe (nur wenn verbose=True)."""
        if self.verbose:
            print(f"[Si4689] {message}")


    # ---------------------------------------------------------------------------
    # Methode für si4689_driver.py  →  Klasse Si4689
    # Abhängigkeit: self.get_digital_service_data(status_only, ack) muss in derselben Klasse vorhanden sein (CMD 0x84).
    # ---------------------------------------------------------------------------

    def get_dls_text(
        self,
        service_index: Optional[int] = None,
        attempts: int = 3,
        chunk_delay_s: float = 0.025,
        timeout: float = 0.5,
    ) -> str:
        """GET_DIGITAL_SERVICE_DATA (0x84): DLS-Text des laufenden DAB-Dienstes.

        Der Si4689 liefert DLS automatisch über den DSRV-Queue sobald ein
        Audiodienst läuft.  Pakete mit DATA_SRC=2 (DLS/DLS+ über PAD gemäss
        AN649 Section 7.14.2) werden gesammelt, dekodiert und dedupliziert.

        - Der Si4689 befüllt den DSRV-Queue automatisch für den laufenden Dienst.
        - Der Parameter ``service_index``, wurde beim T4B/T5A gebraucht, bleibt aus Kompatibilitätsgründen erhalten, wird aber ignoriert.
        - ``attempts``     : max. Anzahl DLS-Pakete die aus dem Queue gelesen
                            werden (nicht: Wiederholungen desselben Requests).
        - ``chunk_delay_s``: Polling-Intervall wenn noch kein Paket bereit.
                            25 ms ist praxisnäher als 8 ms beim T5A.
        - ``timeout``      : Gesamtwartezeit auf das erste Paket.

        Args:
            service_index: Nicht verwendet (Compat-Platzhalter für T5A-API).
            attempts:      Maximale Anzahl DLS-Pakete die gelesen werden (def. 3).
            chunk_delay_s: Pause zwischen Polling-Versuchen in Sekunden (def. 25 ms).
            timeout:       Gesamtwartezeit auf das erste Paket in Sekunden (def. 0.5).

        Returns:
            Dekodierter, deduplizierter DLS-Text als str.
            Leerer String wenn innerhalb von ``timeout`` kein DLS-Paket ankam.
        """
        # --- Charset-Mapping gemäss ETSI EN 300 401 Tabelle 8 / AN649 ---
        # Field 2 bits [7:4] des 2-Byte DLS-Prefix (AN649 Table 21/22)
        _CHARSET_MAP: dict[int, str] = {
            0:  "latin-1",      # EBU Latin – Standard-Fallback
            4:  "iso-8859-2",
            6:  "iso-8859-4",
            8:  "iso-8859-5",
            9:  "iso-8859-6",
            10: "iso-8859-7",
            11: "iso-8859-8",
            12: "iso-8859-9",
            15: "utf-8",
        }
        DATA_SRC_DLS = 2          # DATA_SRC[7:6] == 0b10  →  DLS/DLS+ über PAD

        parts: list[str] = []
        deadline = time.monotonic() + timeout
        reads = 0

        while reads < max(1, attempts):

            # ------------------------------------------------------------------
            # 1. DSRV-Queue auf bereitstehende Pakete prüfen (STATUS_ONLY)
            # ------------------------------------------------------------------
            status = self.get_digital_service_data(status_only=True, ack=False)

            if not status.get("packet_ready"):
                # Kein Paket bereit → warten oder abbrechen
                if time.monotonic() >= deadline:
                    break
                time.sleep(chunk_delay_s)
                continue

            # ------------------------------------------------------------------
            # 2. Paket vollständig lesen und aus Queue entfernen (ACK=True)
            # ------------------------------------------------------------------
            pkt = self.get_digital_service_data(status_only=False, ack=True)
            reads += 1

            # Nur DLS-PAD-Pakete auswerten (DATA_SRC == 2)
            if pkt.get("data_src") != DATA_SRC_DLS:
                # Anderen Pakettyp (z.B. MOT/SLS) verwerfen;
                # Queue-Loop sofort fortsetzen wenn noch Pakete vorhanden
                if pkt.get("buffer_count", 0):
                    continue
                break

            payload: bytes = pkt.get("payload", b"")

            # Mindestens: 2 Byte Prefix + 1 Byte Nutztext
            if len(payload) < 3:
                continue

            # ------------------------------------------------------------------
            # 3. 2-Byte DLS-Prefix parsen  (AN649 Table 21 / Table 22)
            #
            #    Field 1:  Toggle[7]  RFU[6:5]  C[4]  …
            #    Field 2:  (C=0)  Charset[7:4]  RFU[3:0]
            # ------------------------------------------------------------------
            field1 = payload[0]
            field2 = payload[1]

            c_flag = bool(field1 & 0x10)   # C=1 → DL Plus Command, kein Text
            if c_flag:
                # DL Plus Tags Command → kein displaybarer Text, überspringen
                if pkt.get("buffer_count", 0):
                    continue
                break

            charset_nibble = (field2 >> 4) & 0x0F
            encoding = _CHARSET_MAP.get(charset_nibble, "latin-1")

            # ------------------------------------------------------------------
            # 4. Nutztext dekodieren (Payload ab Byte 2, Null-Terminator kürzen)
            # ------------------------------------------------------------------
            text_bytes = payload[2:]
            null_pos = text_bytes.find(b"\x00")
            if null_pos >= 0:
                text_bytes = text_bytes[:null_pos]

            try:
                text = text_bytes.decode(encoding, errors="replace").strip()
            except (LookupError, UnicodeDecodeError):
                text = text_bytes.decode("latin-1", errors="replace").strip()

            if text and text not in parts:
                parts.append(text)

            # ------------------------------------------------------------------
            # 5. Queue-Ende erkennen: BUFF_COUNT == 0 → keine weiteren Pakete
            # ------------------------------------------------------------------
            if not pkt.get("buffer_count", 0):
                break

        return " ".join(parts)