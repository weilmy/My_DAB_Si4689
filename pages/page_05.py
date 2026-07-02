#!/usr/bin/env python3
# ('my_venv_314':venv)

# -*- coding: utf-8 -*-

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

"""
page_05.py – FM-Radio (Si4689 / fmhd_radio_5_3_3.bin)
Autoradio-Retrolook, Raspberry Pi 5 / RaspiAudio DAB HAT

Lifecycle:
  activate()       → Ladescreen (FM-Firmware lädt im Dispatcher)
  on_fm_ready(f)   → FM bereit: Hauptansicht + Signal-Poll starten
  on_page_hide()   → Polls stoppen
  shutdown()       → alle Timers stoppen
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Optional

import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox
from PIL import Image, ImageTk

from .base_page import BasePage


# ---------------------------------------------------------------------------
# Preset-Verwaltung
# ---------------------------------------------------------------------------

class _PresetHandler:
    DEFAULT_FREQS = [88.9, 95.6, 97.7, 101.7, 105.0, 106.9]

    def __init__(self, json_path: str) -> None:
        self.json_path = json_path
        self.preset_freqs: list[float] = self._load()

    def _load(self) -> list[float]:
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path) as f:
                    data = json.load(f)
                freqs = data.get("preset_freqs", self.DEFAULT_FREQS.copy())
                if isinstance(freqs, list) and len(freqs) >= 6:
                    return [float(x) for x in freqs[:6]]
        except Exception:
            pass
        return self.DEFAULT_FREQS.copy()

    def _save(self) -> None:
        data: dict = {}
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path) as f:
                    data = json.load(f)
        except Exception:
            pass
        data["preset_freqs"] = self.preset_freqs
        try:
            with open(self.json_path, "w") as f:
                json.dump(data, f, indent=4)
        except OSError:
            pass

    def set(self, index: int, freq: float) -> None:
        if 1 <= index <= 6:
            self.preset_freqs[index - 1] = round(freq, 1)
            self._save()

    def get(self, index: int) -> Optional[float]:
        if 1 <= index <= 6:
            return self.preset_freqs[index - 1]
        return None

    def save_last_freq(self, freq: float) -> None:
        data: dict = {}
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path) as f:
                    data = json.load(f)
        except Exception:
            pass
        data["last_fm_freq"] = round(freq, 1)
        try:
            with open(self.json_path, "w") as f:
                json.dump(data, f, indent=4)
        except OSError:
            pass

    def load_last_freq(self) -> Optional[float]:
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path) as f:
                    data = json.load(f)
                freq = float(data.get("last_fm_freq", 101.7))
                if 88.0 <= freq <= 108.0:
                    return freq
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Hauptklasse
# ---------------------------------------------------------------------------

class Page05(BasePage):
    MIN_FREQ    = 88.0
    MAX_FREQ    = 108.0
    SCALE_MIN_X = 143
    SCALE_MAX_X = 595

    # ── Scan-Konstanten (europäisches FM-Band, 100 kHz-Raster) ──────────────
    SCAN_START_KHZ = 87_700   # 87.7 MHz  – 100 kHz über unterem Bandende
    SCAN_END_KHZ   = 107_900  # 107.9 MHz – 100 kHz unter oberem Bandende
    SCAN_STEP_KHZ  = 100      # 100 kHz   – europäischer Kanalabstand
    SCAN_RSSI_MIN  = 20       # dBµV      – entspricht FM_SEEK_TUNE_RSSI_THRESHOLD
    SCAN_SNR_MIN   = 3        # dB        – entspricht FM_SEEK_TUNE_SNR_THRESHOLD

    # FM-Lautstärke-Boost: Ausgleich des inherenten Pegelunterschieds FM vs. DAB
    # 1.35 ≈ +4.5 dB  |  1.25 ≈ +3 dB  |  1.50 ≈ +6 dB  → nach Gehör anpassen
    FM_VOL_BOOST = 1.5

    def __init__(self, parent: tk.Widget, controller) -> None:
        super().__init__(parent, controller)
        self.app = controller

        self.json_path = str(
            Path(self.app.base_path) / "assets" / "jsons" / "dab_state.json"
        )
        self.presets      = _PresetHandler(self.json_path)
        self._cur_freq    = 101.7
        self._seek_running        = False
        self._scan_next_khz: int  = self.SCAN_START_KHZ   # persiste Scan-Position
        self._signal_after_id: Optional[str] = None
        self._status_after_id: Optional[str] = None
        self._press_times: dict[int, float]  = {}
        self._knob_angle: float = 0.0
        self._last_angle: Optional[float] = None

        self.configure(bg="#153e81")
        self._load_images()
        self._build_gui()

    # =======================================================================
    # Bilder
    # =======================================================================

    def _load_images(self) -> None:
        _p = lambda n: str(Path(self.app.base_path) / "assets" / "pictures" / n)
        self.img_scale   = tk.PhotoImage(file=_p("fm_scala.png"))
        self.img_zeiger  = tk.PhotoImage(file=_p("Scala_Zeiger.png"))
        self.img_btn     = ImageTk.PhotoImage(file=_p("Button_01.png"))
        self._knob_orig  = Image.open(_p("knob_01.png"))
        self._knob_photo = ImageTk.PhotoImage(self._knob_orig)
        self.sig_imgs: list[ImageTk.PhotoImage] = []
        for i in range(7):
            img = Image.open(_p(f"Signalstaerke_{i}.png")).resize((45, 45), Image.LANCZOS)
            self.sig_imgs.append(ImageTk.PhotoImage(img))

    # =======================================================================
    # GUI aufbauen
    # =======================================================================

    def _build_gui(self) -> None:
        for row, mh, wt in [(0,28,0),(1,22,0),(2,0,0),(3,0,0),(4,0,0),(5,0,1)]:
            self.grid_rowconfigure(row, minsize=mh, weight=wt)
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=1)

        # Titel
        tk.Label(self, text="UKW / FM-Radio",
                 font=("Helvetica", 25), bg="#153e81", fg="#f6d939"
                 ).grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)

        # Statusbar
        self._status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self._status_var,
                 fg="#f6d939", bg="#153e81", anchor="center",
                 borderwidth=0, highlightthickness=0, padx=5, pady=2
                 ).grid(row=1, column=0, columnspan=2, sticky=tk.NSEW)

        # Ladescreen
        """
        self._loading_frame = tk.Frame(self, bg="#0a1f3e")
        self._loading_frame.grid(row=2, column=0, columnspan=2,
                                 rowspan=4, sticky=tk.NSEW)
        self._loading_label = tk.Label(
            self._loading_frame,
            text="📻  FM wird geladen …",
            font=("Helvetica", 18), fg="#f6d939", bg="#0a1f3e"
        )
        self._loading_label.place(relx=0.5, rely=0.4, anchor="center")
        self._loading_progress = ttk.Progressbar(
            self._loading_frame, mode="indeterminate", length=300
        )
        self._loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        """

        # Hauptinhalt
        self._main_frame = tk.Frame(self, bg="#153e81")
        self._main_frame.grid(row=2, column=0, columnspan=2,
                              rowspan=4, sticky=tk.NSEW)
        self._build_main_content(self._main_frame)

        # Ladescreen anfangs oben
        #self._loading_frame.tkraise()

    def _build_main_content(self, parent: tk.Frame) -> None:
        for row, mh, wt in [(0,0,0),(1,0,0),(2,0,0),(3,0,1)]:
            parent.grid_rowconfigure(row, minsize=mh, weight=wt)
        parent.grid_columnconfigure(0, weight=3)
        parent.grid_columnconfigure(1, weight=1)

        scala_w = self.img_scale.width() + 10
        scala_h = 130
        knob_w, knob_h = self._knob_orig.size

        scale_frame = tk.Frame(parent, width=scala_w, height=scala_h, bg="#153e81")
        scale_frame.grid(row=0, column=0, sticky=tk.NW, padx=(5,0), pady=(5,0))
        scale_frame.grid_propagate(False)

        knob_frame = tk.Frame(parent, width=knob_w+20,
                              height=max(scala_h, knob_h+20), bg="#153e81")
        knob_frame.grid(row=0, column=1, sticky=tk.NE, padx=(0,5), pady=(5,0))
        knob_frame.grid_propagate(False)

        # Skala
        tk.Label(scale_frame, image=self.img_scale, borderwidth=0, bg="#153e81"
                 ).grid(column=0, row=0, padx=(5,0), pady=(20,10), sticky=tk.NW)

        # Zeiger
        self._zeiger = tk.Label(scale_frame, image=self.img_zeiger,
                                borderwidth=0, bg="#153e81",
                                cursor="sb_h_double_arrow")
        self._zeiger.place(x=25, y=25)
        self._zeiger.bind("<Button-1>",        self._zeiger_press)
        self._zeiger.bind("<B1-Motion>",       self._zeiger_drag)
        self._zeiger.bind("<ButtonRelease-1>", lambda e: self._play_entry_freq())

        # Frequenz-Eingabe
        self._freq_entry = tk.Entry(
            scale_frame, width=8, font=("Calibri", 15),
            fg="#ffffff", bg="#0a1f3e",
            borderwidth=0, highlightthickness=0, insertbackground="#ffffff"
        )
        self._freq_entry.grid(column=0, row=0, sticky=tk.NW, padx=(10,0), pady=(42,0))
        self._freq_entry.insert(0, "101.7")
        self._freq_entry.bind("<Return>", lambda e: self._play_entry_freq())

        tk.Label(scale_frame, text="MHz", font=("Calibri", 15),
                 fg="#ffffff", bg="#0a1f3e",
                 borderwidth=0, highlightthickness=0
                 ).grid(column=0, row=0, sticky=tk.NW, padx=(75,0), pady=(42,0))

        # Sendername
        self._sender_label = tk.Label(
            scale_frame, text="—",
            font=("Calibri", 12), fg="#ffffff", bg="#0a1f3e"
        )
        self._sender_label.grid(column=0, row=0, sticky=tk.NW, padx=(10,0), pady=(68,0))

        # Drehknopf
        self._knob_label = tk.Label(
            knob_frame, image=self._knob_photo, bg="#153e81", cursor="fleur"
        )
        self._knob_label.grid(column=0, row=0, padx=0, pady=10, sticky=tk.N)
        knob_frame.grid_columnconfigure(0, weight=1)
        self._knob_label.bind("<Button-1>",        self._knob_start)
        self._knob_label.bind("<B1-Motion>",       self._knob_rotate)
        self._knob_label.bind("<ButtonRelease-1>", lambda e: self._play_entry_freq())

        # Preset-Buttons + Scan  (Breite = img_scale – kein Ausdehnen in Spalte 0)
        btn_row = tk.Frame(parent, bg="#153e81",
                           width=self.img_scale.width(), height=50)
        btn_row.grid_propagate(False)
        btn_row.grid(row=1, column=0, sticky=tk.NW, padx=(10, 0), pady=(5, 0))

        labels_cmds = [
            ("1", None), ("2", None), ("3", None),
            ("4", None), ("5", None), ("6", None),
            ("Scan ▶", self._seek_forward),
        ]
        for idx, (lbl, cmd) in enumerate(labels_cmds):
            btn_row.grid_columnconfigure(idx, weight=1)   # Spalten gleich breit verteilen
            btn = tk.Button(
                btn_row, text=lbl, image=self.img_btn, compound="center",
                width=80, height=40, borderwidth=0, highlightthickness=0,
                bg="#153e81", activebackground="#153e81", relief="flat",
                font=("Calibri", 10, "bold"), fg="#225983",
            )
            btn.grid(row=0, column=idx, padx=2, sticky=tk.EW)
            if lbl in ("1","2","3","4","5","6"):
                pi = int(lbl)
                btn.bind("<ButtonPress-1>",   lambda e, i=pi: self._press_start(i))
                btn.bind("<ButtonRelease-1>", lambda e, i=pi: self._press_end(i))
            elif cmd:
                btn.config(command=cmd)

        # Signalstärke-Icon (eigene Zelle column=1, Zeile 1)
        self._sig_icon = tk.Label(
            parent, image=self.sig_imgs[0],
            borderwidth=0, highlightbackground="#f6d939",
            highlightthickness=2, bg="#153e81"
        )
        self._sig_icon.grid(row=1, column=1, padx=(5,5), pady=(5,0))

        # Info-Zeile
        info_row = tk.Frame(parent, bg="#0a1f3e")
        info_row.grid(row=2, column=0, sticky=tk.NSEW, padx=(10,5), pady=(5,5))
        info_row.grid_columnconfigure(0, weight=1)
        info_row.grid_columnconfigure(1, weight=0)

        self._info_label = tk.Label(
            info_row, text="Bereit",
            font=("Calibri", 12), fg="#74bcf3", bg="#0a1f3e", anchor="w",
            borderwidth=0, highlightthickness=0
        )
        self._info_label.grid(column=0, row=0, sticky=tk.W, padx=(10,0), pady=(2,2))

        self._freq_label = tk.Label(
            info_row, text="",
            fg="#f6d939", bg="#0a1f3e", anchor="e",
            font=("Calibri", 12, "bold"),
            borderwidth=0, highlightthickness=0
        )
        self._freq_label.grid(column=1, row=0, sticky=tk.E, padx=(0,10), pady=(2,2))

        self._rds_label = tk.Label(
            info_row, text="",
            font=("Calibri", 12), fg="#74bcf3", bg="#0a1f3e", anchor="w",
            borderwidth=0, highlightthickness=0
        )
        self._rds_label.grid(column=0, row=1, columnspan=2, sticky=tk.W,
                             padx=(10, 10), pady=(0, 2))

        # Lautstärke
        vol_frame = tk.Frame(parent, bg="#153e81")
        vol_frame.grid(row=2, column=1, sticky=tk.NSEW, padx=(0,5), pady=(5,5))

        self._vol_label = ttk.Label(
            vol_frame,
            text=str(getattr(self.app.state, "AktuelleLautstaerke_DAB", 50)),
            font=("Calibri", 12), background="#153e81", foreground="#f6d939"
        )
        self._vol_label.grid(row=0, column=0, sticky=tk.W, padx=(55,0), pady=(5,0))

        self._vol_scale = ttk.Scale(
            vol_frame, from_=0, to=100, orient="horizontal",
            style="Horizontal.TScale", length=120
        )
        self._vol_scale.set(getattr(self.app.state, "AktuelleLautstaerke_DAB", 50))
        self._vol_scale.grid(row=1, column=0, sticky=tk.NSEW, padx=(10,0), pady=(5,0))
        self._vol_scale.bind("<ButtonRelease-1>", self._vol_release)

    # =======================================================================
    # Ladescreen
    # =======================================================================

    def _show_loading(self, text: str = "FM wird geladen …") -> None:
        try:
            self._loading_label.config(text=f"📻  {text}")
            self._loading_progress.start(15)
            self._loading_frame.tkraise()
        except Exception:
            pass

    def _hide_loading(self) -> None:
        try:
            self._loading_progress.stop()
            self._main_frame.tkraise()
        except Exception:
            pass

    def set_status_text(self, text: str, timeout_ms: int = 0) -> None:
        try:
            self._status_var.set(text or "")
        except Exception:
            pass
        if timeout_ms > 0:
            if self._status_after_id:
                try:
                    self.after_cancel(self._status_after_id)
                except Exception:
                    pass
            self._status_after_id = self.after(
                timeout_ms, lambda: self._status_var.set("")
            )

    # =======================================================================
    # Lifecycle
    # =======================================================================

    def activate(self) -> None:
        """Ladescreen zeigen – FM-Firmware lädt parallel im Dispatcher."""
        print("📻 FM-Radio: Seite aktiviert – warte auf FM-Firmware …")
        self._scan_next_khz = self.SCAN_START_KHZ   # Scan-Position bei Seitenwechsel zurücksetzen
        self._update_vol()
        #self._show_loading("FM wird geladen …")

    def on_fm_ready(self, freq_mhz: float) -> None:
        """Aufgerufen von App._switch_to_fm_mode() via gui_batcher sobald FM bereit."""
        print(f"📻 FM bereit: {freq_mhz:.1f} MHz")
        self._cur_freq = freq_mhz
        self._set_freq_display(freq_mhz, update_zeiger=True)
        self._sender_label.config(text=self._lookup(freq_mhz))
        self._freq_label.config(text=f"{freq_mhz:.1f} MHz")
        self._info_label.config(text=f"▶  {self._lookup(freq_mhz)}")
        # FM-Boost sofort anwenden (Slider-Wert unverändert, ALSA boosted)
        try:
            vol = int(round(self.app.state.AktuelleLautstaerke_DAB))
            vol_eff = self._fm_boost(vol)
            self.app.dispatcher.submit(
                lambda v=vol_eff: self.app.volume_service(v), key="volume"
            )
        except Exception:
            pass
        #self._hide_loading()
        self._start_signal_poll()

    def on_page_hide(self) -> None:
        """Polls stoppen bevor Seite verlassen wird."""
        self._stop_signal_poll()
        # FM-Boost rückgängig machen: originalen Pegel (ohne Faktor) wiederherstellen
        try:
            vol = int(round(self.app.state.AktuelleLautstaerke_DAB))
            self.app.dispatcher.submit(
                lambda v=vol: self.app.volume_service(v), key="volume"
            )
        except Exception:
            pass
        # try:
        #    self._loading_progress.stop()
        # except Exception:
        #     pass
        try:
            self._rds_label.config(text="")
        except Exception:
            pass
        print("📻 FM-Radio: Seite versteckt")

    def shutdown(self) -> None:
        self._stop_signal_poll()

    # =======================================================================
    # Lautstärke
    # =======================================================================

    def _vol_release(self, event=None) -> None:
        try:
            vol = round(self._vol_scale.get())
            self.app.state.AktuelleLautstaerke_DAB = vol
            self._vol_label.config(text=str(vol))
            vol_eff = self._fm_boost(vol)
            self.app.dispatcher.submit(
                lambda v=vol_eff: self.app.volume_service(v), key="volume"
            )
        except Exception as e:
            print(f"[Page05] Lautstärke-Fehler: {e}")

    def _fm_boost(self, vol: int) -> int:
        """Wendet FM_VOL_BOOST an wenn Chip im FM-Modus ist, sonst unveränderter Wert."""
        si = getattr(self.app, "si4689", None)
        if si is not None and getattr(si, "current_mode", None) == "fm":
            return min(100, round(vol * self.FM_VOL_BOOST))
        return vol

    def _update_vol(self) -> None:
        try:
            v = int(round(self.app.state.AktuelleLautstaerke_DAB))
            self._vol_label.config(text=str(v))
            self._vol_scale.set(v)
        except Exception:
            pass

    # =======================================================================
    # Drehknopf
    # =======================================================================

    def _knob_start(self, event: tk.Event) -> None:
        self._last_angle = self._angle(event.x, event.y)

    def _knob_rotate(self, event: tk.Event) -> None:
        angle = self._angle(event.x, event.y)
        if self._last_angle is None:
            self._last_angle = angle
        delta = angle - self._last_angle
        if abs(delta) > 180:
            delta -= 360 * (1 if delta > 0 else -1)
        self._last_angle = angle
        self._knob_angle += delta

        rot_range = 360 * 5
        norm = self._knob_angle % rot_range
        freq = self.MIN_FREQ + (norm / rot_range) * (self.MAX_FREQ - self.MIN_FREQ)
        freq = max(self.MIN_FREQ, min(self.MAX_FREQ, freq))

        self._update_knob_image()
        self._set_freq_display(freq, update_zeiger=True)

    def _angle(self, x: int, y: int) -> float:
        dx = x - self._knob_photo.width()  // 2
        dy = y - self._knob_photo.height() // 2
        return (math.degrees(math.atan2(-dy, dx)) + 360) % 360

    def _update_knob_image(self) -> None:
        rot = self._knob_orig.rotate(
            self._knob_angle, resample=Image.BICUBIC, fillcolor="#153e81"
        )
        self._knob_photo = ImageTk.PhotoImage(rot)
        self._knob_label.config(image=self._knob_photo)

    # =======================================================================
    # Zeiger
    # =======================================================================

    def _zeiger_press(self, event: tk.Event) -> None:
        self._drag_ox = event.x

    def _zeiger_drag(self, event: tk.Event) -> None:
        nx = self._zeiger.winfo_x() + event.x - self._drag_ox
        nx = max(self.SCALE_MIN_X, min(self.SCALE_MAX_X, nx))
        self._zeiger.place(x=nx, y=25)
        freq = self.MIN_FREQ + ((nx - self.SCALE_MIN_X) /
                                (self.SCALE_MAX_X - self.SCALE_MIN_X)
                                ) * (self.MAX_FREQ - self.MIN_FREQ)
        self._set_freq_display(freq, update_zeiger=False)

    def _update_zeiger(self, freq: float) -> None:
        freq = max(self.MIN_FREQ, min(self.MAX_FREQ, freq))
        x = int(self.SCALE_MIN_X + ((freq - self.MIN_FREQ) /
                (self.MAX_FREQ - self.MIN_FREQ)) * (self.SCALE_MAX_X - self.SCALE_MIN_X))
        try:
            self._zeiger.place(x=x, y=25)
        except Exception:
            pass

    def _set_freq_display(self, freq: float, update_zeiger: bool = True) -> None:
        freq = round(max(self.MIN_FREQ, min(self.MAX_FREQ, freq)), 1)
        self._cur_freq = freq
        try:
            self._freq_entry.delete(0, tk.END)
            self._freq_entry.insert(0, f"{freq:.1f}")
        except Exception:
            pass
        if update_zeiger:
            self._update_zeiger(freq)

    # =======================================================================
    # Tunen
    # =======================================================================

    def _play_entry_freq(self) -> None:
        raw = self._freq_entry.get().replace(",", ".")
        try:
            mhz = float(raw)
            if not (self.MIN_FREQ <= mhz <= self.MAX_FREQ):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Ungültige Frequenz",
                f"Bitte {self.MIN_FREQ:.1f}–{self.MAX_FREQ:.1f} MHz eingeben."
            )
            return
        self._tune_to(mhz)

    def _tune_to(self, freq_mhz: float) -> None:
        if self._seek_running:
            return
        si = getattr(self.app, "si4689", None)
        if si is None or not si.is_ready or si.current_mode != "fm":
            return

        self._info_label.config(text=f"Tuning … {freq_mhz:.1f} MHz")

        def _worker(f=freq_mhz):
            ok = si.fm_tune(f)
            if not ok:
                self._gui_status(f"Tune fehlgeschlagen: {f:.1f} MHz")
                return
            rsq = si.fm_rsq_status(stcack=True)
            actual = rsq.get("freq_khz", int(f*1000)) / 1000.0
            rssi   = rsq.get("rssi", -99)
            self.presets.save_last_freq(actual)
            self.app.gui_batcher.schedule_update(
                lambda a=actual, r=rssi: self._after_tune(a, r)
            )

        try:
            self.app.dispatcher.submit(_worker, key="fm_tune")
        except Exception as e:
            print(f"[Page05] Tune-Fehler: {e}")

    def _after_tune(self, freq: float, rssi: int) -> None:
        self._set_freq_display(freq)
        name = self._lookup(freq)
        self._sender_label.config(text=name)
        self._freq_label.config(text=f"{freq:.1f} MHz")
        self._info_label.config(text=f"▶  {name}")
        self._update_sig(rssi)
        try:
            self._rds_label.config(text="")
        except Exception:
            pass

    # =======================================================================
    # Scan aufwärts (100 kHz-Schritte)
    # =======================================================================

    def _seek_forward(self) -> None:
        self._seek()

    def _seek(self) -> None:
        if self._seek_running:
            return
        si = getattr(self.app, "si4689", None)
        if si is None or not si.is_ready or si.current_mode != "fm":
            return

        start_khz = self._scan_next_khz
        self._seek_running = True
        self._stop_signal_poll()
        self._info_label.config(
            text=f"Scan ▶ ab {start_khz / 1000:.1f} MHz …"
        )

        def _worker(start=start_khz):
            try:
                found    = False
                freq_khz = start

                while freq_khz <= self.SCAN_END_KHZ:
                    mhz = freq_khz / 1000.0
                    self._gui_status(f"Scan ▶ {mhz:.1f} MHz …")

                    rsq = si.fm_tune_and_check(mhz)

                    if rsq:
                        valid = rsq.get("valid", False)
                        rssi  = int(rsq.get("rssi", -99))
                        snr   = int(rsq.get("snr",    0))

                        if valid and rssi >= self.SCAN_RSSI_MIN and snr >= self.SCAN_SNR_MIN:
                            actual_khz = rsq.get("freq_khz", freq_khz)
                            actual_mhz = actual_khz / 1000.0

                            next_khz = actual_khz + self.SCAN_STEP_KHZ
                            self._scan_next_khz = (
                                next_khz if next_khz <= self.SCAN_END_KHZ
                                else self.SCAN_START_KHZ
                            )
                            self.presets.save_last_freq(actual_mhz)
                            self.app.gui_batcher.schedule_update(
                                lambda f=actual_mhz, r=rssi: self._after_tune(f, r)
                            )
                            found = True
                            break

                    freq_khz += self.SCAN_STEP_KHZ

                if not found:
                    self._scan_next_khz = self.SCAN_START_KHZ
                    self._gui_status(
                        f"Scan ▶ Ende – nächster Scan ab "
                        f"{self.SCAN_START_KHZ / 1000:.1f} MHz"
                    )

            except Exception as e:
                self._gui_status(f"Scan-Fehler: {e}")
            finally:
                self._seek_running = False
                self.app.gui_batcher.schedule_update(self._start_signal_poll)

        try:
            self.app.dispatcher.submit(_worker, key="fm_seek")
        except Exception:
            self._seek_running = False
            self._start_signal_poll()

    # =======================================================================
    # Presets (Kurzdruck = abrufen, Langdruck ≥2s = speichern)
    # =======================================================================

    def _press_start(self, i: int) -> None:
        self._press_times[i] = time.time()

    def _press_end(self, i: int) -> None:
        dur = time.time() - self._press_times.get(i, time.time())
        if dur >= 2.0:
            self.presets.set(i, self._cur_freq)
            self.set_status_text(f"Preset {i}: {self._cur_freq:.1f} MHz gespeichert", 3000)
        else:
            freq = self.presets.get(i)
            if freq is not None:
                self._tune_to(freq)

    # =======================================================================
    # Signal/RDS-Polling
    # =======================================================================

    def _start_signal_poll(self) -> None:
        self._stop_signal_poll()
        self._signal_poll_tick()

    def _stop_signal_poll(self) -> None:
        if self._signal_after_id:
            try:
                self.after_cancel(self._signal_after_id)
            except Exception:
                pass
            self._signal_after_id = None

    def _signal_poll_tick(self) -> None:
        try:
            self.app.dispatcher.submit(self._fetch_signal, key="fm_signal")
        except Exception:
            pass
        # Nur EINEN after() schedulen
        try:
            self._signal_after_id = self.after(500, self._signal_poll_tick)
        except Exception:
            pass

    def _fetch_signal(self) -> None:
        if self._seek_running:
            return
        si = getattr(self.app, "si4689", None)
        if si is None or not si.is_ready or si.current_mode != "fm":
            return
        try:
            rsq = si.fm_rsq_status(stcack=False)
        except Exception:
            return
        rssi  = rsq.get("rssi", -99)
        pilot = rsq.get("pilot", False)
        valid = rsq.get("valid", False)
        fmhz  = rsq.get("freq_khz", 0) / 1000.0

        # RDS lesen (best-effort; kein Abbruch bei Fehler)
        rds_text = ""
        try:
            rds = si.fm_rds_poll()
            if rds:
                rt        = rds.get("rt", "")
                ps_scroll = rds.get("ps_scroll", "")
                ps        = rds.get("ps", "")
                if rt:
                    rds_text = rt            # RT hat Vorrang (vollständiger Titel)
                elif ps_scroll:
                    rds_text = ps_scroll     # Dynamic PS akkumuliert
                elif ps:
                    rds_text = ps            # einzelner PS-Frame als Fallback
        except Exception:
            pass

        def _ui(r=rssi, p=pilot, v=valid, f=fmhz, rds=rds_text):
            self._update_sig(r)
            if v:
                stereo = "Stereo" if p else "Mono"
                name   = self._lookup(f)
                self._info_label.config(text=f"▶  {name}  [{stereo}  {r} dBµV]"
                )
            if rds:   # nur überschreiben wenn neuer Wert vorhanden
                self._rds_label.config(text=f"▶  {rds}")
        try:
            self.app.gui_batcher.schedule_update(_ui)
        except Exception:
            pass

    def _update_sig(self, rssi: int) -> None:
        grenzen = [10, 25, 35, 45, 55, 65]
        idx = len(grenzen)
        for i, g in enumerate(grenzen):
            if rssi < g:
                idx = i
                break
        try:
            self._sig_icon.config(image=self.sig_imgs[idx])
        except Exception:
            pass

    # =======================================================================
    # Hilfsmethoden
    # =======================================================================

    def _lookup(self, freq_mhz: float) -> str:
        json_path = str(
            Path(self.app.base_path) / "assets" / "jsons" / "fm_stations_ch.json"
        )
        try:
            with open(json_path) as f:
                stations = json.load(f)
            for s in stations:
                if abs(s.get("freq_mhz", 0) - freq_mhz) < 0.15:
                    return s.get("name", f"{freq_mhz:.1f} MHz")
        except Exception:
            pass
        return f"{freq_mhz:.1f} MHz"

    def _gui_status(self, text: str) -> None:
        try:
            self.app.gui_batcher.schedule_update(
                lambda t=text: self._info_label.config(text=t)
            )
        except Exception:
            pass


__all__ = ["Page05"]