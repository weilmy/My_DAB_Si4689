#!/usr/bin/env python3
# ('my_venv_314':venv)

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

# dab.raw wird ueber die ALSA-Pipeline geschrieben (kein GStreamer).
# Systemweiter EQ (ALSA) zentral gesteuert:
# - AlsaEQController erkennt Band-Namen automatisch via amixer -D equal scontrols.
# - 10-Band-Mapping.
# - Klangmuster Presets
# Kette: App → hifiberry_play_plug → SoftMaster → systemweiter EQ → dmix → HW.
# Equaliser-Slider wirken auf den systemweiten Equalizer.
# Visualisierung mit Balkendiagramm

import os
import time
import json
import re
import threading
import subprocess
import tkinter as tk
from tkinter import *
import tkinter.ttk as ttk
from tkinter import messagebox

import numpy as np
os.environ.setdefault("MPLBACKEND", "TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from pathlib import Path
from .base_page import BasePage

RAW_PATH = os.path.join(os.path.expanduser("~"), "dab.raw")


class Page03(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app = controller

        self.configure(bg="#dc93f5")
        self.bg_color = "#dc93f5"

        self.sliders = []
        self.labels = []
        self.slider_value_labels = []

        self.alsa_eq = None            # AlsaEQController (system-wide EQ)
        self._eq_apply_job = None      # debounce handle

        # Plot-Steuerung
        self.running = False           # steuert read_audio_data-Schleife
        self._bars_job = None          # Tk-after-Job für update_bars
        self._reader_thread = None     # Thread-Objekt für read_audio_data
        self._update_bars_cb = None    # Callback, der einmal update_bars ausführt

        self.build_gui()

    # ---------------- UI ----------------
    def build_gui(self):
        self.create_frames()
        self.create_equalizer()
        self.create_listbox()
        self.create_plot_bar() # nur Figur/Canvas + Callback aufbauen, noch NICHT starten

    def create_frames(self):
        try:
            if hasattr(self.app, "geometry"):
                self.app.geometry("800x480")
        except Exception:
            pass

        # Spalten: links flexibel, rechts schmal
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, minsize=25, weight=0)  # Header fix 25px
        self.grid_rowconfigure(1, weight=1)              # Top
        self.grid_rowconfigure(2, weight=1)              # Bottom (Plot)

        # Header
        if hasattr(self, "label") and self.label.winfo_exists():
            self.label.configure(font=("Helvetica", 25), background="#be3bea",
                                foreground="#db8df5", text="Equalizer/Plot")
        else:
            self.label = tk.Label(self, text="Equalizer/Plot", font=("Helvetica", 25),
                                background="#be3bea", foreground="#db8df5")
        self.label.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)

        # Top-Left: EQ
        self.top_left_frame = tk.Frame(self, bg=self.bg_color)
        self.top_left_frame.grid(row=1, column=0, sticky=tk.NSEW, padx=(10, 0), pady=(5, 0))
        self.top_left_frame.grid_rowconfigure(0, weight=1)
        self.top_left_frame.grid_columnconfigure(0, weight=1)

        # Top-Right: Presets (nur vertikal strecken, horizontal schmal halten)
        self.top_right_frame = tk.Frame(self, bg=self.bg_color)
        self.top_right_frame.grid(row=1, column=1, sticky=tk.NS, padx=(8, 10), pady=(5, 0))
        self.top_right_frame.grid_rowconfigure(0, weight=1)
        self.top_right_frame.grid_columnconfigure(0, weight=0)

        # Bottom: Plot
        self.bottom_frame = tk.Frame(self, bg=self.bg_color)
        self.bottom_frame.grid(row=2, column=0, columnspan=2, sticky=tk.NSEW, padx=10, pady=(5, 5))
        self.bottom_frame.grid_rowconfigure(0, weight=1)
        self.bottom_frame.grid_columnconfigure(0, weight=1)


    def create_equalizer(self):
        self.slider_frame = ttk.Frame(self.top_left_frame, style='Frame3.TFrame')
        self.slider_frame.grid(row=0, column=0, sticky=tk.NW)
        self.top_left_frame.grid_rowconfigure(0, weight=1)
        self.top_left_frame.grid_columnconfigure(0, weight=1)

        # Styling der Skalen beibehalten
        self.app.style.configure("Vertical.TScale", background="#65009b",
                                troughcolor=self.bg_color, sliderthickness=20, sliderrelief='flat')
        self.app.style.map("Vertical.TScale", foreground=[('active', '#2f88f5')])

        # 10 EQ-Bänder
        freqs = ["31 Hz", "63 Hz", "125 Hz", "250 Hz", "500 Hz",
                "1 kHz", "2 kHz", "4 kHz", "8 kHz", "16 kHz"]

        self.sliders = []
        self.slider_value_labels = []

        SLIDER_LEN = 180

        for i in range(10):
            slider_col = i * 2
            marker_col = slider_col + 1

            # dB-Anzeige über dem Slider
            value_label = tk.Label(self.slider_frame, text="0 dB", font=('Calibri', 8), width=5, bg=self.bg_color)
            value_label.grid(row=0, column=slider_col, pady=(0, 2))
            self.slider_value_labels.append(value_label)

            # Slider selbst (0..100%, Mitte=50 → 0 dB)
            slider = ttk.Scale(self.slider_frame, orient=tk.VERTICAL, from_=100, to=0,
                            length=SLIDER_LEN, style="Vertical.TScale")
            slider.grid(row=1, column=slider_col, padx=3)
            slider.set(self.app.state.eq_pct_10[i])
            self.sliders.append(slider)

            def _on_move(val, index=i):
                self.update_slider_display(index)
                self.app.state.eq_pct_10[index] = int(round(float(val)))
                # Debounce Apply
                if getattr(self, "_eq_apply_job", None):
                    try: self.after_cancel(self._eq_apply_job)
                    except Exception: pass
                self._eq_apply_job = self.after(100, self.apply_eq_from_sliders)

            def _on_release(event, index=i):
                self.update_slider_display(index)
                self.app.state.eq_pct_10[index] = int(round(self.sliders[index].get()))
                self.apply_eq_from_sliders()
                self.save_eq_state() 

            slider.configure(command=_on_move)
            slider.bind("<ButtonRelease-1>", _on_release)

            # Markerskala neben dem Slider
            marker = tk.Canvas(self.slider_frame, width=15, height=SLIDER_LEN, bg='#2f88f5', highlightthickness=0)
            marker.grid(row=1, column=marker_col, sticky=tk.W)
            for j in range(0, 101, 10):
                y = SLIDER_LEN - (j * (SLIDER_LEN / 100.0))
                if j % 50 == 0:
                    marker.create_line(0, y, 10, y, fill='white', width=2)
                else:
                    marker.create_line(0, y, 5, y, fill='white', width=1)

        # Frequenz-Beschriftungen
        for i, text in enumerate(freqs):
            tk.Label(self.slider_frame, text=text, font=('Calibri', 8), bg=self.bg_color).grid(
                row=1, rowspan=2, column=i*2, pady=(SLIDER_LEN + 10, 0), sticky=tk.E
            )

        # Volume ganz rechts
        vol_col = 20  # 10 Bänder * 2 Spalten (Slider+Marker)
        self.vol_value_label = tk.Label(self.slider_frame, text=self.app.state.AktuelleLautstaerke_DAB, font=('Calibri', 8), bg=self.bg_color)
        self.vol_value_label.grid(row=0, column=vol_col, pady=(0, 2))

        self.volume_slider = ttk.Scale(self.slider_frame, orient=tk.VERTICAL, from_=100, to=0, length=SLIDER_LEN, style="Vertical.TScale")
        self.volume_slider.grid(row=1, column=vol_col, padx=4)
        self.volume_slider.set(self.app.state.AktuelleLautstaerke_DAB)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_slider_change)

        vol_marker = tk.Canvas(self.slider_frame, width=10, height=SLIDER_LEN, bg='#2f88f5', highlightthickness=0)
        vol_marker.grid(row=1, column=vol_col + 1, sticky=tk.W)
        for j in range(0, 17):  # 0..16
            y = SLIDER_LEN - (j * (SLIDER_LEN / 16.0))
            vol_marker.create_line(0, y, 10 if j % 4 == 0 else 5, y, fill='white', width=2 if j % 4 == 0 else 1)

        tk.Label(self.slider_frame, text="Volume", font=('Calibri', 8), bg=self.bg_color).grid(
            row=1, rowspan=2, column=vol_col, pady=(SLIDER_LEN + 10, 0), sticky=tk.E
        )
        #self.update_volume_display()

    def create_listbox(self):
        self.listbox_frame = ttk.Frame(self.top_right_frame, style='Frame3.TFrame')
        self.listbox_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=0, pady=0)
        self.listbox_frame.grid_rowconfigure(0, weight=1)
        self.listbox_frame.grid_rowconfigure(1, weight=0)
        self.listbox_frame.grid_columnconfigure(0, weight=1)
        self.listbox_frame.grid_columnconfigure(1, weight=0)

        self.listbox_presets = Listbox(self.listbox_frame, width=15, height=4, justify=CENTER,)
        self.listbox_presets.grid(row=0, column=0, padx=0, pady=(17, 10), sticky=tk.NSEW)
        self.listbox_presets.bind("<Double-Button-1>", self.load_selected_preset)

        self.scrollbar_presets = ttk.Scrollbar(self.listbox_frame, orient=tk.VERTICAL,
                                               command=self.listbox_presets.yview)
        self.listbox_presets.configure(yscrollcommand=self.scrollbar_presets.set)
        self.scrollbar_presets.grid(row=0, column=1, pady=(17, 10), sticky=tk.NS)

        self.set_presets = tk.Button(self.listbox_frame, text="Set Preset",
                                     font=('Calibri', 9), width=15, command=self.set_preset)
        self.set_presets.grid(row=1, column=0)

        try:
            with open(self.app.config_data["eq_presets"], "r") as f:
                presets = json.load(f)
                for name in presets:
                    self.listbox_presets.insert(tk.END, name)
                self.fill_listbox_with_colors_presets()
        except Exception as e:
            print(f"⚠️ Konnte Presets nicht laden: {e}")
        
        self.after_idle(self.fit_preset_column)
        self.listbox_presets.bind("<Configure>", lambda e: self.fit_preset_column())

    def fit_preset_column(self):
        """Macht die rechte Spalte genauso breit wie Listbox + Scrollbar (Pixelgenau)."""
        # Widgets schon gemappt?
        if not hasattr(self, "listbox_presets") or not self.listbox_presets.winfo_ismapped():
            self.after(50, self.fit_preset_column)
            return

        # Geometrie ermitteln
        self.update_idletasks()
        lbw = max(self.listbox_presets.winfo_reqwidth(), self.listbox_presets.winfo_width())
        sbw = self.scrollbar_presets.winfo_reqwidth() if hasattr(self, "scrollbar_presets") else 0
        w = lbw + sbw + 6  # kleiner Rand

        # Spalte 1 auf diese Breite festlegen
        self.grid_columnconfigure(1, minsize=w, weight=0)

        # Frame wirklich auf w px festsetzen (Kinder sollen ihn NICHT weiter aufziehen)
        self.top_right_frame.grid_propagate(False)
        self.top_right_frame.configure(width=w)

    def create_plot_bar(self):
        """
        Initialisiert die Matplotlib-Figur und den Update-Callback, startet aber
        noch keinen Hintergrund-Thread oder Tk-After-Job. Diese werden in
        start_plot() aktiviert (über apply_resource_profile()).
        """
        # vorhandene Canvas (falls vorhanden) säubern
        if hasattr(self, "canvas"):
            try:
                widget = self.canvas.get_tk_widget()
                if widget and widget.winfo_exists():
                    widget.destroy()
            except Exception:
                pass
            self.canvas = None

        # neue Figur + Balken aufbauen
        self._build_plot_bar()

    # --------------- Equalizer control ---------------
    def _amix(self, cmd: str) -> str:
        try:
            return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            print("amixer:", e.output)
            return ""

    def apply_eq_from_sliders(self):
        if self.alsa_eq is None:
            try:
                self.alsa_eq = AlsaEQController()
            except Exception as e:
                print(f"❌ ALSA-EQ Controller Fehler (lazy): {e}")
                return

        SCALE = float(getattr(self.app.state, "eq_scale", 1.0))

        def to_pct(idx: int) -> int:
            v = float(self.sliders[idx].get()) # 0..100, 50 = 0 dB
            return int(round(max(0, min(100, 50 + (v - 50) * SCALE))))

        p = [to_pct(i) for i in range(10)]
        self.alsa_eq.set_pct_10band(*p)
        self.app.state.eq_pct_10 = p
        self.save_eq_state()  # <- PERSISTENZ

    # --------------- Volume -----------------
    def on_volume_slider_change(self, val):
        try:
            volume = round(self.volume_slider.get())
            self.app.state.AktuelleLautstaerke_DAB = volume
            self.vol_value_label.config(text=self.app.state.AktuelleLautstaerke_DAB)
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume")
        except Exception as e:
            print(f"⚠️ Fehler beim Setzen der Lautstärke (amixer): {e}")

    # --------------- Presets -----------------
    def set_preset(self):
        from tkinter.simpledialog import askstring
        preset_name = askstring("Preset speichern", "Name für neues Preset:")
        valid, result = self.validate_preset_name(preset_name)
        if not valid:
            if result:
                messagebox.showwarning("Ungültiger Name", result)
            return
        preset_name = result

        if preset_name in self.listbox_presets.get(0, tk.END):
            overwrite = messagebox.askyesno(
                "Überschreiben?",
                f"Preset '{preset_name}' existiert bereits. Überschreiben?"
            )
            if not overwrite:
                return

        # 10 dB-Werte aus 10 Slidern
        db_values = [int(round((s.get() - 50) * 0.8)) for s in self.sliders]

        try:
            with open(self.app.config_data["eq_presets"], "r") as f:
                presets = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            presets = {}

        presets[preset_name] = db_values

        try:
            with open(self.app.config_data["eq_presets"], "w") as f:
                json.dump(presets, f, indent=4)
        except Exception as e:
            print(f"❌ Fehler beim Speichern des Presets: {e}")
            return

        if preset_name not in self.listbox_presets.get(0, tk.END):
            self.listbox_presets.insert(tk.END, preset_name)
            self.fill_listbox_with_colors_presets()

    def validate_preset_name(self, name):
        if name is None:
            return False, None
        name = name.strip()
        if not name:
            return False, "Der Preset-Name darf nicht leer sein."
        if len(name) > 30:
            return False, "Bitte wähle einen kürzeren Namen (max. 30 Zeichen)."
        if not re.match(r'^[\w\- ]+$', name):
            return False, "Nur Buchstaben, Zahlen, Leerzeichen, Unterstrich und Bindestrich erlaubt."
        return True, name

    def load_selected_preset(self, event=None):
        selection = self.listbox_presets.curselection()
        if not selection:
            return
        preset_name = self.listbox_presets.get(selection[0])
        try:
            with open(self.app.config_data["eq_presets"], "r") as f:
                presets = json.load(f)
        except Exception as e:
            print(f"❌ Fehler beim Laden der Presets: {e}")
            return
        if preset_name not in presets:
            print(f"⚠️ Preset '{preset_name}' nicht gefunden.")
            return

        db_values = presets[preset_name]

        def map5to10_db(db5):
            # Kompatibles 5→10 Mapping (0 dB neutral für die fehlenden Zwischenbänder)
            # Indizes: [125, 330≈→(250/500), 1k, 3.3k≈4k, 10k]
            out = [0]*10
            out[0] = 0            # 31 Hz
            out[1] = 0            # 63 Hz
            out[2] = db5[0]       # 125 Hz
            out[3] = int(round(0.6*db5[1]))  # 250 Hz
            out[4] = int(round(0.4*db5[1]))  # 500 Hz
            out[5] = db5[2]       # 1 kHz
            out[6] = 0            # 2 kHz
            out[7] = db5[3]       # 4 kHz (≈3.3k)
            out[8] = int(round(0.7*db5[4]))  # 8 kHz
            out[9] = int(round(0.3*db5[4]))  # 16 kHz
            return out

        if len(db_values) == 5:
            db_values = map5to10_db(db_values)
        elif len(db_values) != 10:
            print("⚠️ Preset hat unerwartete Länge – wird ignoriert.")
            return

        # Slider setzen (dB → Slider-Prozent umrechnen: 0.8 dB/Prozentpunkt)
        for i, db in enumerate(db_values):
            slider_value = (db / 0.8) + 50
            self.sliders[i].set(slider_value)
            self.update_slider_display(i)
            self.app.state.eq_pct_10[i] = int(round(slider_value))

        self.app.state.eq_selected_preset = preset_name
        self.apply_eq_from_sliders()
        self.save_eq_state()

    def fill_listbox_with_colors_presets(self):
        for index in range(self.listbox_presets.size()):
            if index % 2 == 1:
                self.listbox_presets.itemconfig(index, background='#f2c9f1')

    # --------------- Plot intern aufbauen -----------------
    def _build_plot_bar(self):
        """
        Baut die Matplotlib-Figur und den update_bars-Callback auf, ohne die
        Schleife zu starten. start_plot() ruft dann self._update_bars_cb() auf.
        """
        fig, ax = plt.subplots(figsize=(4, 4.4), dpi=50)
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        ax.set_facecolor('black')
        num_bars = 60
        ax.set_xlim(0, num_bars)
        ax.set_ylim(0, 32767)
        overall_gain = 2.5
        ymax = int(32767 * overall_gain)
        ax.set_ylim(0, ymax)
        ax.set_xticks([])
        ax.set_yticks([])
        bar_width = 0.55
        bars = ax.bar(
            np.arange(num_bars),
            np.zeros(num_bars),
            width=bar_width,
            color=plt.cm.rainbow(np.linspace(0, 1, num_bars))
        )

        # --- sanfte Kompensation (links etwas runter, rechts etwas rauf)
        tilt_db_per_span = 6.0
        tilt_db = np.linspace(0.0, tilt_db_per_span, num_bars)  # 0 → +6 dB
        comp = 10.0 ** (tilt_db / 20.0)                         # dB → linear
        comp[:7] *= 10.0 ** (-2.0 / 20.0)                       # Low-Shelf -2 dB

        gamma = 0.85  # <1.0 hebt kleine Werte etwas an, >1.0 drückt sie

        def update_bars():
            # nur arbeiten, wenn running=True
            if not getattr(self, "running", False):
                return

            if hasattr(self, "audio_signal") and len(getattr(self, "audio_signal", [])) > 0:
                if np.max(np.abs(self.audio_signal)) < 300:
                    fft_result = np.zeros(num_bars)
                else:
                    fft_result = np.abs(np.fft.rfft(self.audio_signal))
                    
                    # Auf exakt num_bars bringen
                    if len(fft_result) < num_bars:
                        fft_result = np.pad(fft_result, (0, num_bars - len(fft_result)))
                    else:
                        fft_result = fft_result[:num_bars]

                    if np.max(fft_result) > 0:
                        fft_result = fft_result / np.max(fft_result)

                    fft_result = np.convolve(fft_result, np.ones(3) / 3, mode='same')[:num_bars]
                    fft_result = np.power(fft_result, gamma) * comp
                    fft_result = np.clip(fft_result * overall_gain * 32000.0, 0, ymax)
            else:
                fft_result = np.zeros(num_bars)

            for bar, height in zip(bars, fft_result):
                bar.set_height(height)

            fig.canvas.draw()
            # nächste Aktualisierung planen, solange running=True
            self._bars_job = self.bottom_frame.after(100, update_bars)

        # Callback merken, aber NICHT starten
        self._update_bars_cb = update_bars

        # Canvas aufbauen (statisches Bild, Bars alle = 0)
        self.canvas = FigureCanvasTkAgg(fig, master=self.bottom_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        fig.canvas.draw()

    # --------------- Audio-Reader -----------------
    def read_audio_data(self):
        file_path = RAW_PATH
        last_inode = None
        raw_file = None
        try:
            while getattr(self, "running", False):
                try:
                    if not os.path.exists(file_path):
                        time.sleep(0.1)
                        continue
                    current_inode = os.stat(file_path).st_ino
                    if current_inode != last_inode:
                        if raw_file:
                            raw_file.close()
                            raw_file = None
                        try:
                            temp_file = open(file_path, "rb")
                            temp_file.seek(0, os.SEEK_END)
                            raw_file = temp_file
                            last_inode = current_inode
                        except Exception:
                            raw_file = None
                            time.sleep(0.2)
                            continue
                    if raw_file:
                        frames = raw_file.read(4096)
                        if frames and len(frames) % 4 == 0:
                            audio_signal = np.frombuffer(frames, dtype=np.int32)
                            if len(audio_signal) % 2 == 0:
                                audio_signal = audio_signal.reshape(-1, 2)[:, 0]
                            # S32_LE: 24-Bit-PCM MSB-ausgerichtet → obere 16 Bit für Spektrum
                            audio_signal = (audio_signal >> 16).astype(np.int16)
                            self.audio_signal = np.clip(
                                audio_signal * 10.0,
                                -32768, 32767
                            ).astype(np.int16)
                        else:
                            time.sleep(0.05)
                    else:
                        time.sleep(0.1)
                except Exception:
                    time.sleep(0.1)
        finally:
            if raw_file:
                try:
                    raw_file.close()
                except Exception:
                    pass

    # --------------- Slider-Anzeige -----------------
    def update_slider_display(self, i):
        val = self.sliders[i].get()
        db = (val - 50) * 0.8
        text = f"{int(db):+} dB"
        if db > 10:
            color = "red"
        elif db > 0:
            color = "orange"
        elif db == 0:
            color = "black"
        elif db > -10:
            color = "blue"
        else:
            color = "#0033cc"
        self.slider_value_labels[i].config(text=text, fg=color)

    def on_first_activate(self):
        try:
            self.update_volume_display()
        except Exception:
            pass

    def on_reactivate(self):
        try:
            self.update_volume_display()
        except Exception:
            pass

    def _eq_state_path(self) -> Path | None:
        try:
            cfg = getattr(self.app, "config_data", {}) or {}
            if "eq_state_path" in cfg:
                return Path(cfg["eq_state_path"]).expanduser()
        except Exception:
            pass

    def _ensure_parent_dir(self, p: Path) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)

    def save_eq_state(self) -> None:
        """Persistiert eq_pct_10, eq_selected_preset, eq_scale aus AppState."""
        try:
            p = self._eq_state_path()
            if p is None:
                return
            self._ensure_parent_dir(p)
            data = {
                "eq_pct_10": list(getattr(self.app.state, "eq_pct_10", [50]*10)),
                "eq_selected_preset": getattr(self.app.state, "eq_selected_preset", None),
                "eq_scale": float(getattr(self.app.state, "eq_scale", 1.0)),
            }
            with p.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ EQ-State speichern fehlgeschlagen: {e}")

    def load_eq_state_into_appstate(self) -> None:
        """
        Lädt Persistenz in AppState, ohne UI anzufassen.
        Ruft NICHT automatisch ALSA an (macht apply_eq_* separat).
        """
        try:
            p = self._eq_state_path()
            if p is None or not p.exists():
                return
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if "eq_pct_10" in data and isinstance(data["eq_pct_10"], list) and len(data["eq_pct_10"]) == 10:
                self.app.state.eq_pct_10 = [int(max(0, min(100, x))) for x in data["eq_pct_10"]]
            if "eq_selected_preset" in data:
                self.app.state.eq_selected_preset = data["eq_selected_preset"]
            if "eq_scale" in data:
                self.app.state.eq_scale = float(data["eq_scale"])
        except Exception as e:
            print(f"⚠️ EQ-State laden fehlgeschlagen: {e}")

    @staticmethod
    def restore_eq_state(app, apply_eq: bool = False) -> None:
        """
        Lädt den EQ-State in app.state und wendet optional sofort den ALSA-EQ an.
        - app: die App-Instanz (muss app.config_data und app.state haben)
        - apply_eq: wenn True, wird versucht, einen AlsaEQController zu instanziieren und die Werte anzuwenden.
        """
        def _eq_state_path_for(app) -> Path | None:
            try:
                cfg = getattr(app, "config_data", {}) or {}
                if "eq_state_path" in cfg:
                    return Path(cfg["eq_state_path"]).expanduser()
            except Exception:
                pass

        try:
            p = _eq_state_path_for(app)
            if p is None or not p.exists():
                return
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if "eq_pct_10" in data and isinstance(data["eq_pct_10"], list) and len(data["eq_pct_10"]) == 10:
                app.state.eq_pct_10 = [int(max(0, min(100, x))) for x in data["eq_pct_10"]]
            if "eq_selected_preset" in data:
                app.state.eq_selected_preset = data["eq_selected_preset"]
            if "eq_scale" in data:
                try:
                    app.state.eq_scale = float(data["eq_scale"])
                except Exception:
                    pass

            # Optional: sofort anwenden (systemweiter ALSA-EQ)
            if apply_eq:
                try:
                    alsa = AlsaEQController()
                    pct = getattr(app.state, "eq_pct_10", [50]*10)
                    pct = [int(max(0, min(100, x))) for x in pct]
                    alsa.set_pct_10band(*pct)
                    print("Systemweiter ALSA-EQ angewendet ✓")
                except Exception as e:
                    # Nicht fatal — nur protokollieren
                    print(f"❌ ALSA-EQ beim Restore nicht anwendbar: {e}")
        except Exception as e:
            print(f"⚠️ EQ-State Restore fehlgeschlagen: {e}")

    # --- Aktualisieren von Volume-Anzeige/Slider ---
    def update_volume_display(self):
        """Synchronisiert vol_value_label und volume_slider mit app.state.AktuelleLautstaerke_DAB."""
        try:
            vol = int(round(getattr(self.app.state, "AktuelleLautstaerke_DAB", 50)))
        except Exception:
            vol = 50
        try:
            if hasattr(self, "vol_value_label"):
                self.vol_value_label.config(text=vol)
            if hasattr(self, "volume_slider"):
                self.volume_slider.set(vol)
        except Exception as e:
            print(f"⚠️ Fehler beim Aktualisieren der Volume-Anzeige: {e}")

    def shutdown(self):
        """Sauberes Herunterfahren von Recorder/Reader/Plot (z.B. beim App-Beenden)."""
        # 1) Plot-Schleife & Reader stoppen
        try:
            self.stop_plot()
        except Exception:
            pass

        # 2) Canvas/Figur endgültig zerstören
        try:
            if getattr(self, "canvas", None):
                try:
                    widget = self.canvas.get_tk_widget()
                    if widget and widget.winfo_exists():
                        widget.destroy()
                except Exception:
                    pass
                try:
                    fig = getattr(self.canvas, "figure", None)
                    if fig is not None:
                        import matplotlib.pyplot as _plt
                        try:
                            _plt.close(fig)
                        except Exception:
                            pass
                except Exception:
                    pass
                self.canvas = None
        except Exception:
            pass

    def stop_plot(self):
        """Stoppt die laufende Balkenanzeige und den Lesethread."""
        # Tk-after-Job abbrechen
        try:
            if getattr(self, "_bars_job", None):
                try:
                    self.bottom_frame.after_cancel(self._bars_job)
                except Exception:
                    pass
                self._bars_job = None
        except Exception:
            pass

        # Reader-Schleife stoppen
        try:
            if getattr(self, "running", None):
                self.running = False
        except Exception:
            pass

    def start_plot(self):
        """
        Startet Balkenanzeige + Lesethread, falls nicht bereits aktiv.
        Wird von apply_resource_profile() aufgerufen, wenn Page03 aktiv ist
        und PAGE_PROFILES['Page03'].mpl_page03 == True.
        """
        # Schon aktiv?
        if getattr(self, "_bars_job", None):
            return

        # sicherstellen, dass ein Callback existiert
        if not getattr(self, "_update_bars_cb", None):
            self._build_plot_bar()

        # Reader-Thread starten (falls noch nicht läuft)
        if not getattr(self, "_reader_thread", None) or not self._reader_thread.is_alive():
            self.running = True
            self._reader_thread = threading.Thread(
                target=self.read_audio_data,
                daemon=True
            )
            self._reader_thread.start()
        else:
            # Thread lebt noch, nur Flag setzen
            self.running = True

        # erste Aktualisierung anstoßen
        cb = getattr(self, "_update_bars_cb", None)
        if cb:
            cb()

    def on_page_hide(self):
        """Wird beim Verlassen der Page aufgerufen"""
        # Lokale Bilder freigeben
        # (ImageManager cached global, aber wir können Keys freigeben)
        
        # Optional: Nur wenn Page-spezifische Bilder geladen wurden
        # die NICHT in anderen Pages gebraucht werden
        pass  # Meist nicht nötig, da global gecacht


class AlsaEQController:
    """Steuert den systemweiten ALSA-Equalizer über Prozent (0..100%)."""

    def __init__(self):
        self.band_names = self._discover_band_names()
        if len(self.band_names) < 10:
            # Fallback – sollte normalerweise nicht nötig sein
            self.band_names = [
                "00. 31 Hz","01. 63 Hz","02. 125 Hz","03. 250 Hz","04. 500 Hz",
                "05. 1 kHz","06. 2 kHz","07. 4 kHz","08. 8 kHz","09. 16 kHz"
            ]

    def _run(self, cmd: str) -> str:
        try:
            return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            return ""

    def _discover_band_names(self) -> list[str]:
        out = self._run("amixer -D equal scontrols")
        names = []
        for line in out.splitlines():
            m = re.search(r"Simple mixer control '([^']+)'", line)
            if not m:
                m = re.search(r"[Ss]teuerung '([^']+)'", line)
            if m and ("Hz" in m.group(1) or "kHz" in m.group(1) or "Band" in m.group(1)):
                names.append(m.group(1))
        return names[:10]

    def _set_band_pct(self, band_idx: int, pct: float):
        pct = max(0, min(100, int(round(pct))))
        name = self.band_names[max(0, min(9, band_idx))]
        self._run(f"amixer -D equal sset '{name}' {pct}%")

    def set_pct_5band(self, p0, p1, p2, p3, p4):
        # 5→10 Mapping in Prozent
        self._set_band_pct(0, 50)                   # 31 Hz neutral
        self._set_band_pct(1, 50)                   # 63 Hz neutral
        self._set_band_pct(2, p0)                   # ~125 Hz
        self._set_band_pct(3, 0.6 * p1 + 0.4 * 50)  # 250 Hz
        self._set_band_pct(4, 0.4 * p1 + 0.6 * 50)  # 500 Hz
        self._set_band_pct(5, p2)                   # 1 kHz
        self._set_band_pct(6, 50)                   # 2 kHz neutral
        self._set_band_pct(7, p3)                   # 4 kHz
        self._set_band_pct(8, 0.7 * p4 + 0.3 * 50)  # 8 kHz
        self._set_band_pct(9, 0.3 * p4 + 0.7 * 50)  # 16 kHz

    def set_pct_10band(self, *pcts):
        """Setzt alle 10 Bänder direkt in Prozent (0..100)."""
        if len(pcts) != 10:
            raise ValueError("set_pct_10band erwartet 10 Werte.")
        for i, v in enumerate(pcts):
            self._set_band_pct(i, v)

__all__ = ["Page03"]