#!/usr/bin/env python3
# ('my_venv_314':venv)

# page_02.py

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

# - Dual-Channel Mixer
# - Sound Mixer DAB/Player
# - Visualisierung in Wellenform
# - MP3 Player mit üblichen Funktionen
# - Progressbar mit seek und scrub Funktion

import tkinter as tk
from tkinter import *
from tkinter import ttk, messagebox
from tkinter import filedialog
import subprocess, random
from .base_page import BasePage
from PIL import Image, ImageTk
import os, glob
os.environ.setdefault("MPLBACKEND", "TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import threading
import numpy as np
import shutil
import re

from mutagen.id3 import ID3, ID3NoHeaderError, APIC
from mutagen.easyid3 import EasyID3
import io


class Page02(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app = controller

        self.configure(bg='#90EE90')
        self.scale_active: bool = False

        self.build_gui()

    def build_gui(self):
        self.create_frames()
        self.load_images()
        self.create_channel_dab()
        self.create_channel_player()

        # alten Controller (falls vorhanden) entsorgen
        try:
            if hasattr(self, "player_controller") and self.player_controller:
                self.player_controller.destroy()
        except Exception:
            pass

        self.player_controller = Player_Controller(self)

    def create_frames(self):
        # Zielauflösung
        try:
            if hasattr(self.app, "geometry"):
                self.app.geometry("800x480")
        except Exception:
            pass

        # --- Seitenraster: 2 Spalten (50/50), 3 Zeilen (Header, Top, Bottom)
        for c in (0, 1):
            self.grid_columnconfigure(c, weight=1, uniform="col")
        self.grid_rowconfigure(0, minsize=25, weight=0)  # Überschrift 25px
        self.grid_rowconfigure(1, weight=1)              # obere Hälfte
        self.grid_rowconfigure(2, weight=1)              # untere Hälfte

        # Header (spannt über beide Spalten)
        if hasattr(self, "label") and self.label.winfo_exists():
            self.label.configure(font=("Helvetica", 25), background="#0C560C",
                                foreground="#DFFDDF", text="Player/Mixer")
        else:
            self.label = tk.Label(self, text="Player", font=("Helvetica", 25),
                                background="#0C560C", foreground="#C8CDF7")
        self.label.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW, padx=0, pady=0)

        # ---------- TOP ----------
        self.top_left_frame = tk.Frame(self, bg="#90EE90")
        self.top_left_frame.grid(row=1, column=0, sticky=tk.NSEW, padx=6, pady=(6, 3))
        self.top_left_frame.grid_columnconfigure(0, weight=1)
        self.top_left_frame.grid_rowconfigure(0, weight=1) # nur 1 Zeile: Cover

        # Cover-Show
        if not hasattr(self, "cover_frame") or not self.cover_frame.winfo_exists():
            self.cover_frame = tk.Frame(self.top_left_frame, bg="black")
            self.cover_frame.grid(row=0, column=0, sticky=tk.NSEW)

            self.cover_label = tk.Label(self.cover_frame, bg="black")
            self.cover_label.pack(expand=True, fill="both", pady=(5, 5))

            # für dynamisches Neuzeichnen bei Größenänderung:
            self._cover_last_pil = None
            self._cover_last_imgtk = None
            self.cover_frame.bind("<Configure>", lambda e: self._redraw_cover())
        else:
            self.cover_frame.grid(row=0, column=0, sticky=tk.NSEW)

        # rechts: Play-List (oben), darunter Progressbar, darunter Controls
        self.top_right_frame = tk.Frame(self, bg="#90EE90")
        self.top_right_frame.grid(row=1, column=1, sticky=tk.NSEW, padx=(3, 6), pady=(6, 3))
        self.top_right_frame.grid_columnconfigure(0, weight=1)
        self.top_right_frame.grid_rowconfigure(0, weight=1)   # Playlist
        self.top_right_frame.grid_rowconfigure(1, minsize=12) # Progressbar
        self.top_right_frame.grid_rowconfigure(2, weight=0)   # Controls

        # Container für rechts unten:
        self.right_progress_frame = tk.Frame(self.top_right_frame, bg="#90EE90")
        self.right_progress_frame.grid(row=1, column=0, sticky=tk.EW, padx=8, pady=(0, 0))
        self.right_progress_frame.grid_columnconfigure(0, weight=1)

        self.right_controls_frame = tk.Frame(self.top_right_frame, bg="#90EE90")
        self.right_controls_frame.grid(row=2, column=0, sticky=tk.EW, padx=8, pady=(0, 0))
        for c in range(7):
            self.right_controls_frame.grid_columnconfigure(c, weight=1)

        # --- BOTTOM ----------
        # links:
        self.bottom_left_frame = tk.Frame(self, bg="#90EE90")
        self.bottom_left_frame.grid(row=2, column=0, sticky=tk.NSEW, padx=6, pady=(3, 6))

        # rechts: Matplotlib-Widget
        self.bottom_right_frame = tk.Frame(self, bg="#90EE90")
        self.bottom_right_frame.grid(row=2, column=1, sticky=tk.NSEW, padx=(3, 6), pady=(3, 6))
        self.bottom_right_frame.grid_columnconfigure(0, weight=1)
        self.bottom_right_frame.grid_rowconfigure(0, weight=1)

    def load_images(self):
        self.dab_logo = Image.open(self.app.config_data["DAB_Logo"])
        self.dab_logo = self.dab_logo.resize((112, 72), Image.LANCZOS)
        self.Logo_DAB = ImageTk.PhotoImage(self.dab_logo)

        self.Verzeichnis_musik = Image.open(self.app.config_data["Logo_Player"])
        self.Verzeichnis_musik = self.Verzeichnis_musik.resize((112, 72), Image.LANCZOS)
        self.Music_Folder = ImageTk.PhotoImage(self.Verzeichnis_musik)

    def create_channel_dab(self):
        self.dab_image = tk.Label(self.bottom_left_frame, image=self.Logo_DAB, background="#0C290C", borderwidth=0)
        self.dab_image.image = self.Logo_DAB
        self.dab_image.grid(row=0, column=0, sticky=tk.NW, padx=(0,0), pady=(15,0))

        self.Frame_Kanal1_h1=ttk.Frame(self.bottom_left_frame, height=5, width=275, style='Kanal1v.TFrame')
        self.Frame_Kanal1_h1.grid(column=0, row=0, sticky=tk.NW, padx=(113, 0), pady=(50, 0)) 

        self.Checkbutton1 = tk.IntVar(value=1)
        self.Button1 = tk.Checkbutton(self.bottom_left_frame, variable=self.Checkbutton1, onvalue=1, offvalue=0, command=self.on_dab_toggle)
        self.Button1.grid(column=0, row=0, sticky=tk.NW, padx=(140, 0), pady=(42, 0))

        # Lautstärkeanzeige
        self.volume_dab = ttk.Label(self.bottom_left_frame, text=self.app.state.AktuelleLautstaerke_DAB, background='#90EE90')
        self.volume_dab.grid(column=0, row=0, sticky=tk.NW, padx=(250, 0), pady=(25, 0))
        # Lautstärkeregler
        self.scale_value_dab = tk.DoubleVar(value=self.app.state.AktuelleLautstaerke_DAB)
        self.scale_dab = ttk.Scale(self.bottom_left_frame, from_=0, to=100, orient='horizontal', style="Horizontal.TScale", length=120)
        self.scale_dab.grid(column=0, row=0, sticky=tk.NW, padx=(200, 0), pady=(45, 0))
        self.scale_dab.set(self.app.state.AktuelleLautstaerke_DAB)
        self.scale_dab.bind("<ButtonRelease-1>", self.on_release_volume_dab)

    def on_release_volume_dab(self, event):
        self.scale_active = False
        try:
            volume = round(self.scale_dab.get())
            self.app.state.AktuelleLautstaerke_DAB = volume
            self.volume_dab.config(text=f"{volume}")
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume") # Stellt die Lautstärke ein

        except Exception as e:
            print(f"[Fehler] Lautstärke konnte beim Loslassen nicht aktualisiert werden: {e}")

    def create_channel_player(self):
        self.Player_image = tk.Label(self.bottom_left_frame, image=self.Music_Folder, background="#0C290C", borderwidth=0)
        self.Player_image.image = self.Music_Folder
        self.Player_image.grid(row=0, column=0, sticky=tk.NW, padx=(0,0), pady=(103,0))
        self.Player_image.bind("<Button-1>", lambda e: self.player_controller.open_music_dialog())

        self.Frame_Kanal1_v2=ttk.Frame(self.bottom_left_frame, height=5, width=275, style='Kanal1v.TFrame')
        self.Frame_Kanal1_v2.grid(column=0, row=0, sticky=tk.NW, padx=(113, 0), pady=(138, 0))

        self.Checkbutton2 = tk.IntVar(value=1)
        self.Button2 = tk.Checkbutton(self.bottom_left_frame, variable=self.Checkbutton2, onvalue=1, offvalue=0, command=self.on_player_toggle)
        self.Button2.grid(column=0, row=0, sticky=tk.NW, padx=(140, 0), pady=(130, 0))

        self.volume_player = ttk.Label(self.bottom_left_frame, text=self.app.state.AktuelleLautstaerke_Player, background='#90EE90')
        self.volume_player.grid(column=0, row=0, sticky=tk.NW, padx=(250, 0), pady=(113, 0))

        # Lautstärke Player-Kanal
        self.scale_value_player = tk.DoubleVar(value=self.app.state.AktuelleLautstaerke_Player)
        self.scale_player = ttk.Scale(self.bottom_left_frame, from_=0, to=100, orient='horizontal', style="Horizontal.TScale", length=120)
        self.scale_player.grid(column=0, row=0, sticky=tk.NW, padx=(200, 0), pady=(133, 0))
        self.scale_player.set(self.app.state.AktuelleLautstaerke_Player)
        self.scale_player.bind("<ButtonRelease-1>", self.on_release_volume_player)

    def on_dab_toggle(self):
        if self.Checkbutton1.get() == 0:
            # deaktiviert → DAB auf 0 (nur Alsamixer , Player und Modul bleibt unberührt)
            self.app.state.dab_prev_volume = self.app.state.AktuelleLautstaerke_DAB
            self.app.state.AktuelleLautstaerke_DAB = 0
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume") # Stellt die Lautstärke ein

        else:
            # aktiviert → DAB auf dab_prev_volume (nur Alsamixer, Player und Modul bleibt unberührt)
            vol = self.app.state.dab_prev_volume if self.app.state.dab_prev_volume is not None else 0
            self.app.state.AktuelleLautstaerke_DAB = vol
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume") # Stellt die Lautstärke ein


    def on_player_toggle(self):
        if self.Checkbutton2.get() == 0:
            self.app.state.player_prev_volume = self.app.state.AktuelleLautstaerke_Player
            self.app.state.AktuelleLautstaerke_Player = 0
            self.player_controller.engine.set_player_volume(0)
        else:
            vol = self.app.state.player_prev_volume if self.app.state.player_prev_volume is not None else 0
            self.app.state.AktuelleLautstaerke_Player = vol
            self.player_controller.engine.set_player_volume(vol)

    def on_release_volume_player(self, event=None):
        self.scale_active = False
        try:
            volume = round(self.scale_player.get())
            self.app.state.AktuelleLautstaerke_Player = volume
            self.volume_player.config(text=volume)
            # NUR Player regeln:
            self.player_controller.engine.set_player_volume(volume)
        except Exception as e:
            print(f"[Fehler] Player-Volume: {e}")

    def update_cover_show(self, pil_img):
        # Altes Bild freigeben
        if self._cover_last_pil and self._cover_last_pil != pil_img:
            self._cover_last_pil.close()
        self._cover_last_pil = pil_img
        self._redraw_cover()


    def _redraw_cover(self):
        """Zeichnet das letzte Cover quadratisch, zentriert, mit 5px Top/Bottom."""
        pil = getattr(self, "_cover_last_pil", None)
        if pil is None:
            return # nichts zu zeichnen

        # verfügbare Größe im Frame
        w = max(0, int(self.cover_frame.winfo_width()))
        h = max(0, int(self.cover_frame.winfo_height()))

        if w < 10 or h < 10:
            self.after(50, self._redraw_cover)
            return

        side = min(w, max(1, h - 10))
        resized = pil.resize((side, side), Image.LANCZOS)

        imgtk = ImageTk.PhotoImage(resized)
        self._cover_last_imgtk = imgtk  # Referenz halten (sonst GC!)
        self.cover_label.configure(image=imgtk)
        try:
            self.cover_label.pack_configure(pady=(5, 5))
        except Exception:
            pass

    def update_volume_display(self):
        """Synchronisiert volume_dab und scale_dab mit app.state.AktuelleLautstaerke_DAB."""
        try:
            vol = int(round(getattr(self.app.state, "AktuelleLautstaerke_DAB", 50)))
        except Exception:
            vol = 50
        try:
            # Label und Slider aktualisieren (setzen des Sliders löst kein ButtonRelease-Event aus)
            if hasattr(self, "volume_dab"):
                self.volume_dab.config(text=vol)
            if hasattr(self, "scale_dab"):
                self.scale_dab.set(vol)
        except Exception as e:
            print(f"⚠️ Fehler beim Aktualisieren der Volume-Anzeige: {e}")

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

    def on_page_hide(self):
        """Wird beim Verlassen der Page aufgerufen"""
        # Lokale Bilder freigeben
        # (ImageManager cached global, aber wir können Keys freigeben)
        
        # Optional: Nur wenn Page-spezifische Bilder geladen wurden
        # die NICHT in anderen Pages gebraucht werden
        pass  # Meist nicht nötig, da global gecacht


class MPG123Engine:
    """Steuert mpg123 im Remote-Modus (-R) über stdin/stdout."""
    def __init__(self, alsa_device="wm8731"):
        self.alsa_device = alsa_device
        self.proc: subprocess.Popen | None = None

        self.on_track_end = None
        self._reader_thread: threading.Thread | None = None
        self._last_user_action: str | None = None # 'play' | 'pause' | 'stop' | None

        self._cur_frame: int | None = None
        self._total_frames: int | None = None
        self._length_ms: int | None = None   # geschätzt aus Frames
        self._samplerate: int | None = None  # optional, falls gemeldet
        self._mpeg_layer: int | None = None  # 1/2/3 (nur für Länge, optional)

        self._mpeg_version: float | None = None  # 1.0 / 2.0 / 2.5
        self._layer: int | None = None           # 1 / 2 / 3
        self._sr_from_output: bool | None = None

        self.on_track_start = None
        self._current_path: str | None = None
        self._start_notified = False

        self._frames_left: int = 0 

        # Regex-Pattern als Klassenvariablen kompilieren
        self.PATTERN_MPEG = re.compile(r"MPEG\s+([12](?:\.[05])?)")
        self.PATTERN_LAYER = re.compile(r"layer\s+(\d+|I{1,3})", re.IGNORECASE)
        self.PATTERN_SR_KHZ = re.compile(r"(\d+(?:\.\d+)?)\s*kHz", re.IGNORECASE)
        self.PATTERN_SR_HZ = re.compile(r"(\d{4,6})\s*Hz", re.IGNORECASE)
        self.PATTERN_FRAME = re.compile(r"@F\s+(\d+)\s*(?:/|\s)\s*(\d+)")

    def request_status(self):
        """Einmalig Positions-/Statusdaten anfordern (@F, @P, …)."""
        self._send("STAT")
        
    def start(self):
        """
        Startet mpg123 im Remote-Modus (-R) und gibt über ALSA aus.
        Prio der Ausgabegeräte:
        1) self.alsa_device (wenn gesetzt)
        2) MIXOUT   (Loopback-Ausgang aus /etc/asound.conf)
        3) wm8731   (dein Codec)
        4) default  (System-Default)
        """
        # Läuft schon?
        if getattr(self, "proc", None) and self.proc.poll() is None:
            return

        mpg = shutil.which("mpg123")
        if not mpg:
            messagebox.showerror(
                "mpg123 fehlt",
                "Das CLI-Programm 'mpg123' ist nicht installiert.\n"
                "Bitte mit 'sudo apt install mpg123' nachinstallieren."
            )
            raise FileNotFoundError("mpg123 binary not found")

        candidates = ["player_tap"]
        if getattr(self, "alsa_device", None):
            candidates.append(self.alsa_device)
        candidates += ["MIXOUT", "wm8731", "default"]

        try:
            devlist = subprocess.run(["aplay", "-L"], capture_output=True, text=True, check=True).stdout
        except Exception:
            devlist = ""

        target = next((d for d in candidates if d and d in devlist), candidates[-1])

        # Fixe Ausgabeparameter: 48kHz / s16 → passt zum Plot-Reader
        self.proc = subprocess.Popen(
                    [mpg, "-R", "-o", "alsa", "-a", target, "-r", "48000", "-e", "s16"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                    env=os.environ.copy(),
                )

        if not self._reader_thread or not self._reader_thread.is_alive():
            self._reader_thread = threading.Thread(target=self._read_stdout_loop, daemon=True)
            self._reader_thread.start()
        # Statusfluss aktivieren / anstoßen
        self._send("S")

    def _read_stdout_loop(self):
        """Liest Zeilen von mpg123 (-R). Reagiert auf Song-Ende."""
        if not self.proc or not self.proc.stdout:
            return
        try:
            for raw in self.proc.stdout:
                line = (raw or "").strip()
                if not line:
                    continue

                # --- Track-Ende
                if line.startswith("END"):
                    cb = self.on_track_end
                    if cb:
                        try: 
                            # Sicherstellen, dass GUI-Updates im Main-Thread erfolgen
                            if hasattr(cb, '__self__') and hasattr(cb.__self__, 'page'):
                                cb.__self__.page.after(0, cb)
                            else:
                                cb()
                        except Exception: pass
                    self._last_user_action = None
                    self._start_notified = False
                    self._current_path = None
                    continue

                # --- Play/Stop-Status
                if line.startswith("@P"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "1": # spielt
                        self._notify_track_start()
                        # NICHT zurücksetzen
                    elif len(parts) >= 2 and parts[1] == "0":
                        # Pause vs. echtes Ende vs. expliziter Stop unterscheiden
                        if self._last_user_action == "pause":
                            # echte Pause: nicht zum nächsten Titel springen
                            self._start_notified = False

                        elif self._last_user_action == "stop":
                            # expliziter Stop: ebenfalls NICHT weiter schalten
                            self._current_path = None
                            self._start_notified = False

                        else:
                            # echtes Ende (oder Stream abgerissen) → nächster Titel
                            cb = self.on_track_end
                            if cb:
                                try:
                                    cb()
                                except Exception:
                                    pass
                            self._current_path = None

                        self._last_user_action = None
                    continue

                # --- Sample-Rate (falls gemeldet)
                # Beispiele: "@S 48000 2" oder "@SAMPLE_RATE 44100"
                m = re.match(r"@S(?:AMPLE_RATE)?\s+(\d+)", line)
                if m and not self._samplerate:
                    try:
                        self._samplerate = int(m.group(1))
                        self._sr_from_output = True
                    except Exception:
                        pass
                    continue

                # --- MPEG-Info: Version, Layer (auch römisch), native SR
                if line.startswith("@I"):
                    # Version 1.0 / 2.0 / 2.5
                    vm = self.PATTERN_MPEG.search(line)
                    if vm:
                        try: self._mpeg_version = float(vm.group(1))
                        except Exception: pass

                    # Layer (Zahl oder römisch I/II/III)
                    lm = self.PATTERN_LAYER.search(line, re.IGNORECASE)
                    if lm:
                        tok = lm.group(1).upper()
                        self._layer = int(tok) if tok.isdigit() else {"I":1, "II":2, "III":3}.get(tok, None)

                    # native SR: "44.1 kHz" / "48000 Hz" etc.
                    sm = self.PATTERN_SR_KHZ.search(line, re.IGNORECASE)
                    if sm:
                        try:
                            self._samplerate = int(round(float(sm.group(1)) * 1000))
                            self._sr_from_output = False
                        except Exception:
                            pass
                    else:
                        sm = self.PATTERN_SR_HZ.search(line, re.IGNORECASE)
                        if sm:
                            try:
                                self._samplerate = int(sm.group(1))
                                self._sr_from_output = False
                            except Exception:
                                pass

                    # ggf. Länge mit korrektem SPF neu berechnen
                    if self._total_frames and self._samplerate:
                        sppf = self._samples_per_frame()
                        seconds = self._total_frames * (sppf / self._samplerate)
                        if seconds < 24*60*60:  # sanity
                            self._length_ms = int(seconds * 1000)
                    continue

                # --- Frame-Status: aktuelle / gesamte Frames
                # Häufige Varianten:
                # "@F <cur>/<total>" ODER "@F <cur> <total>" ODER "@F <cur>"
                if line.startswith("@F"): # erstes Fortschritts-Update: Track läuft sicher
                    self._notify_track_start()
                    m = self.PATTERN_FRAME.search(line)
                    if m:
                        cur = int(m.group(1))
                        oth = int(m.group(2))
                        self._cur_frame = cur

                        # Heuristik, ob 'oth' total oder rest ist:
                        is_total = (oth >= cur)
                        cand_total = oth if is_total else (cur + oth)

                        # monoton wachsende Obergrenze für total
                        if self._total_frames is None:
                            self._total_frames = cand_total
                        else:
                            self._total_frames = max(self._total_frames, cand_total, oth, cur + oth)

                        # verbleibende Frames merken
                        self._frames_left = (oth if not is_total else max(self._total_frames - cur, 0))

                    else:
                        m = re.match(r"@F\s+(\d+)", line)
                        if m:
                            self._cur_frame = int(m.group(1))
                            if self._total_frames:
                                self._frames_left = max(self._total_frames - self._cur_frame, 0)

                    # Länge (ms) berechnen, wenn SR bekannt – Restzeit wird gleich unabhängig aus Frames berechnet
                    if self._total_frames and self._samplerate and not (self._length_ms and self._length_ms > 0):
                        sppf = self._samples_per_frame()
                        seconds = self._total_frames * (sppf / self._samplerate)
                        if seconds < 24*60*60:
                            self._length_ms = int(seconds * 1000)
                    continue
        except Exception:
            pass

    def _send(self, line: str):
        if not self.proc or self.proc.poll() is not None:
            self.start()
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def load(self, path: str):
        self.start()
        self._current_path = path
        self._start_notified = False
        self._cur_frame = 0
        self._total_frames = None
        self._length_ms = None
        self._samplerate = None
        self._mpeg_version = None
        self._layer = None
        self._last_user_action = "play"
        self._send(f"LOAD {path}")

    def pause_toggle(self):
        self._last_user_action = "pause"
        self._send('PAUSE')

    def stop(self):
        self._last_user_action = "stop"
        self._send('STOP')

    def quit(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.terminate()
                self.proc = None
        except Exception:
            pass

    def set_player_volume(self, percent: int):
        p = max(0, min(100, int(percent)))
        self._send(f"VOLUME {p}") # mpg123 remote: VOLUME/V <0..100>

    def get_progress_percent(self) -> float | None:
        """0.0..1.0 Fortschritt; None wenn unbekannt."""
        if self._cur_frame is None: 
            return None
        if self._total_frames and self._total_frames > 0:
            return max(0.0, min(1.0, self._cur_frame / float(self._total_frames)))
        # ohne Total: keine verlässliche Prozentangabe
        return None

    def set_position(self, rel: float):
        """
        Springe relativ (0..1) im Track.
        Bevorzugt per Frames (JUMP <frame>), sonst per Sekunden (JUMP <sec>s).
        """
        rel = max(0.0, min(1.0, float(rel)))
        # 1) bevorzugt Frames (exakt im mpg123-Remote)
        if self._total_frames:
            target_frame = int(round(rel * self._total_frames))
            self._send(f"JUMP {target_frame}")
            return
        # 2) Fallback über Sekunden (Suffix 's' ist gültig)
        if self._length_ms:
            target_sec = int(round(rel * (self._length_ms / 1000.0)))
            self._send(f"JUMP {target_sec}s")

    def _samples_per_frame(self) -> int:
        # Layer I: 384; Layer II: 1152; Layer III: 1152 (MPEG-1) / 576 (MPEG-2/2.5)
        if self._layer == 1:
            return 384
        if self._layer == 3 and (self._mpeg_version or 0) >= 2.0:
            return 576
        return 1152

    def _notify_track_start(self):
        if not self._start_notified:
            self._start_notified = True
            cb = self.on_track_start
            if cb:
                try: cb(self._current_path)
                except Exception: pass

    def set_on_track_end(self, cb):
        self.on_track_end = cb

    def set_on_track_start(self, cb):
        self.on_track_start = cb


class Player_Controller:
    def __init__(self, page):
        self.page = page          # Page02-Instanz
        self.app  = page.app      # Root-App

        self.latest_signal = None
        self.waveform_thread = None
        self.waveform_running = False

        self._plot_job = None
        self._update_plot_cb = None
        self._progress_job = None

        # Plot vorbereiten (Figure/Canvas anlegen, aber noch NICHT starten)
        self.build_plot()
        # Player + Progressbar
        self.build_player()
        self.Progressbar()

    def build_player(self):
        # Rechte Seite: oben Playlist (Row 0 im top_right_frame)
        self.Frame_player = ttk.Frame(self.page.top_right_frame, style='frames.TFrame')
        self.Frame_player.grid(column=0, row=0, sticky=tk.NSEW, padx=0, pady=0)
        self.Frame_player.grid_columnconfigure(0, weight=1)
        self.Frame_player.grid_rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(self.Frame_player, columns=("title",), show="headings", selectmode="browse")
        self.tree.heading("title", text="Titel")
        self.tree.column("title", anchor="w", width=420, stretch=True)

        vsb = ttk.Scrollbar(self.Frame_player, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        self.tree.bind("<Double-1>", self.on_tree_double)

        # Player Engine + Zustand
        self.engine = MPG123Engine(alsa_device="player_tap")
        self.engine.start()
        self.engine.set_on_track_end(self._on_engine_track_end)
        self.engine.set_on_track_start(self._on_engine_track_start)

        self.build_controls()

    def _on_engine_track_end(self):
        self.page.after(0, self._advance_after_end)

    def _on_engine_track_start(self, path: str | None):
        self.page.after(0, lambda: self._kick_cover_lookup(path))

    # Wechsel zum nächsten Titel (Wrap am Ende)
    def _advance_after_end(self):
        if self.app.state.player_playlist:
            if hasattr(self, "progress"):
                self.progress.configure(value=0.0)
            self.next_track()

    def build_plot(self):
        # evtl. laufendes Update stoppen
        if getattr(self, "_plot_job", None):
            try:
                self.page.after_cancel(self._plot_job)
            except Exception:
                pass
            self._plot_job = None

        # Container (unten rechts)
        if hasattr(self, "Frame_plot") and self.Frame_plot.winfo_exists():
            for w in self.Frame_plot.winfo_children():
                w.destroy()
        else:
            self.Frame_plot = ttk.Frame(self.page.bottom_right_frame, style='frames.TFrame')
            self.Frame_plot.grid(column=0, row=0, sticky=tk.NSEW, padx=0, pady=0)

        # --- Figure + Achsen
        self._yspan = 1.0
        fig = Figure(figsize=(5, 3), dpi=100, facecolor="black")
        self.ax1 = fig.add_subplot(2, 1, 1)
        self.ax2 = fig.add_subplot(2, 1, 2, sharex=self.ax1)
        self.ax1.set_facecolor("black"); self.ax2.set_facecolor("black")
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0, hspace=0)

        self.ax1.clear(); self.ax2.clear()
        for ax in (self.ax1, self.ax2):
            ax.set_facecolor("black")
            ax.set_xticks([]); ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_visible(False)
            ax.grid(True, alpha=0.3)
            ax.set_ylim(-self._yspan, self._yspan)

        self.canvas = FigureCanvasTkAgg(fig, master=self.Frame_plot)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self._dab_file = os.path.expanduser("~/dab.raw")
        self._ply_file = os.path.expanduser("~/player.raw")
        self._sr = 48000
        self._ch = 2
        self._sec = 1.0
        self._plot_interval_ms = 200

        def _read_tail_pcm(path: str, seconds: float):
            try:
                if not os.path.exists(path):
                    return None
                n_frames = int(self._sr * seconds)
                bytes_per_sample = 4  # S32_LE = 4 Bytes pro Sample
                nbytes = n_frames * self._ch * bytes_per_sample
                with open(path, "rb") as f:
                    try:
                        size = os.fstat(f.fileno()).st_size
                        if size <= 0:
                            return None
                        if size > nbytes:
                            f.seek(-nbytes, os.SEEK_END)
                    except Exception:
                        pass
                    data = f.read()
                if not data:
                    return None
                arr = np.frombuffer(data, dtype=np.int32)  # S32_LE korrekt lesen
                if arr.size < self._ch:
                    return None
                frames = (arr.size // self._ch)
                arr = arr[:frames * self._ch].reshape(-1, self._ch)
                mono = arr.mean(axis=1).astype(np.float32) / 2147483648.0  # 2^31
                return mono
            except Exception:
                return None

        def _update_plot():
            dab = _read_tail_pcm(self._dab_file, self._sec)
            ply = _read_tail_pcm(self._ply_file, self._sec)
            
            # DAB: dab.raw kommt direkt vom ADC (vor SoftMaster) → Lautstärke simulieren
            dab_volume_factor = (self.app.state.AktuelleLautstaerke_DAB / 100.0) * 2.5
            if dab is not None:
                dab = dab * dab_volume_factor
            # Player: player.raw enthält bereits mpg123-VOLUME-skaliertes Signal → nur Anzeigeverstärkung
            if ply is not None:
                ply = ply * 2.5
            
            self.ax1.clear(); self.ax2.clear()
            for ax in (self.ax1, self.ax2):
                ax.set_facecolor("black")
                ax.set_xticks([]); ax.set_yticks([])
                for sp in ax.spines.values():
                    sp.set_visible(False)
            for ax, y in ((self.ax1, dab), (self.ax2, ply)):
                ax.grid(True, alpha=0.3)
                ax.set_ylim(-self._yspan, self._yspan)
                if y is not None and getattr(y, "size", 0):
                    x = np.linspace(0.0, y.size / self._sr, num=y.size, endpoint=False)
                    ax.plot(x, y, linewidth=0.8, color='cyan')
                    if x.size:
                        x_end = x[-1]
                        x_start = max(0.0, x_end - self._sec)
                        ax.set_xlim(x_start, x_end)
                else:
                    ax.set_xlim(0, self._sec)
                    ax.text(0.5, 0.5, "keine Daten", ha="center", va="center",
                            alpha=0.5, fontsize=9, transform=ax.transAxes)
            self.canvas.draw_idle()
            # nur weiter planen, wenn wir noch laufen sollen
            self._plot_job = self.page.after(self._plot_interval_ms, _update_plot)

        # Callback merken, aber NOCH NICHT starten
        self._update_plot_cb = _update_plot

    def build_controls(self):
        f = self.page.right_controls_frame
        for w in f.winfo_children():
            w.destroy()

        ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "Icons")

        self._icon_prev = Image.open(f"{ICON_DIR}/prev.png") # alle Icons auf 28x28px scalieren
        self._icon_prev = self._icon_prev.resize((28, 28), Image.LANCZOS)
        self.icon_prev = ImageTk.PhotoImage(self._icon_prev)

        self._icon_play_pa = Image.open(f"{ICON_DIR}/play_pause.png")
        self._icon_play_pa = self._icon_play_pa.resize((28, 28), Image.LANCZOS)
        self.icon_play_pa = ImageTk.PhotoImage(self._icon_play_pa)

        self._icon_play = Image.open(f"{ICON_DIR}/play.png")
        self._icon_play = self._icon_play.resize((28, 28), Image.LANCZOS)
        self.icon_play = ImageTk.PhotoImage(self._icon_play)

        self._icon_next = Image.open(f"{ICON_DIR}/next.png")
        self._icon_next = self._icon_next.resize((28, 28), Image.LANCZOS)
        self.icon_next = ImageTk.PhotoImage(self._icon_next)

        self._icon_repeat = Image.open(f"{ICON_DIR}/repeat.png")
        self._icon_repeat = self._icon_repeat.resize((28, 28), Image.LANCZOS)
        self.icon_repeat = ImageTk.PhotoImage(self._icon_repeat)

        self._icon_shuffle = Image.open(f"{ICON_DIR}/shuffle.png")
        self._icon_shuffle = self._icon_shuffle.resize((28, 28), Image.LANCZOS)
        self.icon_shuffle = ImageTk.PhotoImage(self._icon_shuffle)

        self._icon_folder_open = Image.open(f"{ICON_DIR}/folder_open.png")
        self._icon_folder_open = self._icon_folder_open.resize((28, 28), Image.LANCZOS)
        self.icon_folder_open = ImageTk.PhotoImage(self._icon_folder_open)

        self.btn_prev    = ttk.Button(f, image=self.icon_prev, width=4, command=self.prev_track)
        self.pl_pause    = ttk.Button(f, image=self.icon_play_pa, width=6, command=self.play_pause)
        self.btn_play    = ttk.Button(f, image=self.icon_play, width=4, command=self.play)
        self.btn_next    = ttk.Button(f, image=self.icon_next, width=4, command=self.next_track)
        self.btn_repeat  = ttk.Button(f, image=self.icon_repeat, width=4, command=self.toggle_repeat)
        self.btn_shuffle = ttk.Button(f, image=self.icon_shuffle, width=4, command=self.toggle_shuffle)
        self.btn_open_folder = ttk.Button(f, image=self.icon_folder_open, command=self.open_music_folder)

        # 7 Buttons in einer Reihe
        self.btn_prev.grid(   row=0, column=0, padx=(5, 2), pady=(2, 2))
        self.pl_pause.grid(   row=0, column=1, padx=(2, 2), pady=(2, 2))
        self.btn_play.grid(   row=0, column=2, padx=(2, 2), pady=(2, 2))
        self.btn_next.grid(   row=0, column=3, padx=(2, 2), pady=(2, 2))
        self.btn_repeat.grid( row=0, column=4, padx=(2, 2), pady=(2, 2))
        self.btn_shuffle.grid(row=0, column=5, padx=(2, 2), pady=(2, 2))
        self.btn_open_folder.grid(row=0, column=6, padx=(2, 5), pady=(2, 2))

    def open_music_dialog(self):
        initial = "/home/weilmy/My_smart_DAB_Radio/assets/sounds"
        files = filedialog.askopenfilenames(
            title="MP3 auswählen",
            initialdir=initial,
            filetypes=[("MP3 Dateien", "*.mp3")]
        )
        if files:
            self.load_playlist(list(files))

    def load_playlist(self, files: list[str]):
        self.tree.delete(*self.tree.get_children())
        self.app.state.player_playlist = files[:]
        self.app.state.player_current_index = -1
        for p in self.app.state.player_playlist:
            title = os.path.basename(p)
            self.tree.insert("", "end", values=(title,), tags=(p,))
        # nach Erstellen der Playlist sofort ersten Titel spielen
        if self.app.state.player_playlist:
            self.play_index(0)

    def on_tree_double(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        path = self.tree.item(item, "tags")[0]
        try:
            idx = self.app.state.player_playlist.index(path)
        except ValueError:
            return
        self.play_index(idx)

    def play_index(self, idx: int):
        if not self.app.state.player_playlist:
            return
        self.app.state.player_current_index = idx % len(self.app.state.player_playlist)
        path = self.app.state.player_playlist[self.app.state.player_current_index]
        for iid in self.tree.get_children(""):
            if path in self.tree.item(iid, "tags"):
                self.tree.selection_set(iid)
                self.tree.see(iid)
                break
        self.engine.load(path)

        if hasattr(self, "progress"):
            self.progress.configure(value=0.0)

    def play_pause(self):
        # Startet erstes Lied
        if self.app.state.player_current_index == -1 and self.app.state.player_playlist:
            self.play_index(0)
        else:
            self.engine.pause_toggle()

    def stop(self):
        self.engine.stop()

    def play(self):
        """
        Spielt den in der Playlist ausgewählten Titel.
        - Wenn in der Treeview etwas markiert ist → genau diesen Titel abspielen
        - Wenn nichts markiert ist:
            * bei bestehendem Index: aktuellen Titel abspielen
            * sonst: ersten Titel der Playlist abspielen
        """
        # 1) Auswahl in der Treeview verwenden (falls vorhanden)
        sel = self.tree.selection()
        if sel:
            iid = sel[0]
            tags = self.tree.item(iid, "tags")
            if tags:
                path = tags[0]
                try:
                    idx = self.app.state.player_playlist.index(path)
                except (ValueError, AttributeError):
                    idx = None
                if idx is not None:
                    self.play_index(idx)
                    return

        # 2) Kein selektierter Eintrag oder Pfad nicht in der Playlist
        if not self.app.state.player_playlist:
            # keine Playlist → nichts zu tun
            return

        # 3) Wenn schon ein Index existiert → diesen spielen
        if self.app.state.player_current_index >= 0:
            self.play_index(self.app.state.player_current_index)
        else:
            # noch kein Index → ersten Titel der Playlist spielen
            self.play_index(0)

    def next_track(self):
        if not self.app.state.player_playlist:
            return
        if self.app.state.player_shuffle:
            nxt = random.randrange(len(self.app.state.player_playlist))
            if len(self.app.state.player_playlist) > 1:
                while nxt == self.app.state.player_current_index:
                    nxt = random.randrange(len(self.app.state.player_playlist))
        else:
            nxt = (self.app.state.player_current_index + 1) % len(self.app.state.player_playlist)
        self.play_index(nxt)

    def prev_track(self):
        if not self.app.state.player_playlist:
            return
        prv = (self.app.state.player_current_index - 1) % len(self.app.state.player_playlist)
        self.play_index(prv)

    def toggle_repeat(self):
        self.app.state.player_repeat = not self.app.state.player_repeat
        self.btn_repeat.configure(style=("Accent.TButton" if self.app.state.player_repeat else "TButton"))

    def toggle_shuffle(self):
        self.app.state.player_shuffle = not self.app.state.player_shuffle
        self.btn_shuffle.configure(style=("Accent.TButton" if self.app.state.player_shuffle else "TButton"))

    def open_music_folder(self):
        initial = "/home/weilmy/My_smart_DAB_Radio/assets/sounds"
        folder = filedialog.askdirectory(title="Ordner mit MP3s wählen", initialdir=initial)
        if not folder:
            return
        files = sorted(glob.glob(os.path.join(folder, "*.mp3")))
        if not files:
            messagebox.showinfo("Keine Dateien", "In diesem Ordner wurden keine MP3s gefunden.")
            return
        self.load_playlist(files)

    def Progressbar(self):
        self.progress = ttk.Progressbar(
            self.page.right_progress_frame,
            length=1,
            mode="determinate",
            orient=tk.HORIZONTAL,
            style='text.Horizontal.TProgressbar'
        )
        self.progress.grid(row=0, column=0, sticky=tk.EW, padx=4, pady=(2, 2))
        self.page.right_progress_frame.grid_columnconfigure(0, weight=1)

        self.progress.bind("<Button-1>", self.seek_in_song)
        self.progress.bind("<B1-Motion>", self.scrub_in_song)
        self.progress.bind("<ButtonRelease-1>", self.seek_in_song)

        if not getattr(self, "_progress_job", None):
            self._tick_progress()

    def seek_in_song(self, event):
        width = max(1, self.progress.winfo_width())
        rel_position = min(max(event.x / width, 0.0), 1.0)
        self.engine.set_position(rel_position)
        self.progress.configure(value=rel_position * 100.0)

    def scrub_in_song(self, event):
        width = max(1, self.progress.winfo_width())
        rel_position = min(max(event.x / width, 0.0), 1.0)
        self.engine.set_position(rel_position)
        self.progress.configure(value=rel_position * 100.0)

    def _fmt_ms(self, ms: int) -> str:
        m = max(0, ms) // 60000
        s = (max(0, ms) % 60000) // 1000
        return f"{m:02d}:{s:02d}"

    def _tick_progress(self):
        try:
            self.engine.request_status()
            pct = self.engine.get_progress_percent()
            if pct is not None and hasattr(self, "progress"):
                self.progress.configure(value=pct * 100.0)
        except Exception:
            pass
        self._progress_job = self.page.after(300, self._tick_progress)

    def _kick_cover_lookup(self, path: str | None):
        if not path:
            return
        self.page.after(0, lambda: self._update_meta_and_cover(path))

    def _update_meta_and_cover(self, path: str):
        # a) Artist/Title/Album lesen
        artist, title, album = self._read_id3_artist_title_album(path)

        # b) eingebettetes Cover öffnen und anzeigen
        pil_img = self._read_embedded_cover_image(path)
        if pil_img:
            self.page.update_cover_show(pil_img)

    def _read_id3_artist_title_album(self, path: str) -> tuple[str, str, str]:
        artist = title = album = ""
        try:
            tags = EasyID3(path)
            artist = (tags.get("artist", [""])[0] or "").strip()
            title  = (tags.get("title",  [""])[0] or "").strip()
            album  = (tags.get("album",  [""])[0] or "").strip()
        except Exception:
            pass
        # Fallbacks aus Ordner-/Dateinamen
        if not artist:
            artist = os.path.basename(os.path.dirname(path))
        if not title:
            base = os.path.splitext(os.path.basename(path))[0]
            m = re.match(r"\s*\d+\s*-\s*(.+)$", base)
            title = (m.group(1) if m else base).strip()
        return artist, title, album

    def _read_embedded_cover_image(self, path: str):
        try:
            id3 = ID3(path)
            apics = [f for f in id3.values() if isinstance(f, APIC)]
            if not apics:
                return None
            # Bevorzuge Front-Cover (type == 3), sonst erstes Bild
            frame = next((f for f in apics if getattr(f, "type", None) == 3), apics[0])
            return Image.open(io.BytesIO(frame.data)).convert("RGB")
        except Exception:
            return None
        

    def destroy(self):
        """Stoppe Progress-Updates, Engine und Plot bevor ein neuer Controller erzeugt wird."""
        # Plot-Updates stoppen
        try:
            self.stop_plot()
        except Exception:
            pass

        # Progress-Updates stoppen
        try:
            if getattr(self, "_progress_job", None):
                try:
                    self.page.after_cancel(self._progress_job)
                except Exception:
                    pass
                self._progress_job = None
        except Exception:
            pass

        # Engine beenden
        try:
            if hasattr(self, "engine") and self.engine:
                self.engine.quit()
        except Exception:
            pass

        # Canvas zerstören
        try:
            if hasattr(self, "canvas"):
                widget = self.canvas.get_tk_widget()
                if widget and widget.winfo_exists():
                    widget.destroy()
                self.canvas = None
        except Exception:
            pass

    def stop_plot(self):
        """Stoppt die laufende Waveform-Aktualisierung."""
        if getattr(self, "_plot_job", None):
            try:
                self.page.after_cancel(self._plot_job)
            except Exception:
                pass
            self._plot_job = None

    def start_plot(self):
        """
        Startet die Waveform-Aktualisierung, falls noch nicht aktiv.
        Wird von apply_resource_profile() aufgerufen, wenn Page02 aktiv ist
        und PAGE_PROFILES['Page02'].mpl_page02 == True.
        """
        if getattr(self, "_plot_job", None):
            # schon aktiv
            return

        # Falls der Callback noch nicht existiert, Plot-Struktur neu aufbauen
        if not getattr(self, "_update_plot_cb", None):
            self.build_plot()

        cb = getattr(self, "_update_plot_cb", None)
        if cb:
            cb()

__all__ = ["Page02"]