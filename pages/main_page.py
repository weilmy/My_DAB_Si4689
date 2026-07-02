#!/usr/bin/env python3
# ('my_venv_314':venv)

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

# Senderwahl mittels Senderliste, Favoriten, Chartliste, Programmtyp, Sendersuchfunktion und Zufallsgenerator
# Senderlogo, Cover und Slideshow
# Zustandsanzeige: Sendername, Typ, Status und Signalstärke
# DLS Anzeige mit Newslaufschrift (tagesschau.de) und Artist-Biografie
# DLS Analyse für Datenspeicherung Sender/Artist/Titel/Genre
# Vor-/Rückwärtswahl gewählter Sender

import tkinter as tk
from tkinter import *
import tkinter.ttk as ttk
import time
import json
from collections import Counter
import re
import tkinter.font as tkfont
import hashlib
import datetime as dt
from PIL import Image, ImageTk, UnidentifiedImageError
import os
import sqlite3
import requests
from io import BytesIO
import traceback
from datetime import datetime
from collections import OrderedDict
import random
import feedparser
import threading

from concurrent.futures import ThreadPoolExecutor
from utils.helper import ChipButton, Cover_url, Bio_url

class MainPage(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.app = controller
        self.configure(bg='#237E71')
        self.style = ttk.Style()

        self.gui_controller    = GUIController(self)
        self.data_controller   = DataController(self)
        self.image_manager     = ImageManager(self)     # Bildanzeige für alle GUI-Elemente
        self.news_manager      = NewsManager(self)      # Newslogik & Threads
        self.search_controller = SearchController(self) # Suchfunktion
        self.dls_manager       = DlsManager(self)       # Organisiert Musiktextanzeige

        self.sorted_Type_Label: list[str] = []
        self.Label_Typ:         list[str] = []
        self.Label_Typ_org:     list[str] = []

        self.build_gui()

    def build_gui(self):
        self.load_images()
        self.create_frames()
        self.gui_controller.build_gui()
        self.create_navigation_controls()
        self.create_mute_toggle()

    def load_images(self):
        """Lädt Bilder mit ImageManager (automatisches Cleanup)"""
        cfg = self.app.config_data
        img_mgr = self.app.image_manager
        
        # Simple PhotoImages
        self.mute_image = img_mgr.load_photoimage('main_mute', cfg["mute_icon"])
        self.un_mute_image = img_mgr.load_photoimage('main_unmute', cfg["unmute_icon"])
        self.previous_image = img_mgr.load_photoimage('previous_icon', cfg['previous_icon'])
        self.next_image = img_mgr.load_photoimage('next_icon', cfg['next_icon'])

        # Mit Resize (nutzt PIL intern)
        self.status_red = img_mgr.load_image('main_red', cfg["status_red"], resize=(52, 17))
        self.status_yellow = img_mgr.load_image('main_yellow', cfg["status_yellow"], resize=(52, 17))
        self.status_green = img_mgr.load_image('main_green', cfg["status_green"], resize=(52, 17))
        self.tree_points = img_mgr.load_image('main_drei_punkte', cfg["drei_punkte"], resize=(25, 14))
        self.zeiger_dreieck = img_mgr.load_image('main_zeiger_dreieck', cfg["zeiger_dreieck"], resize=None)
        self.kanal_skala = img_mgr.load_image('main_kanalskala', cfg["kanalskala"], resize=(780, 72))
        self.reload_dreieck = img_mgr.load_image('main_reload_dreieck', cfg["reload_dreieck"], resize=None)
        self.signal_dreieck = img_mgr.load_image('main_signal_dreieck', cfg["signal_dreieck"], resize=None)
    
    def create_frames(self):
        # Info (links)
        self.Info_frame = ttk.Frame(self, width=234, height=276, style='Frame2.TFrame')
        self.Info_frame.grid(column=0, row=1, sticky=tk.NW)
        self.Info_frame.grid_propagate(False)

        # Listbox (mitte)
        self.ListBox_frame = ttk.Frame(self, width=455, height=276, style='Frame2.TFrame')
        self.ListBox_frame.grid(column=1, row=1, sticky=tk.NW)
        self.ListBox_frame.grid_propagate(False)

        # Spaltenbreiten für Zeile 0 konfigurieren (Radiobuttons + Label für konstannte Breiten unabhängig Sendername)
        self.ListBox_frame.grid_columnconfigure(0, minsize=110) # Label "Senderliste"
        self.ListBox_frame.grid_columnconfigure(1, minsize=110) # Label Favorit, Chart und Typ

        # Volume (rechts)
        self.Volume_frame = ttk.Frame(self, width=101, height=276, style='Frame2.TFrame')
        self.Volume_frame.grid(column=2, row=1, sticky=tk.NE)
        self.Volume_frame.grid_propagate(False)

        # Button-Leiste unten über ganze Breite
        self.Button_frame = ttk.Frame(self, width=785, height=75, style='Frame2.TFrame')
        self.Button_frame.grid(column=0, row=2, columnspan=3, sticky=tk.NW)
        self.Button_frame.grid_propagate(False)

        # Settings-Leiste unten über ganze Breite
        self.settings_frame = ttk.Frame(self, width=800, height=92, style='Frame2.TFrame')
        self.settings_frame.grid(column=0, row=3, columnspan=3, sticky=tk.NW)
        self.settings_frame.grid_propagate(False)

        # Reset-Status und Frame für Abfrage zum Löschen der Chartlist
        self.reset_confirm = False
        self.confirm_frame = tk.Frame(self.Button_frame)

        # Linker Bereich – orange
        self.menu_left = tk.Frame(self, width=234, height=75, bg='orange')
        self.menu_left.grid(row=0, column=0, sticky="nsw")
        self.menu_left.grid_propagate(False)

        # Mittlerer Bereich – blaugrau
        self.menu_middle = tk.Frame(self, width=455, height=75, bg='#547a94') # blaugrau
        self.menu_middle.grid(row=0, column=1, sticky=tk.NS)
        self.menu_middle.grid_propagate(False)
        self.menu_middle.grid_columnconfigure(0, weight=1)
        self.menu_middle.grid_rowconfigure(0, weight=1)
        self.menu_middle.grid_rowconfigure(1, weight=1)

        # Rechter Bereich – hellblau
        self.menu_right = tk.Frame(self, width=96, height=75, bg='#237E71')
        self.menu_right.grid(row=0, column=2, sticky=tk.NS)

    def create_mute_toggle(self):
        self.is_mute = False
        self.mute_un_mute_image = tk.Label(self.Volume_frame, image=self.un_mute_image, borderwidth=0)
        self.mute_un_mute_image.image = self.un_mute_image
        self.mute_un_mute_image.grid(column=0, row=1, sticky=tk.NW, padx=(20, 0), pady=(10, 0))
        self.mute_un_mute_image.bind("<Button-1>", self.mute_un_mute)

    def create_navigation_controls(self):
        # Vorheriger Sender
        self.previous_sender_label = tk.Label(self.menu_right, image=self.previous_image, cursor="hand2", borderwidth=0)
        self.previous_sender_label.image = self.previous_image
        self.previous_sender_label.grid(column=0, row=0, sticky=tk.NE, padx=(0, 20), pady=(3, 0))
        self.previous_sender_label.bind("<Button-1>", self.data_controller.previous_sender)

        # Nächster Sender
        self.next_sender_label = tk.Label(self.menu_right, image=self.next_image, cursor="hand2", borderwidth=0)
        self.next_sender_label.image = self.next_image
        self.next_sender_label.grid(column=0, row=0, sticky=tk.NE, padx=(0, 20), pady=(30, 0))
        self.next_sender_label.bind("<Button-1>", self.data_controller.next_sender)

    def mute_un_mute(self, event: tk.Event) -> None:
        self.is_mute = not self.is_mute

        if self.is_mute:
            # 🔇 Mute
            self.mute_un_mute_image.config(image=self.mute_image)
            self.app.dispatcher.submit(lambda: self.app.volume_service(0), key="volume") # Stellt die Lautstärke auf 0
        else:
            # 🔊 Unmute (aktuelle Lautstärke wieder herstellen)
            self.mute_un_mute_image.config(image=self.un_mute_image)
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume") # Stellt die Lautstärke wieder ein

    def get_random(self):
        import random
        if not self.app.state.Sender_Name: return
        idx = random.randrange(len(self.app.state.Sender_Name))
        self.app.dispatcher.submit(lambda i=idx: self.app.tune_service(i), key="tune")

    def activate(self):
        """Wird aufgerufen, wenn MainPage sichtbar wird. Aktualisiert die Volume-Widgets."""
        try:
            vol = int(round(getattr(self.app.state, "AktuelleLautstaerke_DAB", 0)))
        except Exception:
            vol = 0
        try:
            gc = getattr(self, "gui_controller", None)
            if gc:
                # Label aktualisieren
                try:
                    gc.lautst_label.config(text=f"Volume {vol}")
                except Exception:
                    pass
                try:
                    gc.volumen_scale.set(vol)
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ Fehler in MainPage.activate(): {e}")

    def on_page_hide(self):
        """Wird beim Verlassen der Page aufgerufen"""
        pass  # Meist nicht nötig, da global gecacht

    def open_page09(self, event=None):
        """Öffnet die Einstellungen-Seite (Page09)."""
        try:
            self.app.show_page("Page09")
        except Exception as e:
            print(f"⚠️ Konnte Page09 nicht öffnen: {e}")


class GUIController:
    # ── Kanalzeiger-Positionen (Y ist fix=18, X variiert) ──────────────────────
    CHANNEL_ARROW_X: dict[str, int] = {
        "5A": 24,  "5B": 24,  "5C": 48,  "5D": 72,
        "6A": 96, "6B": 120, "6C": 144, "6D": 168,
        "7A": 192, "7B": 216, "7C": 240, "7D": 264,
        "8A": 388, "8B": 312, "8C": 336, "8D": 360,
        "9A": 384, "9B": 408, "9C": 432, "9D": 456,
        "10A": 480, "10B": 504, "10C": 528, "10D": 552,
        "11A": 576, "11B": 600, "11C": 624, "11D": 648,
        "12A": 672, "12B": 696, "12C": 720, "12D": 744,
    }
    CHANNEL_ARROW_Y: int = 5   # konstant, vertikal zentriert auf der Skala
    _CANVAS_X:       int = 10  # Canvas-Ursprung X relativ zur settings_frame (= padx der Skala)
    _CANVAS_Y:       int = 7   # Canvas-Ursprung Y relativ zur settings_frame (= pady der Skala)
    _SCALE_OFFSET_Y: int = 13  # Skalenbild im Canvas nach unten (24 - 5), bleibt auf gleicher Höhe

    def __init__(self, page):
        self.page = page          # MainPage-Instanz
        self.app  = page.app      # Root-App (enthält Sender_Id, Sender_Name, si4689_driver, audio_codec_hifiberry, DLS-Text)
        self._reload_blink_id: object = None    # after()-ID des laufenden Reload-Blink-Timers
        self._reload_blink_state: bool = False  # True = reload_dreieck, False = zeiger_dreieck
        self._signal_blink_id: object = None    # after()-ID des laufenden Signal-Blink-Timers
        self._signal_blink_state: bool = False  # True = signal_dreieck, False = zeiger_dreieck

    def build_gui(self):
        self.sender_display()
        self.create_listboxes()
        self.create_radio_buttons()
        self.update_visible_listbox()
        self.page.data_controller.ReadStore()         # Favoriten laden & anzeigen
        self.page.data_controller.load_sender_chart() # Cartliste laden & anzeigen
        self.create_buttons()
        self.create_volume_controls()
        self.create_signal_bar()
        self.create_logo_display()
        self.create_footer_display()
        self.create_datetime_labels()
        self.update_datetime()
        self.page.search_controller.create_search_controls()

    def sender_display(self):
        self.page.menu_left.grid_rowconfigure(0, weight=1)
        self.page.menu_left.grid_columnconfigure(0, weight=1)
        self.sender_name_label = tk.Label(
            self.page.menu_left,
            text="Sendername",
            font=('Arial', 14, 'bold italic'),
            foreground="#d9bfe7",          # Textfarbe
            background='orange',             # Hintergrund
            relief=tk.RIDGE,                 # sichtbarer Rahmen
            bd=2,                            # Innenrahmen
            highlightbackground="#d9bfe7", # Rahmenfarbe außen
            highlightthickness=1,            # Dicke des farbigen Rahmens
            anchor='center'                  # Text zentriert im Label
        )
        self.sender_name_label.grid(column=0, row=0, sticky=tk.NSEW, padx=10, pady=20)
        self.sender_name_label.config(text="Sender wird gewählt", foreground="#d9bfe7", highlightbackground="#d9bfe7")

        # Program Typ
        self.programtyp=ttk.Label(self.page.menu_left, text="", font=('Calibri',9), foreground="blue", background='orange')
        self.programtyp.grid(column=0, row=0, sticky=tk.NW, padx=10, pady=0)

        # Status Mono/Stereo
        self.stereo_label = ttk.Label(self.page.menu_left, text="Stereo", font=('Calibri', 9), foreground="blue", background='orange')
        self.stereo_label.grid(column=0, row=0, sticky=tk.NE, padx=(0, 10), pady=(0, 0))

        # Ensemble_Sender
        self.Ensemble_Sender=ttk.Label(self.page.menu_left, text="-", font=('Calibri',9), foreground="blue", background='orange')
        self.Ensemble_Sender.grid(column=0, row=0, sticky=tk.SW, padx=10, pady=0)

        # si4689-Status-Ampel
        self.status_image =ttk.Label(self.page.menu_left, image=self.page.status_red)
        self.status_image.grid(column=0, row=0, sticky=tk.SE, padx=(10, 10), pady=(0, 0))

        # Musiktitel
        self.musiktitel_up_label = ttk.Label(self.page.menu_middle, text="", font=('Quicksand Medium', 12), foreground="yellow", background='#547a94')
        self.musiktitel_up_label.grid(column=0, row=0, sticky=tk.NSEW, padx=7, pady=(10, 0))

        # Für normale DLS-Anzeige
        self.musiktitel_down_label = ttk.Label(self.page.menu_middle,text="",font=('Quicksand Medium', 12),foreground="yellow",background='#547a94')
        self.musiktitel_down_label.grid(column=0, row=1, sticky=tk.SW, padx=7, pady=(0, 10))

        # Für News-Lauftext
        self.musiktitel_canvas = tk.Canvas(self.page.menu_middle,height=24,width=455,bg="#547a94",highlightthickness=0)
        self.musiktitel_canvas.grid(column=0, row=1, sticky=tk.SW, padx=5, pady=(15, 0))
        self.musiktitel_canvas.grid_remove()
        self.musiktitel_canvas.bind("<Button-1>", self.page.news_manager.on_news_click)

    def create_listboxes(self):
        # Senderliste
        self.lboxSenderliste = Listbox(self.page.ListBox_frame, height=12)
        self.lboxSenderliste.bind("<<ListboxSelect>>", self.page.data_controller.on_select_SenderListe)
        self.lboxSenderliste.grid(column=0, row=1, sticky=tk.NW, padx=(10, 0), pady=(5, 0))
        # Jede zweite Zeile in der Listbox einfärben
        self.scbar1 = ttk.Scrollbar(self.page.ListBox_frame, orient=VERTICAL, command=self.lboxSenderliste.yview)
        self.scbar1.grid(column=0, row=1, sticky=NS, padx=(175, 0), pady=(5, 0))
        self.lboxSenderliste['yscrollcommand'] = self.scbar1.set

        # Favoriten (Read)
        self.lboxRead = Listbox(
            self.page.ListBox_frame,
            width=19, height=12, justify=CENTER,
            selectmode=tk.SINGLE, exportselection=False
        )
        self.lboxRead.bind("<ButtonPress-1>", self.page.data_controller.mausklick_Read)
        self.lboxRead.bind("<ButtonRelease-1>", self.page.data_controller.on_release_Read)
        self.lboxRead.grid(column=1, row=1, sticky=NW, padx=(10, 0), pady=(5, 0))
        # Inhalte werden von DataController.ReadStore() befüllt.
        self.lboxRead.grid_remove()

        # Jede zweite Zeile in der Listbox einfärben
        self.lboxRead.delete(0, 'end')
        for i, item in enumerate(self.page.data_controller.SaveSenderList):
            self.lboxRead.insert('end', item)
            if i % 2 == 0:
                self.lboxRead.itemconfigure(i, background='#f2c9f1')
        self.scbar3 = ttk.Scrollbar(self.page.ListBox_frame, orient=VERTICAL, command=self.lboxRead.yview)
        self.scbar3.grid(column=1, row=1, sticky=NS, padx=(160, 0), pady=(5, 0))
        self.scbar3.grid_remove()
        self.lboxRead['yscrollcommand'] = self.scbar3.set

        # Chartliste
        self.lboxChart = Listbox(self.page.ListBox_frame, width=19, height=12, justify=LEFT)
        self.lboxChart.bind("<<ListboxSelect>>", self.page.data_controller.on_select_Chart)
        self.lboxChart.grid(column=1, row=1, sticky=NW, padx=(10, 0), pady=(5, 0))
        self.lboxChart.grid_remove()
        self.scbar4 = ttk.Scrollbar(self.page.ListBox_frame, orient=VERTICAL, command=self.lboxChart.yview)
        self.scbar4.grid(column=1, row=1, sticky=NS, padx=(160, 0), pady=(5, 0))
        self.lboxChart['yscrollcommand'] = self.scbar4.set
        self.scbar4.grid_remove()

        # Typ-Liste
        self.lboxTyp = Listbox(self.page.ListBox_frame, width=19, height=12, justify=CENTER)
        self.lboxTyp.bind("<<ListboxSelect>>", self.page.data_controller.on_select_Typ)
        self.lboxTyp.grid(column=1, row=1, sticky=NW, padx=(10, 0), pady=(5, 0))
        self.scbar5 = ttk.Scrollbar(self.page.ListBox_frame, orient=VERTICAL, command=self.lboxTyp.yview)
        self.scbar5.grid(column=1, row=1, sticky=NS, padx=(160, 0), pady=(5, 0))

        self.lboxTyp['yscrollcommand'] = self.scbar5.set

    def create_radio_buttons(self):
        # Variable für Auswahl (tk.StringVar)
        self.listbox_selection = tk.StringVar(value="chart")  # default: chart
 
        # ----- Sortier-State initialisieren -----
        self.sort_state = "index"          # "index" | "asc" | "desc"
        self._lbox_index_map: list[int] = []  # Position → original DAB-Index
 
        # Sub-Frame: "Senderliste" + Sort-Pfeil nebeneinander
        sl_header_frame = ttk.Frame(self.page.ListBox_frame)
        sl_header_frame.grid(column=0, row=0, sticky=tk.W, padx=(10, 0), pady=(5, 0))
 
        text1 = ttk.Label(sl_header_frame, text="Senderliste", font=('Calibri', 11), foreground="lightblue", background='#237E71')
        text1.pack(side=tk.LEFT)
 
        self.text2 = ttk.Label(sl_header_frame, text="  -", font=('Calibri', 11, 'bold'), foreground="lightblue", background='#237E71', cursor="hand2")
        self.text2.pack(side=tk.LEFT, padx=(0, 0))
        self.text2.bind("<Button-1>", self._toggle_sort)
 
        # Frame für die Radiobuttons
        rb_frame = ttk.Frame(self.page.ListBox_frame)
        rb_frame.grid(column=1, row=0, sticky=tk.W, padx=(0, 0), pady=(5, 0))
 
        # Radiobutton Favorit
        self.rb_Favorit = ttk.Radiobutton(
            rb_frame, text="Favorit ", value="read",
            style='my_01.TRadiobutton',
            variable=self.listbox_selection,
            command=self.update_visible_listbox
        )
        self.rb_Favorit.pack(side=tk.LEFT, padx=(0))
 
        # Radiobutton Chart
        self.rb_Chart = ttk.Radiobutton(
            rb_frame, text="Chart ", value="chart",
            style='my.TRadiobutton',
            variable=self.listbox_selection,
            command=self.update_visible_listbox
        )
        self.rb_Chart.pack(side=tk.LEFT, padx=0)
 
        # Radiobutton Typ
        self.rb_Typ = ttk.Radiobutton(
            rb_frame, text="Typ", value="typ",
            style='my_01.TRadiobutton',
            variable=self.listbox_selection,
            command=self.update_visible_listbox
        )
        self.rb_Typ.pack(side=tk.LEFT, padx=0)

    def _toggle_sort(self, event=None):
        """Wechselt den Sortierstatus: index → asc → desc → asc → ..."""
        if self.sort_state == "index":
            self.sort_state = "asc"
        elif self.sort_state == "asc":
            self.sort_state = "desc"
        else:
            self.sort_state = "asc"
        self._sort_senderliste()
 
    def _sort_senderliste(self):
        """Baut lboxSenderliste anhand des aktuellen sort_state neu auf
        und aktualisiert _lbox_index_map (Position → original DAB-Index)."""
        names = self.app.state.Sender_Name
        if not names:
            return
 
        if self.sort_state == "index":
            indices = list(range(len(names)))
            symbol  = " -"
        elif self.sort_state == "asc":
            indices = sorted(range(len(names)), key=lambda i: names[i].casefold())
            symbol  = "  ↓"
        else:
            indices = sorted(range(len(names)), key=lambda i: names[i].casefold(), reverse=True)
            symbol  = "  ↑"
 
        self._lbox_index_map = indices
 
        # Pfeil-Label aktualisieren (guard, falls text2 noch nicht existiert)
        try:
            self.text2.config(text=symbol)
        except AttributeError:
            pass
 
        # Listbox neu befüllen
        lb = self.lboxSenderliste
        lb.delete(0, tk.END)
        for pos, orig_idx in enumerate(indices):
            lb.insert(tk.END, names[orig_idx])
            if pos % 2 == 0:
                lb.itemconfig(pos, {'bg': '#f2c9f1'})
 

    def update_visible_listbox(self):
        # Alles verstecken
        for lb, sc in [(self.lboxChart, self.scbar4),
                   (self.lboxRead, self.scbar3),
                   (self.lboxTyp, self.scbar5)]:
            lb.grid_remove()
            sc.grid_remove()
        auswahl = self.listbox_selection.get()
        if auswahl == "chart":
            self.rb_Favorit.configure(style="my_01.TRadiobutton")
            self.rb_Chart.configure(style="my.TRadiobutton")
            self.rb_Typ.configure(style="my_01.TRadiobutton")
            self.lboxChart.grid()
            self.scbar4.grid()
        elif auswahl == "read":
            self.rb_Favorit.configure(style="my.TRadiobutton")
            self.rb_Chart.configure(style="my_01.TRadiobutton")
            self.rb_Typ.configure(style="my_01.TRadiobutton")
            self.lboxRead.grid()
            self.scbar3.grid()
        elif auswahl == "typ":
            self.rb_Favorit.configure(style="my_01.TRadiobutton")
            self.rb_Chart.configure(style="my_01.TRadiobutton")
            self.rb_Typ.configure(style="my.TRadiobutton")
            self.lboxTyp.grid()
            self.scbar5.grid()

    def create_buttons(self):
        # Platzhalter
        self.textx = ttk.Label(self.page.Button_frame, text="                                          ", font=('Calibri', 11), foreground="#547a94", background='#237E71')
        self.textx.pack(side=tk.LEFT, padx=(10))
        base_bg = self.page.cget('bg')  # "#237E71" aus MainPage

        self.button1 = ChipButton(self.page.Button_frame, text='Cover', command=lambda: self.page.image_manager.handle_cover_button(), base_bg=base_bg)
        self.button1.pack(side=tk.LEFT, padx=(10, 0), pady=(6, 8))

        self.button2 = ChipButton(self.page.Button_frame, text='Search', command=lambda: self.page.search_controller.Search_Sender(), base_bg=base_bg)
        self.button2.pack(side=tk.LEFT, padx=(10), pady=(6, 8))

        self.button3 = ChipButton(self.page.Button_frame, text='Random', command=lambda: self.page.get_random(), base_bg=base_bg)
        self.button3.pack(side=tk.LEFT, padx=(10), pady=(6, 8))

        self.button4 = ChipButton(self.page.Button_frame, text='Reset', command=self.Chart_Reset, base_bg=base_bg)
        self.button4.pack(side=tk.LEFT, padx=(10, 30), pady=(6, 8))

    def create_volume_controls(self):
        # Lautstärkeanzeige
        self.lautst_label = ttk.Label(self.page.Volume_frame, text=f"Volume= {self.app.state.AktuelleLautstaerke_DAB}", font=('Calibri', 11), foreground="lightblue", background='#237E71')
        self.lautst_label.grid(column=0, row=0, sticky=tk.NW, padx=(0, 0), pady=(10, 0))
        # Lautstärkeregler
        self.scale_value = tk.DoubleVar(value=self.app.state.AktuelleLautstaerke_DAB)
        self.volumen_scale = ttk.Scale(self.page.Volume_frame, from_=100, to=0, orient='vertical', style="Vertical.TScale", length=180, command=self.page.data_controller.volume_activated)
        self.volumen_scale.grid(column=0, row=0, sticky=tk.NW, padx=(35, 0), pady=(40, 0))
        self.volumen_scale.set(self.app.state.AktuelleLautstaerke_DAB)
        self.volumen_scale.bind("<ButtonPress-1>", self.page.data_controller.on_press_volume)
        self.volumen_scale.bind("<ButtonRelease-1>", self.page.data_controller.on_release_volume)

    def create_signal_bar(self):
        self.progressbar = ttk.Progressbar(self.page.Info_frame, length=230, mode="determinate", orient=tk.HORIZONTAL, style='text.Horizontal.TProgressbar')
        self.progressbar.grid(column=0, row=0, sticky=tk.NW, padx=(2, 0), pady=(0, 0))

    def create_logo_display(self):
        # Hintergrund für Senderlogo und Cover
        self.logo_label = ttk.Label(self.page.Info_frame, anchor="center", background='#237E71')
        self.logo_label.grid(column=0, row=0, sticky=tk.NW, padx=(3, 0), pady=(7, 0))

        # Modusanzeige für Logo/Cover/Slidesshow/ oder Coverbildname
        self.Sender_logo = ttk.Label(self.page.Info_frame, text="", font=('Calibri', 9),foreground="lightblue", background='#237E71')
        self.Sender_logo.grid(column=0, row=0, sticky=tk.NW, padx=(3, 0), pady=(8, 0))

    def create_footer_display(self):
        # Einstellungen
        self.settings=tk.Label(self.page.settings_frame, image=self.page.tree_points, cursor="hand2", borderwidth=0)
        self.settings.image=self.page.tree_points
        self.settings.grid(column=0, row=0, sticky=tk.NW, padx=10, pady=(0, 0))
        self.settings.bind("<Button-1>", self.page.open_page09)

        # Kanalskala + Kanalzeiger gemeinsam auf einem Canvas
        # -> transparente PNGs werden echt überlagert, kein bg-Rechteck mehr
        self.scale_canvas = tk.Canvas(
            self.page.settings_frame,
            width=780, height=91,          # vorher 72  (+19 für den Text)
            highlightthickness=0, borderwidth=0,
            bg='#237E71',
        )
        self.scale_canvas.grid(column=0, row=0, sticky=tk.NW, padx=(10, 0), pady=(5, 0))  # vorher (24,0)

        # Skalenbild 19px nach unten -> bleibt bildschirm-identisch zu vorher
        self.scale_canvas.create_image(0, self._SCALE_OFFSET_Y, anchor=tk.NW, image=self.page.kanal_skala)

        # Kanalzeiger – jetzt komplett sichtbar inkl. Textbereich oben
        self._arrow_item = self.scale_canvas.create_image(
            self.CHANNEL_ARROW_X["5A"] - self._CANVAS_X,
            self.CHANNEL_ARROW_Y - self._CANVAS_Y,
            anchor=tk.NW, image=self.page.zeiger_dreieck,
        )

        # Der größere Canvas würde sonst die "•••"-Punkte verdecken -> nach oben heben
        self.settings.configure(bg='#237E71')
        self.settings.lift()

        # Signalstärke
        self.Signal_staerke=ttk.Label(self.page.Info_frame, text="", font=('Calibri',9), foreground="lightblue", background='#237E71')
        self.Signal_staerke.grid(column=0, row=0, sticky=tk.NW, padx=10, pady=237)

        # Laufender Sender und Anzahl Sender im Sendegebiet
        self.AnzahlSender=ttk.Label(self.page.Info_frame, text="", font=('Calibri',9), foreground="lightblue", background='#237E71')
        self.AnzahlSender.grid(column=0, row=0,  sticky=tk.NW, padx=10, pady=(257, 0))

    def create_datetime_labels(self):
        # Label für Datum
        self.date_label = ttk.Label(self.page.Button_frame, text="", font=('Calibri', 9), foreground="lightblue", background='#237E71')
        self.date_label.grid(column=0, row=0, sticky=tk.NW, padx=(10, 0), pady=(0, 0))

        # Label für Zeit
        self.time_label = ttk.Label(self.page.Button_frame, text="", font=('Calibri', 9), foreground="lightblue", background='#237E71')
        self.time_label.grid(column=0, row=0, sticky=tk.NW, padx=(10, 0), pady=(18, 0))

    def update_channel_arrow(self, channel: str | None) -> None:
        """
        Positioniert self.chanel_arrow auf den übergebenen DAB-Channel.
        Wird von App._apply_tune_updates() aufgerufen.
        channel: z.B. "9D", "5A" – oder None/unbekannt → kein Update
        """
        if not hasattr(self, "scale_canvas"):
            return
        if not channel:
            return
        channel = str(channel).strip().upper()
        x = self.CHANNEL_ARROW_X.get(channel)
        if x is None:
            print(f"[Kanalzeiger] Unbekannter Channel: {channel!r}")
            return
        y = self.CHANNEL_ARROW_Y
        self.scale_canvas.coords(self._arrow_item, x - self._CANVAS_X, y - self._CANVAS_Y)
        # print(f"[Kanalzeiger] → {channel} x={x}, y={y}")

    # ---------------------------------------------------------------------
    # Reload-Blinker: zeigt während FM→DAB-Firmware-Reload an, dass
    # der Chip neu startet. Stoppt automatisch, wenn Firmware-Reload fertig.
    # ---------------------------------------------------------------------

    def start_reload_blink(self) -> None:
        """500ms-Wechsel zwischen zeiger_dreieck und reload_dreieck starten."""
        self.stop_signal_blink()       # <-- Reload hat Priorität: Signal-Blinker zwingend beenden
        self._cancel_reload_blink()
        self._reload_blink_state = False
        self._reload_blink_tick()

    def stop_reload_blink(self) -> None:
        """Blinken stoppen und zeiger_dreieck wiederherstellen."""
        self._cancel_reload_blink()
        try:
            self.scale_canvas.itemconfig(self._arrow_item, image=self.page.zeiger_dreieck)
        except Exception:
            pass

    def _cancel_reload_blink(self) -> None:
        if self._reload_blink_id is not None:
            try:
                self.page.after_cancel(self._reload_blink_id)
            except Exception:
                pass
            self._reload_blink_id = None

    def _reload_blink_tick(self) -> None:
        try:
            img = (self.page.reload_dreieck if self._reload_blink_state
                   else self.page.zeiger_dreieck)
            self.scale_canvas.itemconfig(self._arrow_item, image=img)
            self._reload_blink_state = not self._reload_blink_state
            self._reload_blink_id = self.page.after(500, self._reload_blink_tick)
        except Exception:
            self._reload_blink_id = None

    # ------------------------------------------------------------------
    # Signal-Blinker: zeigt schwaches Signal (RSSI < 30 dBuV) an.
    # Reload hat Priorität – Signal-Blinker startet nicht während Reload.
    # ------------------------------------------------------------------

    def start_signal_blink(self) -> None:
        if self._reload_blink_id is not None:
            return  # Reload hat Priorität
        if self._signal_blink_id is not None:
            return  # läuft bereits
        self._signal_blink_state = False
        self._signal_blink_tick()

    def stop_signal_blink(self) -> None:
        if self._signal_blink_id is None:
            return
        try:
            self.page.after_cancel(self._signal_blink_id)
        except Exception:
            pass
        self._signal_blink_id = None
        try:
            self.scale_canvas.itemconfig(self._arrow_item, image=self.page.zeiger_dreieck)
        except Exception:
            pass

    def _signal_blink_tick(self) -> None:
        try:
            img = (self.page.signal_dreieck if self._signal_blink_state
                   else self.page.zeiger_dreieck)
            self.scale_canvas.itemconfig(self._arrow_item, image=img)
            self._signal_blink_state = not self._signal_blink_state
            self._signal_blink_id = self.page.after(500, self._signal_blink_tick)
        except Exception:
            self._signal_blink_id = None

    def Chart_Reset(self):
        if not hasattr(self, 'reset_confirm'):
            self.reset_confirm = False
        if not self.reset_confirm:
            self.button4.config(text="Ok")
            self.page.confirm_frame.pack(side=tk.LEFT, padx=(5, 5))
            self.cancel_button = ttk.Button(self.page.confirm_frame, text="Abbrechen", command=self.Cancel_Reset)
            self.cancel_button.pack(side=tk.LEFT, padx=(5, 5))
            self.textx.config(text="                ")
            self.reset_confirm = True
        else:
            self.page.data_controller.chart_ranking = []
            self.page.data_controller.sender_chart = []
            self.page.data_controller._refresh_chart_listbox()
            self.page.data_controller.save_sender_chart()
            self.button4.config(text="Reset done")
            self.cancel_button.pack_forget()
            self.page.confirm_frame.pack_forget()
            self.textx.config(text="                                          ")
            self.reset_confirm = False

    def Cancel_Reset(self):
        self.button4.config(text="Reset")
        self.cancel_button.pack_forget()
        self.page.confirm_frame.pack_forget()
        self.textx.config(text="                                          ")
        self.reset_confirm = False

    def progress_bar_color(self, signal_target):
        current_value = self.progressbar["value"]
        if signal_target > current_value:
            step = 1
        else:
            step = -1
        def animate():
            nonlocal current_value
            if (step > 0 and current_value < signal_target) or (step < 0 and current_value > signal_target):
                current_value += step
                self.progressbar["value"] = current_value
                self.page.after(10, animate)
            else:
                self.progressbar["value"] = signal_target
        animate()
        if signal_target >= 70:
            bar_color = 'green'
        elif signal_target >= 40:
            bar_color = 'yellow'
        else:
            bar_color = 'red'
        self.app.style.configure('text.Horizontal.TProgressbar', background=bar_color)

    def update_datetime(self):
        now = time.localtime()
        datum = time.strftime("%a %d.%m.%Y", now)
        uhrzeit = time.strftime("%H:%M:%S", now)
        self.date_label.config(text=datum)
        self.time_label.config(text=uhrzeit)
        self.page.after(1000, self.update_datetime)


class DataController:
    def __init__(self, page):
        self.page = page           # MainPage-Instanz
        self.app  = page.app       # Root-App (enthält Sender_Id, Sender_Name, si4689_driver, audio_codec_hifiberry, DLS-Text)
        self.cfg  = self.app.config_data
        self.SaveSenderList: list[str] = []
        self.press_time = None     # Zeitstempel für das Drücken der Maustaste
        self.timer_started = False # Flag, um zu überprüfen, ob der Timer gestartet wurde
        self._press_time = None
        self._press_index = None
        self._longpress_threshold = 2.0 # Sekunden

        self.sender_chart:  list[str] = []
        self.Chartlist:     list[str] = []
        self.chart_ranking: list[str] = []

        self.last_update_time  = 0
        self.scale_active:bool = False # Variable, um den aktuellen Status des Reglers zu speichern (ob er noch aktiv ist)
        self.previous_value    = 0
        self._statuslamp_busy = False
        self._statuslamp_last = 0.0
    
    # ---Senderliste aus dab_scans.sqlite laden ------------------------------------
    def _load_scan_data(self) -> list:
        """
        Sender-Daten aus dab_scans.sqlite laden.
        Befüllt state.Sender_Name und state.Sender_Id (Grundlage für die GUI-Listbox).

        Quelle: Tabelle si4689_datenbank, Spalten si4689_idx / name.
        Reihenfolge: ORDER BY si4689_idx ASC  →  Listenposition == Tune-Index.
        """
        # --- DB-Pfad auflösen ---
        cfg_rel = (self.app.config_data or {}).get(
            "dab_scan_db", "assets/DB/dab_scans.sqlite"
        )
        if os.path.isabs(cfg_rel):
            db_path = cfg_rel
        else:
            root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..")
            )
            db_path = os.path.abspath(os.path.join(root, cfg_rel))

        # --- SQLite lesen ---
        rows = []
        try:
            con = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
            try:
                cur = con.cursor()
                cur.execute("""
                    SELECT si4689_idx, name, channel, ensemble, mhz,
                           freq_index, service_id, component_id
                    FROM   si4689_datenbank
                    ORDER  BY si4689_idx ASC;
                """)
                rows = [
                    {
                        "si4689_idx":   row[0],
                        "label":        row[1] or f"Service {row[0]}",
                        "channel":      row[2],
                        "ensemble":     row[3],
                        "mhz":          row[4],
                        "freq_index":   row[5],   # für dab_tune(channel)
                        "service_id":   row[6],   # SID für dab_start_service
                        "component_id": row[7],   # CID für dab_start_service
                    }
                    for row in cur.fetchall()
                ]
            finally:
                con.close()
        except Exception as exc:
            print(f"⚠️ dab_scans.sqlite konnte nicht gelesen werden: {exc}")
            return []

        if not rows:
            print("⚠️ si4689_datenbank ist leer – Autoscan in Page 04 durchführen.")
            return []

        # --- state befüllen ---
        self.app.state.Sender_Name.clear()
        self.app.state.Sender_Id.clear()
        for entry in rows:
            self.app.state.Sender_Name.append(entry["label"])
            self.app.state.Sender_Id.append(entry["si4689_idx"])

        print(
            f"✅ Senderliste geladen: {len(rows)} Sender "
            f"aus {db_path}"
        )
        print(self.app.state.Sender_Name)

        # --- Listbox aktualisieren ---
        try:
            self.page.gui_controller._sort_senderliste()
        except Exception:
            pass

        return rows

    def _find_next_distinct_index(self, hist: list[str], current_index: int, direction: int) -> int:
        if not hist or len(set(hist)) == 1:
            return current_index
        start_name = hist[current_index]
        idx = current_index
        for _ in range(len(hist) - 1):
            idx = (idx + direction) % len(hist)
            if hist[idx] != start_name:
                return idx
        return current_index

    def previous_sender(self, event=None):
        hist = self.app.state.sender_history
        if not hist:
            return
        new_index = self._find_next_distinct_index(hist, self.app.state.current_index, direction=-1)
        if new_index == self.app.state.current_index:
            return  # kein anderer Sender vorhanden
        name = hist[new_index]
        self.app.state.current_index = new_index

        try:
            tune_idx = self.app.state.Sender_Name.index(name)
        except ValueError:
            return
        print(f"previous Sender {name}")
        self.app.dispatcher.submit(lambda: self.app.tune_service(tune_idx, record_history=False), key="tune")

    def next_sender(self, event=None):
        hist = self.app.state.sender_history
        if not hist:
            return
        new_index = self._find_next_distinct_index(hist, self.app.state.current_index, direction=+1)
        if new_index == self.app.state.current_index:
            return
        name = hist[new_index]
        self.app.state.current_index = new_index

        try:
            tune_idx = self.app.state.Sender_Name.index(name)
        except ValueError:
            return
        print(f"next Sender {name}")
        self.app.dispatcher.submit(lambda: self.app.tune_service(tune_idx, record_history=False), key="tune")

# ---------- Von Senderliste Sender wählen ---------------------------
    def on_select_SenderListe(self, event=None):
        sel = self.page.gui_controller.lboxSenderliste.curselection()
        if not sel:
            return
        pos = int(sel[0])
 
        # Listbox-Position → original DAB-Index (berücksichtigt Sortierung)
        idx_map = getattr(self.page.gui_controller, '_lbox_index_map', [])
        idx = idx_map[pos] if (idx_map and pos < len(idx_map)) else pos
 
        if hasattr(self.page, "_sel_job") and self.page._sel_job:
            try:
                self.page.after_cancel(self.page._sel_job)
            except Exception:
                pass
        self.page._sel_job = self.page.after(160, lambda i=idx: self._enqueue_tune(i))
 

    def _enqueue_tune(self, idx: int):
        # über 'key="tune"' werfen wir ältere Tuning-Aufträge raus → keine Flut bei Scroll
        self.app.dispatcher.submit(lambda: self.app.tune_service(idx), key="tune")

# ----------Gespeicherte Sender/Favoriten ---------------------------
    def mausklick_Read(self, event):
        """Merke Startzeit + Index des angeklickten Slots."""
        self._press_time = time.time()
        self._press_index = event.widget.nearest(event.y)

    def on_release_Read(self, event):
        """B/C: Kurz = abspielen; Lang (≥2s) = aktuellen Sender speichern."""
        if self._press_time is None:
            return
        elapsed = time.time() - self._press_time
        index = self._press_index
        self._press_time = None
        self._press_index = None
        if index is None or index < 0:
            return
        if elapsed >= self._longpress_threshold:
            # C) Speichern
            self.Store_in_lboxRead(index)
        else:
            # B) Abspielen
            self.LboxRead(index)

    def Store_in_lboxRead(self, index: int):
        """C) Laufenden Sender in Slot 'index' speichern und Datei updaten."""
        # Aktuellen Sender bestimmen
        try:
            sender_idx = int(self.app.state.AktuelleSenderId)
        except Exception:
            sender_idx = 0
        if not (0 <= sender_idx < len(self.app.state.Sender_Name)):
            print("[WARN] AktuelleSenderId ungültig – Abbruch Speichern.")
            return
        name = self.app.state.Sender_Name[sender_idx]

        # Liste frisch laden, Slot sicherstellen, schreiben
        self.ReadStore()
        self._ensure_store_length()
        if 0 <= index < len(self.SaveSenderList):
            self.SaveSenderList[index] = name
            self.WriteStore()
            self._refresh_lboxRead()
        else:
            print(f"[WARN] Index {index} außerhalb Bereichs.")

    def WriteStore(self):
        """Favoritenliste in JSON sichern."""
        self._ensure_store_length()
        data = self._load_dab_state()
        data["safe_settings"] = {f"SaveSender{i+1:02d}": item for i, item in enumerate(self.SaveSenderList)}
        self.update_dab_state("safe_settings", data["safe_settings"])

    def _ensure_store_length(self, n: int = 13):
        """Sorgt dafür, dass immer genau n Slots existieren."""
        if len(self.SaveSenderList) < n:
            self.SaveSenderList += [""] * (n - len(self.SaveSenderList))
        elif len(self.SaveSenderList) > n:
            self.SaveSenderList = self.SaveSenderList[:n]

    def _refresh_lboxRead(self):
        """Listbox-Inhalt komplett neu aufbauen (mit Zebramuster)."""
        lb = self.page.gui_controller.lboxRead
        lb.delete(0, 'end')
        for i, item in enumerate(self.SaveSenderList):
            lb.insert('end', item)
            if i % 2 == 0:
                lb.itemconfigure(i, background='#f2c9f1')

    def ReadStore(self):
        """A) Favoriten aus JSON laden und Listbox füllen."""
        data = self._load_dab_state()
        safe = data.get("safe_settings", {})
        # Slots 01..13
        self.SaveSenderList = [safe.get(f"SaveSender{i:02d}", "") for i in range(1, 14)]
        self._ensure_store_length()
        self._refresh_lboxRead()

    def update_dab_state(self, key, data):
        dab_state = self._load_dab_state()
        dab_state[key] = data
        with open(self.cfg["dab_state_file"], "w", encoding="utf-8") as f:
            json.dump(dab_state, f, indent=4, ensure_ascii=False)

    def LboxRead(self, index: int | None = None):
        """B) Aus Slot 'index' den Sendernamen suchen und tunen."""
        if index is None:
            sel = self.page.gui_controller.lboxRead.curselection()
            if not sel:
                return
            index = int(sel[0])
        if not (0 <= index < len(self.SaveSenderList)):
            print(f"[WARN] Ausgewählter Index {index} out of range.")
            return
        label = self.SaveSenderList[index]
        if not label:
            print("[INFO] Slot ist leer.")
            return
        if label in self.app.state.Sender_Name:
            tune_idx = self.app.state.Sender_Name.index(label)
        else:
            print(f"[INFO] Sender '{label}' nicht gefunden – nehme Index 0.")
            tune_idx = 0
        self.app.dispatcher.submit(lambda: self.app.tune_service(tune_idx), key="tune")
        
    def _load_dab_state(self):
        default_state = {"last_sender": {}, "sender_chart": [], "safe_settings": {}}
        try:
            with open(self.cfg["dab_state_file"], "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print("[WARNUNG] dab_state.json fehlt oder ist beschädigt – Standardzustand wird verwendet.")
            return default_state
        
    def _write_dab_state(self, data):
        path = self.cfg["dab_state_file"]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[FEHLER] dab_state.json konnte nicht gespeichert werden: {e}")

    def Read_last_tune_volume(self) -> None:
        state_data = self._load_dab_state()
        last_sender = state_data.get("last_sender", {}).get("last_sender_name")
        last_volume = state_data.get("last_volume", {}).get("Volume", 8)

        self.app.state.LetzterSender = last_sender
        print(f"Wir beginnen mit 📻 {self.app.state.LetzterSender}")

        if self.app.state.LetzterSender in self.app.state.Sender_Name:
            idx = self.app.state.Sender_Name.index(self.app.state.LetzterSender)
        else:
            idx = 0

        # initiales Tuning / Volume erst nach Start der Tk mainloop, leicht verzögert mit app.after, damit
        # tune_service (der self.after(...) aufruft) nicht aus einem Worker-Thread kommt, bevor die Tk-Hauptschleife bereit ist.
        def submit_tune_and_volume():
            try:
                self.app.dispatcher.submit(lambda: self.app.tune_service(idx), key="tune")
            except Exception as e:
                print(f"[WARN] dispatcher.submit(tune) failed: {e}")
                try:
                    # Fallback: synchroner Aufruf falls Dispatcher nicht verfügbar
                    self.app.tune_service(idx)
                except Exception as e2:
                    print(f"[ERROR] direct tune_service fallback failed: {e2}")

        # Letzte Lautstärke speichern anzeigen und Regler einstellen
        self.app.state.AktuelleLautstaerke_DAB = last_volume
        self.page.gui_controller.lautst_label.config(text=f"Volume {self.app.state.AktuelleLautstaerke_DAB}")
        self.page.gui_controller.volumen_scale.set(self.app.state.AktuelleLautstaerke_DAB)
        try:
            self.app.dispatcher.submit(lambda v=last_volume: self.app.volume_service(v), key="volume")
        except Exception as e:
            print(f"[WARN] dispatcher.submit(volume) failed: {e}")
            try:
                self.app.volume_service(last_volume)
            except Exception as e2:
                print(f"[ERROR] direct volume_service fallback failed: {e2}")

        try:
            # Plant die Einreihung in den Tk-Mainthread
            self.app.after(500, submit_tune_and_volume)
        except Exception:
            # Fallback: sofort einreihen
            submit_tune_and_volume()

    def SaveLastSender(self):
        try:
            sender_index = int(self.app.state.AktuelleSenderId)
            if 0 <= sender_index < len(self.app.state.Sender_Name):
                current_sender = self.app.state.Sender_Name[sender_index].strip()
                self.update_dab_state("last_sender", {"last_sender_name": current_sender})
            else:
                print(f"Index {sender_index} out of range for Sender_Name with length {len(self.app.state.Sender_Name)}")
        except (IndexError, ValueError) as e:
            print(f"Error in SaveLastSender: {e}")

    def SaveLastVolume(self):
        try:
            vol = int(self.app.state.AktuelleLautstaerke_DAB)
            self.update_dab_state("last_volume", {"Volume": vol})
        except (IndexError, ValueError) as e:
            print(f"Error in SaveLastVolume: {e}")

# ---------Chartliste: Laden → (Anhängen bei Tuning) → Speichern → Anzeigen ----------------------------------
    def on_select_Chart(self, event):
        """Bei Auswahl in der Chart-Listbox den Sender tunen."""
        sel = self.page.gui_controller.lboxChart.curselection()
        if not sel:
            return
        self.LboxChart(int(sel[0]))

    def LboxChart(self, index: int):
        """Aus der Ranking-Liste (name, count) den echten Senderindex bestimmen und tunen."""
        if not (0 <= index < len(self.chart_ranking)):
            print(f"[INFO] Chart-Index {index} out of range.")
            return
        name = self.chart_ranking[index][0]
        try:
            tune_idx = self.app.state.Sender_Name.index(name)
            print(f"Aus Chartlist gewählt:{name}, ID:{tune_idx}")
        except ValueError:
            # fallback: case-insensitiv
            norm = name.casefold().strip()
            matches = [i for i, n in enumerate(self.app.state.Sender_Name)
                       if n.casefold().strip() == norm]
            tune_idx = matches[0] if matches else 0
        self.app.dispatcher.submit(lambda: self.app.tune_service(tune_idx), key="tune")

    def load_sender_chart(self):
        """Chartliste aus dab_state.json laden und anzeigen."""
        try:
            state = self._load_dab_state()
            raw = state.get("sender_chart", [])
            lst: list[str] = []
            for item in raw:
                if isinstance(item, str):
                    lst.append(item)                      # moderner Fall
                elif isinstance(item, dict) and "name" in item:
                    lst.append(str(item["name"]))         # sehr alter Fallback
                elif item is not None:
                    lst.append(str(item))                 # harter Fallback
            self.sender_chart = lst
            self.chart_rebuild()                          # Ranking + UI
        except Exception as e:
            print(f"Fehler beim Laden der Chartliste: {e}")
            self.sender_chart = []
            self.chart_rebuild()

    def chart_on_tuned(self, name_or_index):
        """Wird aus tune_service aufgerufen: aktuellen Sender anhängen & speichern."""
        # → robust sowohl mit Name (str) als auch Index (int) nutzbar
        if isinstance(name_or_index, int):
            idx = name_or_index
            if not (0 <= idx < len(self.app.state.Sender_Name)):
                print("[WARN] chart_on_tuned: Index außerhalb Bereichs – ignoriert.")
                return
            name = self.app.state.Sender_Name[idx]
        else:
            name = str(name_or_index).strip()
        if not name:
            return

        # ↓↓↓ NEU: Hördauer-Tracking ↓↓↓
        self._sender_log_close()     # vorherigen Eintrag abschliessen
        self._sender_log_open(name)  # neuen Eintrag starten
        # ↑↑↑ NEU ↑↑↑

        # Optional: Historie begrenzen (sonst wächst JSON unendlich)
        MAX_HISTORY = 500
        self.sender_chart.append(name)
        if len(self.sender_chart) > MAX_HISTORY:
            self.sender_chart = self.sender_chart[-MAX_HISTORY:]

        self.save_sender_chart()
        self.chart_rebuild()       # Ranking neu berechnen + UI aktualisieren

    def _sender_log_open(self, sender: str):
            """Startet einen neuen Höreintrag — wird beim Senderwechsel aufgerufen."""
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._current_sender_ts   = ts
            self._current_sender_name = sender
            self._current_log_id      = None   # Sicherheitsreset

            db = getattr(self.app, 'listener_db_manager', None)
            if db is None:
                return
            try:
                with db.get_cursor() as (conn, cursor):
                    cursor.execute(
                        "INSERT INTO sender_log (sender, ts_start) VALUES (?, ?)",
                        (sender, ts)
                    )
                    conn.commit()
                    self._current_log_id = cursor.lastrowid
            except Exception as e:
                print(f"[SenderLog] Open-Fehler: {e}")

    def _sender_log_close(self):
        """Schliesst den laufenden Eintrag ab — setzt ts_end und duration_sec."""
        if not getattr(self, '_current_log_id', None):
            return
        from datetime import datetime
        try:
            ts_end   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ts_start = getattr(self, '_current_sender_ts', ts_end)
            dt_start = datetime.strptime(ts_start, "%Y-%m-%d %H:%M:%S")
            dt_end   = datetime.strptime(ts_end,   "%Y-%m-%d %H:%M:%S")
            duration = max(0, int((dt_end - dt_start).total_seconds()))
        except Exception:
            ts_end, duration = "", 0

        db = getattr(self.app, 'listener_db_manager', None)
        if db:
            try:
                with db.get_cursor() as (conn, cursor):
                    cursor.execute(
                        """UPDATE sender_log
                              SET ts_end = ?, duration_sec = ?
                            WHERE id = ?""",
                        (ts_end, duration, self._current_log_id)
                    )
                    conn.commit()
            except Exception as e:
                print(f"[SenderLog] Close-Fehler: {e}")

        self._current_log_id = None

    def save_sender_chart(self):
        """Chartliste in dab_state.json speichern."""
        try:
            state = self._load_dab_state()
            state["sender_chart"] = list(self.sender_chart)
            self._write_dab_state(state)
        except Exception as e:
            print(f"Fehler beim Speichern der Chartliste: {e}")

    def chart_rebuild(self):
        """Ranking (name, count) berechnen und Listbox befüllen."""
        counts = Counter(self.sender_chart)
        self.chart_ranking = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
        self._refresh_chart_listbox()

    def _refresh_chart_listbox(self):
        """Listbox lboxChart aus self.chart_ranking neu aufbauen."""
        lb = self.page.gui_controller.lboxChart
        lb.delete(0, 'end')
        for i, (name, cnt) in enumerate(self.chart_ranking):
            lb.insert(tk.END, f"{i+1}. {name} ({cnt})")
            if i % 2 == 0:
                lb.itemconfigure(i, background='#f2c9f1')

# ---------- Programm-Typen: Laden → Speichern → Anzeigen ----------

    def on_select_Typ(self, event=None):
        sel = self.page.gui_controller.lboxTyp.curselection()
        if not sel:
            return
        self.LboxTyp()

    def _process_pty_results(self, cursor, pty_idx_to_label, name_to_pty):
        """Verarbeitet PTY-Query-Resultate (verhindert Code-Duplikation)"""
        for nm, pidx, ptxt in cursor.fetchall():
            key = (nm or "").strip().casefold()
            try:
                pidx_int = int(pidx) if pidx is not None else 0
            except Exception:
                pidx_int = 0
            if pidx_int not in pty_idx_to_label and isinstance(ptxt, str) and ptxt.strip():
                pty_idx_to_label[pidx_int] = ptxt.strip()
            name_to_pty[key] = (pidx_int, ptxt)

    def parse_dab_program_types(self):
        """PTY-basierte Senderliste erstellen."""
        # 1) DB-Pfad ermitteln
        cfg_path = (self.app.config_data or {}).get("dab_scan_db", "assets/DB/dab_scans.sqlite")
        if not os.path.isabs(cfg_path):
            try:
                root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                db_path = os.path.abspath(os.path.join(root, cfg_path))
            except Exception:
                db_path = cfg_path
        else:
            db_path = cfg_path
        if not os.path.exists(db_path):
            db_path = "/home/weilmy/My_DAB_Si4689/assets/DB/dab_scans.sqlite"

        # 2) DB lesen
        name_to_pty = {}
        pty_idx_to_label = {}
        
        try:
            db_manager = getattr(self.app, 'scan_db_manager', None)
            pool = getattr(self.app, 'si4689_pool', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    cursor.execute("SELECT name, pty_idx, pty_txt FROM si4689_datenbank")
                    self._process_pty_results(cursor, pty_idx_to_label, name_to_pty)
            
            elif pool:
                con = pool.get_connection()
                try:
                    cur = con.cursor()
                    cur.execute("SELECT name, pty_idx, pty_txt FROM si4689_datenbank")
                    self._process_pty_results(cur, pty_idx_to_label, name_to_pty)
                finally:
                    pool.return_connection(con)
            
            else:
                con = sqlite3.connect(db_path, timeout=5, isolation_level=None, check_same_thread=False)
                try:
                    cur = con.cursor()
                    cur.execute("SELECT name, pty_idx, pty_txt FROM si4689_datenbank")
                    self._process_pty_results(cur, pty_idx_to_label, name_to_pty)
                finally:
                    con.close()
            
        except Exception as e:
            print(f"[PTY] SQLite-Fehler: {e}")
        
        # 3) Aktuelle Senderliste verarbeiten
        rows = []
        for nm in getattr(self.app.state, "Sender_Name", []):
            key = (nm or "").strip().casefold()
            pidx, _ptxt = name_to_pty.get(key, (0, None))
            rows.append((int(pidx), nm))

        # 4) Sortierung
        rows.sort(key=lambda t: (t[0], t[1].casefold()))

        # 5) Ziel-Container leeren
        self.page.sorted_Type_Label.clear()
        self.page.Label_Typ.clear()
        self.page.Label_Typ_org.clear()

        # 6) Gruppentitel
        try:
            from pages.page_04 import PTY_MAP as PTY_MAP
            pty_map = PTY_MAP
        except ImportError:
            pty_map = {}

        def _normalize_header(label: str | None, tp: int) -> str:
            lbl = (label or "").strip()
            if not lbl:
                lbl = (pty_map.get(tp) or pty_map.get(str(tp)) or "").strip()
            if not lbl:
                if tp == 0:
                    return "kein Typ"
                return f"PTY {tp}"
            low = lbl.lower()
            if low in {"none", "no programme type", "no program type", "kein typ"}:
                return "kein Typ"
            return lbl

        current_type = None
        for tp, nm in rows:
            if current_type != tp:
                current_type = tp
                label = _normalize_header(pty_idx_to_label.get(tp), tp)
                header = f"--- {label} ---"
                self.page.sorted_Type_Label.append(header)
            self.page.sorted_Type_Label.append(nm)
            self.page.Label_Typ.append([tp, nm])
            self.page.Label_Typ_org.append([tp])

        # 7) UI aktualisieren
        self._refresh_lboxTyp()

    def _refresh_lboxTyp(self):
        """Listbox lboxTyp aus self.page.sorted_Type_Label neu aufbauen.
        - Zebramuster für Einträge
        - Gruppentitel/Überschriften (Farbe + '--- ... ---')
        - Gruppentitel sind nicht selektierbar: Auswahl springt auf nächsten Eintrag
        """
        gc = getattr(self.page, "gui_controller", None)
        if gc is None:
            return
        lb = getattr(gc, "lboxTyp", None)
        if lb is None:
            return
        lb.delete(0, 'end')

        # Einträge einsetzen und optisch formatieren
        for i, item in enumerate(self.page.sorted_Type_Label):
            lb.insert('end', item)
            is_header = isinstance(item, str) and item.startswith('---') and item.endswith('---')
            if is_header:
                # Header deutlich einfärben
                try:
                    lb.itemconfigure(i, background='#ffe9a8', foreground='#000000')
                except Exception:
                    pass  # einige Tk-Themes erlauben nicht alle Optionen
            else:
                # Zebra für normale Zeilen
                if i % 2 == 0:
                    try:
                        lb.itemconfigure(i, background='#f2c9f1')
                    except Exception:
                        pass

        # Bind einmalig: Header dürfen nicht selektiert bleiben
        if not getattr(lb, "_cc_header_bind", False):
            def _on_select(evt):
                w = evt.widget
                sel = w.curselection()
                if not sel:
                    return
                idx = sel[0]
                txt = w.get(idx)
                is_header = isinstance(txt, str) and txt.startswith('---') and txt.endswith('---')
                if not is_header:
                    return  # normale Auswahl ok
                # Header -> zur nächsten nicht-Header-Zeile springen
                size = w.size()
                new_idx = None
                for j in range(idx + 1, size):
                    t = w.get(j)
                    if not (isinstance(t, str) and t.startswith('---') and t.endswith('---')):
                        new_idx = j
                        break
                if new_idx is None:
                    for j in range(idx - 1, -1, -1):
                        t = w.get(j)
                        if not (isinstance(t, str) and t.startswith('---') and t.endswith('---')):
                            new_idx = j
                            break
                w.selection_clear(0, 'end')
                if new_idx is not None:
                    w.selection_set(new_idx)
                    w.activate(new_idx)
                    try:
                        w.see(new_idx)
                    except Exception:
                        pass
                return "break"
            try:
                lb.bind('<<ListboxSelect>>', _on_select, add='+')
                setattr(lb, "_cc_header_bind", True)
            except Exception:
                pass

    def LboxTyp(self):
        """
        Klick in der Typ-Listbox:
        - Überschriften (---) ignorieren
        - Sender suchen und tunen
        """
        sel = self.page.gui_controller.lboxTyp.curselection()
        if not sel:
            return
        idx = sel[0]
        label = self.page.gui_controller.lboxTyp.get(idx)
        # Überschrift erkennen (deine Header bestehen aus Bindestrichen)
        if label.strip().startswith("-") or label.strip().startswith("—"):
            print("Überschrift – keine Aktion.")
            return
        norm = label.strip().casefold()
        try:
            real_idx = next(i for i, nm in enumerate(self.app.state.Sender_Name)
                            if nm.strip().casefold() == norm)
        except StopIteration:
            print(f"[INFO] Sender '{label}' nicht in aktueller Liste gefunden.")
            return
        self.app.dispatcher.submit(lambda: self.app.tune_service(real_idx), key="tune")

#-------------Volume/Lautstärkeregelung--------------------------
    def volume_activated(self, value):
        current_time = time.time()
        if current_time - self.last_update_time < 0.3:
            return
        value = round(float(value), 1)
        if abs(value - self.previous_value) >= 0.5:  # mind. 0.5 Unterschied
            self.last_update_time = current_time
            self.previous_value = value

    def on_press_volume(self, event):
        self.scale_active = True

    def on_release_volume(self, event):
        self.scale_active = False
        try:
            volume = round(self.page.gui_controller.volumen_scale.get())
            self.app.state.AktuelleLautstaerke_DAB = volume
            self.page.gui_controller.lautst_label.config(text=f"Volume {volume}")
            self.SaveLastVolume()
            self.app.dispatcher.submit(lambda: self.app.volume_service(self.app.state.AktuelleLautstaerke_DAB), key="volume") # Stellt die Lautstärke ein

        except Exception as e:
            print(f"[Fehler] Lautstärke konnte beim Loslassen nicht aktualisiert werden: {e}")

    def refresh_si4689_status_lamp_async(self) -> None:
            """
            Si4689-Chip-Status abfragen und Ampel aktualisieren.
            Kombiniert GET_SYS_STATE + DAB_DIGRAD_STATUS + DAB_GET_EVENT_STATUS,
            um sowohl den Chip-Zustand als auch den Audio-Empfang zu bewerten.

            Läuft als Hintergrundtask im Dispatcher (kein SPI-Aufruf im Tk-Thread).
            Danach wird das Ampel-Label im Tk-Hauptthread per self.page.after() gesetzt.
            Die Methode plant sich selbst alle 5 Sekunden neu ein (self-scheduling).

            Ampel-Logik:
            🔴 Rot    – ERR_CMD oder fataler Chip-Fehler
            🟡 Gelb   – Chip bootet (PUP_STATE != 3) ODER Sender noch nicht eingerastet
                        (acq=False / valid=False / fic_quality<50 / mute=True)
            🟢 Grün   – App läuft (PUP_STATE=3), Ensemble synchronisiert,
                        gutes Signal, kein Soft-Mute → Ton ist da
            """
            # --- Guard: kein paralleler Aufruf, Mindestabstand 3 Sekunden ---
            if self._statuslamp_busy:
                return
            now = time.time()
            if now - self._statuslamp_last < 3.0:
                return
            self._statuslamp_busy = True

            def _worker() -> dict:
                """Alle drei SPI-Abfragen im Hintergrund-Thread."""
                try:
                    chip = getattr(self.app.si4689, "_radio", None)
                    if chip is None:
                        return {"color": "red", "label": "Si4689 chip nicht gefunden", "rssi": -128}

                    # 1) Chip-Grundzustand: PUP_STATE, CTS, ERR, fatal
                    sys = chip.get_sys_state()
                    color     = sys.get("color", "red")
                    pup_state = sys.get("pup_state", 0)
                    label     = sys.get("label", "")
                    rssi      = -128  # Standardwert falls pup_state != 3

                    # Fehler sofort zurückgeben – keine weiteren Abfragen nötig
                    if color == "red":
                        return {"color": "red", "label": label, "rssi": -128}

                    # 2) Nur wenn App läuft (PUP_STATE=3): DAB-Empfangsstatus prüfen
                    if pup_state == 3:
                        # DAB_DIGRAD_STATUS: Ensemble-Sync und Signalqualität
                        sig = chip.get_dab_signal_strength()   # intern: dab_digrad_status()
                        acq         = sig.get("acq",         False)
                        valid       = sig.get("valid",       False)
                        fic_quality = sig.get("fic_quality", 0)
                        snr         = sig.get("snr",         0)
                        rssi        = sig.get("rssi",        -128)

                        # DAB_GET_EVENT_STATUS: Soft-Mute (ACF) prüfen
                        evt  = chip.dab_get_event_status(ack=False)
                        mute = evt.get("mute", False)

                        # Ton-Bereitschaft bewerten
                        audio_ready = (
                            acq
                            and valid
                            and fic_quality >= 50
                            and snr > 3
                            and not mute
                        )

                        if audio_ready:
                            color = "green"
                            label = (
                                f"Empfang OK – ACQ={acq} VALID={valid} "
                                f"FIC={fic_quality}% SNR={snr}dB RSSI={rssi}dBuV"
                            )
                        else:
                            color = "yellow"
                            reason_parts = []
                            if not acq:          reason_parts.append("kein ACQ")
                            if not valid:        reason_parts.append("kein VALID")
                            if fic_quality < 50: reason_parts.append(f"FIC={fic_quality}%")
                            if snr <= 3:         reason_parts.append(f"SNR={snr}dB")
                            if mute:             reason_parts.append("Soft-Mute aktiv")
                            label = (
                                f"Sender empfangen, warte auf Audio – "
                                f"RSSI={rssi}dBuV, " + ", ".join(reason_parts)
                            )

                    return {"color": color, "label": label, "rssi": rssi}

                except Exception as e:
                    # CTS+ERR (Status=0xC...) = Chip momentan beschäftigt (Signal-Dropout,
                    # internes Re-Sync) → YELLOW statt RED; kein dauerhafter Hardwarefehler
                    err = str(e)
                    if "Status=0xC" in err or "Kommandofehler" in err:
                        return {"color": "yellow", "label": f"Statusabfrage kurz unterbrochen", "rssi": -128}
                    return {"color": "red", "label": f"Statusabfrage Fehler: {e}", "rssi": -128}

            def _update_gui(result: dict) -> None:
                """GUI-Update im Tk-Hauptthread."""
                try:
                    color = result.get("color", "red")
                    label = result.get("label", "")

                    img_map = {
                        "green":  self.page.status_green,
                        "yellow": self.page.status_yellow,
                        "red":    self.page.status_red,
                    }
                    img = img_map.get(color, self.page.status_red)

                    status_lbl = getattr(self.page.gui_controller, "status_image", None)
                    if status_lbl is not None:
                        status_lbl.configure(image=img)
                        status_lbl.image = img   # GC-Schutz

                    # Blinker-Steuerung: Reload > Signal
                    gc = getattr(self.page, "gui_controller", None)
                    if gc is not None:
                        rssi = result.get("rssi", -128)
                        if color == "green":
                            try:
                                gc.stop_reload_blink()
                            except Exception:
                                pass
                        if color in ("green", "yellow"):
                            try:
                                if rssi < 30:
                                    gc.start_signal_blink()
                                else:
                                    gc.stop_signal_blink()
                            except Exception:
                                pass
                        else:
                            try:
                                gc.stop_signal_blink()
                            except Exception:
                                pass

                    # Nur bei erstem Ampel GREEN in Konsole ausgeben
                    last = getattr(self, "_statuslamp_last_color", None)
                    if color != last:
                        if color == "green" and self.app.state.new_tune:
                            print(f"🟢 {label}")
                            self.app.state.new_tune=False                            
                        self._statuslamp_last_color = color

                except Exception as e:
                    print(f"[Si4689] Ampel GUI-Update Fehler: {e}")
                finally:
                    self._statuslamp_busy = False
                    self._statuslamp_last = time.time()
                    # Nächste Abfrage in 5 Sekunden einplanen
                    try:
                        if not getattr(self.app, "_is_closing", False):
                            self.page.after(5_000, self.refresh_si4689_status_lamp_async)
                    except Exception:
                        pass

            def _dispatch() -> None:
                result = _worker()
                try:
                    self.page.after(0, lambda: _update_gui(result))
                except Exception as e:
                    print(f"[Si4689] after() Fehler: {e}")
                    self._statuslamp_busy = False

            try:
                self.app.dispatcher.submit(_dispatch, key="si4689_status_lamp")
            except Exception as e:
                print(f"[Si4689] Dispatcher Fehler: {e}")
                self._statuslamp_busy = False

    def anzahl_sender(self):
            total = len(self.app.state.Sender_Name)
            self.page.gui_controller.AnzahlSender.config(text=f"Sender: {self.app.state.AktuelleSenderId} von {total}")


class DlsManager:
    def __init__(self, page):
        self.page = page
        self.app  = page.app

        with open(self.app.config_data["dls_filter_config"], "r", encoding="utf-8") as f:
            self.dls_filter_config = json.load(f)
        
        self._debounce_s = int(self.dls_filter_config.get("debounce_seconds", 75))
        self._min_chars  = int(self.dls_filter_config.get("min_chars_to_store", 8))
        self._allow_raw  = bool(self.dls_filter_config.get("allow_store_raw_when_unparsed", True))
        self._drop_rx    = [re.compile(pat, re.IGNORECASE) for pat in self.dls_filter_config.get("drop_regex", [])]
        self._ad_patterns = [re.compile(pat, re.IGNORECASE) for pat in self.dls_filter_config.get("ad_patterns", [])]
        self._ad_keywords = [s.casefold() for s in self.dls_filter_config.get("ad_keywords", [])]

        # Debounce-Marker
        self._last_key: str | None = None
        self._last_ts: float = 0.0

        # 1) Sets/Listen vorbereiten
        self._kw_upper = tuple(s.upper() for s in self.dls_filter_config.get("sender_keywords", []))
        self._no_sender_cf = {s.casefold() for s in self.dls_filter_config.get("no_sender_list", [])}
        self._invert_artist_cf = {s.casefold() for s in self.dls_filter_config.get("invert_artist_song", [])}

        # 2) Fonts & Breitenmessung
        try:
            up_font_name   = self.page.gui_controller.musiktitel_up_label.cget("font")
            down_font_name = self.page.gui_controller.musiktitel_down_label.cget("font")
            self._font_up   = tkfont.Font(font=up_font_name)
            self._font_down = tkfont.Font(font=down_font_name)
        except Exception:
            # Fallback, wenn GUI noch nicht bereit
            self._font_up = self._font_down = tkfont.Font(family="Quicksand Medium", size=12)

        # 3) Anzeige-Cache
        self._last_up_down = ("", "")

    # ---------------- Hauptlogik ----------------

    def analyze_dls_text(self, text: str):
        """Plant die DLS-Analyse immer in den nächsten Idle-Slot der Tk-Loop ein."""
        try:
            if getattr(self, "_analyze_after_id", None):
                self.page.after_cancel(self._analyze_after_id)
        except Exception:
            pass

        # Reentrancy-Guard – nicht zwei Analysen parallel
        if getattr(self, "_analyzing", False):
            self._pending_dls_text = text
            return

        def _run():
            self._analyze_after_id = None
            self._analyzing = True
            try:
                self._analyze_dls_text_impl(text)
            finally:
                self._analyzing = False
                # Falls währenddessen neue Daten ankamen, gleich wieder (idle) einplanen
                pending = getattr(self, "_pending_dls_text", None)
                if pending is not None:
                    self._pending_dls_text = None
                    self.analyze_dls_text(pending)

        self._analyze_after_id = self.page.after_idle(_run)

    def _analyze_dls_text_impl(self, song_info: str) -> None:
        """
        DLS-Analyse mit Multi-Artist-Support und Werbe-Erkennung.
        """
        try:
            genre_n: str | None = None

            # ============ ERWEITERTE HELPER ============

            def _safe_display(txt: str) -> None:
                try:
                    self.page.after(0, lambda: self.dls_on_Display(txt))
                except Exception:
                    self.dls_on_Display(txt)

            def _iso_utc_now() -> str:
                return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

            def _clean_piece(s: str) -> str:
                s = re.sub(r"\s+", " ", (s or "")).strip()
                return s.strip(" -–—/:·•|[]\"'""‚'")

            def _is_likely_advertisement(text: str, sender: str) -> bool:
                """
                Multi-Stage Werbe-Erkennung.
                """
                t_lower = (text or "").lower()
                t_upper = (text or "").upper()
                
                # Stage 1: Bekannte Werbekeywords
                for kw in getattr(self, "_ad_keywords", []):
                    if kw in t_lower:
                        return True
                
                # Stage 2: Wiederholte identische Worte (DJ DJ DJ)
                words = t_lower.split()
                if len(words) >= 3:
                    word_counts = {}
                    for w in words:
                        if len(w) > 2:  # Nur Worte >2 Zeichen zählen
                            word_counts[w] = word_counts.get(w, 0) + 1
                    if any(count >= 3 for count in word_counts.values()):
                        return True
                
                # Stage 3: Sehr kurze Texte ohne Buchstaben
                clean_text = re.sub(r'[^a-zA-Z]', '', text)
                if len(clean_text) < 5:
                    return True
                
                # Stage 4: Nur Großbuchstaben und sehr kurz (Sender-ID)
                if len(text) < 13 and text == t_upper and not any(c.isdigit() for c in text):
                    # ABER: Multi-Artist in Caps ist OK!
                    if not any(sep in text for sep in [';', '&', ',', ' FEAT', ' FT', ' - ', ' / ', ' | ']):
                        return True
                
                # Stage 5: KRITISCHE VERBESSERUNG - Spam-Pattern OHNE ; und &
                # WICHTIG: ; und & sind legitim für "ARTIST; ARTIST" oder "ARTIST & ARTIST"
                spam_pattern = r'[@#$%^*=+\[\]{}|<>?!]{2,}'  # OHNE ; und &
                spam_sequences = len(re.findall(spam_pattern, text))
                if spam_sequences >= 2:
                    return True
                
                # Stage 6: Keyword-Match
                kw_upper = getattr(self, "_kw_upper", ())
                if any(kw in t_upper for kw in kw_upper):
                    return True
                
                # Stage 7: Kompilierte Regex-Pattern
                ad_patterns = getattr(self, "_ad_patterns", [])
                for pattern in ad_patterns:
                    try:
                        if pattern.search(t_lower):
                            return True
                    except Exception:
                        pass
                
                # Stage 8: Nur Sonderzeichen, keine echten Worte
                word_chars = sum(1 for c in text if c.isalnum())
                special_chars = sum(1 for c in text if not c.isalnum() and not c.isspace())
                if special_chars > word_chars:
                    return True
                
                return False

            def _is_valid_artist_title_pair(artist: str | None, title: str | None) -> bool:
                """
                Validierung mit Multi-Artist-Support.
                """
                if not artist or not title:
                    return False
                
                a = (artist or "").strip()
                t = (title or "").strip()
                
                # Mindestlängen
                if len(a) < 2 or len(t) < 2:
                    return False
                
                # Multi-Artist explizit erlauben (weniger strenge Validierung)
                if ';' in a or '&' in a or ',' in a:
                    # Multi-Artist erkannt - nur Basis-Checks
                    return True
                
                # Artist/Title nicht identisch
                if a.lower() == t.lower():
                    return False
                
                # Keine Wiederholung des gleichen Wortes
                a_words = set(a.lower().split())
                t_words = set(t.lower().split())
                if a_words == t_words and len(a_words) > 0:
                    return False
                
                # Zu viele gemeinsame Worte
                intersection = a_words & t_words
                if len(intersection) > min(len(a_words), len(t_words)) * 0.6:
                    return False
                
                # Mindestens ein Buchstabe in beiden
                if not any(c.isalpha() for c in a) or not any(c.isalpha() for c in t):
                    return False
                
                return True

            def _normalize_multi_artist(artist: str) -> str:
                if not artist:
                    return ""
                
                # Erkenne Trennzeichen
                separators = [';', '&', ' feat.', ' feat ', ' ft.', ' ft ', ',']
                found_sep = None
                for sep in separators:
                    if sep in artist:
                        found_sep = sep
                        break
                
                if not found_sep:
                    # Einzelner Artist - normale Title-Case
                    return _titlecase(artist)
                
                # Multi-Artist - jeden Teil einzeln behandeln
                parts = re.split(r'([;&,]|\s+feat\.?\s+|\s+ft\.?\s+)', artist, flags=re.IGNORECASE)
                normalized = []
                
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    
                    # Trennzeichen beibehalten
                    if part.lower() in ['&', ';', ',', 'feat.', 'feat', 'ft.', 'ft']:
                        normalized.append(part)
                    else:
                        # Artist-Name normalisieren
                        normalized.append(_titlecase(part))
                
                return ' '.join(normalized)

            def _titlecase(s: str) -> str:
                """Intelligente Title-Case mit Multi-Artist-Support."""
                keep_lower = {
                    "of", "von", "und", "feat.", "ft.", "and", "mit", 
                    "the", "le", "la", "les", "de", "des", "du", "a", "an"
                }
                
                out = []
                words = (s or "").split()
                
                for i, w in enumerate(words):
                    # Akronyme (2-4 Großbuchstaben) beibehalten
                    if w.isupper() and 2 <= len(w) <= 4:
                        out.append(w)
                    # Kleine Worte (außer am Anfang)
                    elif i > 0 and w.lower() in keep_lower:
                        out.append(w.lower())
                    # Normale Worte
                    else:
                        out.append(w[0].upper() + w[1:].lower() if len(w) > 0 else w)
                
                return " ".join(out)

            def _key_and_id(sender: str, artist: str | None, title: str | None, genre: str | None, raw: str) -> tuple[str, str]:
                s = re.sub(r"\s+", " ", (sender or "")).strip().casefold()
                if artist and title:
                    a = re.sub(r"\s+", " ", artist.strip()).casefold()
                    t = re.sub(r"\s+", " ", title.strip()).casefold()
                    key = f"{s}|{a}|{t}"
                else:
                    r = re.sub(r"\s+", " ", (raw or "").strip()).casefold()
                    key = f"{s}|raw|{r}"
                hid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
                return key, f"sha1:{hid}"

            def _parse_artist_title(text: str, sender: str) -> tuple[str | None, str | None]:
                """
                Parser mit Multi-Artist-Support.
                """
                if not text:
                    return None, None
                
                t = text.strip()
                s_cf = (sender or "").casefold()
                invert = getattr(self, "_invert_artist_cf", set())

                # Sortierte Liste von Trennzeichen mit Priorität
                separators = [
                    (r'\s+[-–—]\s+', False, "dash"),          # "Artist - Title" (HÖCHSTE PRIORITÄT)
                    (r'\s+/\s+', False, "slash"),              # "Artist / Title"
                    (r'\s+:\s+', False, "colon"),              # "Artist: Title"
                    (r'\s+\|\s+', False, "pipe"),              # "Artist | Title"
                    (r'\s+von\s+', True, "von"),               # "Title von Artist"
                    (r'\s+mit\s+', False, "mit"),              # "Artist mit Title"
                    (r'\s+und\s+', True, "und"),               # "Title und Artist"
                ]

                for sep_pattern, swap, sep_type in separators:
                    # Case-insensitive für Worte wie "von", "mit"
                    flags = re.IGNORECASE if sep_type in ["von", "mit", "und"] else 0
                    m = re.split(sep_pattern, t, maxsplit=1, flags=flags)
                    
                    if len(m) == 2:
                        left = _clean_piece(m[0])
                        right = _clean_piece(m[1])
                        
                        if not left or not right:
                            continue
                        
                        artist, title = left, right
                        
                        # Swap-Logik
                        if swap or s_cf in invert:
                            artist, title = title, artist
                        
                        # Multi-Artist normalisieren
                        artist = _normalize_multi_artist(artist)
                        title = _titlecase(title)
                        
                        # Validierung mit Multi-Artist-Support
                        if _is_valid_artist_title_pair(artist, title):
                            return artist, title
                
                # Kein Trennzeichen gefunden
                return None, None

            # ============ HAUPTLOGIK ============
            sender = (self.app.state.AktuellerSender or "").strip()
            raw = (song_info or "").strip()

            # Branding/Noise entfernen
            cleaned = raw.replace("*** www.ipmusic.ch", "").strip()
            drop_rx = getattr(self, "_drop_rx", None)
            if drop_rx:
                for rx in drop_rx:
                    cleaned = rx.sub(" ", cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip()

            # Zu kurz
            min_chars = getattr(self, "_min_chars", 8)
            if len(cleaned) < min_chars:
                _safe_display(raw)
                self.app.state.artist_bio_state = False
                return

            # **VERBESSERTE Werbe-Erkennung**
            if _is_likely_advertisement(cleaned, sender):
                # print(f"🚫 [FILTERED_AD] {sender}: {cleaned}")
                _safe_display(cleaned)
                self.app.state.artist_bio_state = False
                return

            # Sender auf Ignore-Liste
            no_sender_cf = getattr(self, "_no_sender_cf", set())
            if sender.casefold() in no_sender_cf:
                _safe_display(cleaned)
                self.app.state.artist_bio_state = False
                return

            # Parser anwenden (mit Multi-Artist-Support)
            artist, title = _parse_artist_title(cleaned, sender)
            if artist and title:
                self.app.state.artist_n = artist  # Bereits normalisiert
                title_n = title  # Bereits normalisiert
                confidence = 0.9
            else:
                self.app.state.artist_n = None
                title_n = None
                confidence = 0.5

            # UI updaten
            _safe_display(cleaned)
            if self.app.state.artist_n is None or title_n is None:
                self.app.state.artist_bio_state = False
                return

            # Eindeutiger Schlüssel für Debounce
            try:
                track_key, track_id = _key_and_id(sender, self.app.state.artist_n, title_n, genre_n, cleaned)
            except Exception:
                track_key, track_id = "", ""

            # Debounce
            now_s = time.time()
            if hasattr(self, "_should_store") and not self._should_store(track_key, now_s):
                return
            if hasattr(self, "_mark_stored"):
                self._mark_stored(track_key, now_s)

            # Genre-Lookup
            try:
                genre_n = Cover_url.fetch_genre_url(self.app.state.artist_n, title_n)
            except Exception:
                genre_n = None

            # Biografie im Hintergrund laden
            self._schedule_bio_fetch(self.app.state.artist_n, track_key)

            md = {
                "ts_utc": _iso_utc_now(),
                "sender": sender,
                "artist": self.app.state.artist_n,
                "title": title_n,
                "genre": genre_n,
                "raw": cleaned,
                "source": "RDS",
                "confidence": confidence,
                "track_key": track_key,
                "track_id": track_id,
            }
            try:
                print(f"💾 Speicherung in SQL Datenbank: {sender}, {self.app.state.artist_n}, {title_n}, {genre_n}")
                self.app.store_in_SQL(md)
            except Exception:
                traceback.print_exc()

        except Exception:
            traceback.print_exc()
            self.app.state.artist_bio_state = False

    # ---------------- Anzeige ----------------
    def _display_async(self, dls_text: str):
        """UI-Update immer im Tk-Hauptthread ausführen."""
        try:
            self.page.after(0, lambda: self.dls_on_Display(dls_text))
        except Exception:
            self.dls_on_Display(dls_text)

    def dls_on_Display(self, dls_text: str):
        title_up, title_down = self._split_for_two_lines(dls_text)
        # Dedup: unnötige Label-Updates vermeiden
        if (title_up, title_down) == self._last_up_down:
            return
        self._last_up_down = (title_up, title_down)
        self.display_split_title(title_up, title_down)

    # ------------ Zweizeilige Darstellung ------------
    def _split_for_two_lines(self, text: str) -> tuple[str, str]:
        try:
            import pyphen
        except Exception:
            pyphen = None

        def norm_space(s: str) -> str:
            return re.sub(r"\s+", " ", (s or "").strip())

        def grid_padx_total(widget) -> int:
            try:
                gi = widget.grid_info()
                px = gi.get("padx", 0)
                if isinstance(px, (tuple, list)):
                    return int(float(px[0])) + int(float(px[1] if len(px) > 1 else px[0]))
                s = str(px).strip().replace(",", " ")
                if s.startswith("{") and s.endswith("}"):
                    s = s[1:-1].strip()
                parts = s.split()
                if len(parts) == 2:
                    return int(float(parts[0])) + int(float(parts[1]))
                if len(parts) == 1 and parts[0]:
                    return int(float(parts[0])) * 2
            except Exception:
                pass
            return 14

        def hyphenate_fit(word: str, font: tkfont.Font, max_px: int) -> tuple[str | None, str | None]:
            if not pyphen:
                return (None, None)
            if len(word) < 16 or not word.isalpha() or word.isupper():
                return (None, None)
            dic = None
            for lang in ("de_DE", "en_US"):
                try:
                    dic = pyphen.Pyphen(lang=lang)
                    break
                except Exception:
                    dic = None
            if not dic:
                return (None, None)

            parts = dic.inserted(word).split("-")
            if len(parts) <= 1:
                return (None, None)
            built = ""
            consumed_chars = 0
            for i, p in enumerate(parts):
                piece = p + ("-" if i < len(parts) - 1 else "")
                if font.measure(built + piece) <= max_px:
                    built += piece
                    consumed_chars += len(p)
                else:
                    break

            if built and built != word and built.endswith("-"):
                rest = word[consumed_chars:]
                return (built, rest)
            return (None, None)

        try:
            container_px = self.page.menu_middle.winfo_width()
            if container_px <= 1:
                container_px = 455
        except Exception:
            container_px = 455

        try:
            up_lbl = self.page.gui_controller.musiktitel_up_label
        except Exception:
            up_lbl = None
        pad_total = grid_padx_total(up_lbl) if up_lbl else 14
        max_px = max(50, container_px - pad_total)

        font_up = getattr(self, "_font_up", None)
        if font_up is None:
            try:
                font_up = tkfont.Font(font=self.page.gui_controller.musiktitel_up_label.cget("font"))
            except Exception:
                font_up = tkfont.Font(family="Quicksand Medium", size=12)

        text = norm_space(text)
        if not text:
            return "", ""
        words = text.split(" ")
        up = ""
        i = 0
        while i < len(words):
            w = words[i]
            trial = (up + " " + w).strip() if up else w
            if font_up.measure(trial) <= max_px:
                up = trial
                i += 1
                continue

            if not up:
                front, rest = hyphenate_fit(w, font_up, max_px)
                if front and rest:
                    down = (rest + " " + " ".join(words[i + 1:])).strip()
                    return front.strip(), down
                down = (" ".join(words[i:])).strip()
                return "", down

            down = (w + " " + " ".join(words[i + 1:])).strip()
            return up.strip(), down
        return up.strip(), ""

    def display_split_title(self, title_up: str, title_down: str):
        nm = self.page.news_manager
        gc = self.page.gui_controller

        if gc.musiktitel_up_label.cget("text") != title_up:
            gc.musiktitel_up_label.config(text=title_up)

        target_mode = "two_line" if (title_down or "").strip() else "news"

        if self.app.current_page_name != "MainPage":
            if nm.ui_mode != "two_line":
                nm.stop_news_loop()
                nm.enabled = False
                gc.musiktitel_canvas.grid_remove()
                gc.musiktitel_down_label.grid()
            if gc.musiktitel_down_label.cget("text") != title_down:
                bg_color = getattr(nm, "COLOR_BG_IDLE", "#547a94")
                gc.musiktitel_down_label.config(
                    text=title_down, font=('Quicksand Medium', 12),
                    foreground="yellow", background=bg_color
                )
            nm.last_title_down = title_down
            nm.ui_mode = "two_line"
            return

        if nm.ui_mode != target_mode:
            if target_mode == "two_line":
                nm.stop_news_loop()
                gc.musiktitel_canvas.grid_remove()
                gc.musiktitel_down_label.grid()
                bg_color = getattr(nm, "COLOR_BG_IDLE", "#547a94")
                gc.musiktitel_down_label.config(
                    text=title_down, font=('Quicksand Medium', 12),
                    foreground="yellow", background=bg_color
                )
                nm.last_title_down = title_down
                nm.ui_mode = "two_line"
            else:
                gc.musiktitel_down_label.grid_remove()
                gc.musiktitel_canvas.grid()
                try:
                    active_bg = getattr(nm, "COLOR_BG_ACTIVE", "#410885")
                    gc.musiktitel_canvas.configure(bg=active_bg)
                except Exception:
                    pass
                nm.last_title_down = ""
                nm.ui_mode = "news"
                nm.enabled = True
                if nm.state in ("idle", "paused"):
                    nm.display_news()
                return

        if nm.ui_mode == "two_line":
            if gc.musiktitel_down_label.cget("text") != title_down:
                bg_color = getattr(nm, "COLOR_BG_IDLE", "#547a94")
                gc.musiktitel_down_label.config(
                    text=title_down, font=('Quicksand Medium', 12),
                    foreground="yellow", background=bg_color
                )
            nm.last_title_down = title_down
        else:
            nm.last_title_down = ""
            if nm.enabled and nm.state in ("idle", "paused"):
                try:
                    gc.musiktitel_down_label.grid_remove()
                    gc.musiktitel_canvas.grid()
                    gc.musiktitel_canvas.configure(bg=getattr(nm, "COLOR_BG_ACTIVE", "#410885"))
                except Exception:
                    pass
                nm.display_news()

    # --------- SQL-Datenbank Helper ---------
    def _iso_utc_now(self) -> str:
        return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    def _norm(self, s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def _key_for(self, sender: str, artist: str | None, title: str | None, genre: str | None, raw: str) -> tuple[str, str, str]:
        s_norm = self._norm(sender).casefold()
        if artist and title:
            a_norm = self._norm(artist).casefold()
            t_norm = self._norm(title).casefold()
            key = f"{s_norm}|{a_norm}|{t_norm}"
            kind = "parsed"
        else:
            r_norm = self._norm(raw).casefold()
            key = f"{s_norm}|raw|{r_norm}"
            kind = "raw"
        hid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return key, f"sha1:{hid}", kind

    def _passes_filters(self, raw: str) -> bool:
        txt = self._norm(raw)
        if len(txt) < self._min_chars:
            return False
        for rx in self._drop_rx:
            if rx.search(txt):
                return False
        return True

    def _should_store(self, key: str, now_s: float) -> bool:
        """Debounce: nur speichern, wenn Key neu ODER älter als X Sekunden."""
        if key != self._last_key:
            return True
        if (now_s - self._last_ts) >= self._debounce_s:
            return True
        return False

    def _mark_stored(self, key: str, now_s: float) -> None:
        self._last_key = key
        self._last_ts  = now_s

    def _dispatch_save(self, md: dict) -> None:
        """Dispatcher im Tk-Mainthread aufrufen (thread-safe)."""
        try:
            self.page.after(0, lambda: self.app.dispatcher.store_in_SQL(md))
        except Exception:
            # Fallback, wenn 'after' noch nicht bereit ist:
            self.app.dispatcher.store_in_SQL(md)

    def _schedule_bio_fetch(self, artist_name: str, track_key: str) -> None:
        """
        Startet eine Hintergrundabfrage für die Künstler-Biografie, ohne die GUI zu blockieren.

        - Läuft in einem separaten Thread (daemon=True)
        - Ergebnis wird im Tk-Hauptthread in app.state.artist_bio/_state übernommen
        - Über track_key wird sichergestellt, dass die Bio nur für den noch
          aktuellen Titel gesetzt wird.
        """
        artist_name = (artist_name or "").strip()
        if not artist_name or not track_key:
            return

        # Alte Bio vorübergehend deaktivieren – bis neue fertig geladen ist
        try:
            self.app.state.artist_bio_state = False
            self.app.state.artist_bio = ""
        except Exception:
            pass

        def worker():
            try:
                bio_text = Bio_url.fetch_artist_bio(artist_name)
            except Exception as e:
                print(f"[Bio] Fehler beim Laden für {artist_name!r}: {e}")
                bio_text = ""

            def apply_result():
                # Wenn inzwischen ein anderer Track aktiv ist, nichts tun
                if self._last_key != track_key:
                    return

                if bio_text and bio_text.strip():
                    self.app.state.artist_bio = bio_text
                    self.app.state.artist_bio_state = True
                    #print(f"Biographie von {artist_name}: {bio_text}")
                else:
                    # Keine brauchbare Bio gefunden → Flag wieder aus
                    self.app.state.artist_bio_state = False

            try:
                # Ergebnis sicher im Tk-Hauptthread anwenden
                self.page.after(0, apply_result)
            except Exception:
                traceback.print_exc()
                # Fallback, falls 'after' nicht verfügbar ist
                try:
                    apply_result()
                except Exception:
                    traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()


class NewsManager:
    def __init__(self, page):
        self.page = page
        self.app  = page.app

        # --- Zustände / Flags
        self.enabled          = True          # News generell erlaubt
        self.state            = "idle"        # idle|fetching|scrolling|paused
        self.last_title_down  = ""            # wird extern gesetzt, wenn zweizeiliger DLS aktiv ist
        self.last_news_text   = ""
        self.last_news_entry  = None
        self.ui_mode          = "news"        # "news" (einzeilig/Marquee) | "two_line" (zweizeilig DLS)

        # --- Canvas / Animation
        self.text_object      = None
        self.marquee_speed_pps = 30           # Pixel pro Sekunde (kleiner = langsamer)
        self._after_ids       = set()         # aktive after-Callbacks zum Canceln
        self._anim_after_id   = None          # aktuell laufender Canvas-Timer
        self.current_source   = "news"        # "news" oder "bio" – was gerade läuft

        # --- Farben/Fonts
        self.COLOR_BG_ACTIVE  = "#410885"
        self.COLOR_BG_IDLE    = "#547a94"
        self.COLOR_TEXT       = "#FFFFFF"
        self.FONT_NEWS        = ('Calibri', 12, 'italic')
        self.FONT_DLS         = ('Quicksand Medium', 12)

        # --- Feeds
        self.news_categories = {
            "Topmeldungen":  "https://www.tagesschau.de/xml/rss2",
            "Inland":        "https://www.tagesschau.de/inland/index~rss2.xml",
            "Ausland":       "https://www.tagesschau.de/ausland/index~rss2.xml",
            "Europe":        "https://www.tagesschau.de/ausland/europe/index~rss2.xml",
            "Wirtschaft":    "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
            "Sport":         "https://www.tagesschau.de/sport/index~rss2.xml",
            "Klima & Umwelt":"https://www.tagesschau.de/wissen/klima/index~rss2.xml",
        }
        self._cache_ttl_sec   = 180
        self._feed_cache      = {}  # {cat: {"ts": float, "items": [entries], "idx": int}}
        self._recent_ids      = []  # letzte N IDs/Links zur Deduplizierung
        self._recent_max      = 20
        self._http            = requests.Session()
        self._image_cache     = OrderedDict()
        self._image_cache_cap = 25

        self._executor = getattr(self.app, "executor", None) or ThreadPoolExecutor(max_workers=2, thread_name_prefix="News")

    # ----------------- Public API -----------------

    def on_news_click(self, event=None):
        self.stop_news_loop()
        self.enabled = True
        self._call_after(200, self.display_news)

    def display_news(self):
        # Wenn DLS zweiteilig angezeigt werden soll, DLS zeigen & News pausieren
        if self.last_title_down:
            self._show_dls_title()
            self.state = "paused"
            return

        if not self.enabled or self.state in ("fetching", "scrolling"):
            return

        self.state = "fetching"
        self._prepare_canvas_display()

        # Asynchron Headline + Bild ermitteln
        future = self._executor.submit(self._fetch_one_headline_and_image)
        # Ergebnis in den GUI-Thread „heben“
        self.page.after(0, lambda f=future: self._on_fetch_finished(f))

    def stop_news_loop(self):
        self.state = "idle"
        self.enabled = False
        # Timer sauber abbrechen
        for aid in list(self._after_ids):
            try:
                self.page.after_cancel(aid)
            except Exception:
                pass
            finally:
                self._after_ids.discard(aid)
        self._after_ids.clear()

        # Canvas auf „ruhig“ setzen
        canvas = self.page.gui_controller.musiktitel_canvas
        try:
            canvas.configure(bg=self.COLOR_BG_IDLE)
            if self.text_object:
                canvas.delete(self.text_object)
                self.text_object = None
        except Exception:
            pass

    # ----------------- Internals -----------------

    def _on_fetch_finished(self, future):
        """Callback im Tk-Thread, sobald der Worker-Thread die News geholt hat."""
        # Wenn News inzwischen deaktiviert oder Zustand nicht mehr passend → ignorieren
        if not self.enabled or self.state != "fetching":
            return

        # 1) Fehler im Worker?
        exc = future.exception()
        if exc is not None:
            print("[News] Fehler beim Laden:", exc)
            self.state = "idle"
            self._retry_later()   # nutzt Default 3000 ms
            return

        # 2) Ergebnis holen
        result = future.result()

        # Ergebnis in ein einheitliches Schema bringen:
        # - alte Variante: (text, entry, image_url)
        # - mögliche neue Variante: {"text": ..., "entry": ..., "image_path": ...}
        text = entry = img = None

        # a) altes Tuple-Format
        if isinstance(result, tuple) and len(result) == 3:
            text, entry, img = result

        # b) Dict-Format (für spätere Erweiterungen)
        elif isinstance(result, dict):
            text  = result.get("text") or ""
            entry = result.get("entry")
            img   = result.get("image_path") or result.get("image_url")

        # c) alles andere → Fehler + Retry
        else:
            print("[News] Unerwartetes Ergebnis (Typ):", type(result), result)
            self.state = "idle"
            self._retry_later()
            return

        # Kein Text → später erneut versuchen
        if not text:
            self.state = "idle"
            self._retry_later()
            return

        self.last_news_text  = text
        self.last_news_entry = entry

        # Wenn gerade zweizeiliger DLS (Titel unten) aktiv ist → News pausieren
        if self.last_title_down:
            self.state = "paused"
            self._show_dls_title()
            return

        # Bild (wenn im Logo-Modus) aktualisieren
        if img and getattr(self.page.image_manager, "logo_cover_slideshow_state", "") == "Logo":
            try:
                self._executor.submit(self._load_and_show_image, img)
            except Exception as ex:
                print("[News] Bild-Update fehlgeschlagen:", ex)

        # Canvas vorbereiten und News-Scroll starten (als Quelle "news")
        self._prepare_canvas_display()
        self._start_canvas_scroll(text, source="news")

    def _retry_later(self, ms: int = 3000):
        """News erst in ms Millisekunden wieder versuchen."""
        self.state = "idle"
        self._call_after(ms, self.display_news)

    def _fetch_one_headline_and_image(self):
        """Blocking – läuft im Threadpool."""
        import time
        cat, url = random.choice(list(self.news_categories.items()))
        now = time.time()

        # Cache verwenden
        bucket = self._feed_cache.get(cat)
        if not bucket or now - bucket["ts"] > self._cache_ttl_sec:
            feed = feedparser.parse(url)
            items = feed.entries or []
            self._feed_cache[cat] = {"ts": now, "items": items, "idx": 0}
            bucket = self._feed_cache[cat]

        items = bucket["items"]
        if not items:
            return None, None, None

        # Rotieren & deduplizieren
        for _ in range(len(items)):
            idx = bucket["idx"] % len(items)
            bucket["idx"] += 1
            entry = items[idx]
            title = (getattr(entry, "title", "") or "").strip()
            entry_id = getattr(entry, "id", None) or getattr(entry, "link", None) or title
            if not title:
                continue
            if entry_id in self._recent_ids:
                continue
            # Neu – akzeptieren
            self._recent_ids.append(entry_id)
            if len(self._recent_ids) > self._recent_max:
                self._recent_ids = self._recent_ids[-self._recent_max:]
            text = f"{cat}: {title}"
            image_url = self._extract_image_url(entry)
            return text, entry, image_url

        # Falls alles dedupliziert wurde
        return None, None, None

    def _extract_image_url(self, entry):
        # 1) media_content
        media = getattr(entry, "media_content", None)
        if media and isinstance(media, list):
            for m in media:
                url = m.get("url")
                if url:
                    return url
        # 2) links mit type=image/*
        links = getattr(entry, "links", None)
        if links:
            for l in links:
                if str(l.get("type", "")).startswith("image/") and l.get("href"):
                    return l["href"]
        # 3) HTML in content/summary
        html_parts = []
        for c in getattr(entry, "content", []):
            html_parts.append(c.get("value", ""))
        summary = getattr(entry, "summary", "")
        html_str = " ".join([summary] + html_parts)
        m = re.search(r'<img\s+[^>]*src="([^"]+)"', html_str, flags=re.I)
        return m.group(1) if m else None

    def _news_image_cache_get(self, image_url: str) -> Image.Image | None:
        if not image_url:
            return None
        img = self._image_cache.get(image_url)
        if img is not None:
            self._image_cache.move_to_end(image_url)
        return img

    def _news_image_cache_set(self, image_url: str, pil_img: Image.Image | None) -> None:
        if not image_url or pil_img is None:
            return
        self._image_cache[image_url] = pil_img
        self._image_cache.move_to_end(image_url)
        if len(self._image_cache) > self._image_cache_cap:
            self._image_cache.popitem(last=False)

    def _load_and_show_image(self, image_url):
        try:
            cached = self._news_image_cache_get(image_url)
            if cached is None:
                r = self._http.get(image_url, timeout=3)
                if "image" not in r.headers.get("Content-Type", ""):
                    return
                img = Image.open(BytesIO(r.content))
                resized = self.page.image_manager.prepare_image(img)
                self._news_image_cache_set(image_url, resized)
            else:
                resized = cached
            tk_img = ImageTk.PhotoImage(resized)
        except Exception as e:
            print(f"[NewsBild] Laden fehlgeschlagen: {e}")
            return

        def _apply():
            label = self.page.gui_controller.logo_label
            label.config(image=tk_img)
            label.image = tk_img
            self.page.image_manager.update_mode_label("Newsbild")

        self.page.after(0, _apply)

    # ----------------- Canvas & Scroll -----------------

    def _show_dls_title(self):
        canvas = self.page.gui_controller.musiktitel_canvas
        canvas.delete("all")
        canvas.configure(bg=self.COLOR_BG_IDLE)
        canvas.create_text(
            5, 12, anchor="w",
            text=self.last_title_down,
            font=self.FONT_DLS,
            fill="yellow"
        )

    def _prepare_canvas_display(self):
        canvas = self.page.gui_controller.musiktitel_canvas
        canvas.delete("all")
        canvas.configure(bg=self.COLOR_BG_ACTIVE)

    # -------- Bio-Helfer --------

    def _has_bio(self) -> bool:
        """
        True, wenn aktuell eine Biografie angezeigt werden darf:
        - app.state.artist_bio_state == True
        - sinnvoller Text in app.state.artist_bio
        """
        state = getattr(self.app, "state", None)
        if state is None:
            return False
        if not getattr(state, "artist_bio_state", False):
            return False
        bio = getattr(state, "artist_bio", "") or ""
        return bool(str(bio).strip())

    def _format_bio_text(self) -> str | None:
        """
        Formatiert den Biografie-String inkl. Prefix.
        """
        if not self._has_bio():
            return None

        state  = self.app.state
        artist = (getattr(state, "artist_n", "") or "").strip()
        bio    = (getattr(state, "artist_bio", "") or "").strip()
        if not bio:
            return None

        if artist:
            prefix = f"Biographie von {artist}: "
        else:
            prefix = "Biographie: "
        return prefix + bio

    def _decide_next_source(self, finished_source: str) -> str:
        """
        Entscheidet, ob nach 'finished_source' News oder Bio dran sind.
        - Wenn keine Bio aktiv ist → immer 'news'
        - Wenn Bio aktiv ist → News/Bio abwechselnd:
          News → Bio → News → Bio → ...
        """
        if not self._has_bio():
            return "news"

        if finished_source == "news":
            return "bio"
        return "news"

    def _on_scroll_finished(self, finished_source: str) -> None:
        """
        Wird aufgerufen, nachdem eine komplette Laufschrift einmal
        bis zum linken Rand durchgelaufen ist.
        """
        # Falls zwischenzeitlich deaktiviert → nichts tun
        if not self.enabled:
            return

        # Zweizeiliger DLS-Text hat immer Vorrang
        if self.last_title_down:
            self.ui_mode = "two_line"
            self._show_dls_title()
            self.state = "paused"
            return

        # Nächste Quelle bestimmen (News/Bio)
        next_source = self._decide_next_source(finished_source)

        bio_text = None
        if next_source == "bio":
            bio_text = self._format_bio_text()
            if not bio_text:
                # Biografie nicht (mehr) verfügbar → auf News zurückfallen
                next_source = "news"

        # Sicherstellen, dass es News-Text gibt
        if next_source == "news":
            if not self.last_news_text:
                # Noch kein Text → neue News laden
                self.display_news()
                return
            text = self.last_news_text
        else:
            # Bio
            text = bio_text

        # Nächste Laufschrift starten
        self._prepare_canvas_display()
        self._start_canvas_scroll(text, source=next_source)

    def _start_canvas_scroll(self, text: str, source: str = "news"):
        """
        Startet eine neue Laufschrift (News oder Bio).
        - Läuft einmal komplett durch.
        - Am Ende wird _on_scroll_finished(...) aufgerufen, der über
          News/Bio-Wechsel entscheidet.
        """
        self.state = "scrolling"
        self.current_source = source

        canvas = self.page.gui_controller.musiktitel_canvas

        # Text-Objekt vorbereiten
        scroll_text = "➜ " + text + " ⚫"
        if self.text_object:
            canvas.delete(self.text_object)
            self.text_object = None

        canvas_width = int(canvas.cget("width"))
        x_start = canvas_width + 10

        self.text_object = canvas.create_text(
            x_start, 12, anchor="w",
            text=scroll_text,
            font=self.FONT_NEWS,
            fill=self.COLOR_TEXT,
        )
        self.page.update_idletasks()
        x0, _, x1, _ = canvas.bbox(self.text_object)

        # --- Zeitbasierte Parameter
        px_per_second = max(
            5,
            float(getattr(self, "marquee_speed_pps", 25))
        )  # Pixel pro Sekunde
        frame_ms = 20  # ca. 50 FPS Rendering
        self._anim_after_id = None
        last_t = time.perf_counter()
        carry = 0.0

        def animate():
            nonlocal last_t, carry
            # Abbrechen, wenn wir nicht mehr scrollen sollen
            if self.state != "scrolling" or not self.text_object:
                return

            now = time.perf_counter()
            dt = now - last_t
            last_t = now
            if dt > 0.1:  # große Sprünge glätten
                dt = 0.1

            move_f = px_per_second * dt + carry
            move_px = int(move_f)
            carry = move_f - move_px
            if move_px < 1:
                self._anim_after_id = self.page.after(frame_ms, animate)
                return

            canvas.move(self.text_object, -move_px, 0)
            a0, _, a1, _ = canvas.bbox(self.text_object)

            if a1 < 0:
                # Ein kompletter Durchlauf ist fertig: Text ist links raus
                self.state = "idle"
                canvas.delete(self.text_object)
                self.text_object = None
                self._anim_after_id = None

                # Nächsten Schritt (News/Bio-Wechsel) im Tk-Thread planen
                self._call_after(0, lambda: self._on_scroll_finished(source))
                return

            self._anim_after_id = self.page.after(frame_ms, animate)
        # Start
        self._anim_after_id = self.page.after(frame_ms, animate)

    # ----------------- helpers -----------------
    def _call_after(self, ms, func):
        aid = self.page.after(ms, func)
        self._after_ids.add(aid)
        return aid


class SearchController:
    def __init__(self, page):
        self.page = page
        self.app  = page.app
        self.matched_indexes: list[int] = []
        self.keyboard_state = False

    def hide_all_listboxes(self):
        mainpage = self.page.gui_controller
        for widget in [
            mainpage.lboxSenderliste, mainpage.scbar1,
            mainpage.lboxRead, mainpage.scbar3,
            mainpage.lboxChart, mainpage.scbar4,
            mainpage.lboxTyp, mainpage.scbar5
        ]:
            widget.grid_remove()

    def create_search_controls(self):
        mainpage_listbox = self.page.ListBox_frame
        # Entry-Feld für Suche
        self.entry = tk.Entry(mainpage_listbox, width=19)
        self.entry.grid(column=0, row=2, sticky=tk.W, padx=(10, 0), pady=(10, 0))
        self.entry.grid_remove()  # Anfangs verstecken
        # Listbox für Suchergebnisse
        self.lboxSearchResult = Listbox(mainpage_listbox, width=19, height=12, justify=tk.CENTER)
        self.lboxSearchResult.bind("<<ListboxSelect>>", self.on_select_Search)
        self.lboxSearchResult.grid(column=0, row=3, sticky=tk.W, padx=(10, 0), pady=(30, 0))
        self.lboxSearchResult.grid_remove()
        # Scrollbar für Listbox
        self.scbar6 = ttk.Scrollbar(mainpage_listbox, orient=tk.VERTICAL, command=self.lboxSearchResult.yview)
        self.scbar6.grid(column=1, row=3, sticky=tk.NS, padx=(0, 0), pady=(5, 0))
        self.scbar6.grid_remove()
        self.lboxSearchResult['yscrollcommand'] = self.scbar6.set
        self.entry.bind("<FocusIn>", lambda event: self.open_virtual_keyboard())
        self.entry.bind("<FocusOut>", lambda event: self.close_virtual_keyboard())

    def Suche_DAB_Sender(self):
        # Der Suchstring (kann zwischen 1 und 5 Buchstaben haben)
        current_input = self.entry.get()
        matched_labels = []
        search_length = min(len(current_input), 5)
        self.matched_indexes.clear()
        print(f"Suche_DAB_Sender {current_input}")
        # Sucht nach dem Suchstring von Beginn im Element
        for index, sender in enumerate(self.app.state.Sender_Name):
            if sender[:search_length].lower().startswith(current_input.lower()):
                matched_labels.append(self.app.state.Sender_Name[index])
                self.matched_indexes.append(index)
        for index, sender in enumerate(self.app.state.Sender_Name):
            if current_input.lower() in sender.lower():  # Sucht nach dem Suchstring im gesamten Element
                matched_labels.append(self.app.state.Sender_Name[index])
                print(f"Gefundene Labels: {self.app.state.Sender_Name[index]}")
                self.matched_indexes.append(index)
        print(f"Gefundene Labels: {matched_labels}")
        # Listbox lboxSearchResult aktualisieren:
        self.lboxSearchResult.delete(0, 'end')
        for i, item in enumerate(matched_labels):
            self.lboxSearchResult.insert('end', item)
            if i % 2 == 0:
                self.lboxSearchResult.itemconfigure(i, background='#f2c9f1')

    def Search_Sender(self):
        mainpage = self.page.gui_controller
        self.hide_all_listboxes()
        if not self.keyboard_state:
            self.entry.grid(column=1, row=0, sticky=tk.W, padx=(120, 0), pady=(10, 0))
            self.lboxSearchResult.grid(column=1, row=1, sticky=tk.NW, padx=(120, 0), pady=(5, 0))
            self.scbar6.grid(column=1, row=1, sticky=tk.NS, padx=(275, 0), pady=(5, 0))
            # Buttons deaktivieren
            for button in [mainpage.button1, mainpage.button3, mainpage.button4]:
                button.state(["disabled"])
            self.open_virtual_keyboard()
            self.keyboard_state = True
        else:
            self.entry.grid_remove()
            self.lboxSearchResult.grid_remove()
            self.scbar6.grid_remove()
            
            # Normale Ansicht wiederherstellen
            mainpage.lboxSenderliste.grid(column=0, row=1, sticky=tk.NW, padx=(10, 0), pady=(5, 0))
            mainpage.scbar1.grid(column=0, row=1, sticky=tk.NS, padx=(175, 0), pady=(5, 0))
            mainpage.lboxChart.grid(column=1, row=1, sticky=tk.NW, padx=(10, 0), pady=(5, 0))
            mainpage.scbar4.grid(column=1, row=1, sticky=tk.NS, padx=(160, 0), pady=(5, 0))

            # Buttons wieder aktivieren
            for button in [mainpage.button1, mainpage.button3, mainpage.button4]:
                button.state(["!disabled"])
            self.close_virtual_keyboard()
            self.keyboard_state = False

    def create_frame_keyboard(self):
        # Bereits offen? -> nichts tun
        if getattr(self, "virtual_keyboard", None) and self.virtual_keyboard.winfo_exists():
            # Beim erneuten Öffnen nur neu positionieren
            self._place_keyboard()
            return

        # 1) Parent = gesamte MainPage -> kein Clipping durch Info_frame
        parent = self.page
        self.virtual_keyboard = tk.Frame(parent, bd=2, relief="raised", bg="#f9f9f9")

        # 2) Tasten aufbauen
        self.keys = [
            '1','2','3','4','5','6','7','8','9','0','&',
            '@','. ','=','+','/','*','#','? ','! ','-','%',
            'Q','W','E','R','T','Z','U','I','O','P','ü',
            'A','S','D','F','G','H','J','K','L','ö','ä',
            ' ',' ','Y','X','C','V','B','N','M',' '
        ]
        self.lowercase = False
        self.buttons = []

        row_val, col_val = 0, 0
        for key in self.keys:
            cmd = lambda x=key: self.click(x)
            btn = tk.Button(self.virtual_keyboard, text=key, width=1, command=cmd)
            btn.grid(row=row_val, column=col_val, padx=1, pady=1)
            self.buttons.append(btn)
            col_val += 1
            if col_val > 10:
                col_val = 0
                row_val += 1

        self.shift_button  = tk.Button(self.virtual_keyboard, text="⇧", width=1, command=self.shift)
        self.delete_button = tk.Button(self.virtual_keyboard, text="⌫", width=1, command=self.delete_last_character)
        self.shift_button.grid(row=row_val, column=0, padx=1, pady=1, sticky=tk.W)
        self.delete_button.grid(row=row_val, column=1, padx=1, pady=1, sticky=tk.W)

        # 3) Overlay-Position bestimmen & anzeigen
        self._place_keyboard()

        # 4) Bei Fenster-/Layoutänderung neu positionieren (Binding einmalig setzen/überschreiben)
        self.page.bind("<Configure>", lambda e: self._place_keyboard())

    def _place_keyboard(self):
        """Positioniert die Tastatur relativ zum Info_frame und sorgt dafür,
        dass sie vollständig innerhalb des Fensters sichtbar bleibt."""
        if not getattr(self, "virtual_keyboard", None):
            return
        self.page.update_idletasks()

        # Basis: linke obere Ecke des Info_frame + kleiner Versatz nach unten
        info = self.page.Info_frame
        base_x = info.winfo_x()
        base_y = info.winfo_y() + 30   # unterhalb des Logos

        # Gewünschte Breite/Höhe der Tastatur
        kb_w = self.virtual_keyboard.winfo_reqwidth()
        kb_h = self.virtual_keyboard.winfo_reqheight()

        # Fensterbreite/-höhe (MainPage)
        page_w = self.page.winfo_width()
        page_h = self.page.winfo_height()

        # Falls rechts über den Rand -> nach links schieben
        x = base_x
        if x + kb_w > page_w:
            x = max(0, page_w - kb_w - 5)

        # Falls unten über den Rand -> nach oben schieben
        y = base_y
        if y + kb_h > page_h:
            y = max(0, page_h - kb_h - 5)

        # Als Overlay platzieren und in den Vordergrund holen
        self.virtual_keyboard.place(x=x, y=y)
        self.virtual_keyboard.lift()

    def open_virtual_keyboard(self):
        print("open_virtual_keyboard")
        self.create_frame_keyboard() # erstellt (oder repositioniert) + zeigt an

    def close_virtual_keyboard(self):
        print("close_virtual_keyboard")
        vk = getattr(self, "virtual_keyboard", None)
        if vk and vk.winfo_exists():
            vk.destroy() # vollständig entfernen
        self.virtual_keyboard = None
        
    def delete_last_character(self):        # Button "Delete"
        current_text = self.entry.get()
        if current_text:                    # Überprüfen, ob der Text nicht leer ist
            new_text = current_text[:-1]    # Entferne das letzte Zeichen 
            self.entry.delete(0, tk.END)    # Lösche das Eingabefeld
            self.entry.insert(0, new_text)  # Setze den neuen Text
        self.Suche_DAB_Sender()

    def click(self, key):
        # Zeichen, welches auf dem virtuellen Keyboard gedrückt wurde verarbeiten
        if self.lowercase:
            self.entry.insert(tk.END, key.lower())
        else:
            self.entry.insert(tk.END, key)
        self.Suche_DAB_Sender()

    def shift(self): # Button "Shift"
        self.lowercase = not self.lowercase
        for i, button in enumerate(self.buttons):
            key = self.keys[i]
            if self.lowercase:
                button.config(text=key.lower())
            else:
                button.config(text=key)

    def SearchResult(self):
        aktuell_ausgewaehlt = self.lboxSearchResult.curselection()
        if aktuell_ausgewaehlt: # Sicherstellen, dass eine Auswahl existiert
            index = aktuell_ausgewaehlt[0]
            self.app.state.AktuelleSenderId = self.matched_indexes[index]
            search_idx = self.app.state.AktuelleSenderId
            self.app.dispatcher.submit(lambda: self.app.tune_service(search_idx), key="tune")

    def on_select_Search(self, event):
        aktuell_ausgewaehlt = self.lboxSearchResult.curselection()
        if aktuell_ausgewaehlt:
            self.SearchResult()


class ImageManager:
    def __init__(self, page):
        self.page = page
        self.app  = page.app
        self.last_successful_image = None
        self.last_successful_image_raw = None
        self.logo_cover_slideshow_state = "Logo" # mögliche Werte: 'Logo', 'Cover', 'Slideshow'
        self.direction = "inkrement"
        self.cover_slideshow_running = False
        self.cover_slideshow_index = 0
        self.cover_slideshow_list = []
        self.cover_slideshow_job = None
        self._http = requests.Session()
        self._cover_url_cache = OrderedDict()
        self._cover_cache_cap = 200 # max. Cache-Einträge
        self._cover_image_cache = OrderedDict()
        self._cover_image_cache_cap = 60

    def SenderTyp_Ensemble_on_Display(self):
        try:
            sender_idx = int(self.app.state.AktuelleSenderId)

            pty_txt  = None
            ensemble = None
            try:
                import sqlite3
                db_path = self.app.config_data.get("dab_scan_db")
                if db_path:
                    with sqlite3.connect(db_path, timeout=5) as con:
                        cur = con.execute(
                            "SELECT pty_txt, ensemble FROM si4689_datenbank "
                            "WHERE si4689_idx = ? LIMIT 1;",
                            (sender_idx,),
                        )
                        row = cur.fetchone()
                        if row:
                            pty_txt, ensemble = row
            except Exception as db_exc:
                print(f"[PTY/ENSEMBLE] DB-Fehler: {db_exc}")

            if pty_txt:
                self.page.gui_controller.programtyp.config(text=f"Genre: {pty_txt}")
            else:
                self.page.gui_controller.programtyp.config(text="Genre: —")

            if ensemble:
                self.page.gui_controller.Ensemble_Sender.config(text=f"{ensemble}/{self.app._current_channel:}")
            else:
                self.page.gui_controller.Ensemble_Sender.config(text="—")

        except Exception as e:
            print(f"[PTY/ENSEMBLE] Allgemeiner Fehler: {e}")

        
    def SenderEnsemble_on_Display(self):
        try:
            sender_idx = int(self.app.state.AktuelleSenderId)
            # ensemble aus DB si4689_datenbank lesen (sender_idx = SI4689_IDX)
            pty_txt = None
            try:
                import sqlite3
                db_path = self.app.config_data.get("dab_scan_db")
                if db_path:
                    with sqlite3.connect(db_path, timeout=5) as con:
                        cur = con.execute(
                            "SELECT pty_txt FROM si4689_datenbank "
                            "WHERE si4689_idx = ? LIMIT 1;",
                            (sender_idx,),
                        )
                        row = cur.fetchone()
                        if row:
                            ensemble = row[0]
            except Exception as db_exc:
                print(f"[PTY] DB-Fehler: {db_exc}")

            if ensemble:
                self.page.gui_controller.Ensemble_Sender.config(text=f"{ensemble}")
            else:
                self.page.gui_controller.Ensemble_Sender.config(text=" —")

        except Exception as e:
            print(f"[Ensemble] Allgemeiner Fehler: {e}")


# ---------------Senderlogo------------------------------------
    def Logo_on_Display(self):
        image_path = self.find_sender_logo()
        if not image_path:
            image_path = self.get_fallback_logo()
        self.display_image(image_path)

    def find_sender_logo(self):
        path = self.app.config_data["logo_dir"]
        sender_name = self.app.state.AktuellerSender
        for ext in ['.png', '.jpg']:
            candidate = os.path.join(path, f"{sender_name}{ext}")
            if os.path.exists(candidate):
                return candidate
        return None
    
    def get_fallback_logo(self):
        return self.app.config_data["fallback_logo"]
    
    def display_image(self, image_path):
        try:
            image = Image.open(image_path)
            resized = self.prepare_image(image)
            tk_image = ImageTk.PhotoImage(resized)
            self.last_successful_image = tk_image
            self.last_successful_image_raw = resized
            label = self.page.gui_controller.logo_label
            label.config(image=tk_image)
            label.image = tk_image # verhindert, dass das Bild vergessen wird
        except (FileNotFoundError, UnidentifiedImageError, OSError) as e:
            print(f"Fehler beim Laden des Bildes: {e}")
            self.display_last_successful()

    def prepare_image(self, image):
        target_size = (220, 220)
        image.thumbnail(target_size, Image.LANCZOS)
        final_image = Image.new("RGBA", target_size, (255, 255, 255, 0))
        pos = ((target_size[0] - image.size[0]) // 2, (target_size[1] - image.size[1]) // 2)
        final_image.paste(image, pos)
        return final_image

    def display_last_successful(self):
        if self.last_successful_image:
            self.page.gui_controller.logo_label.config(image=self.last_successful_image)
        else:
            fallback_path = self.get_fallback_logo()
            self.display_image(fallback_path)

    def Cover_on_Display_async(self):
        """Nicht-blockierende Variante: führt Cover_on_Display beim nächsten Idle aus."""
        try:
            self.page.after_idle(self.Cover_on_Display)
        except Exception:
            # wenn page/after_idle nicht verfügbar ist
            self.Cover_on_Display()

    def Cover_on_Display(self):
        """
        Zeigt im Modus 'Cover' das zuletzt gespeicherte (Artist, Title) Coverbild an.
        - Holt (artist, song) aus SQLite im Worker.
        - Sucht iTunes-Cover (fetch_cover_url) im Worker.
        - Lädt/Skaliert Bild im Worker.
        - Setzt das Tk-Image ausschließlich im UI-Thread.
        - Verwendet Fallback-Cover bei Fehlern.
        """
        if self.logo_cover_slideshow_state != "Cover":
            return

        def _ui_set_image(pil_img, text=None):
            # Sicherheitscheck: Nur anwenden, wenn wir immer noch im Cover-Modus sind
            if self.logo_cover_slideshow_state != "Cover":
                return
            try:
                tk_img = ImageTk.PhotoImage(pil_img)
                self.last_successful_image = tk_img
                self.last_successful_image_raw = pil_img
                lbl = self.page.gui_controller.logo_label
                lbl.config(image=tk_img)
                lbl.image = tk_img
                if text is not None:
                    self.page.gui_controller.Sender_logo.config(text=text)
            except Exception:
                self.display_fallback_cover()
                self.page.gui_controller.Sender_logo.config(text="Fehler beim Laden")

        def _ui_fallback(text):
            if self.logo_cover_slideshow_state != "Cover":
                return
            self.display_fallback_cover()
            self.page.gui_controller.Sender_logo.config(text=text)

        def _worker():
            try:
                # 1) Letzten Track des aktuellen Senders aus der DB lesen
                # ▼▼▼ DatabaseManager bevorzugt ▼▼▼
                db_manager = getattr(self.app, 'music_db_manager', None)
                
                if db_manager:
                    # BESTE OPTION: DatabaseManager (automatisches Cleanup)
                    with db_manager.get_cursor() as (conn, cursor):
                        cursor.execute("""
                            SELECT artist, COALESCE(song, title) AS song
                            FROM music_log
                            WHERE sender = ?
                            ORDER BY id DESC
                            LIMIT 1
                        """, (self.app.state.AktuellerSender,))
                        row = cursor.fetchone()
                
                else:
                    # FALLBACK: Direkter Connect
                    cfg = getattr(self.app, "config_data", {}) or {}
                    db_path = cfg.get("music_data_db")
                    if not isinstance(db_path, str) or not db_path:
                        try:
                            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                            db_path = os.path.join(root, "assets", "DB", "music_data.sqlite")
                        except Exception:
                            db_path = os.path.join(os.path.expanduser("~"), "music_data.sqlite")
                    
                    with sqlite3.connect(db_path) as conn:
                        c = conn.cursor()
                        c.execute("""
                            SELECT artist, COALESCE(song, title) AS song
                            FROM music_log
                            WHERE sender = ?
                            ORDER BY id DESC
                            LIMIT 1
                        """, (self.app.state.AktuellerSender,))
                        row = c.fetchone()
                # ▲▲▲
                
                if not row or not (row[0] and row[1]):
                    self.app.after(0, lambda: _ui_fallback("Kein Cover"))
                    return

                # 2) Bereinigen
                artist = self.clean(row[0])
                song   = self.clean(row[1])
                if not artist or not song:
                    self.app.after(0, lambda: _ui_fallback("Kein Cover"))
                    return

                # 3) Cover-URL ermitteln (LRU-cache)
                cover_url = self._get_cover_url_cached(artist, song)
                if not cover_url:
                    self.app.after(0, lambda: _ui_fallback("Kein Cover"))
                    return

                # 4) Bild laden + vorbereiten
                try:
                    cached = self._cover_image_cache_get(cover_url)
                    if cached is not None:
                        self.app.after(0, lambda: _ui_set_image(cached))
                        return
                    resp = self._http.get(cover_url, timeout=5)
                    resp.raise_for_status()
                    image = Image.open(BytesIO(resp.content))
                    resized = self.prepare_image(image)
                    self._cover_image_cache_set(cover_url, resized)
                    self.app.after(0, lambda: _ui_set_image(resized))
                except Exception:
                    self.app.after(0, lambda: _ui_fallback("Fehler beim Laden"))

            except Exception as e:
                print(f"⚠️ Fehler in Cover_on_Display(): {e}")
                traceback.print_exc()
                self.app.after(0, lambda: _ui_fallback("Fehler beim Laden"))

        # Immer asynchron ausführen (UI nicht blockieren) – ältere Cover-Jobs verwerfen
        try:
            self.app.dispatcher.submit(_worker, key="cover")
        except Exception:
            # Fallback, falls kein Dispatcher verfügbar ist
            import threading
            threading.Thread(target=_worker, daemon=True).start()

    def display_fallback_cover(self):
        fallback_path = self.app.config_data.get("fallback_cover", self.get_fallback_logo())
        self.display_image(fallback_path)

    def clean(self, text): # Klammern und unerwünschte Zusätze entfernen
        import re
        text = re.sub(r"\([^)]*\)", "", text)  # (1981)
        text = re.sub(r"\[[^]]*\]", "", text)  # [Remix]
        text = re.sub(r"(feat\.?|ft\.?)\s+[^\-,]+", "", text, flags=re.IGNORECASE) # feat./ft.
        text = re.sub(r"\s{2,}", " ", text)  # doppelte Leerzeichen
        return text.strip()

    def Slideshow_on_Display(self):
        if not self.prepare_slideshow_list():
            return
        self.cover_slideshow_running = True
        self.schedule_next_cover()

    # 1) Helper-Funktion für prepare_slideshow):
    def _fetch_slideshow_rows(self, cursor, current_sender, today):
        """Holt Slideshow-Rows aus DB (heute + Fallback)"""
        # 1) Heute
        cursor.execute("""
            SELECT artist, COALESCE(song, title) AS song
            FROM music_log
            WHERE sender = ?
            AND DATE(COALESCE(ts_local, ts_utc, timestamp)) = ?
            ORDER BY COALESCE(ts_local, ts_utc, timestamp) ASC
        """, (current_sender, today))
        rows = cursor.fetchall()
        
        # 2) Fallback
        if not rows:
            cursor.execute("""
                SELECT artist, COALESCE(song, title) AS song
                FROM music_log
                WHERE sender = ?
                ORDER BY COALESCE(ts_local, ts_utc, timestamp) DESC
                LIMIT 40
            """, (current_sender,))
            rows = cursor.fetchall()
        
        return rows

    # 2) prepare_slideshow_list() refactoren:
    def prepare_slideshow_list(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            current_sender = self.app.state.AktuellerSender
            
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    rows = self._fetch_slideshow_rows(cursor, current_sender, today)
            
            else:
                # Fallback
                cfg = getattr(self.app, "config_data", {}) or {}
                db_path = cfg.get("music_data_db")
                if not isinstance(db_path, str) or not db_path:
                    try:
                        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                        db_path = os.path.join(root, "assets", "DB", "music_data.sqlite")
                    except Exception:
                        db_path = os.path.join(os.path.expanduser("~"), "music_data.sqlite")
                
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    rows = self._fetch_slideshow_rows(c, current_sender, today)
            
            # Verarbeitung
            valid_entries = []
            for artist, song in rows:
                artist = self.clean(artist)
                song = self.clean(song)
                cover_url = self._get_cover_url_cached(artist, song)
                if cover_url:
                    valid_entries.append((artist, song, cover_url))
            
            if not valid_entries:
                print("⚠️ Keine gültigen Covers für Slideshow gefunden.")
                return False
            
            self.cover_slideshow_list = valid_entries
            self.cover_slideshow_index = 0
            print(f"🖼️  Slideshow geladen mit {len(valid_entries)} gültigen Covern.")
            return True
            
        except Exception as e:
            print(f"❌ Fehler beim Laden der Slideshow-Titel: {e}")
            return False

    def schedule_next_cover(self):
        if not self.cover_slideshow_running or not self.cover_slideshow_list:
            return
        def after_display():
            self.cover_slideshow_index = (self.cover_slideshow_index + 1) % len(self.cover_slideshow_list)
            self.cover_slideshow_job = self.app.after(6000, self.schedule_next_cover)
        _, _, cover_url = self.cover_slideshow_list[self.cover_slideshow_index]
        self.show_cover_url_async(cover_url, on_display_done=after_display)

    def reset_cover_slideshow(self):
        self.cover_slideshow_list = []
        self.cover_slideshow_index = 0
        self.stop_cover_slideshow()

    def stop_cover_slideshow(self):
        self.cover_slideshow_running = False
        if self.cover_slideshow_job:
            self.app.after_cancel(self.cover_slideshow_job)
            self.cover_slideshow_job = None

    def handle_cover_button(self):
        # --Senderwahl setz self.logo_cover_slideshow_state auf "Logo" und zeigt self.Logo_on_Display()--

        if self.logo_cover_slideshow_state == "Logo":
            self.direction = "inkrement"
            self.logo_cover_slideshow_state = "Cover"
            self.update_mode_label("Cover")
            self.Cover_on_Display()
            return
        if self.logo_cover_slideshow_state == "Cover" and self.direction=="inkrement":
            self.logo_cover_slideshow_state = "Slideshow"
            self.update_mode_label("Slideshow")
            self.Slideshow_on_Display()
            return
        if self.logo_cover_slideshow_state == "Cover" and self.direction=="decrement":
            self.logo_cover_slideshow_state = "Logo"
            self.update_mode_label("Logo")
            self.Logo_on_Display()
            return
        if self.logo_cover_slideshow_state == "Slideshow":
            self.direction = "decrement"
            self.reset_cover_slideshow()
            self.logo_cover_slideshow_state = "Cover"
            self.update_mode_label("Cover")
            self.Cover_on_Display()
            return

    def update_mode_label(self, mode_text):
        self.page.gui_controller.Sender_logo.config(text=mode_text)

    # --------- Helfer: URL/Image LRU ----------
    def _cover_cache_key(self, artist: str, song: str) -> str:
        a = (artist or "").strip().casefold()
        s = (song or "").strip().casefold()
        if not a and not s:
            return ""
        return f"{a}|{s}"

    def _cover_cache_get_url(self, artist: str, song: str) -> str | None:
        key = self._cover_cache_key(artist, song)
        if not key:
            return None
        url = self._cover_url_cache.get(key)
        if url:
            self._cover_url_cache.move_to_end(key)
        return url

    def _cover_cache_set_url(self, artist: str, song: str, url: str | None) -> None:
        if not url:
            return
        key = self._cover_cache_key(artist, song)
        if not key:
            return
        self._cover_url_cache[key] = url
        self._cover_url_cache.move_to_end(key)
        if len(self._cover_url_cache) > self._cover_cache_cap:
            self._cover_url_cache.popitem(last=False)

    def _get_cover_url_cached(self, artist: str, song: str) -> str | None:
        url = self._cover_cache_get_url(artist, song)
        if url:
            return url
        try:
            url = Cover_url.fetch_cover_url(artist, song)
        except Exception:
            return None
        if url:
            self._cover_cache_set_url(artist, song, url)
        return url

    def _cover_image_cache_get(self, cover_url: str) -> Image.Image | None:
        if not cover_url:
            return None
        img = self._cover_image_cache.get(cover_url)
        if img is not None:
            self._cover_image_cache.move_to_end(cover_url)
        return img

    def _cover_image_cache_set(self, cover_url: str, pil_img: Image.Image | None) -> None:
        if not cover_url or pil_img is None:
            return
        self._cover_image_cache[cover_url] = pil_img
        self._cover_image_cache.move_to_end(cover_url)
        if len(self._cover_image_cache) > self._cover_image_cache_cap:
            self._cover_image_cache.popitem(last=False)

    def _set_label_image(self, pil_image):
        tk_image = ImageTk.PhotoImage(pil_image)
        self.last_successful_image = tk_image
        self.last_successful_image_raw = pil_image
        lbl = self.page.gui_controller.logo_label
        lbl.config(image=tk_image)
        lbl.image = tk_image

    def show_cover_async(self, artist, song, on_display_done=None):
        """Lädt ein Coverbild im Worker und setzt es thread-sicher im UI (mit Cache)."""
        artist = self.clean(artist)
        song = self.clean(song)

        def worker():
            cover_url = self._get_cover_url_cached(artist, song)
            if not cover_url:
                self.app.after(
                    0,
                    lambda: (
                        self.display_fallback_cover(),
                        self.page.gui_controller.Sender_logo.config(text="Kein Cover"),
                    ),
                )
                return

            try:
                cached = self._cover_image_cache_get(cover_url)
                if cached is not None:
                    self.app.after(
                        0,
                        lambda: (
                            self._set_label_image(cached),
                            on_display_done and on_display_done(),
                        ),
                    )
                    return
                resp = self._http.get(cover_url, timeout=5)
                resp.raise_for_status()
                pil_img = Image.open(BytesIO(resp.content))
                resized = self.prepare_image(pil_img)
                self._cover_image_cache_set(cover_url, resized)
            except Exception:
                self.app.after(
                    0,
                    lambda: (
                        self.display_fallback_cover(),
                        self.page.gui_controller.Sender_logo.config(text="Fehler beim Laden"),
                    ),
                )
                return

            self.app.after(
                0,
                lambda: (
                    self._set_label_image(resized),
                    on_display_done and on_display_done(),
                ),
            )

        self.app.dispatcher.submit(worker, key="cover")

    def show_cover_url_async(self, cover_url, on_display_done=None):
        """Wie oben, aber URL bereits bekannt (Slideshow)."""
        def worker():
            try:
                cached = self._cover_image_cache_get(cover_url)
                if cached is not None:
                    self.app.after(0, lambda: (self._set_label_image(cached), on_display_done and on_display_done()))
                    return
                resp = self._http.get(cover_url, timeout=5)
                if not resp.ok:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                pil = Image.open(BytesIO(resp.content))
                resized = self.prepare_image(pil)
                self._cover_image_cache_set(cover_url, resized)
            except Exception:
                self.app.after(0, self.display_fallback_cover)
                return
            self.app.after(0, lambda: (self._set_label_image(resized), on_display_done and on_display_done()))
        self.app.dispatcher.submit(worker, key="cover")

    def display_news_image(self, image_url):
        try:
            response = self._http.get(image_url, timeout=8)
            # ⬅️ Gültigkeit des Bildes prüfen
            if "image" not in response.headers.get("Content-Type", ""):
                raise ValueError("Keine gültige Bilddatei")
            img = Image.open(BytesIO(response.content))
            resized = self.prepare_image(img)
            tk_image = ImageTk.PhotoImage(resized)
            label = self.page.gui_controller.logo_label
            label.config(image=tk_image)
            label.image = tk_image  # ← verhindert, dass das Bild vergessen wird!
            self.update_mode_label("Newsbild")
        except Exception as e:
            print(f"[NewsBild] Fehler beim Laden: {e}")
            self.display_fallback_cover()
            self.update_mode_label("Newsbild Fehler")

__all__ = ["MainPage"]