#!/usr/bin/env python3
# ('my_venv_314':venv)
# -*- coding: utf-8 -*-

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

import tkinter as tk
import platform
import re
import sqlite3
import subprocess
import os
from datetime import datetime

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False

try:
    import smbus2 as _smbus2
    SMBUS2_OK = True
except ImportError:
    SMBUS2_OK = False

from .base_page import BasePage

try:
    from hardware.si4689_driver import DAB_BAND_III as _DAB_BAND_III
except ImportError:
    _DAB_BAND_III = []


# ═══════════════════════════════════════════════════════════════════════════════
#  FARBSCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════
THEMES = {
    "hardware": {
        "bg_panel":    "#1A2F1A",   # tiefes Dunkelgrün
        "fg_title":    "#7ED87E",   # Hellgrün
        "fg_key":      "#B0C8B0",
        "fg_val":      "#E8F5E8",
        "btn_active":  ("#1A2F1A", "#7ED87E"),   # (bg, fg)
        "btn_inactive":("#2E4A2E", "#5A8A5A"),
    },
    "soundkarte": {
        "bg_panel":    "#111E30",   # tiefes Dunkelblau
        "fg_title":    "#6DB3F2",   # Himmelblau
        "fg_key":      "#9AB4C8",
        "fg_val":      "#E0EFFF",
        "btn_active":  ("#111E30", "#6DB3F2"),
        "btn_inactive":("#1C2E44", "#3A6A9A"),
    },
    "dab": {
        "bg_panel":    "#2A1A0E",   # tiefes Dunkelorange/Braun
        "fg_title":    "#FFB347",   # Orange
        "fg_key":      "#C8A882",
        "fg_val":      "#FFF0DC",
        "btn_active":  ("#2A1A0E", "#FFB347"),
        "btn_inactive":("#3E2A14", "#9A6A28"),
    },
}

BG_PAGE    = "#E6E6E2"
BG_BTNROW  = "#1A1A1A"
FONT_BTN   = ("Helvetica", 11, "bold")
FONT_TIT   = ("Courier", 10, "bold")
FONT_TXT   = ("Courier", 10)
REFRESH_MS = 5000


# ═══════════════════════════════════════════════════════════════════════════════
#  HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════════════

def _read_file(path, default="?"):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default

def _run(cmd, default="?"):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL,
                                       timeout=2).decode().strip()
    except Exception:
        return default


def get_os_info():
    ver = platform.version().split("#")[0].strip()
    return {
        "OS":       f"{platform.system()} {ver}",
        "Distro":   _run(["lsb_release", "-ds"]),
        "Kernel":   platform.release(),
        "Hostname": platform.node(),
    }

def get_pi_model():
    return _read_file("/proc/device-tree/model").replace("\x00", "").strip() or "?"

_ARM_PARTS = {
    "0xd0b": "Cortex-A76",
    "0xd0a": "Cortex-A75",
    "0xd09": "Cortex-A73",
    "0xd08": "Cortex-A72",
    "0xd07": "Cortex-A57",
    "0xd03": "Cortex-A53",
}
_ARM_IMPLEMENTERS = {
    "0x41": "ARM",
    "0x51": "Qualcomm",
    "0x53": "Samsung",
}

def get_cpu_info():
    model = "?"
    try:
        impl = part = hw = model_name = None
        for line in open("/proc/cpuinfo"):
            if line.startswith("Model name"):
                model_name = line.split(":", 1)[1].strip()
            elif line.startswith("Hardware"):
                hw = line.split(":", 1)[1].strip()
            elif line.startswith("CPU implementer"):
                impl = line.split(":", 1)[1].strip()
            elif line.startswith("CPU part"):
                part = line.split(":", 1)[1].strip()
        if model_name:
            model = model_name
        elif part:
            core = _ARM_PARTS.get(part, f"part {part}")
            mfr  = _ARM_IMPLEMENTERS.get(impl, "ARM") if impl else "ARM"
            soc  = f"  ({hw})" if hw else ""
            model = f"{mfr} {core}{soc}"
        elif hw:
            model = hw
    except Exception:
        pass
    cores    = os.cpu_count() or "?"
    freq     = "?"
    if PSUTIL_OK:
        f = psutil.cpu_freq()
        if f:
            freq = f"{f.current:.0f} MHz"
    load_pct = psutil.cpu_percent(interval=0.1) if PSUTIL_OK else "?"
    return {
        "Modell": model,
        "Kerne":  str(cores),
        "Takt":   freq,
        "Last":   f"{load_pct} %" if isinstance(load_pct, float) else "?",
    }

def get_ram_info():
    if not PSUTIL_OK:
        return {"Gesamt": "psutil fehlt", "Benutzt": "–", "Verfügbar": "–"}
    m = psutil.virtual_memory()
    return {
        "Gesamt":    f"{m.total / 1024**3:.1f} GB",
        "Benutzt":   f"{m.used  / 1024**3:.1f} GB  ({m.percent:.0f} %)",
        "Verfügbar": f"{m.available / 1024**3:.1f} GB",
    }

def get_disk_info():
    if not PSUTIL_OK:
        return {"Gesamt": "psutil fehlt", "Benutzt": "–", "Frei": "–"}
    d = psutil.disk_usage("/")
    return {
        "Gesamt":  f"{d.total / 1024**3:.1f} GB",
        "Benutzt": f"{d.used  / 1024**3:.1f} GB  ({d.percent:.0f} %)",
        "Frei":    f"{d.free  / 1024**3:.1f} GB",
    }

def get_temperature():
    temp = None
    raw  = _run(["vcgencmd", "measure_temp"])
    if "temp=" in raw:
        try:
            temp = float(raw.split("=")[1].replace("'C", ""))
        except Exception:
            pass
    if temp is None:
        raw = _read_file("/sys/class/thermal/thermal_zone0/temp")
        if raw.replace("-", "").isdigit():
            temp = int(raw) / 1000.0
    if temp is None and PSUTIL_OK:
        for key in ("cpu_thermal", "coretemp"):
            t = psutil.sensors_temperatures().get(key)
            if t:
                temp = t[0].current
                break
    if temp is None:
        return "?"
    warn = "  ⚠ WARM" if temp >= 70 else ""
    return f"{temp:.1f} °C{warn}"

def get_load():
    try:
        l1, l5, l15 = os.getloadavg()
        return f"{l1:.2f}  /  {l5:.2f}  /  {l15:.2f}"
    except Exception:
        return "?"

def get_uptime():
    raw = _read_file("/proc/uptime").split()[0]
    try:
        secs = int(float(raw))
        d, r = divmod(secs, 86400)
        h, r = divmod(r, 3600)
        m    = r // 60
        return f"{d} Tage  {h:02d}h {m:02d}m"
    except Exception:
        return "?"

def get_throttle_status() -> str:
    raw = _run(["vcgencmd", "get_throttled"], "")
    if not raw or "=" not in raw:
        return "?"
    try:
        val = int(raw.split("=")[1], 16)
    except Exception:
        return raw
    if val == 0:
        return "OK  (kein Throttling)"
    current = []
    history = []
    if val & (1 << 0):  current.append("Unterspannung")
    if val & (1 << 1):  current.append("Takt gedrosselt")
    if val & (1 << 2):  current.append("Throttled")
    if val & (1 << 3):  current.append("Temp-Limit")
    if val & (1 << 16): history.append("Unterspannung")
    if val & (1 << 17): history.append("Takt-Cap")
    if val & (1 << 18): history.append("Throttled")
    if val & (1 << 19): history.append("Temp-Limit")
    parts = []
    if current:
        parts.append("AKTIV: " + ", ".join(current))
    if history:
        parts.append("(seit Boot: " + ", ".join(history) + ")")
    return "  ".join(parts) if parts else f"0x{val:05X}"

def get_volts_core() -> str:
    raw = _run(["vcgencmd", "measure_volts", "core"], "")
    if "volt=" in raw:
        return raw.split("=")[1].strip()
    return "?"

def get_arm_clock() -> str:
    raw = _run(["vcgencmd", "measure_clock", "arm"], "")
    # Ausgabe: "frequency(48)=1800000000"
    if "=" in raw:
        try:
            hz = int(raw.split("=")[1])
            return f"{hz / 1_000_000:.0f} MHz"
        except Exception:
            pass
    return "?"

def get_gpu_memory() -> str:
    gpu = _run(["vcgencmd", "get_mem", "gpu"], "")
    arm = _run(["vcgencmd", "get_mem", "arm"], "")
    gpu_val = gpu.split("=")[1].strip() if "=" in gpu else "?"
    arm_val = arm.split("=")[1].strip() if "=" in arm else "?"
    return f"ARM {arm_val}  /  GPU {gpu_val}"


# ── Soundkarten-Hilfsfunktionen ───────────────────────────────────────────────

def _sc_find_card() -> str:
    """Findet HifiBerry Kartennummer aus aplay -l. Fallback: '0'."""
    try:
        out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL,
                                      timeout=2).decode()
        for line in out.splitlines():
            if any(t in line.lower() for t in
                   ("hifiberry", "dacplusadc", "sndrpihifiberry")):
                m = re.search(r'card\s+(\d+)', line, re.IGNORECASE)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "0"

def _sc_alsa_card_name(card_num: str) -> str:
    """Liest ALSA-Karten-ID aus /proc/asound."""
    raw = _read_file(f"/proc/asound/card{card_num}/id", "?")
    # Langname aus aplay -l
    try:
        out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.DEVNULL,
                                      timeout=2).decode()
        for line in out.splitlines():
            m = re.search(rf'card\s+{card_num}[^[]*\[([^\]]+)\]', line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return raw

def _sc_stream_params(card_num: str) -> dict:
    """Liest live hw_params für Playback (pcm0p) oder Capture (pcm0c)."""
    result = {"rate": "–  (kein Stream)", "format": "–", "active": False}
    for pcm in (f"/proc/asound/card{card_num}/pcm0p/sub0/hw_params",
                f"/proc/asound/card{card_num}/pcm0c/sub0/hw_params"):
        raw = _read_file(pcm, "")
        if raw and "closed" not in raw and "NONE" not in raw.upper():
            result["active"] = True
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("rate:"):
                    hz = line.split(":", 1)[1].strip().split()[0]
                    result["rate"] = f"{hz} Hz  ▶"
                elif line.startswith("format:"):
                    result["format"] = line.split(":", 1)[1].strip()
                elif line.startswith("channels:"):
                    ch = line.split(":", 1)[1].strip()
                    result["format"] += f"  {ch}ch"
            break
    return result

def _sc_amixer_pct(card_num: str, control: str) -> str:
    """Liest Prozentwert eines ALSA-Mixer-Controls."""
    out = _run(["amixer", "-c", card_num, "sget", control], "")
    for line in out.splitlines():
        if "[" in line and "%" in line:
            idx = line.find("[")
            end = line.find("%]", idx)
            if idx >= 0 and end > idx:
                return line[idx + 1:end] + " %"
    return "–"

def _sc_pcm5122_status() -> dict:
    """
    Liest PCM5122 Status-Register via I2C (force=True wegen UU-Kernel-Lock).
    Reg 0x0D Bits[3:2]: Takt-Quelle (00=SCK/externer MCLK, 01=BCK/PLL, ...)
    Reg 0x5F Bits[3:2]: Auto-Mute Left/Right
    HifiBerry Pro hat Onboard-Oszillator → SCK-Modus, kein interner PLL.
    """
    if not SMBUS2_OK:
        return {"clk": "smbus2 fehlt", "amute": "smbus2 fehlt"}
    try:
        bus = _smbus2.SMBus(1, force=True)
        r0d = bus.read_byte_data(0x4D, 0x0D)   # Takt-Quelle
        r5f = bus.read_byte_data(0x4D, 0x5F)   # Auto-Mute
        bus.close()
    except OSError:
        return {"clk": "I2C Fehler", "amute": "I2C Fehler"}

    clk_src = (r0d >> 2) & 0x03
    clk_map = {
        0: "SCK ✓  (externer Oszillator)",
        1: "PLL  (BCK)",
        2: "PLL  (BCK/4)",
        3: "PLL  (GPIO)",
    }
    clk = clk_map.get(clk_src, f"Modus {clk_src}")

    am_l = bool(r5f & 0x08)
    am_r = bool(r5f & 0x04)
    if am_l or am_r:
        ch    = "/".join(c for c, a in (("L", am_l), ("R", am_r)) if a)
        amute = f"aktiv ({ch}) ◼"
    else:
        amute = "aus ▶"
    return {"clk": clk, "amute": amute}

def _sc_fmt_uptime(seconds) -> str:
    """Formatiert Sekunden als h mm ss."""
    if seconds is None:
        return "–"
    s = int(seconds)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


class Page09(BasePage):
    """System-Informationen: Hardware / Soundkarte / DAB-Modul"""

    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller  = controller
        self.app         = controller
        self._ui_built   = False
        self._refresh_id = None
        self._active     = False
        self._current_tab       = "hardware"
        self._dab_fetch_running = False
        self._dab_part_cached   = None   # get_part_info() Ergebnis – ändert sich nie
        self.configure(bg=BG_PAGE)

    # ── BasePage Lifecycle ────────────────────────────────────────────────────

    def on_first_activate(self):
        if not self._ui_built:
            self.build_gui()
            self._ui_built = True
        self._active = True
        self._start_refresh()

    def on_reactivate(self):
        self._active = True
        self._start_refresh()

    def on_deactivate(self):
        self._active = False
        self._stop_refresh()

    # ── GUI-Aufbau ────────────────────────────────────────────────────────────

    def build_gui(self):
        self.configure(bg=BG_PAGE)
        self.grid_rowconfigure(0, minsize=30, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, minsize=22, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # ── Seitentitel ──────────────────────────────────────────────────────
        hdr = tk.Frame(self, height=30, bg=BG_PAGE)
        hdr.grid(row=0, column=0, sticky="nsew")
        hdr.grid_propagate(False)
        tk.Label(hdr, text="System-Informationen",
                 bg=BG_PAGE, fg="#222222",
                 font=("Helvetica", 14, "bold")).pack(fill="both", expand=True)

        # ── text_frame ───────────────────────────────────────────────────────
        self.text_frame = tk.Frame(self, bg=BG_BTNROW)
        self.text_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        self.text_frame.grid_propagate(False)
        self.text_frame.grid_rowconfigure(0, minsize=36, weight=0)  # Button-Reihe
        self.text_frame.grid_rowconfigure(1, weight=1)               # Inhalt
        self.text_frame.grid_columnconfigure(0, weight=1)

        # ── Button-Reihe ─────────────────────────────────────────────────────
        self._btn_row = tk.Frame(self.text_frame, bg=BG_BTNROW, height=36)
        self._btn_row.grid(row=0, column=0, sticky="nsew")
        self._btn_row.grid_propagate(False)

        self._tab_buttons = {}
        tabs = [
            ("hardware",   "  🖥  Hardware  "),
            ("soundkarte", "  🔊  Soundkarte  "),
            ("dab",        "  📻  DAB-Modul  "),
        ]
        for key, label in tabs:
            btn = tk.Button(
                self._btn_row,
                text=label,
                font=FONT_BTN,
                relief="flat",
                bd=0,
                padx=12,
                cursor="hand2",
                command=lambda k=key: self._switch_tab(k),
            )
            btn.pack(side="left", fill="y", padx=(3, 0), pady=4)
            self._tab_buttons[key] = btn

        tk.Button(
            self._btn_row,
            text="  🏠  Home  ",
            font=FONT_BTN,
            relief="flat",
            bd=0,
            padx=12,
            cursor="hand2",
            bg="#3A3A3A", fg="#DDDDDD",
            activebackground="#555555", activeforeground="#FFFFFF",
            command=lambda: self.controller.show_page("MainPage"),
        ).pack(side="right", fill="y", padx=(0, 6), pady=4)

        # ── Inhalts-Container ─────────────────────────────────────────────────
        self._content_frame = tk.Frame(self.text_frame, bg=BG_BTNROW)
        self._content_frame.grid(row=1, column=0, sticky="nsew")
        self._content_frame.grid_propagate(False)
        self._content_frame.grid_rowconfigure(0, weight=1)
        self._content_frame.grid_columnconfigure(0, weight=1)

        # StringVar-Dict für alle Panels
        self._vars: dict = {}

        # Panels aufbauen
        self._panels = {}
        self._build_panel_hardware()
        self._build_panel_soundkarte()
        self._build_panel_dab()

        # ── Statuszeile ───────────────────────────────────────────────────────
        sf = tk.Frame(self, height=22, bg=BG_PAGE)
        sf.grid(row=2, column=0, sticky="nsew")
        sf.grid_propagate(False)
        self.status_var = tk.StringVar(value="Bereit")
        tk.Label(sf, textvariable=self.status_var,
                 bg=BG_PAGE, fg="#333333",
                 font=("Arial", 9), anchor="w", padx=8).pack(fill="both", expand=True)

        # Ersten Tab anzeigen
        self._switch_tab("hardware")

    # ── Tab-Switching ─────────────────────────────────────────────────────────

    def _switch_tab(self, key: str):
        self._current_tab = key
        th = THEMES[key]

        # Buttons einfärben
        for k, btn in self._tab_buttons.items():
            if k == key:
                bg, fg = THEMES[k]["btn_active"]
            else:
                bg, fg = THEMES[k]["btn_inactive"]
            btn.configure(bg=bg, fg=fg,
                          activebackground=bg, activeforeground=fg)

        # Hintergründe anpassen
        self._btn_row.configure(bg=th["bg_panel"])
        self._content_frame.configure(bg=th["bg_panel"])
        self.text_frame.configure(bg=th["bg_panel"])

        # Panels umschalten
        for k, panel in self._panels.items():
            if k == key:
                panel.grid(row=0, column=0, sticky="nsew")
            else:
                panel.grid_remove()

        # Sofort Daten laden
        self._do_refresh()

    # ── Panel: Hardware ───────────────────────────────────────────────────────

    def _build_panel_hardware(self):
        th  = THEMES["hardware"]
        frm = tk.Frame(self._content_frame, bg=th["bg_panel"])
        frm.grid_columnconfigure(0, weight=1)
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(0, weight=1)
        self._panels["hardware"] = frm

        left  = tk.Frame(frm, bg=th["bg_panel"])
        right = tk.Frame(frm, bg=th["bg_panel"])
        left.grid (row=0, column=0, sticky="nsew", padx=(10, 4), pady=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=6)

        self._fill_col(left, th, [
            ("System", [
                ("hw_os",       "OS"),
                ("hw_distro",   "Distribution"),
                ("hw_kernel",   "Kernel"),
                ("hw_hostname", "Hostname"),
                ("hw_model",    "Pi-Modell"),
            ]),
            ("Systemlast  (1/5/15 min)", [
                ("hw_load",   "Load Avg"),
                ("hw_uptime", "Uptime"),
            ]),
            ("Raspberry Pi", [
                ("hw_throttle", "Throttle"),
                ("hw_volts",    "Spannung Core"),
                ("hw_armclk",   "ARM-Takt live"),
                ("hw_gpu_mem",  "GPU-Speicher"),
            ]),
        ])
        self._fill_col(right, th, [
            ("Prozessor", [
                ("hw_cpu_mod", "CPU"),
                ("hw_cores",   "Kerne"),
                ("hw_freq",    "Takt"),
                ("hw_cpu_pct", "Auslastung"),
                ("hw_temp",    "Temperatur"),
            ]),
            ("Arbeitsspeicher", [
                ("hw_ram_tot", "Gesamt"),
                ("hw_ram_use", "Benutzt"),
                ("hw_ram_fre", "Verfügbar"),
            ]),
            ("Festplatte  /", [
                ("hw_dsk_tot", "Gesamt"),
                ("hw_dsk_use", "Benutzt"),
                ("hw_dsk_fre", "Frei"),
            ]),
        ])

    # ── Panel: Soundkarte ─────────────────────────────────────────────────────

    def _build_panel_soundkarte(self):
        th  = THEMES["soundkarte"]
        frm = tk.Frame(self._content_frame, bg=th["bg_panel"])
        frm.grid_columnconfigure(0, weight=1)
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(0, weight=1)
        self._panels["soundkarte"] = frm

        left  = tk.Frame(frm, bg=th["bg_panel"])
        right = tk.Frame(frm, bg=th["bg_panel"])
        left.grid (row=0, column=0, sticky="nsew", padx=(10, 4), pady=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=6)

        self._fill_col(left, th, [
            ("Pipeline  (arecord → aplay)", [
                ("sc_pipe_status",   "Status"),
                ("sc_pipe_uptime",   "Laufzeit"),
                ("sc_pipe_restarts", "Neustarts"),
                ("sc_pipe_health",   "Health-Check"),
            ]),
            ("PCM1863 – ADC / Capture", [
                ("sc_adc_dout",  "DOUT / GPIO20"),
                ("sc_adc_dev",   "Capture-Device"),
            ]),
        ])
        self._fill_col(right, th, [
            ("PCM5122 – DAC / Playback", [
                ("sc_dac_card",   "ALSA-Karte"),
                ("sc_dac_dev",    "Playback-Device"),
                ("sc_dac_stream", "Stream"),
                ("sc_pll",        "Takt-Modus"),
                ("sc_amute",      "Auto-Mute"),
            ]),
            ("Audio-Format  (live)", [
                ("sc_fmt_fmt",  "Format"),
                ("sc_fmt_rate", "Samplerate"),
            ]),
            ("Lautstärke", [
                ("sc_vol_soft", "SoftMaster"),
                ("sc_vol_dig",  "Digital"),
            ]),
        ])

    # ── Panel: DAB-Modul ──────────────────────────────────────────────────────

    def _build_panel_dab(self):
        th  = THEMES["dab"]
        frm = tk.Frame(self._content_frame, bg=th["bg_panel"])
        frm.grid_columnconfigure(0, weight=1)
        frm.grid_columnconfigure(1, weight=1)
        frm.grid_rowconfigure(0, weight=1)
        self._panels["dab"] = frm

        left  = tk.Frame(frm, bg=th["bg_panel"])
        right = tk.Frame(frm, bg=th["bg_panel"])
        left.grid (row=0, column=0, sticky="nsew", padx=(10, 4), pady=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=6)

        self._fill_col(left, th, [
            ("Si4689 – Chip", [
                ("dab_part",       "Part"),
                ("dab_chiprev",    "Chip-Revision"),
                ("dab_fw_mode",    "Firmware-Modus"),
                ("dab_sys_status", "System-Status"),
            ]),
            ("Signal  (DAB_DIGRAD_STATUS)", [
                ("dab_rssi",  "RSSI"),
                ("dab_snr",   "SNR"),
                ("dab_fic",   "FIC-Qualität"),
                ("dab_cnr",   "CNR"),
                ("dab_valid", "Signal"),
                ("dab_acq",   "Synchronisiert"),
            ]),
        ])
        self._fill_col(right, th, [
            ("Aktueller Sender", [
                ("dab_sender",    "Sender"),
                ("dab_ensemble",  "Ensemble"),
                ("dab_channel",   "Kanal / Frequenz"),
                ("dab_prog_type", "Programmtyp"),
                ("dab_dls",       "DLS"),
            ]),
            ("Audio  (DAB_GET_AUDIO_INFO)", [
                ("dab_audio_mode", "Stereo/Mono"),
                ("dab_bitrate",    "Bitrate"),
                ("dab_type",       "Standard"),
            ]),
            ("Events  (DAB_GET_EVENT_STATUS)", [
                ("dab_mute",     "Mute"),
                ("dab_blk_err",  "Block-Fehler"),
                ("dab_blk_loss", "Block-Verlust"),
            ]),
        ])

    # ── Spalten-Aufbau (shared) ───────────────────────────────────────────────

    def _fill_col(self, parent, theme, sections):
        for title, rows in sections:
            tk.Label(
                parent,
                text=f"─ {title} ",
                bg=theme["bg_panel"], fg=theme["fg_title"],
                font=FONT_TIT, anchor="w",
            ).pack(fill="x", pady=(8, 1))

            for var_key, label in rows:
                rf = tk.Frame(parent, bg=theme["bg_panel"])
                rf.pack(fill="x")

                tk.Label(
                    rf,
                    text=f"  {label:<16}",
                    bg=theme["bg_panel"], fg=theme["fg_key"],
                    font=FONT_TXT, anchor="w", width=18,
                ).pack(side="left")

                var = tk.StringVar(value="…")
                self._vars[var_key] = var

                tk.Label(
                    rf,
                    textvariable=var,
                    bg=theme["bg_panel"], fg=theme["fg_val"],
                    font=FONT_TXT, anchor="w",
                ).pack(side="left", fill="x", expand=True)

    # ── StringVar-Helfer ─────────────────────────────────────────────────────

    def _set_var(self, key, value):
        if key in self._vars:
            self._vars[key].set(str(value))

    # ── Refresh-Logik ────────────────────────────────────────────────────────

    def _start_refresh(self):
        self._stop_refresh()
        self._do_refresh()

    def _stop_refresh(self):
        if self._refresh_id is not None:
            try:
                self.after_cancel(self._refresh_id)
            except Exception:
                pass
            self._refresh_id = None

    def _do_refresh(self):
        if not self._active:
            return
        try:
            if self._current_tab == "hardware":
                self._refresh_hardware()
            elif self._current_tab == "soundkarte":
                self._refresh_soundkarte()
            elif self._current_tab == "dab":
                self._refresh_dab()
            self.status_var.set(
                f"[ {self._current_tab.upper()} ]   "
                f"Aktualisiert: {datetime.now().strftime('%H:%M:%S')}  │  "
                f"psutil: {'✓' if PSUTIL_OK else '✗ – pip install psutil'}"
            )
        except Exception as e:
            self.status_var.set(f"Fehler: {e}")

        if self._active:
            self._refresh_id = self.after(REFRESH_MS, self._do_refresh)

    # ── Hardware-Refresh ─────────────────────────────────────────────────────

    def _refresh_hardware(self):
        oi = get_os_info()
        ci = get_cpu_info()
        ri = get_ram_info()
        di = get_disk_info()

        self._set_var("hw_os",       oi["OS"])
        self._set_var("hw_distro",   oi["Distro"])
        self._set_var("hw_kernel",   oi["Kernel"])
        self._set_var("hw_hostname", oi["Hostname"])
        self._set_var("hw_model",    get_pi_model())
        self._set_var("hw_load",     get_load())
        self._set_var("hw_uptime",   get_uptime())

        self._set_var("hw_cpu_mod",  ci["Modell"])
        self._set_var("hw_cores",    ci["Kerne"])
        self._set_var("hw_freq",     ci["Takt"])
        self._set_var("hw_cpu_pct",  ci["Last"])
        self._set_var("hw_temp",     get_temperature())

        self._set_var("hw_ram_tot",  ri["Gesamt"])
        self._set_var("hw_ram_use",  ri["Benutzt"])
        self._set_var("hw_ram_fre",  ri["Verfügbar"])

        self._set_var("hw_dsk_tot",  di["Gesamt"])
        self._set_var("hw_dsk_use",  di["Benutzt"])
        self._set_var("hw_dsk_fre",  di["Frei"])

        self._set_var("hw_throttle", get_throttle_status())
        self._set_var("hw_volts",    get_volts_core())
        self._set_var("hw_armclk",   get_arm_clock())
        self._set_var("hw_gpu_mem",  get_gpu_memory())

    # ── Soundkarten-Refresh ───────────────────────────────────────────────────

    def _refresh_soundkarte(self):
        codec = getattr(self.controller, "audio_codec", None)

        # ── Kartennummer ermitteln ────────────────────────────────────────────
        if codec is not None:
            card = codec.mixer_card
        else:
            card = _sc_find_card()

        # ── Pipeline-Status aus codec.get_status() ────────────────────────────
        if codec is not None:
            st = codec.get_status()
            running  = st.get("running", False)
            pipe_str = "läuft ▶" if running else "gestoppt ◼"
            self._set_var("sc_pipe_status",   pipe_str)
            self._set_var("sc_pipe_uptime",   _sc_fmt_uptime(st.get("uptime_seconds")))
            self._set_var("sc_pipe_restarts", str(st.get("restart_count", 0)))
            hc = "aktiv ✓" if st.get("health_check_active") else "inaktiv"
            self._set_var("sc_pipe_health",   hc)
            self._set_var("sc_adc_dev",       st.get("capture_device",  "–"))
            self._set_var("sc_dac_dev",       st.get("playback_device", "–"))
        else:
            for k in ("sc_pipe_status", "sc_pipe_uptime", "sc_pipe_restarts",
                      "sc_pipe_health", "sc_adc_dev", "sc_dac_dev"):
                self._set_var(k, "kein Codec" if k == "sc_pipe_status" else "–")

        # ── PCM1863 DOUT-Status (I2C) ─────────────────────────────────────────
        if codec is not None:
            self._set_var("sc_adc_dout", codec.pcm186x_read_dout_state())
        else:
            self._set_var("sc_adc_dout", "–")

        # ── PCM5122 ALSA-Karte ────────────────────────────────────────────────
        self._set_var("sc_dac_card", _sc_alsa_card_name(card))

        # ── Stream-Parameter aus /proc/asound ─────────────────────────────────
        sp = _sc_stream_params(card)
        self._set_var("sc_fmt_fmt",   sp["format"])
        self._set_var("sc_fmt_rate",  sp["rate"])
        stream_str = "aktiv ▶" if sp["active"] else "inaktiv ◼"
        self._set_var("sc_dac_stream", stream_str)

        # ── PCM5122 I2C-Register (PLL-Lock + Auto-Mute) ───────────────────────
        pcm = _sc_pcm5122_status()
        self._set_var("sc_pll",   pcm["clk"])
        self._set_var("sc_amute", pcm["amute"])

        # ── Lautstärke via amixer ─────────────────────────────────────────────
        self._set_var("sc_vol_soft", _sc_amixer_pct(card, "SoftMaster"))
        self._set_var("sc_vol_dig",  _sc_amixer_pct(card, "Digital"))

    # ── DAB-Modul-Refresh ────────────────────────────────────────────────────

    def _refresh_dab(self):
        if not self._active or self._current_tab != "dab":
            return
        if self._dab_fetch_running:
            return
        self._dab_fetch_running = True

        mgr = getattr(self.controller, "si4689", None)
        if mgr is None or not mgr.is_ready:
            self._dab_fetch_running = False
            self._set_var("dab_sys_status",
                          "nicht initialisiert" if mgr else "kein Si4689")
            for k in ("dab_part","dab_chiprev","dab_fw_mode",
                      "dab_rssi","dab_snr","dab_fic","dab_cnr",
                      "dab_valid","dab_acq","dab_ensemble","dab_channel",
                      "dab_audio_mode","dab_bitrate","dab_type",
                      "dab_mute","dab_blk_err","dab_blk_loss"):
                self._set_var(k, "–")
            return

        dispatcher = getattr(self.controller, "dispatcher", None)

        def _fetch():
            res   = {}
            radio = getattr(mgr, "_radio", None)

            # ── Chip-Info (einmalig, dann gecacht) ───────────────────────────
            if self._dab_part_cached is None and radio is not None:
                try:
                    self._dab_part_cached = radio.get_part_info()
                except Exception:
                    pass
            if self._dab_part_cached:
                res["part"]    = self._dab_part_cached.get("part_str", "?")
                res["chiprev"] = f"Rev {self._dab_part_cached.get('chiprev', '?')}"

            # ── System-Status ────────────────────────────────────────────────
            try:
                ss = mgr.get_sys_state()
                res["fw_mode"]    = ss.get("mode", "?")
                res["sys_status"] = ss.get("label", "?")
            except Exception as e:
                res["sys_status"] = f"Err: {e}"

            mode = mgr.current_mode

            if mode == "dab" and radio is not None:
                # ── Signal + Kanal (ein Aufruf für beides) ───────────────────
                try:
                    sig = mgr.dab_digrad_status()
                    res["rssi"]  = f"{sig.get('rssi', '?')} dBm"
                    res["snr"]   = f"{sig.get('snr',  '?')} dB"
                    res["fic"]   = f"{sig.get('fic_quality', '?')} %"
                    res["cnr"]   = f"{sig.get('cnr',  '?')} dB"
                    res["valid"] = "✓ gültig"    if sig.get("valid") else "✗ ungültig"
                    res["acq"]   = "✓ synchron"  if sig.get("acq")   else "✗ nicht synchron"
                    freq_khz  = sig.get("tune_freq_khz", 0)
                    tune_idx  = sig.get("tune_index", None)
                    ch_name   = (_DAB_BAND_III[tune_idx][0]
                                 if tune_idx is not None
                                 and 0 <= tune_idx < len(_DAB_BAND_III) else "")
                    if freq_khz:
                        res["channel"] = (f"{ch_name}  /  {freq_khz / 1000:.3f} MHz"
                                          if ch_name else f"{freq_khz / 1000:.3f} MHz")
                    else:
                        res["channel"] = "–"
                except Exception as e:
                    res["rssi"] = f"Err: {e}"

                # ── Sendername (aus Controller-State) ────────────────────────
                state = getattr(self.controller, "state", None)
                res["sender"] = (getattr(state, "AktuellerSender", "") or "–")

                # ── Ensemble-Name ────────────────────────────────────────────
                try:
                    ei = radio.dab_get_ensemble_info()
                    res["ensemble"] = ei.get("label", "–") or "–"
                except Exception:
                    res["ensemble"] = "–"

                # ── Programmtyp (PTY aus dab_scans.sqlite) ───────────────────
                try:
                    sender_idx = int(getattr(state, "AktuelleSenderId", -1) or -1)
                    cfg        = getattr(self.controller, "config_data", {}) or {}
                    base       = getattr(self.controller, "base_path",
                                         os.path.dirname(os.path.dirname(
                                             os.path.abspath(__file__))))
                    db_rel = cfg.get("dab_scan_db", "assets/DB/dab_scans.sqlite")
                    db_path = os.path.join(base, db_rel)
                    with sqlite3.connect(db_path, timeout=1) as _con:
                        row = _con.execute(
                            "SELECT pty_txt FROM si4689_datenbank "
                            "WHERE si4689_idx=? LIMIT 1", (sender_idx,)
                        ).fetchone()
                    pty = row[0] if row else "–"
                    res["prog_type"] = "–" if pty in ("", "<Prg Type N/A>") else pty
                except Exception:
                    res["prog_type"] = "–"

                # ── DLS-Text (aus Controller-Memory) ─────────────────────────
                res["dls"] = getattr(self.controller, "_last_dls_text", "") or "–"

                # ── Audio-Info ───────────────────────────────────────────────
                try:
                    ai = radio.get_dab_audio_info()
                    res["audio_mode"] = ai.get("mode_str", "–")
                    br = ai.get("bit_rate", 0)
                    res["bitrate"]    = f"{br} kbps" if br else "–"
                    res["type"]       = "DAB+  (SBR)" if ai.get("sbr") else "DAB"
                except Exception:
                    res["audio_mode"] = "–"

                # ── Events ───────────────────────────────────────────────────
                try:
                    ev = radio.dab_get_event_status(ack=False)
                    res["mute"]     = "aktiv ◼" if ev.get("mute")      else "aus ▶"
                    res["blk_err"]  = "✗ Fehler" if ev.get("blk_error") else "✓ OK"
                    res["blk_loss"] = "✗ Verlust" if ev.get("blk_loss") else "✓ OK"
                except Exception:
                    res["mute"] = "–"
            else:
                suffix = "–  (FM-Modus)" if mode == "fm" else "–"
                for k in ("rssi","snr","fic","cnr","valid","acq",
                          "sender","ensemble","channel","prog_type","dls",
                          "audio_mode","bitrate","type",
                          "mute","blk_err","blk_loss"):
                    res[k] = suffix
            return res

        def _apply(res):
            self._dab_fetch_running = False
            if not self._active:
                return
            self._set_var("dab_part",       res.get("part",       "–"))
            self._set_var("dab_chiprev",    res.get("chiprev",    "–"))
            self._set_var("dab_fw_mode",    res.get("fw_mode",    "–"))
            self._set_var("dab_sys_status", res.get("sys_status", "–"))
            self._set_var("dab_rssi",       res.get("rssi",       "–"))
            self._set_var("dab_snr",        res.get("snr",        "–"))
            self._set_var("dab_fic",        res.get("fic",        "–"))
            self._set_var("dab_cnr",        res.get("cnr",        "–"))
            self._set_var("dab_valid",      res.get("valid",      "–"))
            self._set_var("dab_acq",        res.get("acq",        "–"))
            self._set_var("dab_sender",     res.get("sender",     "–"))
            self._set_var("dab_ensemble",   res.get("ensemble",   "–"))
            self._set_var("dab_channel",    res.get("channel",    "–"))
            self._set_var("dab_prog_type",  res.get("prog_type",  "–"))
            self._set_var("dab_dls",        res.get("dls",        "–"))
            self._set_var("dab_audio_mode", res.get("audio_mode", "–"))
            self._set_var("dab_bitrate",    res.get("bitrate",    "–"))
            self._set_var("dab_type",       res.get("type",       "–"))
            self._set_var("dab_mute",       res.get("mute",       "–"))
            self._set_var("dab_blk_err",    res.get("blk_err",    "–"))
            self._set_var("dab_blk_loss",   res.get("blk_loss",   "–"))

        if dispatcher is not None:
            def _task():
                try:
                    r = _fetch()
                except Exception as e:
                    r = {"sys_status": f"Task-Err: {e}"}
                self.after(0, lambda: _apply(r))
            dispatcher.submit(_task)
        else:
            try:
                _apply(_fetch())
            except Exception:
                self._dab_fetch_running = False


__all__ = ["Page09"]
