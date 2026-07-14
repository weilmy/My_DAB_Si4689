#!/usr/bin/env python3
# ('my_venv_314':venv)

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller DAC PCM5122 und ADC PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

import os
from pathlib import Path
import tkinter as tk
import tkinter.ttk as ttk
import json
import traceback
import gc
import psutil
from collections import Counter
from typing import cast

import subprocess
import sys
import time
import signal
import threading
from utils import (ImprovedDispatcher, DatabaseManager, ImageManager, GUIUpdateBatcher, ChipButton)

from datetime import datetime
from typing import Optional, TypedDict
import sqlite3
from dataclasses import dataclass, field
import logging

sys.stdout.reconfigure(encoding='utf-8')
import locale
locale.setlocale(locale.LC_ALL, 'de_CH.utf8')

BASE_PATH = os.path.abspath(os.path.dirname(__file__))

# =============================================================================
# DAB "Complete EBU Latin based repertoire" (ETSI TS 101 756 V2.4.1, Annex C)
# Charset-Indikator 0000b in FIG Type 1 / Dynamic Label. WICHTIG: Das ist NICHT
# identisch mit ISO-8859-1/Latin-1! Im Bereich 0x80-0xFF weichen die Zuordnungen
# komplett ab (z.B. 0x82 = 'é' in EBU Latin, aber ein unsichtbares C1-Steuerzeichen
# in Latin-1 -> Zeichen verschwinden kommentarlos statt als Mojibake aufzufallen).
# Reservierte/undefinierte Codes (0x00, 0x0A, 0x0B, 0x1F) werden als '' abgebildet.
# =============================================================================
_EBU_LATIN_TABLE = (
    "\u0000\u0118\u012E\u0172\u0102\u0116\u010E\u0218\u021A\u010A\u0000\u0000\u0120\u0139\u017B\u0143"
    "\u0105\u0119\u012F\u0173\u0103\u0117\u010F\u0219\u021B\u010B\u0147\u011A\u0121\u013A\u017C\u0000"
    " !\"#\u0142%&'()*+,-./"
    "0123456789:;<=>?"
    "@ABCDEFGHIJKLMNO"
    "PQRSTUVWXYZ[\u016E]\u0141_"
    "\u0104abcdefghijklmno"
    "pqrstuvwxyz\u00AB\u016F\u00BB\u013D\u0126"
    "\u00E1\u00E0\u00E9\u00E8\u00ED\u00EC\u00F3\u00F2\u00FA\u00F9\u00D1\u00C7\u015E\u00DF\u00A1\u0178"
    "\u00E2\u00E4\u00EA\u00EB\u00EE\u00EF\u00F4\u00F6\u00FB\u00FC\u00F1\u00E7\u015F\u011F\u0131\u00FF"
    "\u0136\u0145\u00A9\u0122\u011E\u011B\u0148\u0151\u0150\u20AC\u00A3\u0024\u0100\u0112\u012A\u016A"
    "\u0137\u0146\u013B\u0123\u013C\u0130\u0144\u0171\u0170\u00BF\u013E\u00B0\u0101\u0113\u012B\u016B"
    "\u00C1\u00C0\u00C9\u00C8\u00CD\u00CC\u00D3\u00D2\u00DA\u00D9\u0158\u010C\u0160\u017D\u00D0\u013F"
    "\u00C2\u00C4\u00CA\u00CB\u00CE\u00CF\u00D4\u00D6\u00DB\u00DC\u0159\u010D\u0161\u017E\u0111\u0140"
    "\u00C3\u00C5\u00C6\u0152\u0177\u00DD\u00D5\u00D8\u00DE\u014A\u0154\u0106\u015A\u0179\u0164\u00F0"
    "\u00E3\u00E5\u00E6\u0153\u0175\u00FD\u00F5\u00F8\u00FE\u014B\u0155\u0107\u015B\u017A\u0165\u0127"
)
assert len(_EBU_LATIN_TABLE) == 256, f"EBU-Latin-Tabelle muss 256 Einträge haben, hat {len(_EBU_LATIN_TABLE)}"


def decode_dab_text(raw: bytes, charset_id: int) -> str:
    """
    Dekodiert DAB-Textfelder (DLS, Ensemble-/Service-Label, MOT ContentName)
    unter Berücksichtigung des DAB-spezifischen Charset-Indikators.

    charset_id ist der 4-Bit-Wert aus b7..b4 des Charset-Felds:
      0  -> Complete EBU Latin based repertoire (Annex C, NICHT Latin-1!)
      15 -> UTF-8
      4,6,8,9,10,11,12 -> ISO-8859-x-Varianten (ETSI EN 300 401 Charset-Tabelle)
    Unbekannte/undokumentierte Werte fallen defensiv auf EBU-Latin-Tabelle zurück,
    da das der DAB-Standard-Default ist (nicht Latin-1).
    """
    if charset_id == 0:
        return "".join(_EBU_LATIN_TABLE[b] for b in raw)
    iso_map = {4: "iso-8859-2", 6: "iso-8859-4", 8: "iso-8859-5",
               9: "iso-8859-6", 10: "iso-8859-7", 11: "iso-8859-8", 12: "iso-8859-9"}
    if charset_id == 15:
        try:
            return raw.decode("utf-8", errors="replace")
        except (LookupError, UnicodeDecodeError):
            return "".join(_EBU_LATIN_TABLE[b] for b in raw)
    encoding = iso_map.get(charset_id)
    if encoding:
        try:
            return raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    return "".join(_EBU_LATIN_TABLE[b] for b in raw)

from pages import MainPage, Page01, Page02, Page03, Page04, Page05, Page06, Page07, Page08, Page09
from hardware.si4689_driver import DAB_BAND_III, PROP_INT_CTL_ENABLE, PROP_DIGITAL_SERVICE_INT_SOURCE
from hardware.si4689_init import Si4689Manager
from utils.ta_controller import TaController, TaWindow
from hardware.audio_codec_hifiberry import Audio_Codec, AudioConfig

# ---------- Debug/Logging ----------
logging.basicConfig(
    filename='app.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def _session_info() -> str:
    try:
        ppid = os.getppid()
    except OSError:
        ppid = "?"
    try:
        sid = os.getsid(0)
    except OSError:
        sid = "?"
    return (
        f"ppid={ppid} sid={sid} "
        f"ssh_tty={os.getenv('SSH_TTY', '')} "
        f"ssh_conn={os.getenv('SSH_CONNECTION', '')} "
        f"term={os.getenv('TERM', '')} "
        f"user={os.getenv('USER', '')}"
    )

class Config(TypedDict, total=False):
    """Konfiguration für DAB Radio Application"""
    # Logos und Cover
    logo_dir: str
    fallback_logo: str
    fallback_cover: str
    
    # Pictures
    menue_pages: str
    mute_icon: str
    unmute_icon: str
    previous_icon: str
    next_icon: str
    status_red: str
    status_yellow: str
    status_green: str
    DAB_Logo: str
    Logo_Player: str
    Sender_CH_Karte: str
    srf_1: str
    srf_2: str
    srf_3: str
    srf_4: str
    srf_Musikwelle: str
    srf_Virus: str
    tv_srf_1: str
    tv_srf_2: str
    tv_srf_Info: str
    drei_punkte: str
    zeiger_dreieck: str
    signal_dreieck: str
    reload_dreieck: str
    kanalskala: str
    stau: str
    baustelle: str
    unfall: str
    vorher: str
    naechste: str
    
    # JSON Konfigurationsdateien
    dab_state_file: str
    dls_filter_config: str
    eq_presets: str
    eq_state_path: str
    fm_stations_ch: str
    alsa_config: str
    
    # Datenbanken
    music_data_db: str
    dab_scan_db: str
    
def load_config() -> Config:
    config_path = os.path.join(BASE_PATH, "assets/jsons", "config.json")
    default_config = {
        "logo_dir":          "assets/logos/",
        "fallback_logo":     "assets/logos/KeinSenderLogo.png",
        "fallback_cover":    "assets/logos/fallback_cover.png",
        "menue_pages":       "assets/pictures/MenuePages.png",
        "mute_icon":         "assets/pictures/mute.png",
        "unmute_icon":       "assets/pictures/un_mute.png",
        "previous_icon":     "assets/pictures/previous_sender.png",
        "next_icon":         "assets/pictures/next_sender.png",
        "status_red":        "assets/pictures/status_red.png",
        "status_yellow":     "assets/pictures/status_yellow.png",
        "status_green":      "assets/pictures/status_green.png",
        "DAB_Logo":          "assets/pictures/DAB_neu.png",
        "Logo_Player":       "assets/pictures/Logo_Player.png",
        "Sender_CH_Karte":   "assets/pictures/Sender_CH_Karte_800x430.png",
        "srf_1":             "assets/pictures/SRF_1.png",
        "srf_2":             "assets/pictures/SRF_2.png",
        "srf_3":             "assets/pictures/SRF_3.png",
        "srf_4":             "assets/pictures/SRF_4.png",
        "srf_Musikwelle":    "assets/pictures/SRF_Musikwelle.png",
        "srf_Virus":         "assets/pictures/SRF_Virus.png",
        "tv_srf_1":          "assets/pictures/TV_SRF_1.png",
        "tv_srf_2":          "assets/pictures/TV_SRF_2.png",
        "tv_srf_Info":       "assets/pictures/TV_SRF_Info.png",
        "drei_punkte":       "assets/pictures/Drei_Punkte.png",
        "zeiger_dreieck":    "assets/pictures/Dreieck.png",
        "signal_dreieck":    "assets/pictures/Dreieck_Signal.png",
        "reload_dreieck":    "assets/pictures/Dreieck_Reload.png",
        "kanalskala":        "assets/pictures/Kanalskala.png",
        "dab_state_file":    "assets/jsons/dab_state.json",
        "dls_filter_config": "assets/jsons/dls_filter_config.json",
        "eq_presets":        "assets/jsons/eq_presets.json",
        "eq_state_path":     "assets/jsons/eq_state.json",
        "fm_stations_ch":    "assets/jsons/fm_stations_ch.json",
        "alsa_config":       "assets/jsons/alsa_config.json",
        "music_data_db":     "assets/DB/music_data.sqlite",
        "dab_scan_db":       "assets/DB/dab_scans.sqlite",
        "stau":              "assets/pictures/stau.png",
        "baustelle":         "assets/pictures/baustelle.png",
        "unfall":            "assets/pictures/unfall.png",
        "vorher":            "assets/pictures/vorher.png",
        "naechste":          "assets/pictures/naechste.png"
    }

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for key, val in default_config.items():
            if key not in config:
                print(f"Schlüssel '{key}' fehlt in config.json → Standardwert wird verwendet.")
                config[key] = val
        print("🏗  Konfiguration erfolgreich geladen.")
    except FileNotFoundError:
        print("⚠️ config.json nicht gefunden → Standardwerte werden verwendet.")
        config = default_config
    except json.JSONDecodeError:
        print("❌ Fehler beim Einlesen der config.json:")
        traceback.print_exc()
        config = default_config

    # Absolutpfade erstellen
    for key, rel_path in config.items():
        if isinstance(rel_path, str) and not os.path.isabs(rel_path):
            config[key] = os.path.join(BASE_PATH, rel_path)
    return cast(Config, config)

@dataclass
class AppState:
    Sender_Id:       list[int]         = field(default_factory=list)
    Sender_Name:     list[str]         = field(default_factory=list)
    AktuellerSender: str               = ""
    AktuelleSenderId:int               = 0
    sender_history:  list[str]         = field(default_factory=list)
    current_index:   int               = -1
    AktuelleLautstaerke_DAB:int        = 0
    AktuelleLautstaerke_Player:int     = 50
    TA_Lautstaerke_DAB:int             = 65   # Lautstärke für TA-Durchsage
    LetzterSender:   Optional[str]     = None
    new_md:          tuple[str, str, str] | None = None
    last_md:         tuple[str, str, str] | None = None
    artist_bio_state:bool = False
    news_bio_loop:   bool = False
    artist_bio:      str | None = None
    artist_n:        str = ""
    stats_selected_sender: Optional[str] = None
    new_tune:        bool = False

    # Player:
    player_playlist: list[str] = field(default_factory=list)
    player_current_index: int  = -1
    player_shuffle: bool       = False
    player_repeat: bool        = False
    dab_prev_volume: int       = 0
    player_prev_volume: int    = 50

    # EQ
    eq_pct_10: list[int]              = field(default_factory=lambda: [50]*10)
    eq_selected_preset: Optional[str] = None
    eq_scale: float                   = 1.0

@dataclass
class PageResources:
    needs_raw_dump: bool = False    # Daten in dab.raw schreiben, für Matplotlib Waveform/FFT
    needs_mute: bool = False        # Schaltet den Scanner stumm  
    needs_fm_mode:     bool = False # Chip braucht FM-Firmware
    needs_waveform: bool = False    # Matplotlib-Plots (Waveform) aktivieren
    needs_FFT: bool = False         # Matplotlib-Plots (FFT) aktivieren
    needs_epg: bool = False         # Aktualisierung EPG-Programmdaten
    needs_ta: bool = False          # TA-Polling (Verkehrsdurchsage) aktiv

PAGE_PROFILES: dict[str, PageResources] = {
    # MainPage: DLS-Polling aktiv, kein Raw-Dump, TA-Polling aktiv
    "MainPage": PageResources(needs_ta=True),
    
    # Page01 (Statistik): Keine besonderen Ressourcen
    "Page01": PageResources(),
    
    # Page02 (Player + Waveform): Raw-Dump für Waveform-Plot
    "Page02": PageResources(
        needs_raw_dump=True,
        needs_waveform=True
    ),
    
    # Page03 (Equalizer + FFT): Raw-Dump für FFT-Plot
    "Page03": PageResources(
        needs_raw_dump=True,
        needs_FFT=True
    ),
    
    # Page04 (Scanner): Scanner stumm und keine TAs
    "Page04": PageResources(
        needs_mute=True,
    ),

    # Page05 (FM): Chip braucht FM-Firmware
    "Page05": PageResources(
    needs_fm_mode=True,
    ),
    
    # Page06 (Karte): Keine besonderen Ressourcen
    "Page06": PageResources(),
    
    # Page07 (Guide): Keine besonderen Ressourcen (EPG wird in Page verwaltet)
    "Page07": PageResources(
        needs_epg=True),

    # Page08 (ASTRA Verkehrsmeldungen): Keine besonderen Ressourcen (Traffic-API wird in Page verwaltet)
    "Page08": PageResources(),
}

# ============================================================================
# HAUPTANWENDUNG
# ============================================================================

class App(tk.Tk):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = AppState()

        # --- SIGINT/Beenden -----------------------
        self._is_closing = False
        self.stop_event = threading.Event()
        
        # --- DISPATCHER mit ThreadPoolExecutor und dynamische Anpassung an die verfügbaren CPU-Kerne aktivieren ---
        try:
            detected_cores = len(os.sched_getaffinity(0))
        except AttributeError:
            detected_cores = os.cpu_count() or 4

        optimal_workers = min(detected_cores, 4)  # Max 4 für Ihr Projekt
        print(f"🔧 CPU-Kerne: {detected_cores} → Dispatcher: {optimal_workers} Workers")

        # --- Task-Cancellation aktivieren ----------------------------------------------------------
        self.dispatcher = ImprovedDispatcher(
            max_workers=2,
            enable_memory_tracking=False,
            memory_check_interval=100,
            verbose=False,
            enable_smart_cancellation=True
        )
        print("✅ ImprovedDispatcher mit Smart-Cancellation initialisiert")

        self.serial_lock = threading.RLock()
        signal.signal(signal.SIGINT, self._on_sigint)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._on_sigterm)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, self._on_sighup)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.gui_batcher = GUIUpdateBatcher(self, batch_interval_ms=50)
        self._app_start_time = time.time()
        self.bind_all("<Control-c>", lambda e: self._on_sigint(None, None))
        self._arm_sigint_pump()

        # --- Initialisierung/Konfiguration Si4689 ------------------------
        print("🔧 Si4689 wird initialisiert …")

        self.si4689 = Si4689Manager(verbose=False)
        if not self.si4689.initialize():
            print("⚠️  Si4689 nicht bereit – Hardwareverbindung prüfen")

        self._scan_data: list = []

        # DAB-Zustandsverfolgung für tune_service
        self._current_channel: str | None = None
        self._current_sid:     int | None = None
        self._current_cid:     int | None = None

        # --- Traffic Announcement (TA) ---
        self.ta = TaController()
        self._ta_poll_id      = None    # after()-ID des TA-Polls
        self._ta_busy         = False   # Reentrancy-Schutz für _ta_poll_run
        self._ta_active        = False  # True solange wir (Wartephase/TA) auf SRF 1 sind
        self._ta_window       = None    # TaWindow während bestätigter TA
        self._ta_home         = None    # (sid, cid, channel, index, name) zum Zurückschalten
        self._ta_anno_enabled = False   # enable_announcements() schon gesendet?
        self._ta_anno_prev    = False           # vorheriger anno-Pegel (Rising-Edge -> 0xB6)
        self._ta_is_traffic   = False           # aktuelle Episode ist Verkehrsdurchsage
        self._ta_target       = (None, None)    # (sid, cid) des Trägers aus 0xB6
        self._ta_switched     = False           # haben wir tatsächlich umgeschaltet?
        self.fallindicator    = None            # Zeig an in welchem Fall die TA angekommen ist

        # --- Audio-Pipeline starten --------------------------------------
        # config_data wird hier geladen damit alsa_config_path verfügbar ist.
        # (self.config_data = load_config() erfolgt nochmals bei UI-Setup – idempotent.)
        _cfg = load_config()
        self.audio_codec = Audio_Codec(
            AudioConfig(alsa_config_path=_cfg.get("alsa_config", ""))
        )
        print("=" * 60)
        print("🎵 STARTE AUDIO-PIPELINE")
        print("=" * 60)

        success = self.audio_codec.start_audio_codec()  # Startet nur Haupt-Pipeline
        if success:
            status = self.audio_codec.get_status()
            print(f"   Uptime: {status.get('uptime_seconds', 0):.1f}s")
            print(f"   Health-Check: {'aktiv' if status.get('health_check_active') else 'inaktiv'}")
            print(f"🎸🥁🎷 Audio-Streaming läuft... (Raw-Dump deaktiviert) 🎷🥁🎸")
        else:
            print("⚠️ WARNUNG: Audio-Pipeline konnte nicht gestartet werden!")
            print("   Bitte ALSA-Konfiguration prüfen!")

        # --- UI Basis ----------------------------------------------------
        self.title("My DAB+/FM Radio mit Si4689")
        self.geometry("800x480")
        self.resizable(False, False)
        self.configure(bg   ="#38bb6d")
        self.style          = ttk.Style()
        self.config_data    = load_config()
        self.base_path      = Path(BASE_PATH)
        self._music_db_path = self._resolve_music_db_path()

        # ========== DATABASE MANAGERS ==========
        # a) DB-Manager für Music-Metadata
        music_db_path = self.config_data.get("music_data_db")
        if music_db_path:
            self.music_db_manager = DatabaseManager(
                db_path=music_db_path,
                enable_memory_tracking=False,
                memory_check_interval=100,
                check_same_thread=False,
                verbose=False
            )
            print(f"✅ Music-DB Manager: {music_db_path}")
        else:
            self.music_db_manager = None
            print("⚠️ music_data_db nicht konfiguriert")
        
        # b) DB-Manager für DAB-Scans
        scan_db_path = self.config_data.get("dab_scan_db")
        if scan_db_path:
            self.scan_db_manager = DatabaseManager(
                db_path=scan_db_path,
                enable_memory_tracking=False,
                memory_check_interval=100,
                check_same_thread=False,
                verbose=False
            )
            print(f"✅ Scan-DB Manager: {scan_db_path}")
        else:
            self.scan_db_manager = None
            print("⚠️ dab_scan_db nicht konfiguriert")

        # TA-Lautstärke aus JSON laden (Default 70 falls nicht gesetzt)
        self._load_ta_volume()

        # --- Image-Manager für Icons, Logos und Cover ---
        self.image_manager = ImageManager(
            enable_memory_tracking=True,
            verbose=False
        )
        print("✅ ImageManager initialisiert")

        # --- GUI Konfigurieren ---
        container = tk.Frame(self)
        container.pack(side="top", fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        print("Daten vorbereiten und GUI aktualisieren")
        self.create_layout_styling()
        self.gui_controller   = GUIController(self)
        self.pages = {}
        self.aktuelle_seite = None
        self._volume_before_mute: int | None = None
        self._last_dls_text   = ""
        self._status_poll_id  = None
        self._dls_poll_id     = None
        self.current_page_name  = None

        # --- Seiten anlegen ---
        for PageClass in (MainPage, Page01, Page02, Page03, Page04, Page05, Page06, Page07, Page08, Page09,):
            page_name = PageClass.__name__
            frame = PageClass(parent=container, controller=self)
            self.pages[page_name] = frame
            frame.grid(row=0, column=0, sticky=tk.NSEW)

        # MainPage nach vorne holen, damit keine andere Seite kurz aufblitzt
        if "MainPage" in self.pages:
            try:
                self.pages["MainPage"].tkraise()
            except tk.TclError as e:
                print(f"⚠️ MainPage tkraise fehlgeschlagen: {e}")

        # --- Overlay-Menü aktivieren ---
        self.overlay_frame = tk.Frame(self, bg="#237E71", height=30)
        self.overlay_frame.place(relx=1.0, rely=0.0, anchor="ne")
        self.overlay_frame.pack_propagate(False)
        self.gui_controller.init_overlay(self.overlay_frame)

        self.after(200, self._finish_startup)

    # --- Hilfsmethoden ---
    def _resolve_music_db_path(self) -> str:
        db_path = (self.config_data or {}).get("music_data_db")
        if not isinstance(db_path, str) or not db_path:
            db_path = str(self.base_path / "assets" / "DB" / "music_data.sqlite")
        return db_path

    def _ensure_music_schema(self, conn: sqlite3.Connection) -> None:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS music_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc     TEXT,
                ts_local   TEXT,
                sender     TEXT,
                artist     TEXT,
                title      TEXT,
                genre      TEXT,
                song       TEXT,
                raw        TEXT,
                source     TEXT,
                confidence REAL,
                track_key  TEXT,
                track_id   TEXT
            )
        """)
        c.execute("PRAGMA table_info(music_log)")
        existing_cols = {row[1] for row in c.fetchall()}
        needed = {
            "ts_utc": "TEXT", "ts_local": "TEXT", "sender": "TEXT", "artist": "TEXT",
            "title": "TEXT", "genre": "TEXT", "song": "TEXT", "raw": "TEXT", "source": "TEXT",
            "confidence": "REAL", "track_key": "TEXT", "track_id": "TEXT",
            "timestamp": "TEXT",
        }
        for col, typ in needed.items():
            if col not in existing_cols:
                c.execute(f"ALTER TABLE music_log ADD COLUMN {col} {typ}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_music_sender_date ON music_log(sender, ts_local)")

        try:
            c.execute("PRAGMA index_list(music_log)")
            for _seq, idx_name, is_unique, *_ in c.fetchall():
                if idx_name == "idx_music_track_key" and int(is_unique) == 1:
                    c.execute("DROP INDEX IF EXISTS idx_music_track_key")
        except sqlite3.Error:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS idx_music_track_key_nonuniq ON music_log(track_key)")
        conn.commit()

    def _init_music_db(self) -> None:
        try:
            with sqlite3.connect(self._music_db_path) as conn:
                # Performance-Optimierung
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                
                self._ensure_music_schema(conn)
        except Exception as e:
            print(f"[DB] Initialisierung fehlgeschlagen: {e}")

    def _finish_startup(self):
        """
        Aktiviert MainPage, EQ-Restore.
        """
        # EQ nach ALSA-Restore anwenden
        try:
            Page03.restore_eq_state(self, apply_eq=True)
        except Exception as e:
            print(f"[Startup] EQ-Restore Fehler: {e}")

        try:
            self.gui_controller.switch_page("MainPage")
        except Exception as e:
            print(f"[Startup] switch_page('MainPage') Fehler: {e}")
            return

        try:
            self.gui_controller.toggle_menu()
        except tk.TclError:
            pass

        # DAB-Listen/Last-State laden, Ampel aktivieren (läuft im Tk-Thread, aber verzögert).
        try:
            self.after(50, self._deferred_mainpage_init)
        except tk.TclError as e:
            print(f"[Startup] after() fehlgeschlagen, direkter Aufruf: {e}")
            self._deferred_mainpage_init()

        # Musik-DB Initialisierung im Dispatcher starten.
        try:
            self.dispatcher.submit(self._init_music_db, key="music_db_init")
        except RuntimeError:
            self._init_music_db()

    def _deferred_mainpage_init(self) -> None:
        page = self.pages.get("MainPage")
        if page is None:
            return
        # noinspection PyBroadException
        try:
            self._scan_data = page.data_controller._load_scan_data()
            page.gui_controller._sort_senderliste()
        except Exception as e:
            print(f"[Startup] _load_scan_data Fehler: {e}")
        # noinspection PyBroadException
        try:
            page.data_controller.parse_dab_program_types()
        except Exception as e:
            print(f"[Startup] parse_dab_program_types Fehler: {e}")
        try:
            page.data_controller.Read_last_tune_volume()
        except Exception as e:
            print(f"[Startup] Read_last_tune_volume Fehler: {e}")
        try:
            page.data_controller.refresh_si4689_status_lamp_async()
        except Exception as e:
            print(f"[Startup] refresh_si4689_status_lamp_async Fehler: {e}")        
        
    # --- Hilfen für SIGINT (Ctrl+C und Ausfall ALSA) -----------------------------------------------------------------
    def _arm_sigint_pump(self):
        if not self._is_closing:
            self.after(100, self._arm_sigint_pump)#

    def _on_sigint(self, signum, frame):
        print("\nCtrl-C erkannt → schliessen", flush=True)
        self.on_close()

    def _on_sigterm(self, signum, frame):
        print(f"Signal {signum} empfangen, App wird beendet. {_session_info()}")
        self.on_close()

    def _on_sighup(self, signum, frame):
        print(
            f"Signal {signum} empfangen, App bleibt aktiv (SIGHUP ignoriert). "
            f"{_session_info()}"
        )

    # === App-Logik / Ressourcen-Management =================================================
    def show_page(self, page_name: str) -> None:
        # Alte Page verstecken
        if self.current_page_name:
            old_page = self.pages.get(self.current_page_name)
            if old_page and hasattr(old_page, 'on_page_hide'):
                old_page.on_page_hide()  # Cleanup aufrufen

        old_page = getattr(self, "current_page_name", None)

        # 1) Ressourcen für neue Seite einstellen
        try:
            self.apply_resource_profile(page_name, old_page_name=old_page)
        except Exception as e:
            print(f"[Resources] Fehler beim Anwenden des Profils für {page_name}: {e}")

        # 2) Seite anzeigen
        page = self.pages[page_name]
        self.current_page_name = page_name
        page.tkraise()

        # 3) Bei Seitenwechsel aufrufen. Entscheidet zwischen Erst- und Wiederaktivierung.
        try:
            if hasattr(page, "activate"):
                page.activate()
        except Exception as e:
            print(f"Fehler beim Aktivieren der Seite {page_name}: {e}")

    def apply_resource_profile(self, page_name: str, old_page_name: str | None = None) -> None:
        """
        Ressourcen-Verwaltung.
        Audio-Pipeline läuft permanent mit tee. Mit PageResources-Flag needs_raw_dump in dab.raw speichern.
        """
        new_profile = PAGE_PROFILES.get(page_name, PageResources())
        old_profile = PAGE_PROFILES.get(old_page_name, PageResources()) if old_page_name else PageResources()
        
        # --- 2) a) Audio-Pipeline prüfen nur ob sie noch läuft und starten ggf. neu. Pipeline läuft permanent mit tee. 
        #        b) Prüfen ob PageResources-Flag needs_raw_dump True ist → Daten in dab.raw schreiben, sonst keine Daten in dab.raw schreiben und löschen
        try:
            ac = getattr(self, "audio_codec", None)
            if ac:
                # Haupt-Pipeline: Sicherstellen, dass sie läuft
                if not ac.is_running():
                    print("🎵 Audio-Pipeline: Neustart (war gestoppt)")
                    ac.start_audio_codec()
                
                # Raw-Capture aktivieren
                needs_raw = new_profile.needs_raw_dump
                
                if needs_raw and not ac.is_raw_capture_running():
                    # Raw-Capture starten
                    #print(f"📼 Raw-Capture für {page_name} gestartet")
                    ac.start_raw_capture()
                    
                elif not needs_raw and ac.is_raw_capture_running():
                    # Raw-Capture stoppen (löscht auch dab.raw)
                    #print(f"📼 Raw-Capture gestoppt (verlasse {old_page_name})")
                    ac.stop_raw_capture()
                    
        except Exception as e:
            print(f"[Resources] Audio Fehler: {e}")
        
        # --- 3) Matplotlib-Plots (Waveform) starten/stoppen basierend auf needs_waveform Flag ---
        p2 = self.pages.get("Page02")
        if p2 is not None and hasattr(p2, "player_controller"):
            pc = p2.player_controller
            if new_profile.needs_waveform:
                if hasattr(pc, "start_plot"):
                    try:
                        #print(f"📊 Waveform-Plot gestartet (needs_waveform=True)")
                        pc.start_plot()
                    except Exception as e:
                        print(f"[Resources] Waveform start_plot Fehler: {e}")
            else:
                if hasattr(pc, "stop_plot"):
                    # noinspection PyBroadException
                    try:
                        #print(f"📊 Waveform-Plot gestoppt (verlasse {old_page_name})")
                        pc.stop_plot()
                    except Exception:
                        pass

        # --- 4) Matplotlib-Plots (FFT) starten/stoppen basierend auf needs_FFT Flag ---
        p3 = self.pages.get("Page03")
        if p3 is not None:
            if new_profile.needs_FFT:
                if hasattr(p3, "start_plot"):
                    try:
                        #print(f"📊 FFT-Plot gestartet (needs_FFT=True)")
                        p3.start_plot()
                    except Exception as e:
                        print(f"[Resources] FFT start_plot Fehler: {e}")
            else:
                if hasattr(p3, "stop_plot"):
                    # noinspection PyBroadException
                    try:
                        #print(f"📊 FFT-Plot gestoppt (verlasse {old_page_name})")
                        p3.stop_plot()
                    except Exception:
                        pass

        # --- 5) TA-Polling (Verkehrsdurchsage) nur auf Seiten mit needs_ta ---
        try:
            if new_profile.needs_ta and not old_profile.needs_ta:
                self._start_ta_poll()
            elif old_profile.needs_ta and not new_profile.needs_ta:
                self._stop_ta_poll()
        except Exception as e:
            print(f"[Resources] TA Fehler: {e}")
        
        # --- 6) Prüfen ob PageResources-Flag needs_mute True ist → Mute-Page (z.B auf Page04 Scanner) ---
        if new_profile.needs_mute and not old_profile.needs_mute:
            try:
                current_volume = self.state.AktuelleLautstaerke_DAB
                if current_volume > 0:
                    self._volume_before_mute = current_volume
                    print(f"🔇 Lautstärke stumm (vorher: {current_volume})")
                    self.state.AktuelleLautstaerke_DAB = 0
                    self.dispatcher.submit(
                        lambda: self.volume_service(0),
                        key="volume_mute"
                    )
            except Exception as e:
                print(f"[Resources] Mute Fehler: {e}")
        
        # Verlassen einer Mute-Page
        elif old_profile.needs_mute and not new_profile.needs_mute:
            try:
                if self._volume_before_mute is not None:
                    restored = self._volume_before_mute
                    print(f"🔊 Lautstärke wiederhergestellt: {restored}")
                    self.state.AktuelleLautstaerke_DAB = restored
                    self.dispatcher.submit(
                        lambda v=restored: self.volume_service(v),
                        key="volume_restore"
                    )
                    self._volume_before_mute = None
            except Exception as e:
                print(f"[Resources] Restore Fehler: {e}")
        
        # --- 7): FM aktivieren (DAB → FM) ──────────────────────────
        if new_profile.needs_fm_mode and not old_profile.needs_fm_mode:
            last_freq = self._load_last_fm_freq()
            def _fm_activate(freq=last_freq):
                self._switch_to_fm_mode(freq)
            try:
                self.dispatcher.submit(_fm_activate, key="mode_switch")
            except Exception as e:
                print(f"[Resources] FM-Aktivierung Fehler: {e}")
 
        # --- 8): DAB reaktivieren (FM → DAB) ───────────────────────
        elif old_profile.needs_fm_mode and not new_profile.needs_fm_mode:
            idx = getattr(self.state, "AktuelleSenderId", 0) or 0
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = 0
            if idx < 0:
                idx = 0

            # Reload-Blinker sofort starten (läuft im GUI-Thread)
            try:
                mp = self.pages.get("MainPage")
                if mp and hasattr(mp, "gui_controller"):
                    mp.gui_controller.start_reload_blink()
            except Exception as e:
                print(f"[Resources] Blink-Start Fehler: {e}")

            def _dab_restore(index=idx):
                self._switch_to_dab_mode(index)

            try:
                self.dispatcher.submit(_dab_restore, key="mode_switch")
            except Exception as e:
                print(f"[Resources] DAB-Restore Fehler: {e}")
        
        
        # --- 9) FM → DAB Automatische Rückschaltung. Wenn von FM (Page05) zu einer anderen Page gewechselt wird, automatisch den letzten DAB-Sender wieder abspielen


        # --- 10) EPG-Updates für Page07 starten/stoppen ---
        p7 = self.pages.get("Page07")
        if p7 is not None:
            if new_profile.needs_epg:
                if hasattr(p7, "start_epg_updates"):
                    try:
                        p7.start_epg_updates()
                    except Exception as e:
                        print(f"[Resources] Page07.start_epg_updates() Fehler: {e}")
            else:
                if hasattr(p7, "stop_epg_updates"):
                    # noinspection PyBroadException
                    try:
                        p7.stop_epg_updates()
                    except Exception:
                        pass

    # =================================================================================
    # Traffic Announcement (TA) – auf GPIO-Interrupt von Si4689, nur auf MainPage aktiv
    # =================================================================================
    def _start_ta_poll(self) -> None:
        """Startet den 1-s-TA-Poll (aus apply_resource_profile, Tk-Thread). Liest über _ta_evaluate(si, hw_edge=False) den ANNO-Pegel, unabhängig von GPIO23 Flanken.
           Wenn die Flanken blokiert sind erkennt der 1-Hz-Poll eine TA dennnoch"""
        self.ta.reset()
        if not self._ta_anno_enabled:
            def _enable():
                if self.si4689.enable_announcements():
                    self._ta_anno_enabled = True
            self.dispatcher.submit(_enable, key="ta_enable")
        if self._ta_poll_id is None:
            self._ta_poll_id = self.after(1000, self._ta_poll_tick)

    def _stop_ta_poll(self) -> None:
        """Stoppt den TA-Poll und beendet eine evtl. laufende TA sauber."""
        if self._ta_poll_id is not None:
            try:
                self.after_cancel(self._ta_poll_id)
            except Exception:
                pass
            self._ta_poll_id = None
        if self._ta_active:
            self._ta_back_to_home(reason="page_left")
        self.ta.reset()

    def _ta_poll_tick(self) -> None:
        """Tk-Thread: TA-Poll an Dispatcher, nächsten Tick in 1 s planen."""
        self.dispatcher.submit(self._ta_poll_run, key="ta_poll")
        self._ta_poll_id = self.after(1000, self._ta_poll_tick)

    def _ta_poll_run(self) -> None:
        """Dispatcher-Thread: TA-Auswertung als 1-Hz-Sicherheitsnetz. serial_lock."""
        if self._ta_busy:
            return
        self._ta_busy = True
        try:
            if not self.si4689.is_ready:
                return
            si = getattr(self.si4689, "_radio", None)
            if si is None:
                return
            with self.serial_lock:
                self._ta_evaluate(si, hw_edge=False)
        except Exception as e:
            print(f"[TA] Poll-Fehler: {e}")
        finally:
            self._ta_busy = False

    def _ta_read_dls(self) -> list[str]:
        """DSRV-Queue leeren und DLS-Texte sammeln. serial_lock ist gehalten."""
        si = getattr(self.si4689, "_radio", None)
        if si is None:
            return []
        texts: list[str] = []
        try:
            deadline = time.monotonic() + 0.5
            max_pkts = 20
            while max_pkts > 0:
                status = si.get_digital_service_data(status_only=True, ack=False)
                if not status.get("packet_ready"):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.008)
                    continue
                pkt = si.get_digital_service_data(status_only=False, ack=True)
                max_pkts -= 1
                more = bool(pkt.get("buffer_count", 0))
                if pkt.get("data_src") == 2:           # DLS
                    payload = pkt.get("payload", b"")
                    if len(payload) >= 3 and not (payload[0] & 0x10):
                        charset_id = (payload[1] >> 4) & 0x0F
                        raw = payload[2:]
                        nul = raw.find(b"\x00")
                        if nul >= 0:
                            raw = raw[:nul]
                        t = decode_dab_text(raw, charset_id).strip()
                        if t and t not in texts:
                            texts.append(t)
                if not more:
                    break
        except Exception:
            pass
        return texts

    _ASW_TRAFFIC = 0x0002   # ASW-Bit b1 = Road Traffic flash (TS 101 756, Tab. 14)

    def _ta_read_anno_info(self, si):
        """0xB6 EINMAL lesen (an der ANNO-Rising-Edge). serial_lock gehalten.
        Rückgabe: (traffic_active, target_sid, target_cid)."""
        try:
            si._write_command([0xB6, 0x00])
            r = si._read_reply(16)
        except Exception as e:
            print(f"[TA] 0xB6-Read Fehler: {e}")
            return (False, None, None)
        src       = r[7] & 0x03
        anno_stat = (r[7] >> 3) & 0x01
        asw       = r[8] | (r[9] << 8)
        id1       = r[10] | (r[11] << 8)
        id2       = r[12] | (r[13] << 8)
        # nur lokale (src=0) Verkehrs-Durchsage im START-Zustand
        if anno_stat == 1 and src == 0 and (asw & self._ASW_TRAFFIC):
            return (True, id1, id2)
        return (False, None, None)

    def _ta_evaluate(self, si, hw_edge: bool = False) -> None:
        """Gemeinsamer TA-Auswertepfad (serial_lock gehalten), genutzt von IRQ und Poll.
        Liest anno-Pegel; an der Rising-Edge einmal 0xB6 für Träger+Typ; tickt+führt aus."""
        try:
            evt = si.dab_get_event_status(ack=True)
            anno = bool(evt.get("anno"))
        except Exception as e:
            print(f"[TA] event_status Fehler: {e}")
            return

        rising = anno and not self._ta_anno_prev
        self._ta_anno_prev = anno
        if rising:
            traffic, tsid, tcid = self._ta_read_anno_info(si)
            self._ta_is_traffic = traffic
            self._ta_target = (tsid, tcid)

        ta_anno = anno and self._ta_is_traffic
        if hw_edge and ta_anno:
            # Hardware-Edge ist schon entprellt -> Software-Entprellung überspringen
            self.ta._anno_high_count = self.ta.rising_ticks

        action = self.ta.tick(anno=ta_anno, now=time.monotonic())
        self._ta_exec(action)

        if not anno:
            self._ta_is_traffic = False

    def _ta_target_info(self, sid):
        """(name, channel) zur Träger-SID. Bei mehrdeutiger SID (gleicher Sender
        in mehreren Ensembles) wird der Treffer im AKTUELLEN Kanal bevorzugt –
        sonst würde ein In-Ensemble-Ziel (c) fälschlich als Cross-Channel (d) gelten.
        ("", None) wenn unbekannt."""
        if sid is None:
            return ("", None)
        try:
            cfg = getattr(self, "config_data", {}) or {}
            rel = cfg.get("dab_scan_db", "assets/DB/dab_scans.sqlite")
            db  = rel if os.path.isabs(rel) else os.path.join(
                os.path.dirname(os.path.abspath(__file__)), rel)
            con = sqlite3.connect(db, timeout=5, check_same_thread=False)
            try:
                rows = con.execute(
                    "SELECT name, channel FROM si4689_datenbank "
                    "WHERE service_id = ? ORDER BY si4689_idx ASC;",
                    (sid,),
                ).fetchall()
            finally:
                con.close()
            if not rows:
                return ("", None)
            # Treffer im aktuellen Kanal bevorzugen (In-Ensemble = Fall c)
            for name, channel in rows:
                if channel == self._current_channel:
                    return (name or "", channel)
            # sonst erster Treffer (echtes Cross-Channel = Fall d)
            return (rows[0][0] or "", rows[0][1])
        except Exception as e:
            print(f"[TA] Info-Lookup Fehler: {e}")
            return ("", None)

    def _ta_target_name(self, sid: int) -> str:
        """Nur der Name (für das TA-Fenster)."""
        return self._ta_target_info(sid)[0]

    def _ta_exec(self, action) -> None:
        """Führt eine TaAction aus (Dispatcher-Thread, serial_lock gehalten).
        Die Vier-Fälle-Systematik:
        Fall (a) — Sprecher liest ohne ANNO. Keine Signalisierung, technisch unsichtbar. Abgehakt.
        Fall (b) — ANNO, ID1 == aktueller Sender. In-band, kein Umschalten, nur Fenster + Lautstärke. Bewiesen mit BERN1 (ID1=0x4F08).
        Fall (c) — ANNO, ID1 == anderer Sender im selben Ensemble. Umschalten innerhalb des Ensembles (reiner dab_start_service, kein Retune). Bewiesen mit Swiss Pop+ → SRF 1 ZH SH+ (beide im 12C/SRG-Ensemble).
        Fall (d) — ANNO, ID1 == anderer Sender in einem anderen Ensemble. Cross-Channel, braucht Retune in anderes Ensemble. Bewiesen mit BERN1 (ID1=0x4F08) → SRF 1 ZH SH+ und Ensemble 8B → 7D
        """
        kind = action.kind
        if kind == "start":
            tsid, tcid = self._ta_target
            cur = self._current_sid
            in_band = (tsid is None) or (
                cur is not None and (tsid & 0xFFFF) == (cur & 0xFFFF))

            # Ziel einmal auflösen: Name + Kanal (b/c/d-Entscheidung)
            tname, tchannel = self._ta_target_info(tsid)

            self._ta_home = (self._current_sid, self._current_cid,
                             self._current_channel, self.state.AktuelleSenderId,
                             self.state.AktuellerSender)
            self._ta_active = True
            self._ta_switched = False

            if in_band:
                # Fall (b): Ziel = aktueller Sender -> nur Fenster
                self.fallindicator = "Fall (b)"
                print(f"[TA] Fall (b) in-band – kein Umschalten (ID1 = aktueller Sender): {self.state.AktuellerSender} → {self.state.AktuellerSender}.")
            elif tchannel is None:
                # Ziel nicht in DB -> Kanal unbekannt, NICHT blind umschalten
                print(f"[TA] Ziel 0x{tsid:04X} nicht in DB – Kanal unbekannt, kein Umschalten.")
            elif tchannel != self._current_channel:
                # Fall (d): Ziel in ANDEREM Ensemble -> umschalten zum Zielsender in anderem Ensemble/Channel (z. B. 8B → 7D).
                self.fallindicator = "Fall (d)"
                print(f"[TA] Fall (d) erkannt – Cross-Channel-Ziel 0x{tsid:04X} "
                      f"auf Kanal {tchannel} (aktuell {self._current_channel}), umgeschaltet auf Träger in anderem Ensemble")
            else:
                # Fall (c): Ziel im SELBEN Ensemble -> umschalten zum Zielsender – bleibt in-band.
                try:
                    self.fallindicator = "Fall (c)"
                    if self._current_sid is not None:
                        self.si4689.dab_stop_service(self._current_sid, self._current_cid)
                    if self.si4689.dab_start_service(tsid, tcid):
                        self._current_sid = tsid
                        self._current_cid = tcid
                        self._ta_switched = True
                        suffix = f" – {tname}" if tname else ""
                        print(f"[TA] Fall (c) umgeschaltet auf Träger "
                              f"SID=0x{tsid:04X} CID=0x{tcid:X}{suffix}")
                    else:
                        print("[TA] dab_start_service(Träger) fehlgeschlagen – bleibe in-band.")
                except Exception as e:
                    print(f"[TA] Umschalten Fehler: {e}")

            try:
                ta_vol = int(self.state.TA_Lautstaerke_DAB)
                self.audio_codec.set_volume_amixer(ta_vol)
                print(f"🚦TA-Lautstärke gesetzt: → {ta_vol}")
            except Exception as e:
                print(f"[TA] set_volume_amixer Fehler: {e}")

            self.after(0, self._ta_open_window)
            print("🚦Durchsage aktiv – Fenster + TA-Lautstärke.")

        elif kind == "back":
            self._ta_back_to_home(reason=action.reason)

    def _ta_back_to_home(self, reason: str = "") -> None:
        """Zurück auf den Heimsender (nur falls umgeschaltet), Lautstärke + Fenster."""
        if self._ta_switched:
            self._ta_restore_home_service()
        else:
            self._ta_home = None      # in-band: nichts umzuschalten
        self.audio_codec.set_volume_amixer(self.state.AktuelleLautstaerke_DAB)
        print(f"🚦✓ Lautstärke wiederhergestellt: {self.state.AktuelleLautstaerke_DAB}")
        self.after(0, self._ta_close_window)
        self._ta_active = False
        self._ta_switched = False
        print(f"[TA] zurück auf Heimsender ({self.state.AktuellerSender}).")

    def _ta_restore_home_service(self) -> None:
        if not self._ta_home:
            return
        sid, cid, channel, index, name = self._ta_home
        try:
            if self._current_sid is not None:
                self.si4689.dab_stop_service(self._current_sid, self._current_cid)
            if sid is not None and self.si4689.dab_start_service(sid, cid):
                self._current_sid = sid
                self._current_cid = cid
                self._current_channel = channel
        except Exception as e:
            print(f"[TA] Heimsender-Wiederherstellung Fehler: {e}")
        finally:
            self._ta_home = None

    def _ta_open_window(self) -> None:
        """Tk-Thread: TA-Fenster über MainPage öffnen."""
        """Betreiber der CH-Ensambles"""
        sender_map = {
            "5D" : "Digris",
            "6A" : "Digris",
            "7A" : "SwissMediaCast",
            "7D" : "SwissMediaCast",
            "8A" : "Digris",
            "8B" : "SwissMediaCast",
            "8C" : "SwissMediaCast",
            "9B" : "SwissMediaCast",
            "9D" : "Digris",
            "10A": "Digris",
            "10B": "Romandie Médias",
            "10C": "DABCOM",
            "10D": "Digris",
            "11C": "SwissMediaCast",
            "12A": "SRG SSR",
            "12C": "SRG SSR",
            "12D": "SRG SSR",
        }

        try:
            if self._ta_window and self._ta_window.is_open():
                return
            tsid, _ = getattr(self, "_ta_target", (None, None))
            tname, tchannel = self._ta_target_info(tsid)
            group = sender_map.get(tchannel, "Unbekanntes Ensemble")
            if tname:
                station = f"{tname} Betreiber: ({group} - {self.fallindicator})"
            else:
                station = str(self.state.AktuellerSender)
            parent  = self.pages.get("MainPage", self)
            self._ta_window = TaWindow(
                parent,
                station=station,
                on_louder=self._ta_louder,
                on_quieter=self._ta_quieter,
                on_cancel=self._ta_cancel_for_user,
            )
        except Exception as e:
            print(f"[TA] Fenster öffnen Fehler: {e}")

    def _ta_close_window(self) -> None:
        """Tk-Thread: TA-Fenster schließen."""
        try:
            if self._ta_window:
                self._ta_window.close()
        except Exception:
            pass
        finally:
            self._ta_window = None

    def _ta_louder(self) -> None:
        """Button-Callback: TA-Lautstärke +5 (clamp 0–100), amixer setzen."""
        new_vol = min(100, int(self.state.TA_Lautstaerke_DAB) + 5)
        self.state.TA_Lautstaerke_DAB = new_vol
        try:
            self.audio_codec.set_volume_amixer(new_vol)
        except Exception as e:
            print(f"[TA] _ta_louder Fehler: {e}")
        print(f"🔊 TA-Lautstärke: +5 → {new_vol}")
        self._ta_save_volume_debounced()

    def _ta_quieter(self) -> None:
        """Button-Callback: TA-Lautstärke -5 (clamp 0–100), amixer setzen."""
        new_vol = max(0, int(self.state.TA_Lautstaerke_DAB) - 5)
        self.state.TA_Lautstaerke_DAB = new_vol
        try:
            self.audio_codec.set_volume_amixer(new_vol)
        except Exception as e:
            print(f"[TA] _ta_quieter Fehler: {e}")
        print(f"🔉 TA-Lautstärke: −5 → {new_vol}")
        self._ta_save_volume_debounced()

    def _ta_cancel_for_user(self) -> None:
        """Button-Callback (Tk-Thread): Nutzer bricht die Durchsage ab.
        Fenster + Heimlautstärke sofort; Sender-Rückschaltung (SPI) über den
        Dispatcher unter serial_lock – nicht im Tk-Thread."""
        self.after(0, self._ta_close_window)
        try:
            self.audio_codec.set_volume_amixer(self.state.AktuelleLautstaerke_DAB)
            print(f" 🚦 ✓ Lautstärke wiederhergestellt: {self.state.AktuelleLautstaerke_DAB}")
        except Exception as e:
            print(f"[TA] _ta_cancel set_volume_amixer Fehler: {e}")

        need_restore = self._ta_switched     # nur wenn wirklich umgeschaltet wurde
        self._ta_active   = False
        self._ta_switched = False
        self.ta.cancel()                     # Wiedererkennung bis ANNO-Abfall sperren

        if need_restore:
            def _restore() -> None:
                with self.serial_lock:       # RLock – Dispatcher hält ihn sonst nicht
                    self._ta_restore_home_service()
            self.dispatcher.submit(_restore, key="ta_restore")
        else:
            self._ta_home = None             # in-band: nichts umzuschalten

    def _ta_save_volume_debounced(self) -> None:
        """Schreibt TA-Lautstärke nach 2 s in dab_state.json – debounced."""
        aid = getattr(self, "_ta_save_volume_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(cast(str, aid))
            except tk.TclError:
                pass

        def do_save():
            self._ta_save_volume_after_id = None
            try:
                path = self.config_data["dab_state_file"]
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    state = {}
                ta_block = state.setdefault("traffic_announcement", {})
                ta_block["ta_volume"] = int(self.state.TA_Lautstaerke_DAB)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=4, ensure_ascii=False)
                print(f"💾 TA-Lautstärke gespeichert: {ta_block['ta_volume']}")
            except Exception as e:
                print(f"[TA] JSON-Save Fehler: {e}")

        self._ta_save_volume_after_id = self.after(2000, do_save)


    def _load_ta_volume(self) -> None:
        """Lädt die TA-Lautstärke aus dab_state.json beim App-Start.
        Default ist 70, wenn der Wert nicht in der JSON steht oder
        ungültig ist."""
        path = self.config_data["dab_state_file"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            v = state.get("traffic_announcement", {}).get("ta_volume")
            if isinstance(v, int) and 0 <= v <= 100:
                self.state.TA_Lautstaerke_DAB = v
                print(f"🚦 TA-Lautstärke aus JSON geladen: {v}")
                return
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        # Default beibehalten (in AppState als 70 definiert)
        print(f"🚦 TA-Lautstärke (Default): {self.state.TA_Lautstaerke_DAB}")
 
    def _switch_to_fm_mode(self, freq_mhz: float) -> None:
        '''
        Dispatcher-Thread: FM-Firmware laden und FM-Frequenz einstimmen.
        Dauer: ~5–8 s (Firmware-Load). Fortschritt → Page05-Statusbar.
        '''
        si = getattr(self, "si4689", None)
        if si is None:
            print("[FM-Switch] si4689 nicht vorhanden")
            return
 
        def _progress(text: str) -> None:
            '''GUI-Update aus Dispatcher-Thread heraus.'''
            try:
                page = self.pages.get("Page05")
                if page and hasattr(page, "set_status_text"):
                    self.gui_batcher.schedule_update(
                        lambda t=text: page.set_status_text(t)
                    )
            except Exception:
                pass
 
        # DAB-Status-Timer stoppen (ist noch im DAB-Modus)
        self._cancel_status_timer()
 
        with self.serial_lock:
            ok = si.switch_to_fm(freq_mhz=freq_mhz, progress_cb=_progress)
 
        if ok:
            try:
                si.amp_enable(True)
            except Exception:
                pass
            print(f"✅ FM-Modus aktiv: {freq_mhz:.1f} MHz")
            # Page05 informieren, dass FM jetzt bereit ist
            try:
                page = self.pages.get("Page05")
                if page and hasattr(page, "on_fm_ready"):
                    self.gui_batcher.schedule_update(
                        lambda f=freq_mhz: page.on_fm_ready(f)
                    )
            except Exception:
                pass
        else:
            print("❌ FM-Aktivierung fehlgeschlagen")
            _progress("❌ FM-Aktivierung fehlgeschlagen")
 
    def _switch_to_dab_mode(self, service_index: int) -> None:
        '''
        Dispatcher-Thread: DAB-Firmware laden und letzten Sender einstimmen.
        Dauer: ~5–8 s (Firmware-Load).
        '''
        si = getattr(self, "si4689", None)
        if si is None:
            return
 
        def _progress(text: str) -> None:
            pass   # Optional: MainPage-Statusbar updaten
 
        with self.serial_lock:
            ok = si.switch_to_dab(progress_cb=_progress)
 
        if ok:
            print("✅ DAB-Modus aktiv – wähle letzten Sender")
            # Nach Firmware-Reload kennt der Chip den Kanal nicht mehr --> State zurücksetzen, Sender tunen mit Index aus "AktuelleSenderId"

            self._current_channel = None
            self._current_sid     = None
            self._current_cid     = None

            try:
                self.tune_service(
                    index=service_index,
                    volume=None,
                    record_history=False,
                )
            except Exception as e:
                print(f"[DAB-Restore] tune_service Fehler: {e}")
 
    def _load_last_fm_freq(self) -> float:
        '''Letzte FM-Frequenz aus dab_state.json lesen. Fallback: 101.7'''
        import json
        json_path = str(
            self.base_path / "assets" / "jsons" / "dab_state.json"
        )
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
            freq = float(data.get("last_fm_freq", 101.7))
            if 88.0 <= freq <= 108.0:
                return freq
        except Exception:
            pass
        return 101.7

    # --- Beenden / Aufräumen ---------------------------------------------------------------
    def on_close(self):
        if getattr(self, "_is_closing", False):
            return
        self._is_closing = True

        print("=== Shutdown gestartet ===", flush=True)

        # ========== FINAL MEMORY REPORT (PSUTIL) ==========
        try:
            runtime = time.time() - self._app_start_time
            print(f"\n{'='*60}")
            print(f"📊 FINAL MEMORY REPORT (Laufzeit: {runtime/60:.1f} Min)")
            print(f"{'='*60}")
            
            # Echte Process-Memory
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            
            print(f"\n💾 Prozess-Memory:")
            print(f"  RSS (Resident): {mem_info.rss / 1024 / 1024:.2f} MB")
            print(f"  VMS (Virtual): {mem_info.vms / 1024 / 1024:.2f} MB")
            
            # Python-Objekte
            gc.collect()
            obj_count = len(gc.get_objects())
            print(f"\n🐍 Python-Objekte: {obj_count:,}")
            
            # Top Objekt-Typen
            all_objects = gc.get_objects()
            obj_types = Counter(type(obj).__name__ for obj in all_objects)
            print(f"\n📊 Top 10 Objekt-Typen:")
            for obj_type, count in obj_types.most_common(10):
                print(f"  {obj_type:20s}: {count:,}")

            print(f"{'='*60}\n")
            
        except Exception as e:
            print(f"⚠️ Memory-Report Fehler: {e}")

        def _safe(fn, label, verbose=True):
            if verbose:
                print(f"[CLOSE] {label}...", flush=True)
            try:
                fn()
                if verbose:
                    print(f"[CLOSE] {label} ✓", flush=True)
            except Exception as e:
                print(f"[CLOSE] {label} fehlgeschlagen: {e}", flush=True)

        # Dispatcher-Stats ausgeben
        if hasattr(self.dispatcher, 'get_stats'):
            stats = self.dispatcher.get_stats()
            print(f"\n📊 Dispatcher Statistiken:")
            for key, val in stats.items():
                print(f"  {key}: {val}")

        # 1) DB Connection schließen (DIREKT)
            # DatabaseManager räumt automatisch auf (kein manuelles Close nötig)
            if hasattr(self, 'music_db_manager'):
                print("[CLOSE] DatabaseManager wird automatisch aufgeräumt")
        
        # ========== DISPATCHER CLEANUP ==========
        print("[CLOSE] Cancelling pending tasks...", flush=True)
        try:
            if hasattr(self, 'dispatcher'):
                # Alle pending Tasks abbrechen
                stats = self.dispatcher.get_stats()
                print(f"  Pending Tasks: {stats['pending']}")
                print(f"  Active Tokens: {stats['active_tokens']}")
                
                self.dispatcher.cancel_all() 
                
                # Kurz warten damit Tasks abbrechen können
                time.sleep(0.1)
                
                print("  ✓ Tasks cancelled")
        except Exception as e:
            print(f"  ⚠️ Dispatcher cleanup error: {e}")

        # ========== DISPATCHER SHUTDOWN ==========
        if hasattr(self, 'dispatcher'):
            print("🛑 Dispatcher wird heruntergefahren...")
            try:
                # Stats VOR Shutdown
                stats = self.dispatcher.get_stats()
                print(f"   Stats: {stats}")
                
                # Shutdown mit kurzem Wait, self.dispatcher.shutdown(wait=True)
                self.dispatcher.shutdown(wait=False)
                time.sleep(0.3)  # Kurze Verzögerung
                
                print("✅ Dispatcher heruntergefahren")
            except Exception as e:
                print(f"⚠️ Dispatcher Shutdown-Fehler: {e}")

        # ========== DATABASE CLEANUP ==========
        if hasattr(self, 'music_db_manager') and self.music_db_manager:
            print("🧹 Music-DB Cleanup...")
            self.music_db_manager.cleanup()

        if hasattr(self, 'image_manager'):
            stats = self.image_manager.get_stats()
            print(f"🖼️ ImageManager Stats: {stats}")
            self.image_manager.cleanup_all()

        # 2a) EPG-Updates stoppen (before Audio)
        try:
            p7 = self.pages.get("Page07")
            if p7 is not None and hasattr(p7, "stop_epg_updates"):
                print("🛑 Stoppe EPG-updates...")
                p7.stop_epg_updates()
                print("✅ EPG-updates gestoppt")
        except Exception as e:
            print(f"[CLOSE] EPG-Stop Fehler: {e}")

        # 2) Si4689 schließen (Verstärker AUS, SPI/GPIO freigeben)
        _safe(lambda: getattr(self, "si4689", None) and self.si4689.close(), "Si4689", verbose=False)

        # 4a) Player zuerst stoppen (mpg123 + player_tap/player.raw schliessen)
        _safe(lambda: getattr(self, "pages", {}).get("Page02") and self.pages["Page02"].player_controller.destroy(), "Player", verbose=False)

        # 4b) DAB-Audio stoppen (arecord|aplay Pipeline)
        _safe(lambda: getattr(self, "audio_codec", None) and self.audio_codec.stop_audio_codec(), "Audio", verbose=False)

        # 5) Seiten herunterfahren
        def _shutdown_pages():
            for page in list(getattr(self, "pages", {}).values()):
                if hasattr(page, "shutdown"):
                    # noinspection PyBroadException
                    try:
                        page.shutdown()
                    except Exception:
                        pass
        _safe(_shutdown_pages, "Pages", verbose=False)

        # Equalizer/Plot und Newsmanager schließen
        _safe(lambda: getattr(self, "pages", {}).get("Page03") and self.pages["Page03"].shutdown(), "Equalizer", verbose=False)
        
        # noinspection PyBroadException
        try:
            main_page = self.pages.get("MainPage", None)
            if main_page and hasattr(main_page, "news_manager"):
                main_page.news_manager._executor.shutdown(wait=False)
        except Exception:
            pass

        # 5) Matplotlib global schließen
        try:
            __import__("matplotlib.pyplot", fromlist=["plt"]).close("all")
        except (ImportError, AttributeError):
            pass
        
        # 6) Alle after() callbacks canceln
        try:
            for after_id in self.tk.call('after', 'info'):
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        
        # 7) Tk beenden
        try:
            self.quit()
        except tk.TclError:
            pass
        time.sleep(0.1)
        try:
            self.destroy()
        except tk.TclError:
            pass
        # Notfall-Timer für garantierten Exit
        def force_exit():
            time.sleep(2.0)
            os._exit(1)
        threading.Thread(target=force_exit, daemon=True).start()
        # Normaler Exit
        os._exit(0)

    # --- Dispatcher Sender wählen ----------------------------------------
    def tune_service(self, index: int, volume: int | None = None, record_history: bool = True) -> bool:
        """
        DAB-Dienst per Index (Position in Sender_Name-Liste) abspielen.
        """
        #print(f"[Tune] aufgerufen: index={index}, _scan_data={len(self._scan_data)} Einträge")
        si = getattr(self, "si4689", None)
        if si is None or not si.is_ready:
            print("[Tune] Si4689 nicht bereit")
            return False
        if not self._scan_data or not (0 <= index < len(self._scan_data)):
            print(f"[Tune] Index {index} ausserhalb Bereich (0–{len(self._scan_data) - 1})")
            return False

        entry   = self._scan_data[index]
        sid     = entry["service_id"]
        cid     = entry["component_id"]
        name    = entry.get("label", f"Service {index}")
        channel = DAB_BAND_III[entry["freq_index"]][0]  # z.B. "12C"

        try:
            with self.serial_lock:
                # Laufenden Dienst stoppen
                if self._current_sid is not None:
                    si.dab_stop_service(self._current_sid, self._current_cid)

                # Bis zu 2 Versuche: Ein zweites DAB_TUNE_FREQ auf denselben Kanal
                # "entsticht" den Si4689 nach Firmware-Reload, wenn die Ensemble-
                # Acquisition beim ersten Versuch nicht rechtzeitig abschliesst.
                ok = False
                for _attempt in range(2):
                    if channel != self._current_channel:
                        si.dab_tune(channel)
                    ok = si.dab_start_service(sid, cid)
                    if ok:
                        break
                    # FIC-Timeout: Cache invalidieren damit dab_tune() im
                    # nächsten Schleifendurchlauf erneut gesendet wird.
                    self._current_channel = None
                    self._current_sid     = None
                    self._current_cid     = None
                    if _attempt == 0:
                        print(f"[Tune] FIC-Timeout, Re-Tune Kanal {channel} …")

                if not ok:
                    return False

                si.amp_enable(True)

                if volume is not None:
                    self.state.AktuelleLautstaerke_DAB = volume
                    self.volume_service(volume)

                self.state.AktuelleSenderId = index
                self.state.AktuellerSender  = name
                self._current_channel = channel
                self._current_sid     = sid
                self._current_cid     = cid

        except Exception as exc:
            print(f"[Tune] Fehler idx={index}: {exc}")
            return False

        self.state.new_tune=True
        self.gui_batcher.schedule_update(self._apply_tune_updates, index, name, record_history)
        return True

    def _apply_tune_updates(self, index: int, name: str, record_history: bool):
        """GUI-Updates nach Sender-Wechsel (läuft im GUI-Thread)"""
        page = self.pages["MainPage"]
        try:
            page.gui_controller.stop_reload_blink()
        except Exception:
            pass
        try:
            page.data_controller.SaveLastSender()
            page.data_controller.chart_on_tuned(name)
            if record_history:
                self.add_to_history(name)
        except Exception as e:
            print(f"[WARN] Chart/LastSender Update: {e}")
        
        page.gui_controller.sender_name_label.config(
            text=name or "—",
            foreground="#d9bfe7",
            highlightbackground="#d9bfe7"
        )
        print(f"👉 {name}")
        page.image_manager.SenderTyp_Ensemble_on_Display()
        page.image_manager.reset_cover_slideshow()
        page.image_manager.logo_cover_slideshow_state = "Logo"
        page.image_manager.update_mode_label("Logo")
        page.image_manager.Logo_on_Display()
        page.data_controller.anzahl_sender()
        self._last_dls_text = ""                          # Neuer Sender → alten Text vergessen
        self.after(500, self.start_get_progr_text_dls)    # 3s-DLS-Poll starten, 500ms warten für Service-Initialisierung
        self._start_status_poll()                         # 15s-Timer für Status+Signal
        self._update_channel_arrow_for_index(index, page) # Kanalzeiger positionieren

    def _update_channel_arrow_for_index(self, si4689_idx: int, page) -> None:
        """
        Liest den Channel des laufenden Senders aus dab_scans.sqlite
        und aktualisiert den Kanalzeiger auf der MainPage.
        Läuft im GUI-Thread (wird von _apply_tune_updates aufgerufen).
        """
        db = getattr(self, "scan_db_manager", None)
        if db is None:
            return
        try:
            row = db.execute_query(
                "SELECT channel FROM si4689_datenbank WHERE si4689_idx = ?",
                params=(si4689_idx,),
                fetch="one"
            )
            channel = row["channel"] if row else None
            if channel:
                page.gui_controller.update_channel_arrow(channel)
            else:
                print(f"[Kanalzeiger] Kein Channel für si4689_idx={si4689_idx} in DB")
        except Exception as e:
            print(f"[Kanalzeiger] DB-Fehler: {e}")


    # =========================================================================
    # DLS-TEXT NACH SENDERWAHL:
    # =========================================================================

    def start_get_progr_text_dls(self) -> None:
        """
        Startet den kontinuierlichen 3s-DLS-Poll und aktiviert den GPIO-23-Interrupt.
        Wird nach jeder Senderwahl aufgerufen.
        """
        aid = getattr(self, "_dls_poll_id", None)
        if aid is not None:
            try:
                self.after_cancel(cast(str, aid))
            except tk.TclError:
                pass
        # GPIO-23-Interrupt (DSRVIEN + DEVNTIEN + DSRVPCKTINT) aktivieren
        self._setup_gpio_int()
        # Ersten Poll nach 500 ms: Chip braucht etwas Zeit nach dab_start_service
        self._dls_poll_id = self.after(500, self._dls_poll_tick)

    def _dls_poll_tick(self) -> None:
        """Tk-Thread: DLS-Abfrage an Dispatcher, nächsten Tick in 3 s planen.

        Fallback für den Post-TA-Fall: während einer TA (_ta_active=True) liest
        weder der Interrupt-Handler noch dieser Poll. Nach TA-Ende bleibt INT
        LOW (gepufferte Pakete), es kommt keine neue Falling Edge – erst der
        nächste Poll-Tick räumt die Queue auf und setzt INT wieder HIGH.
        """
        self.dispatcher.submit(self._dls_poll_run, key="dls_poll")
        self._dls_poll_id = self.after(3000, self._dls_poll_tick)

    def _dls_poll_run(self) -> None:
        """
        Dispatcher-Thread: DSRV-Queue vollständig leeren, jeden DLS-Text
        an dls_manager weitergeben. Läuft alle 3 s kontinuierlich.

        Warum get_digital_service_data() statt get_dls_text():
          - get_dls_text() liest max. 3 Pakete und joinst sie → vermisst Texte
          - Hier: Queue-Drain bis buffer_count == 0, jedes Paket separat auswerten
          - Dadurch wird jeder DLS-Text (z.B. Songtitel UND Sendername) an analyze_dls_text() übergeben
        """
        # Während einer TA liest der TA-Poll die DLS – hier nicht drainen/analysieren
        if getattr(self, "_ta_active", False):
            return
        si = getattr(self.si4689, "_radio", None)
        page = self.pages.get("MainPage")
        if page is None or si is None or not self.si4689.is_ready:
            return

        DATA_SRC_DLS = 2
        new_texts: list[str] = []

        try:
            with self.serial_lock:
                deadline = time.monotonic() + 0.5   # max. 500 ms auf erstes Paket
                max_pkts = 20                        # Sicherheitslimit

                while max_pkts > 0:
                    status = si.get_digital_service_data(status_only=True, ack=False)
                    if not status.get("packet_ready"):
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.008)
                        continue

                    pkt = si.get_digital_service_data(status_only=False, ack=True)
                    max_pkts -= 1
                    more = bool(pkt.get("buffer_count", 0))

                    if pkt.get("data_src") == DATA_SRC_DLS:
                        payload: bytes = pkt.get("payload", b"")
                        if len(payload) >= 3 and not (payload[0] & 0x10):
                            charset_id = (payload[1] >> 4) & 0x0F
                            raw = payload[2:]
                            null_pos = raw.find(b"\x00")
                            if null_pos >= 0:
                                raw = raw[:null_pos]
                            text = decode_dab_text(raw, charset_id).strip()
                            if text and text not in new_texts:
                                new_texts.append(text)

                    if not more:
                        break

        except Exception as e:
            if "Kommandofehler" not in str(e):
                print(f"[DLS] Fehler: {e}")
            return

        # Jeden neuen Text anzeigen
        for text in new_texts:
            if text != getattr(self, "_last_dls_text", ""):
                self._last_dls_text = text
                def _show(t=text):
                    try:
                        print(f"[DLS] {t}")
                        page.dls_manager.analyze_dls_text(t)
                    except Exception as ex:
                        print(f"[DLS] GUI Fehler: {ex}")
                self.gui_batcher.schedule_update(_show)

    # =========================================================================
    # DLS-INTERRUPT: GPIO 23 (DSRVPCKTINT) – wird aus start_get_progr_text_dls() aktiviert
    # =========================================================================

    def _setup_gpio_int(self) -> None:
        """
        GPIO-23-Interrupt-Setup für die geteilte INT-Leitung (DSRVINT + DEVNTINT).

        INT_CTL_ENABLE (0x0000):
          Bit4  DSRVIEN  → DLS/MOT-Pakete  (DSRVPCKTINT) → INTB LOW
          Bit13 DEVNTIEN → DAB-Events (ANNOINT nach enable_announcements) → INTB LOW
        DIGITAL_SERVICE_INT_SOURCE (0x8100) Bit0 = DSRVPCKTINT.

        DEVNTIEN ist jetzt aktiv – DAB_EVENT_INTERRUPT_SOURCE (0xB300) bleibt
        zunächst 0x0000 (gesetzt von configure_dab_frontend). Erst enable_announcements()
        setzt dort Bit4 (ANNO_INTEN), damit ANNO-Events tatsächlich auf INTB erscheinen.
        """
        si = getattr(self.si4689, "_radio", None)
        if si is None or not self.si4689.is_ready:
            print("[GPIO-INT] Setup übersprungen – Si4689 nicht bereit.")
            return
        try:
            # DSRVIEN (Bit4) | DEVNTIEN (Bit13) – beide INT-Quellen auf INTB routen
            si.set_property(PROP_INT_CTL_ENABLE, 0x2010)
            si.set_property(PROP_DIGITAL_SERVICE_INT_SOURCE, 0x0001)
            try:
                import RPi.GPIO as GPIO
                GPIO.setup(si.int_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                pin_before = GPIO.input(si.int_pin)
                # print(f"[GPIO-INT] GPIO{si.int_pin} vor add_event_detect: "
                #      f"{'HIGH' if pin_before else 'LOW ← WARNUNG: Pin schon aktiv!'}")
                try:
                    GPIO.remove_event_detect(si.int_pin)
                except Exception:
                    pass
                GPIO.add_event_detect(
                    si.int_pin, GPIO.FALLING,
                    callback=self._on_gpio_int,
                    bouncetime=50,
                )
                # print(f"[GPIO-INT] GPIO{si.int_pin} Interrupt aktiv (DSRVIEN|DEVNTIEN, INT_CTL_ENABLE=0x2010).")
            except RuntimeError as rte:
                print(f"[GPIO-INT] GPIO-Fehler: {rte}")
        except Exception as e:
            print(f"[GPIO-INT] Setup Fehler: {e}")

    def _on_gpio_int(self, channel: int) -> None:
        """
        GPIO-23-Callback – läuft im RPi.GPIO-Thread.
        Kein SPI hier: nur Weiterleitung an den Dispatcher.
        """
        # print(f"[GPIO-INT] GPIO{channel} Falling Edge erkannt")
        try:
            self.dispatcher.submit(self._handle_gpio_int,
                                   key="gpio_int", cancellable=False)
        except Exception as e:
            print(f"[GPIO-INT] dispatcher.submit Fehler: {e}")

    def _handle_gpio_int(self) -> None:
        """
        Dispatcher-Thread: generischer ISR für die geteilte GPIO-23-Leitung.

        Liest Interrupt-Status für beide möglichen Quellen und verzweigt:
          DEVNTINT (annoint=True) → Announcement-Pfad  → _handle_anno_irq()
          DSRVINT  (pkt_ready)    → DLS/MOT-Paket-Pfad → _drain_dls_int()

        Beide Bits können gleichzeitig gesetzt sein und werden beide abgearbeitet.
        """
        si = getattr(self.si4689, "_radio", None)
        if si is None or not self.si4689.is_ready:
            return
        if getattr(self.si4689, "current_mode", None) != "dab":
            return

        with self.serial_lock:
            # --- Interrupt-Quelle triage ---
            try:
                evt = si.dab_get_event_status(ack=False)
                annoint = evt.get("annoint", False)
            except Exception as e:
                print(f"[GPIO-INT] dab_get_event_status Fehler: {e}")
                annoint = False

            try:
                svc = si.get_digital_service_data(status_only=True, ack=False)
                pkt_ready = svc.get("packet_ready", False)
            except Exception as e:
                print(f"[GPIO-INT] get_digital_service_data Fehler: {e}")
                pkt_ready = False

            # print(f"[GPIO-INT] ISR: DEVNTINT(annoint)={annoint}  "
            #      f"DSRVINT(pkt_ready)={pkt_ready}")

            # --- DEVNTINT-Pfad: DAB-Event (Announcement) ---
            if annoint:
                self._handle_anno_irq(si)

            # --- DSRVINT-Pfad: DLS/MOT-Pakete ---
            # Während einer TA (_ta_active) keine Pakete lesen: INT bleibt LOW,
            # der 3s-DLS-Poll übernimmt nach TA-Ende die Aufräumarbeit.
            if pkt_ready and not getattr(self, "_ta_active", False):
                self._drain_dls_int(si)

    def _handle_anno_irq(self, si) -> None:
        """DEVNTINT-Pfad: TA über gemeinsamen Auswertepfad treiben (HW-Edge).
        serial_lock wird vom Aufrufer gehalten."""
        if not getattr(self, "_ta_poll_id", None):
            return                       # TA-Poll inaktiv (anderer Modus) -> ignorieren
        self._ta_evaluate(si, hw_edge=True)

    def _drain_dls_int(self, si) -> None:
        """
        DSRVINT-Pfad: DSRV-Queue vollständig leeren, DLS-Texte anzeigen.

        serial_lock wird vom Aufrufer (_handle_gpio_int) gehalten.
        Liest alle gepufferten Pakete in einem buffer_count-Loop, damit INT
        nach dem Aufruf garantiert HIGH ist und die nächste Falling Edge sauber
        ausgelöst wird.
        """
        DATA_SRC_DLS = 2
        new_texts: list[str] = []

        try:
            max_pkts = 20   # Sicherheitslimit gegen hängende Queue
            while max_pkts > 0:
                status = si.get_digital_service_data(status_only=True, ack=False)
                if not status.get("packet_ready"):
                    break
                pkt = si.get_digital_service_data(status_only=False, ack=True)
                max_pkts -= 1
                more = bool(pkt.get("buffer_count", 0))

                if pkt.get("data_src") == DATA_SRC_DLS:
                    payload: bytes = pkt.get("payload", b"")
                    if len(payload) >= 3 and not (payload[0] & 0x10):
                        charset_id = (payload[1] >> 4) & 0x0F
                        raw = payload[2:].rstrip(b"\x00")
                        if raw:
                            text = decode_dab_text(raw, charset_id).strip()
                            if text and text not in new_texts:
                                new_texts.append(text)
                if not more:
                    break

        except Exception as e:
            print(f"[GPIO-INT] DLS drain Fehler: {e}")

        for text in new_texts:
            if text != getattr(self, "_last_dls_text", ""):
                self._last_dls_text = text
                print(f"[DLS-INT] {text}")
                page = self.pages.get("MainPage")
                if page is not None and hasattr(page, "dls_manager"):
                    def _show(t=text):
                        try:
                            page.dls_manager.analyze_dls_text(t)
                        except Exception as ex:
                            print(f"[DLS-INT] GUI Fehler: {ex}")
                    self.gui_batcher.schedule_update(_show)

    # =========================================================================
    # STATUS + SIGNALSTAERKE: 15s-Timer
    # =========================================================================

    def _start_status_poll(self):
        """
        Startet den 15s-Status-Timer.
        Wird von _apply_tune_updates() aufgerufen.
        """
        self._cancel_status_timer()
        self._status_poll_tick()

    def _status_poll_tick(self):
        """15s-Tick: Status+Signal abrufen."""
        def _work():
            si = getattr(self, "si4689", None)
            if si is None or si.current_mode != "dab":
                return
            self._fetch_status_only()
        self.dispatcher.submit(_work, key="status_poll")
        self._status_poll_id = self.after(15000, self._status_poll_tick)

    def _fetch_status_only(self):
        """
        Dispatcher-Thread: Nur play_state für Ampel + signal_strength für Display-Text und Progressbar.
        """
        page = self.pages.get("MainPage")
        if page is None:
            return

        # Guard: Radio nicht bereit (z.B. während Autoscan) → sofort abbrechen.
        # Direktzugriff auf si4689._radio würde den is_ready-Check umgehen und
        # SPI-Kollisionen mit dem Scan-Thread verursachen.
        if not self.si4689.is_ready:
            return

        # --- Signalstärke des laufenden DAB-Senders abfragen --------------
        #   get_dab_signal_strength() sendet CMD 0xB2 (DAB_DIGRAD_STATUS)
        #   ohne Interrupt-Quittierung und liefert RSSI, SNR, FIC-Qualität usw.
        #   Im Fehlerfall wird strength = -128 gesetzt (sicherer Fallback).
        #
        strength: int = -128          # Fallback: kein/schlechtes Signal
        acq: bool     = False         # Fallback: kein ACQ (war früher undefiniert
                                      # wenn _si_radio None oder Exception → UnboundLocalError)
        try:
            _si_radio = getattr(self.si4689, "_radio", None)
            if _si_radio is not None:
                _sig = _si_radio.get_dab_signal_strength()
                strength = _sig.get("rssi", -128)
                acq = _sig.get("acq", False)   # explizit False als Default
                # Optional: weitere Werte für spätere Verwendung merken
                # snr         = _sig.get("snr", 0)
                # fic_quality = _sig.get("fic_quality", 0)
                # cnr         = _sig.get("cnr", 0)
                # sig_valid   = _sig.get("valid", False)
                
        except Exception as _sig_err:
            print(f"[Signal] Signalstärke-Abfrage Fehler: {_sig_err}")

        # --- Audio-Modus (Stereo / Mono / Joint Stereo / Dual) abfragen ---
        # Nur abfragen wenn ein Service aktiv ist (_current_sid gesetzt)
        modus: str = ""
        if getattr(self, "_current_sid", None) is not None:
            try:
                _si_radio = getattr(self.si4689, "_radio", None)
                if _si_radio is not None:
                    _audio = _si_radio.get_dab_audio_info()
                    modus  = _audio.get("mode_str", "")
            except Exception as _modus_err:
                print(f"[Audio] Modus-Abfrage Fehler: {_modus_err}")
        
        """want_cover löst ein Thread-Sicherheitsproblem Dispatcher-Thread vs. GUI-Thread"""
        want_cover = (
            page.image_manager.logo_cover_slideshow_state == "Cover"
            and getattr(self.state, "new_md", None) != getattr(self.state, "last_md", None)
        )

        def _ui(sig=strength, aq=acq, wc=want_cover, mo=modus):
            try:
                if sig != getattr(self, "_last_strength", None):
                    self._last_strength = sig
                    page.gui_controller.progress_bar_color(sig)
                    page.gui_controller.Signal_staerke.config(
                        text=f"Signalstärke: {sig}dBuV"
                    )
                if aq:
                    page.gui_controller.sender_name_label.config(foreground="#65009b", highlightbackground="#65009b")

                # Stereo/Mono-Label aktualisieren
                page.gui_controller.stereo_label.config(text=mo or "—")

                if wc:
                    self.state.last_md = self.state.new_md
                    try:
                        page.image_manager.Cover_on_Display_async()
                    except AttributeError:
                        self.after_idle(page.image_manager.Cover_on_Display)
            except Exception as ex:
                print(f"[Status-Poll] GUI Fehler: {ex}")
        self.gui_batcher.schedule_update(_ui)
        
    def _cancel_status_timer(self):
        """Stoppt den 15s-Status-Timer."""
        aid = getattr(self, "_status_poll_id", None)
        if aid is not None:
            try:
                self.after_cancel(cast(str, aid))
            except tk.TclError:
                pass
        self._status_poll_id = None

    def store_in_SQL(self, md: dict):
        self.dispatcher.submit(lambda: self.save_music_data_handler(md))

    def save_music_data_handler(self, md: dict):
        """SCHNELLE Version - persistente Connection"""
        try:
            if not isinstance(md, dict):
                return
            def norm(x: object) -> str:
                return str(x).strip() if x is not None else ""
            # Daten extrahieren
            sender = norm(md.get("sender"))
            artist = norm(md.get("artist"))
            title = norm(md.get("title"))
            if not (sender and artist and title):
                return

            # ========== MIT DATABASEMANAGER ==========
            ts_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                with self.music_db_manager.get_cursor() as (conn, cursor):
                    try:
                        cursor.execute("""
                            INSERT INTO music_log
                            (ts_utc, ts_local, sender, artist, title, genre, song, raw, source,
                            confidence, track_key, track_id, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            norm(md.get("ts_utc")),
                            ts_local,
                            sender,
                            artist,
                            title,
                            norm(md.get("genre")),
                            title,
                            norm(md.get("raw")),
                            norm(md.get("source") or "RDS"),
                            float(md.get("confidence") or 0.0),
                            norm(md.get("track_key")),
                            norm(md.get("track_id")),
                            ts_local,
                        ))
                    except sqlite3.OperationalError as e:
                        if "no such table" in str(e).lower():
                            # Schema sicherstellen
                            self._ensure_music_schema(conn)
                            # Nochmal versuchen
                            cursor.execute("""
                                INSERT INTO music_log
                                (ts_utc, ts_local, sender, artist, title, genre, song, raw, source,
                                confidence, track_key, track_id, timestamp)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                norm(md.get("ts_utc")),
                                ts_local,
                                sender,
                                artist,
                                title,
                                norm(md.get("genre")),
                                title,
                                norm(md.get("raw")),
                                norm(md.get("source") or "RDS"),
                                float(md.get("confidence") or 0.0),
                                norm(md.get("track_key")),
                                norm(md.get("track_id")),
                                ts_local,
                            ))
                        else:
                            raise
                    # Commit
                    conn.commit()
                # State aktualisieren
                self.state.new_md = (sender, artist, title)
                
            except Exception as e:
                print(f"❌ Fehler beim Speichern in Music-Log: {e}")

        except Exception as e:
            print(f"❌ Fehler: {e}")

    # --- Lautstärke per ALSA + Si4689 Analog-Volume --------------------------
    def volume_service(self, volume: int | None = None) -> None:
        """Lautstärke setzen (0–100 von der GUI) via ALSA 'Digital' (HifiBerry DAC PCM5122)."""
        if volume is None:
            return
        volume = max(0, min(100, int(volume)))
        self.audio_codec.set_volume_amixer(volume)

    # ---- History-Berechnung --------------------------------------------------------------
    def add_to_history(self, name: str):
        if self.state.sender_history and self.state.sender_history[-1] == name:
            self.state.current_index = len(self.state.sender_history) - 1
            return
        if self.state.current_index < len(self.state.sender_history) - 1:
            self.state.sender_history = self.state.sender_history[:self.state.current_index + 1]
        self.state.sender_history.append(name)
        self.state.current_index = len(self.state.sender_history) - 1

    def create_layout_styling(self):
        self.style.configure('Frame1.TFrame', background="#237E71")
        self.style.configure('Frame2.TFrame', background='#237E71')
        self.style.configure('Frame3.TFrame', background='#dc93f5')
        self.style.configure("Vertical.TScale", background="#65009b", troughcolor='#dc93f5', sliderthickness=15, sliderrelief='raised')
        self.style.configure("Horizontal.TScale", background="#65009b", troughcolor='#dc93f5', sliderthickness=15, sliderrelief='raised')
        self.style.configure("my.TButton", font=('Calibri', 10), padding=(5, 5), width=10)
        self.style.configure("my.TRadiobutton", font=('Calibri', 11), foreground="#65009b", background='#237E71')
        self.style.configure("my_01.TRadiobutton", font=('Calibri', 11), foreground="#dea2ff", background='#237E71')
        self.style.configure('text.Horizontal.TProgressbar', text='0 %', anchor='center', foreground='red', background='yellow', thickness=3)
        self.style.configure('Kanal1v.TFrame', background="green")
        self.style.configure("Hover.TButton", background="#d0f0ff")
        self.style.configure("MyImage.TLabel", background="#ADD8E6", borderwidth=0, relief="flat", padding=0)


class GUIController:
    def __init__(self, app):
        self.app = app
        self.menu_visible = False
        self.buttons = []
        self.btn = None
        self.load_images()

    def load_images(self):
        self.menue_pages = tk.PhotoImage(file=self.app.config_data["menue_pages"])

    def init_overlay(self, parent):
        self.menu_icon_label = tk.Label(parent, image=self.menue_pages, bg="#237E71", cursor="hand2")
        self.menu_icon_label.grid(row=0, column=1, padx=(0, 5), pady=5, sticky=tk.NE)
        self.menu_icon_label.bind("<Button-1>", self.toggle_menu)

        self.button_frame = tk.Frame(parent, bg="#eeeeee")
        self.button_frame.grid(row=0, column=0, padx=(0, 0), pady=5, sticky=tk.NE)
        self.button_frame.grid_remove()

    def create_buttons(self, aktuelle_seite):
        for widget in self.button_frame.winfo_children():
            widget.destroy()
        self.buttons.clear()

        button_names = [
            ("Statistik", "Page01"),
            ("Player",    "Page02"),
            ("Recorder",  "Page03"),
            ("Scanner",   "Page04"),
            ("FM",        "Page05"),
            ("Karte",     "Page06"),
            ("Guide",     "Page07"),
            ("Verkehr",   "Page08"),
            ("Home",      "MainPage"),
        ]
        col_idx = 0
        for name, target in button_names:
            if target != aktuelle_seite:
                btn = self.create_styled_button(name, target)
                btn.grid(row=0, column=col_idx, padx=2)
                self.buttons.append(btn)
                col_idx += 1

    def toggle_menu(self, event=None):
        # Menü-Beep asynchron abspielen – explizit über hifiberry_play_plug
        # (softvol → equalizer → dmix), kein Device-Konflikt mit Hauptpipeline
        # beep_path = "/home/weilmy/My_DAB_Si4689/assets/sounds/menue_beep.wav"
        # try:
        #     subprocess.Popen(
        #         ["aplay", "-q", "-D", "hifiberry_play_plug", beep_path],
        #         stdout=subprocess.DEVNULL,
        #         stderr=subprocess.DEVNULL
        #     )
        # except FileNotFoundError:
        #     pass  # aplay nicht verfügbar – lautlos ignorieren

        self.menu_visible = not self.menu_visible
        if self.menu_visible:
            self.button_frame.grid()
        else:
            self.button_frame.grid_remove()

    def switch_page(self, page_name):
        self.app.show_page(page_name)
        self.aktuelle_seite = page_name
        self._set_menu_color_by_page(page_name)
        self.create_buttons(page_name)
        self.toggle_menu()

    def _set_menu_color_by_page(self, seite):
        seiten_farben = {
            "MainPage": '#237E71',
            "Page01": "#828EE7",
            "Page02": "#90EE90",
            "Page03": "#dc93f5",
            "Page04": "#3D5B0C",
            "Page05": "#153e81",
            "Page06": "#E7EF76",
            "Page07": "#050A65",
            "Page08": "#3288FF",
        }
        farbe = seiten_farben.get(seite)
        if farbe:
            for widget in (self.app.overlay_frame, self.menu_icon_label, self.button_frame):
                widget.configure(bg=farbe)
            for btn in getattr(self, "buttons", []):
                if hasattr(btn, "set_base_bg"):
                    btn.set_base_bg(farbe)

    def create_styled_button(self, text, target):
        page_bg = self.app.overlay_frame.cget("bg")
        chip = ChipButton(
            self.button_frame,
            text=text,
            command=lambda: self.switch_page(target),
            base_bg=page_bg
        )
        return chip


if __name__ == "__main__":
    app = None
    try:
        app = App()
        app.mainloop()
    except KeyboardInterrupt:
        logging.info("Programm durch Benutzer abgebrochen (Ctrl+C)")
    except Exception as e:
        logging.critical("Unbehandelter Fehler: %s", e, exc_info=True)
    finally:
        if app is not None:
            try:
                app.on_close()
            except Exception as close_error:
                logging.error("Fehler beim Schliessen der App: %s", close_error, exc_info=True)
        logging.info("Programm beendet")