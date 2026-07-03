#!/usr/bin/env python3.11.2 ('my_venv':venv)

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

import tkinter as tk
from .base_page import BasePage
from datetime import datetime, timedelta
import tkinter.font as tkfont
import threading


class EPGWidget:
    """
    EPG-Widget mit 6-Programm-Timeline
    
    Timeline-Layout (756px breit):
    │Prog-90|Prog-60|Prog-30|Prog0  |Prog+30|Prog+60│
    │0      |126    |256    |382    |508    |634    │756
    """
    
    def __init__(self, canvas, epg_manager, app, business_unit='srf', 
                 broadcast_type='radio', station_name='SRF 1', station_id='srf-1'):
        """
        Args:
            canvas: tk.Canvas zum Zeichnen
            epg_manager: EPGManager-Instanz
            app: App-Referenz für GUI-Batcher
            business_unit: 'srf', 'rts', 'rsi'
            broadcast_type: 'radio' oder 'tv'
            station_name: Anzeigename (z.B. 'SRF 1')
            station_id: API-ID (z.B. 'srf-1')
        """
        self.canvas = canvas
        self.epg_manager = epg_manager
        self.app = app
        self.business_unit = business_unit
        self.broadcast_type = broadcast_type
        self.station_name = station_name
        self.station_id = station_id
        self._next_update_job = None    # ← NEU: after()-Job-ID
        
        # Timeline-Konfiguration: (x_start, x_end, time_offset_minutes)
        self.timeline_slots = [
            (0, 126, -90),      # Slot 1: -90min
            (126, 256, -60),    # Slot 2: -60min
            (256, 382, -30),    # Slot 3: -30min
            (382, 508, 0),      # Slot 4: Jetzt
            (508, 634, 30),     # Slot 5: +30min
            (634, 756, 60),     # Slot 6: +60min
        ]
        
        # Update-Thread
        self.update_thread = None
        self.running = False
        self._stop_event = threading.Event()

    def start(self):
        """Startet EPG-Polling"""
        if self.running:
            return
        
        self.running = True
        self._stop_event.clear()
        self.update_epg()
        print(f"▶️ EPG gestartet: {self.station_name}")
    
    def stop(self):
        """Stoppt EPG-Polling"""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        
        # ← NEU: pending after()-Job canceln
        if self._next_update_job is not None:
            try:
                self.canvas.after_cancel(self._next_update_job)
            except Exception:
                pass
            self._next_update_job = None
        
        if self.update_thread and self.update_thread.is_alive():
            self.update_thread.join(timeout=2.0)
        print(f"🛑 EPG gestoppt: {self.station_name}")
    
    def update_epg(self):
        """Startet EPG-Update im Background"""
        if not self.running:
            return
        
        if self.update_thread and self.update_thread.is_alive():
            return
        
        self.update_thread = threading.Thread(target=self._fetch_epg_data)
        self.update_thread.daemon = True
        self.update_thread.start()
    
    def _fetch_epg_data(self):
        """Holt EPG-Daten im Background"""
        try:
            # Hole ALLE Programme des Tages (nicht nur aktuelles!)
            epg_data = self.epg_manager.get_epg(
                self.business_unit,
                self.broadcast_type,
                self.station_id
            )
            
            if not epg_data:
                # Fehler: Keine Daten
                self.app.gui_batcher.schedule_update(
                    lambda: self._draw_programs([None] * 6)
                )
                return
            
            # Extrahiere Programme
            if isinstance(epg_data, dict):
                programs = epg_data.get('programs', epg_data.get('data', []))
            else:
                programs = epg_data
            
            # Finde Programme für alle 6 Zeitslots
            now = datetime.now()
            slot_programs = []
            
            for _, _, offset_minutes in self.timeline_slots:
                target_time = now + timedelta(minutes=offset_minutes)
                program = self._find_program_at_time(programs, target_time)
                slot_programs.append(program)
            
            # Update GUI im Main-Thread (MIT BATCHER)
            self.app.gui_batcher.schedule_update(
                lambda progs=slot_programs: self._draw_programs(progs)
            )
            
            # Nächstes Update nur wenn noch running
            if self.running and not self._stop_event.is_set():  # ← NEU: Job-ID speichern statt anonym schedeln
                self._next_update_job = self.canvas.after(300000, self.update_epg)
        
        except Exception as e:
            print(f"⚠️ EPG-Fehler ({self.station_name}): {e}")
            import traceback
            traceback.print_exc()
            self.app.gui_batcher.schedule_update(
                lambda: self._draw_programs([None] * 6)
            )
    
    def _find_program_at_time(self, programs, target_time):
        """
        Findet Programm das zur gegebenen Zeit läuft
        
        Args:
            programs: Liste aller Programme
            target_time: datetime für die gesuchte Zeit
            
        Returns:
            Programm-Dict oder None
        """
        for program in programs:
            date_times = program.get('dateTimes', {})
            start_str = date_times.get('startTime')
            end_str = date_times.get('endTime')
            
            if start_str and end_str:
                start_time = self.epg_manager._parse_datetime(start_str)
                end_time = self.epg_manager._parse_datetime(end_str)
                
                if start_time and end_time:
                    # Programm läuft wenn: start <= target < end
                    if start_time <= target_time < end_time:
                        return program
        
        return None
    
    def _truncate_text(self, text, max_width_px, font_size=12):
        """Kürzt Text auf maximale Pixel-Breite"""
        if not text:
            return ""
        
        # Ellipsen-Breite reservieren (~8px bei Font 12)
        ellipsis_width = font_size * 0.67
        available_width = max_width_px - ellipsis_width
        
        # Pass 1: grobe Schätzung
        base_width = font_size * 0.58
        uppercase_count = sum(1 for c in text if c.isupper())
        total_count = len(text)
        uppercase_ratio = uppercase_count / total_count if total_count > 0 else 0
        
        uppercase_penalty = uppercase_ratio * (font_size * 0.26)
        avg_char_width_estimate = base_width + uppercase_penalty
        max_chars_estimate = int(available_width / avg_char_width_estimate)
        
        if len(text) <= max_chars_estimate:
            return text
        
        # Pass 2: Vom gekürzten Text berechnen
        if max_chars_estimate > 1:
            truncated_part = text[:max_chars_estimate-1]
            
            uppercase_count_truncated = sum(1 for c in truncated_part if c.isupper())
            total_count_truncated = len(truncated_part)
            uppercase_ratio_truncated = uppercase_count_truncated / total_count_truncated if total_count_truncated > 0 else 0
            
            uppercase_penalty_final = uppercase_ratio_truncated * (font_size * 0.26)
            avg_char_width_final = base_width + uppercase_penalty_final
            max_chars_final = int(available_width / avg_char_width_final)
            
            # Minimum nehmen (konservativ)
            max_chars_safe = min(max_chars_estimate, max_chars_final)
            
            if max_chars_safe > 1:
                return text[:max_chars_safe-1] + "…"
            else:
                return "…"
        else:
            return ""
    
    def _draw_programs(self, slot_programs):
        """
        Zeichnet alle 6 Programme + Trennzeichen
        
        Args:
            slot_programs: Liste mit 6 Programm-Dicts (oder None)
        """
        # Lösche alten Content
        self.canvas.delete("all")
        
        # Zeichne jeden Slot
        for i, (x_start, x_end, _) in enumerate(self.timeline_slots):
            # Trennzeichen | (außer beim ersten Slot)
            if i > 0:
                self.canvas.create_line(
                    x_start, 0, x_start, 44,
                    fill="#FFFFFF",
                    width=1,
                    tags="separator"
                )
            
            # Programmtitel
            program = slot_programs[i]
            if program:
                fmt = self.epg_manager.format_program(program)
                title = fmt['title']
            else:
                title = ""
            
            # Text auf Slot-Breite begrenzen (NEU!)
            slot_width = x_end - x_start - 4  # -4px für Padding
            title_clipped = self._truncate_text(title, slot_width, font_size=12)
            
            # Text links-bündig ab x_start + 2px (Abstand zum |)
            # WICHTIG: anchor="w" für links-bündig!
            self.canvas.create_text(
                x_start + 2, 22,
                text=title_clipped,  # ← Gekürzte Version!
                font=("Helvetica", 12),
                fill="#FFFFFF",
                anchor="w",
                tags="epg_text"
            )
    
    def refresh(self):
        """Manueller Refresh (löscht Cache)"""
        self.epg_manager.clear_cache()
        self.update_epg()


class Page07(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app = controller

        self._ui_built = False
        self._time_axis_center = None
        self._time_axis_job = None
        
        # EPG-Manager und Widgets
        self.epg_manager = None
        self.epg_widgets = []
        
        # Sender-Mapping: Key -> exakter Sendername in si4689_datenbank (nur Radio-Sender)
        # WICHTIG: tune_idx wird NICHT mehr hartcodiert (si4689_idx verschiebt sich
        # bei jedem Rescan). Stattdessen wird der aktuelle Index bei jedem Klick
        # live über den Namen in self.app._scan_data nachgeschlagen (siehe
        # _resolve_tune_idx()). Bei mehreren Regional-Varianten (z.B. SRF 1 ZH SH+,
        # SRF 1 BE FR VS+, ...) hier die exakte, bei dir empfangene Variante eintragen.
        self.station_names = {
            'srf_1': 'SRF 1 BE FR VS+',     # ggf. an deine Region anpassen
            'srf_2': 'SRF 2 Kultur+',
            'srf_3': 'SRF 3+',
            'srf_4': 'SRF 4 News+',
            'srf_Musikwelle': 'SRF Musikwelle+',
            'srf_Virus': 'SRF Virus+',
            # TV-Sender haben keine tune_idx
            'tv_srf_1': None,
            'tv_srf_2': None,
            'tv_srf_Info': None,
        }

    def activate(self):
        """Wird beim Seitenwechsel aufgerufen. Entscheidet zwischen Erst- und Wiederaktivierung."""
        if self._first_activation:
            self._first_activation = False
            self.on_first_activate()
        else:
            self.on_reactivate()

    def on_reactivate(self):
        """Bei jedem späteren Umschalten zu dieser Seite."""
        self._initialize_epg()
        self._update_time_axis(force=True)
        self.start_epg_updates()

    # ==== BasePage Lifecycle ====
    def on_first_activate(self):
        # Nur beim allerersten echten Anzeigen der Seite (nicht beim App-Startup)
        if not self._ui_built:
            self.load_images()
            self.build_gui()
            self._initialize_epg()
            self._ui_built = True
            self.start_epg_updates()

    def load_images(self):
        """Lädt Bilder mit ImageManager (automatisches Cleanup)"""
        cfg = self.app.config_data
        img_mgr = self.app.image_manager
        
        # Mit Resize (nutzt PIL intern)
        self.srf_1 = img_mgr.load_image('main_srf_1', cfg["srf_1"], resize=(44, 44))
        self.srf_2 = img_mgr.load_image('main_srf_2', cfg["srf_2"], resize=(44, 44))
        self.srf_3 = img_mgr.load_image('main_srf_3', cfg["srf_3"], resize=(44, 44))
        self.srf_4 = img_mgr.load_image('main_srf_4', cfg["srf_4"], resize=(44, 44))
        self.srf_Musikwelle = img_mgr.load_image('main_srf_Musikwelle', cfg["srf_Musikwelle"], resize=(44, 44))
        self.srf_Virus = img_mgr.load_image('main_srf_Virus', cfg["srf_Virus"], resize=(44, 44))
        self.tv_srf_1 = img_mgr.load_image('main_tv_srf_1', cfg["tv_srf_1"], resize=(44, 44))
        self.tv_srf_2 = img_mgr.load_image('main_tv_srf_2', cfg["tv_srf_2"], resize=(44, 44))
        self.tv_srf_Info = img_mgr.load_image('main_tv_srf_Info', cfg["tv_srf_Info"], resize=(44, 44))
        self.markierung_img = tk.PhotoImage(file='/home/weilmy/My_DAB_Si4689/assets/pictures/Markierung.png')

    def _resolve_tune_idx(self, station_key):
        """
        Löst den AKTUELLEN tune_idx live über den Sendernamen auf, statt einen
        gecachten Index zu verwenden. Durchsucht self.app._scan_data (die exakte
        Liste, auf die tune_service(index) zugreift) nach exaktem Namens-Treffer.

        Wird der Name nach einem Rescan nicht mehr gefunden (Sender aus dem
        Sendegebiet verschwunden), wird None zurückgegeben statt eines falschen
        Index. Bei mehreren Treffern mit demselben Namen (sollte laut Schema
        nicht vorkommen, da si4689_idx UNIQUE) wird der erste genommen.
        """
        name = self.station_names.get(station_key)
        if name is None:
            return None

        scan_data = getattr(self.app, "_scan_data", None) or []
        for idx, entry in enumerate(scan_data):
            entry_name = entry.get("label") or entry.get("name")
            if entry_name == name:
                return idx

        print(f"⚠️ EPG: Sender '{name}' ({station_key}) aktuell nicht im Sendegebiet gefunden")
        return None

    def _on_station_click(self, station_key):
        """Handler für Klick auf Sender (Icon oder Canvas)"""
        tune_idx = self._resolve_tune_idx(station_key)

        if tune_idx is None:
            # TV-Sender, ungültiger Key, oder Sender aktuell nicht empfangbar
            print(f"ℹ️ {station_key}: Keine tune_idx (TV-Sender, unbekannter Key, oder Sender nicht im Scan)")
            return
        
        # Radio-Sender: tune_service aufrufen
        print(f"🎵 Wähle Sender {station_key} (tune_idx={tune_idx})")
        try:
            self.app.dispatcher.submit(
                lambda: self.app.tune_service(tune_idx, record_history=True),
                key="tune"
            )
        except Exception as e:
            print(f"❌ Fehler beim Sender-Wechsel: {e}")


    def build_gui(self):
        # --- Page-Grundlayout (fix 800x480) ---
        self.configure(bg="#050A65", width=800, height=480)
        self.grid_propagate(False)
        try:
            self.pack_propagate(False)  # falls die Page per pack() eingesetzt wird
        except Exception:
            pass

        # Grid: 2 Spalten (Icon 44px + Inhalt 756px)
        self.grid_columnconfigure(0, minsize=44, weight=0)
        self.grid_columnconfigure(1, minsize=756, weight=1)

        # Fixe Zeilenhöhen
        self.grid_rowconfigure(0, minsize=25, weight=0)   # Überschrift
        self.grid_rowconfigure(1, minsize=40, weight=0)   # time_axis
        for r in range(2, 10):                            # rows 2..10
            self.grid_rowconfigure(r, minsize=44, weight=0)

        # Hilfsfunktion: eine "Sender-Zeile" (Row 2..10) mit 1px Rahmen
        def _make_station_row(row: int, key: str):
            outer = tk.Frame(self, bg="#000000", width=800, height=44, highlightthickness=1, bd=0)
            outer.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW)
            outer.grid_propagate(False)

            # Icon Label (44px breit)
            icon = tk.Label(outer, bg="#000000", bd=0, width=44, height=44)
            icon.grid(row=0, column=0, sticky=tk.NSEW)

            # Canvas für EPG-Inhalte (756px breit, 44px hoch)
            epg_canvas = tk.Canvas(outer, width=756, height=44, bg="#000000", highlightthickness=0, bd=0)
            epg_canvas.grid(row=0, column=1, sticky=tk.NSEW)

            # GUI-Widgets separat speichern
            setattr(self, f"{key}_icon", icon)
            setattr(self, f"{key}_canvas", epg_canvas)

            # Bild aus load_images() automatisch ins Icon-Label einsetzen
            img = getattr(self, key, None)
            if img is not None:
                icon.configure(image=img)
                icon.image = img  # wichtig: Referenz halten (Tkinter-GC)
            
            # Click-Handler binden (Icon und Canvas)
            click_handler = lambda e, k=key: self._on_station_click(k)
            icon.bind("<Button-1>", click_handler)
            epg_canvas.bind("<Button-1>", click_handler)
            
            # Cursor ändern für besseres UX (nur bei Radio-Sendern)
            has_radio_mapping = self.station_names.get(key) is not None
            if has_radio_mapping:
                icon.configure(cursor="hand2")
                epg_canvas.configure(cursor="hand2")

        # --- Row 0: Überschrift (self.label), ohne Rahmen ---
        header = tk.Frame(self, bg="#050A65", width=800, height=25, bd=0, highlightthickness=0)
        header.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)
        header.grid_propagate(False)

        self.label = tk.Label(header, text="SRF Programm Guide", font=("Helvetica", 25), bg="#050A65", fg="#3293AE", bd=0)
        self.label.pack(fill="both", expand=True, padx=10, pady=(5, 0))

        # --- Row 1: self.controll (22px), ohne Rahmen ---
        self.controll = tk.Frame(self, bg="#000000", width=800, height=40, bd=0, highlightthickness=0)
        self.controll.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW)
        self.controll.grid_propagate(False)

        # Zeitachse: links 44px Spacer + Canvas 756px
        self.left_spacer = tk.Frame(self.controll, bg="#000000", width=44, height=40)
        self.left_spacer.pack(side="left", fill="y")
        self.left_spacer.pack_propagate(False)

        self.time_axis = tk.Canvas(self.controll, width=756, height=40, bg="#000000", highlightthickness=0, bd=0)
        self.time_axis.pack(side="left", fill="both", expand=True)

        self._update_time_axis(force=True)

        # --- Rows 2..10: Senderzeilen mit 2px Rahmen ---
        _make_station_row(2, "srf_1")
        _make_station_row(3, "srf_2")
        _make_station_row(4, "srf_3")
        _make_station_row(5, "srf_4")
        _make_station_row(6, "srf_Musikwelle")
        _make_station_row(7, "srf_Virus")
        _make_station_row(8, "tv_srf_1")
        _make_station_row(9, "tv_srf_2")
        _make_station_row(10, "tv_srf_Info")

    def _round_to_nearest_half_hour(self, now: datetime) -> datetime:
        """
        Rundung gemäss Vorgabe:
        - 00..15  -> xx:00  (inkl. :15 runter)
        - 16..44  -> xx:30
        - 45..59  -> (xx+1):00 (inkl. :45 rauf)
        """
        m = now.minute
        base = now.replace(second=0, microsecond=0)

        if m <= 15:
            return base.replace(minute=0)
        elif m <= 44:
            return base.replace(minute=30)
        else:
            # auf nächste Stunde
            nxt = base.replace(minute=0) + timedelta(hours=1)
            return nxt

    def _compute_time_axis_times(self) -> list[str]:
        center = self._round_to_nearest_half_hour(datetime.now())
        offsets = [-90, -60, -30, 0, 30, 60, 90]
        times = [(center + timedelta(minutes=o)).strftime("%H:%M") for o in offsets]
        return times

    def _render_time_axis(self, times: list[str]) -> None:
        # Canvas leeren
        self.time_axis.delete("all")

        width = 756
        height = 40
        y = height // 2

        # Monospace macht Messung/Positionierung stabil
        font = ("DejaVu Sans Mono", 10)
        fnt = tkfont.Font(font=font)
        char_w = fnt.measure("0")  # Breite eines Zeichens (Monospace)
        step = width / 6.0  # 7 Zeiten -> 6 Abstände

        # 1. links bündig (l)
        self.time_axis.create_text(0, y, text=times[0], anchor="w", fill="#DBDEDE", font=font)

        # 2..6: Doppelpunkt auf x = i*step (d)
        # Doppelpunkt sitzt nach 2 Zeichen => linke Textkante = x - 2*char_w
        for i in range(1, 6):
            x_colon = i * step
            x_left = x_colon - (2 * char_w)
            self.time_axis.create_text(x_left, y, text=times[i], anchor="w", fill="#DBDEDE", font=font)

        # 7. rechts bündig (r) mit 5px Innenabstand
        self.time_axis.create_text(width - 5, y, text=times[6], anchor="e", fill="#DBDEDE", font=font)

        # Markierung unten (über ganze 756px)
        if getattr(self, "markierung_img", None) is not None:
            self.time_axis.create_image(0, height - 1, image=self.markierung_img, anchor="sw")

    def _update_time_axis(self, force: bool = False) -> None:
        times = self._compute_time_axis_times()
        center = times[3]  # 4. Zeit = Mitte

        if force or (center != self._time_axis_center):
            self._time_axis_center = center
            self._render_time_axis(times)

    def _initialize_epg(self):
        """Initialisiert EPG-Manager und EPG-Widgets für alle Sender"""
        # Prüfen ob Widgets bereits existieren
        if self.epg_widgets:
            print(f"✅ EPG-Widgets bereits vorhanden ({len(self.epg_widgets)})")
            return  # Nicht neu erstellen!
        try:
            # EPG-Manager von App holen oder erstellen
            if hasattr(self.app, 'epg_manager') and self.app.epg_manager is not None:
                self.epg_manager = self.app.epg_manager
                print("✅ EPG-Manager von App übernommen")
            else:
                # Fallback: EPG-Manager erstellen (benötigt epg_config.py)
                try:
                    from assets.epg_config import EPG_CLIENT_ID, EPG_CLIENT_SECRET
                    from utils.epg_manager_radio_tv import EPGManager
                    
                    self.epg_manager = EPGManager(EPG_CLIENT_ID, EPG_CLIENT_SECRET)
                    self.app.epg_manager = self.epg_manager  # In App speichern
                    print("✅ EPG-Manager neu erstellt")
                except ImportError as e:
                    print(f"❌ EPG-Config fehlt: {e}")
                    return
            
            # EPG-Widgets für alle Sender erstellen
            # Format: (canvas_key, broadcast_type, station_name, station_id)
            epg_config = [
                ('srf_1', 'radio', 'SRF 1', 'srf-1'),
                ('srf_2', 'radio', 'SRF 2 Kultur', 'srf-2'),
                ('srf_3', 'radio', 'SRF 3', 'srf-3'),
                ('srf_4', 'radio', 'SRF 4 News', 'srf-4'),
                ('srf_Musikwelle', 'radio', 'SRF Musikwelle', 'srf-musikwelle'),
                ('srf_Virus', 'radio', 'SRF Virus', 'srf-virus'),
                ('tv_srf_1', 'tv', 'SRF 1', 'srf-1'),
                ('tv_srf_2', 'tv', 'SRF zwei', 'srf-2'),
                ('tv_srf_Info', 'tv', 'SRF info', 'srf-info'),
            ]
            
            for canvas_key, bcast_type, station_name, station_id in epg_config:
                canvas = getattr(self, f"{canvas_key}_canvas", None)
                if canvas:
                    widget = EPGWidget(
                        canvas, 
                        self.epg_manager,
                        self.app,
                        business_unit='srf',
                        broadcast_type=bcast_type,
                        station_name=station_name,
                        station_id=station_id
                    )
                    self.epg_widgets.append(widget)
            
            print(f"✅ {len(self.epg_widgets)} EPG-Widgets initialisiert")
            
        except Exception as e:
            print(f"❌ EPG-Initialisierung fehlgeschlagen: {e}")
            import traceback
            traceback.print_exc()

    def stop_epg_updates(self):
        """Stoppt alle EPG-Updates (beim Verlassen der Seite)"""
        if not hasattr(self, 'epg_widgets'):
            return
        
        for widget in self.epg_widgets:
            widget.stop()  # Stoppt Thread
        
        self.stop_time_axis()

        #print("🛑 EPG-Updates gestoppt")

    def start_epg_updates(self):
        """Startet alle EPG-Updates (beim Betreten der Seite)"""
        if not hasattr(self, 'epg_widgets'):
            return
        
        for widget in self.epg_widgets:
            widget.start()  # Startet Thread
        
        #self.start_time_axis()

        print("▶️ EPG-Updates gestartet")

    def stop_time_axis(self):
        """Stoppt Zeitachsen-Updates"""
        if self._time_axis_job is not None:
            self.after_cancel(self._time_axis_job)
            self._time_axis_job = None

"""
    def start_time_axis(self):
        # Startet Zeitachsen-Updates
        # NEU: Prüfen ob time_axis existiert
        if not hasattr(self, 'time_axis'):
            return  # GUI noch nicht gebaut
        
        self._update_time_axis(force=True)
        # Nach 30 Min erneut
        self._time_axis_job = self.after(1800000, self.start_time_axis)
"""

__all__ = ["Page07"]