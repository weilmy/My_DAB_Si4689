#!/usr/bin/env python3
# ('my_venv_314':venv)

# page_01.py

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

# 📈 Statstik:
# - SQL-Datenbank mit Sender, Artist, Songtitel und Genre
# - Top 10 des Tages, Top 10 der Woche, Top 10 des Monats, Top 10 Gesamt
# - Suchfunktion nach Artist und/oder Song
# - Song-auflistung nach Genre

import tkinter as tk
from tkinter import ttk, Toplevel, Label, messagebox
from PIL import Image, ImageTk
import sqlite3
from datetime import datetime, timedelta
from collections import Counter
import sys, os
from functools import partial
from .base_page import BasePage
from utils.helper import ChipButton

sys.stdout.reconfigure(encoding='utf-8')


class Page01(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.app = controller
        self.search_controller = SearchController(self) # Suchfunktion
        self.sender_data = SenderData(self) # Hördauer/Anzahl Senderwahl

        self.configure(bg="#828EE7")

        # Überschrift
        self.label = tk.Label(self, text="Statistik - Auswertung nach Sender, Artist und Songtitel", font=("Helvetica", 20), background="#828EE7", foreground="#C8CDF7")
        self.label.grid(column=0, row=0, sticky=tk.NW, padx=(5, 0), pady=(5, 0))

        # Sortierstatus
        self.sort_orders = {
            "sender": "asc",
            "artist": "asc",
            "song": "asc",
            "genre": "asc",
            "timestamp": "desc"
        }

        self.build_gui()

    def build_gui(self):
        self.content_frame = tk.Frame(self, bg='#828EE7')
        self.content_frame.grid(row=1, column=0, sticky=tk.NSEW, padx=(5, 0), pady=(0, 0))

        # Top-Level dehnbar
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        between = tk.Frame(self.content_frame, bg="lightblue")
        bottom_1 = tk.Frame(self.content_frame, bg="#828EE7")
        bottom_2 = tk.Frame(self.content_frame, bg="#828EE7")
        between.grid(row=3, column=0, columnspan=3, sticky=tk.NW, padx=(5, 0), pady=(5, 0))
        bottom_1.grid(row=4, column=0, columnspan=3, sticky=tk.EW, padx=(5, 0), pady=(5, 0))
        bottom_2.grid(row=5, column=0, columnspan=3, sticky=tk.EW, padx=(5, 0), pady=(5, 0))

        # Zwischenbereich & Tree-Frame dehnbar
        between.grid_columnconfigure(0, weight=1)

        self.content_frame.grid_rowconfigure(3, weight=0)
        self.content_frame.grid_columnconfigure(0, weight=1)

        # Suchleiste
        search_frame = tk.Frame(between, bg="lightblue")
        search_frame.grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)

        tk.Label(search_frame, text="Artist:", bg="lightblue").grid(row=1, column=0, padx=(5, 0), pady=(0, 0))
        self.artist_entry = tk.Entry(search_frame, width=20)
        self.artist_entry.grid(row=1, column=1, padx=(0, 10))
        self.artist_entry.bind("<Button-1>", lambda e: self.search_controller.focus_field(self.artist_entry))
        self.artist_entry.bind("<FocusIn>",  lambda e: self.search_controller.focus_field(self.artist_entry))

        tk.Label(search_frame, text="Song:", bg="lightblue").grid(row=1, column=2)
        self.song_entry = tk.Entry(search_frame, width=20)
        self.song_entry.grid(row=1, column=3, padx=(0, 10))
        self.song_entry.bind("<Button-1>",   lambda e: self.search_controller.focus_field(self.song_entry))
        self.song_entry.bind("<FocusIn>",    lambda e: self.search_controller.focus_field(self.song_entry))

        tk.Label(search_frame, text="Sender:", bg="lightblue").grid(row=1, column=4)
        self.Sender_dropbox = ttk.Combobox(search_frame, width=20, state="readonly")
        self.Sender_dropbox.grid(row=1, column=5)
        self.Sender_dropbox.bind("<<ComboboxSelected>>", self.show_sender_catalog)

        # Lupe-Bild
        cmd = self.search_and_close_keyboard  # Lupe: Keyboard schließen + suchen
        try:
            lupe_img = Image.open("/home/weilmy/My_DAB_Si4689/assets/pictures/Lupe.png")
            lupe_img = lupe_img.resize((24, 24), Image.LANCZOS)
            self.lupe_icon = ImageTk.PhotoImage(lupe_img)
            search_button = tk.Button(search_frame, image=self.lupe_icon, command=cmd, bg="lightblue", bd=0)
        except Exception:
            self.lupe_icon = None
            search_button = tk.Button(search_frame, text="Suchen", command=cmd, bg="lightblue", bd=0)
        search_button.grid(row=1, column=6, padx=(10, 10))

        # X-Zurücksetzen-Button
        reset_button = tk.Button(search_frame, text="❌", command=self.reset_search, bg="lightblue", bd=0)
        reset_button.grid(row=1, column=7, padx=(5, 0))

        # Treeview unterhalb der Suchleiste
        self.tree_frame = tk.Frame(between)
        self.tree_frame.grid(row=2, column=0, sticky=tk.NW)

        # Treeview-Container dehnbar
        self.tree_frame.grid_rowconfigure(1, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(self.tree_frame, orient="vertical")
        self.tree = ttk.Treeview(
            self.tree_frame,
            columns=("id", "timestamp", "sender", "artist", "song", "genre"),
            show="headings",
            yscrollcommand=scrollbar.set,
            height=13
        )
        self._bind_mousewheel(self.tree)
        
        scrollbar.config(command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky=tk.NS)
        self.tree.grid(row=1, column=0, sticky=tk.NSEW)
        self.tree_frame.grid_columnconfigure(0, weight=1)

        # Spaltenüberschriften mit Sortierlogik
        for col, width in zip(("id", "timestamp", "sender", "artist", "song", "genre"),
                              (50, 140, 140, 140, 180, 100)):
            self.tree.column(col, width=width, anchor=tk.W if col != "id" else tk.CENTER)
            self.tree.heading(col, text=col.capitalize())
            
        self.tree.heading("id", text="ID")
        self.tree.heading("timestamp", text="Zeit (ISO) ↓", command=self.toggle_sort_timestamp_column)
        for col in ("sender", "artist", "song", "genre"):
            self.tree.heading(col, text=f"{col.capitalize()} (a-z)", command=lambda c=col: self.toggle_sort_column(c))

        # Buttons
        base_bg = bottom_1.cget("bg")  # -> "#828EE7"

        self.btn_del   = ChipButton(bottom_1, text="Daten löschen", command=self.delete_selected_row, base_bg=base_bg)
        self.btn_del.pack(side="left", padx=5, pady=5)

        self.btn_t10d  = ChipButton(bottom_1, text="Top 10 Heute", command=self.show_top10_today, base_bg=base_bg)
        self.btn_t10d.pack(side="left", padx=10, pady=5)

        self.btn_t10w  = ChipButton(bottom_1, text="Top 10 Woche", command=self.show_top10_week, base_bg=base_bg)
        self.btn_t10w.pack(side="left", padx=10, pady=5)

        self.btn_t10m  = ChipButton(bottom_1, text="Top 10 Monat", command=self.show_top10_month, base_bg=base_bg)
        self.btn_t10m.pack(side="left", padx=10, pady=5)

        self.btn_t10all = ChipButton(bottom_1, text="Top 10 Gesamt", command=self.show_top10_gesamt, base_bg=base_bg)
        self.btn_t10all.pack(side="left", padx=10, pady=5)

        genre_label = tk.Label(bottom_2, text="Genre:", bg=base_bg)
        genre_label.pack(side="left", padx=(10, 2), pady=5)

        self.genre_combobox = ttk.Combobox(bottom_2, width=18, state="readonly")
        self.genre_combobox.pack(side="left", padx=(0, 5), pady=5)
        self.genre_combobox.bind("<<ComboboxSelected>>", self.show_genre_catalog)

        # ========== DB-LADEN-BUTTON ==========
        # Button für alle Einträge laden
        self.btn_load_all = ChipButton(bottom_2, text="DB laden", command=self.load_all_data, base_bg=base_bg)
        self.btn_load_all.pack(side="left", padx=10, pady=5)
        # ==========================================

        # ========== Sender-BUTTON ==========
        # Button für Senderstatstik
        self.btn_sender = ChipButton(bottom_2, text="Senderstatistik", command=self.sender_data._build_sender_stats_popup, base_bg=base_bg)
        self.btn_sender.pack(side="left", padx=10, pady=5)
        # ==========================================


    def reset_search(self):
        self.artist_entry.delete(0, tk.END)
        self.song_entry.delete(0, tk.END)
        self.load_data()

    def search_entries(self):
        artist = self.artist_entry.get().strip().lower()
        song = self.song_entry.get().strip().lower()

        if not artist and not song:
            self.load_data()
            return
        hits = []
        misses = []
        for i in self.tree.get_children():
            values = self.tree.item(i)["values"]
            tree_artist = str(values[3]).lower()
            tree_song = str(values[4]).lower()
            show = (not artist or artist in tree_artist) and (not song or song in tree_song)
            (hits if show else misses).append((i, values))
        self.tree.delete(*self.tree.get_children())
        for i, values in hits:
            self.tree.insert("", "end", values=values, tags=("show",))
        for i, values in misses:
            self.tree.insert("", "end", values=values, tags=("hide",))
        self.tree.tag_configure("hide", foreground="#D3D3D3")
        self.tree.tag_configure("show", foreground="black")
        messagebox.showinfo("Suchergebnis", f"🔍 {len(hits)} Treffer gefunden.")

    def get_db_path(self):
        """Zentrale DB-Pfad-Auflösung für music_log."""
        cfg = getattr(self.app, "config_data", {}) or {}
        db_path = cfg.get("music_data_db")

        if isinstance(db_path, str) and db_path:
            return db_path

        # Fallback: Projekt-Standardpfad
        try:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            return os.path.join(root, "assets", "DB", "music_data.sqlite")
        except Exception:
            # letzte Reserve
            return os.path.join(os.path.expanduser("~"), "music_data.sqlite")

    def delete_selected_row(self):
        selection = self.tree.selection()
        if not selection:
            return
        try:
            # IDs sammeln
            ids = []
            for iid in selection:
                vals = self.tree.item(iid)["values"]
                if vals:
                    ids.append(vals[0])
            if not ids:
                return
            
            # DatabaseManager bevorzugt
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    cursor.executemany("DELETE FROM music_log WHERE id = ?", [(rid,) for rid in ids])
                    conn.commit()
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    conn.executemany("DELETE FROM music_log WHERE id = ?", [(rid,) for rid in ids])
            
            # UI-Refresh
            self.load_data()
        except Exception as e:
            print(f"Fehler beim Löschen: {e}")

    def toggle_sort_column(self, column):
        data = [(self.tree.set(k, column), k) for k in self.tree.get_children("")]
        order = self.sort_orders[column]

        if order == "asc":
            data.sort(key=lambda t: t[0].lower())
            self.sort_orders[column] = "desc"
            self.tree.heading(column, text=f"{column.capitalize()} a-z")
        elif order == "desc":
            data.sort(key=lambda t: t[0].lower(), reverse=True)
            self.sort_orders[column] = "freq"
            self.tree.heading(column, text=f"{column.capitalize()} (z-a)")
        else:
            counter = Counter((v[0] or "").lower() for v in data)
            data.sort(key=lambda t: -counter[(t[0] or "").lower()])
            self.sort_orders[column] = "asc"
            self.tree.heading(column, text=f"{column.capitalize()} (▼)")
        for idx, (_, iid) in enumerate(data):
            self.tree.move(iid, "", idx)

    def toggle_sort_timestamp_column(self):
        data = [(self.tree.set(i, "timestamp"), i) for i in self.tree.get_children("")]
        order = self.sort_orders["timestamp"]
        rev = order == "desc"
        data_nonempty = [item for item in data if item[0]]
        data_empty = [item for item in data if not item[0]]
        data_nonempty.sort(key=lambda t: t[0], reverse=rev)
        data = data_nonempty + data_empty
        self.sort_orders["timestamp"] = "asc" if rev else "desc"
        self.tree.heading("timestamp", text=f"Zeit (ISO) {'↑' if rev else '↓'}")
        for idx, (_, iid) in enumerate(data):
            self.tree.move(iid, "", idx)

    def _fetch_music_log_rows(self, cursor, limit=5000):
        """
        Holt Music-Log-Rows aus DB
        
        Args:
            cursor: DB-Cursor
            limit: Anzahl Zeilen (None = alle, Standard = 5000)
        """
        if limit is None:
            # ALLE Zeilen (für "DB laden" Button)
            cursor.execute("""
                SELECT
                    id,
                    COALESCE(NULLIF(ts_local, ''), timestamp) AS ts_disp,
                    sender,
                    artist,
                    song,
                    genre
                FROM music_log
                ORDER BY COALESCE(NULLIF(ts_local, ''), timestamp) DESC
            """)
        else:
            # LIMITIERT (Standard beim Öffnen)
            cursor.execute("""
                SELECT
                    id,
                    COALESCE(NULLIF(ts_local, ''), timestamp) AS ts_disp,
                    sender,
                    artist,
                    song,
                    genre
                FROM music_log
                ORDER BY COALESCE(NULLIF(ts_local, ''), timestamp) DESC
                LIMIT ?
            """, (limit,))
        
        return cursor.fetchall()

    def load_data(self, limit: int | None = 5000):
        """
        Lädt Daten aus DB ins Treeview
        
        Args:
            limit: Anzahl Zeilen (None = alle)
        """
        try:
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    rows = self._fetch_music_log_rows(cursor, limit=limit)
            
            # Treeview leeren
            self.tree.delete(*self.tree.get_children())

            # Einträge einfügen, Tags für Farben definieren (einmalig)
            self.tree.tag_configure('even', background='#fcfcfa')
            self.tree.tag_configure('odd', background='#ADD8E6')
            genres = set() # Menge aller Genres

            for index, row in enumerate(rows):
                # jetzt 6 Werte entpacken
                id_, ts_disp, sender, artist, song, genre = row
                timestamp_disp = ts_disp or ""
                tag = 'even' if index % 2 == 0 else 'odd'

                # Genres sammeln (nur nicht-leere)
                if genre:
                    g = str(genre).strip()
                    if g:
                        genres.add(g)

                # 6 Werte in die Treeview schreiben
                self.tree.insert(
                    "",
                    tk.END,
                    values=(id_, timestamp_disp, sender, artist, song, genre),
                    tags=(tag,)
                )

            self.refresh_sender_combobox()          # Senderliste aktualisieren
            self.refresh_genre_combobox(genres)     # Genreliste aktualisieren
            loaded = len(rows)
            if limit is None:
                print(f"📌 {loaded:,} Einträge geladen (ALLE)")
            elif loaded == limit:
                print(f"📌 {loaded:,} Einträge geladen (neueste {limit:,})")
                print(f"   💡 Tipp: 'DB laden' Button für alle Einträge")
            else:
                print(f"📌 {loaded:,} Einträge geladen")

        except Exception as e:
            print(f"❌ Fehler beim Laden der Datenbank: {e}")

    def load_all_data(self):
        """
        Lädt ALLE Einträge aus DB (kann länger dauern!)
        """
        # Bestätigung bei großen DBs
        try:
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    cursor.execute("SELECT COUNT(*) FROM music_log")
                    total = cursor.fetchone()[0]
            
            # Warnung bei > 20.000 Einträgen
            if total > 20000:
                response = messagebox.askyesno(
                    "Alle Einträge laden?",
                    f"Die Datenbank enthält {total:,} Einträge.\n\n"
                    f"Das Laden etwas dauern.\n\n"
                    f"Fortfahren?",
                    icon='warning'
                )
                if not response:
                    return
            
            # Ladeanimation vorbereiten
            original_text = "DB laden"
            self.btn_load_all.configure(text="Lädt...")
            self.update_idletasks()
            
            # ALLE laden (limit=None)
            import time
            start = time.time()
            self.load_data(limit=None)
            elapsed = time.time() - start
            
            # Button zurücksetzen
            self.btn_load_all.configure(text=original_text)
            
            # Feedback
            print(f"✅ Alle {total:,} Einträge geladen in {elapsed:.1f} Sekunden")
            
        except Exception as e:
            print(f"❌ Fehler beim Laden aller Einträge: {e}")
            self.btn_load_all.configure(text="DB laden")



    def _fetch_top10_today(self, cursor, today):
        """Holt Top 10 Songs von heute"""
        cursor.execute("""
            SELECT
                artist,
                song_or_title,
                COUNT(*) AS freq
            FROM (
                SELECT
                    COALESCE(artist,'')             AS artist,
                    COALESCE(song, title, '')       AS song_or_title,
                    LOWER(COALESCE(artist,''))      AS a_lower,
                    LOWER(COALESCE(song, title,'')) AS s_lower
                FROM music_log
                WHERE DATE(COALESCE(ts_local, timestamp)) = ?
            )
            GROUP BY artist, song_or_title
            ORDER BY freq DESC
            LIMIT 10
        """, (today,))
        return cursor.fetchall()

    def _build_top10_popup(self, title, label_text, results):
        """
        Sortierbarers Top-10-Popup für Heute / Woche / Monat.

        Sortierlogik (erste Version):
          Artist     → Summe aller Häufigkeiten pro Artist, absteigend
          Song       → Summe aller Häufigkeiten pro Song-Titel (über Artists), absteigend
          Häufigkeit → numerisch absteigend (Originalwert je Zeile)
        """
        from collections import defaultdict

        win = Toplevel(self.tree)
        win.title(title)
        win.geometry("540x320")
        win.configure(bg='lightblue')
        Label(win, text=label_text,
              bg='lightblue', font=("Helvetica", 12, "bold")).pack(pady=10)

        data = [
            ((artist or "").upper(), (song or "").upper(), int(freq or 0))
            for artist, song, freq in results
        ]

        container = ttk.Frame(win)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(
            container,
            columns=("Artist", "Song", "Häufigkeit"),
            show="headings"
        )
        vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

        tree.column("Artist",     width=180, anchor="w")
        tree.column("Song",       width=220, anchor="w")
        tree.column("Häufigkeit", width=90,  anchor="center")

        def populate(rows):
            tree.delete(*tree.get_children())
            for artist, song, freq in rows:
                tree.insert("", tk.END, values=(artist, song, freq))

        def update_headings(active_col):
            for col in ("Artist", "Song", "Häufigkeit"):
                sym = "↓" if col == active_col else "-"
                tree.heading(col, text=f"{col}  {sym}",
                             command=lambda c=col: sort_by(c))

        def sort_by(col):
            update_headings(col)
            if col == "Artist":
                artist_totals: dict = defaultdict(int)
                for a, s, f in data:
                    artist_totals[a] += f
                sorted_data = sorted(
                    data, key=lambda r: (-artist_totals[r[0]], -r[2], r[1])
                )
            elif col == "Song":
                song_totals: dict = defaultdict(int)
                for a, s, f in data:
                    song_totals[s] += f
                sorted_data = sorted(
                    data, key=lambda r: (-song_totals[r[1]], -r[2], r[0])
                )
            else:  # Häufigkeit
                sorted_data = sorted(data, key=lambda r: -r[2])
            populate(sorted_data)

        update_headings(None)
        populate(data)
        self._bind_mousewheel(tree)
        win.transient(self.winfo_toplevel())
        win.focus_force()
        return win

    def _build_top10_gesamt_popup(self, title, label_text, results):
        """
        Sortierbarers Top-10-Popup speziell für 'Top 10 Gesamt'.

        Jeder Sort-Modus stellt eine eigene DB-Abfrage — damit Artists wie
        Ed Sheeran (viele Songs, keiner allein in Top 10) korrekt erscheinen.

          Häufigkeit ↓  → Top 10 Einzel-Songs (artist+song-Paar), meistgespielt zuerst.
                           Standard-Ansicht beim Öffnen des Fensters.

          Artist ↓      → Top 10 Artists nach Gesamtspielzahl aller ihrer Songs.
                           Hat ein Artist mehrere verschiedene Songs → Song-Spalte = "div".

          Song ↓        → Top 10 Song-Titel nach Gesamtspielzahl über alle Artists.
                           Wurde ein Titel von mehreren Artists gespielt → Artist-Spalte = "div".
        """
        win = Toplevel(self.tree)
        win.title(title)
        win.geometry("540x340")
        win.configure(bg='lightblue')
        Label(win, text=label_text,
              bg='lightblue', font=("Helvetica", 12, "bold")).pack(pady=10)

        # Standard-Daten (Häufigkeit-Ansicht): bereits übergeben
        default_data = [
            ((a or "").upper(), (s or "").upper(), int(f or 0))
            for a, s, f in results
        ]

        container = ttk.Frame(win)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(
            container,
            columns=("Artist", "Song", "Häufigkeit"),
            show="headings"
        )
        vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

        tree.column("Artist",     width=180, anchor="w")
        tree.column("Song",       width=220, anchor="w")
        tree.column("Häufigkeit", width=90,  anchor="center")

        def populate(rows):
            tree.delete(*tree.get_children())
            for artist, song, freq in rows:
                tree.insert("", tk.END, values=(artist, song, freq))

        def update_headings(active_col):
            for col in ("Artist", "Song", "Häufigkeit"):
                sym = "↓" if col == active_col else "-"
                tree.heading(col, text=f"{col}  {sym}",
                             command=lambda c=col: sort_by(c))

        def sort_by(col):
            update_headings(col)
            try:
                if col == "Artist":
                    # Neue DB-Abfrage: Top 10 Artists nach Gesamtspielzahl
                    rows = self._run_gesamt_query(self._fetch_top10_gesamt_by_artist)
                    data = [((a or "").upper(), (s or "").upper(), int(f or 0))
                            for a, s, f in rows]
                elif col == "Song":
                    # Neue DB-Abfrage: Top 10 Song-Titel über alle Artists
                    rows = self._run_gesamt_query(self._fetch_top10_gesamt_by_song)
                    data = [((a or "").upper(), (s or "").upper(), int(f or 0))
                            for a, s, f in rows]
                else:  # Häufigkeit → Standard-Daten numerisch sortiert
                    data = sorted(default_data, key=lambda r: -r[2])
                populate(data)
            except Exception:
                import traceback; traceback.print_exc()

        update_headings(None)
        populate(default_data)
        self._bind_mousewheel(tree)
        win.transient(self.winfo_toplevel())
        win.focus_force()
        return win

    def show_top10_today(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            db_manager = getattr(self.app, 'music_db_manager', None)
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    results = self._fetch_top10_today(cursor, today)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    results = self._fetch_top10_today(c, today)
            self._build_top10_popup(
                title="Top 10 Songs – Heute",
                label_text=f"Top 10 Songs für {datetime.now().strftime('%d.%m.%Y')}",
                results=results
            )
        except Exception:
            print("❌ Fehler bei Top-10 Heute:"); import traceback; traceback.print_exc()

    def _fetch_top10_week(self, cursor, seven_days_ago, today):
        """Holt Top 10 Songs dieser Woche"""
        cursor.execute("""
            SELECT
                artist,
                song_or_title,
                COUNT(*) AS freq
            FROM (
                SELECT
                    COALESCE(artist,'')             AS artist,
                    COALESCE(song, title, '')       AS song_or_title,
                    LOWER(COALESCE(artist,''))      AS a_lower,
                    LOWER(COALESCE(song, title,'')) AS s_lower
                FROM music_log
                WHERE DATE(COALESCE(ts_local, timestamp)) BETWEEN ? AND ?
            )
            GROUP BY artist, song_or_title
            ORDER BY freq DESC
            LIMIT 10
        """, (seven_days_ago, today))  # ← Strings direkt verwenden!
        return cursor.fetchall()

    def show_top10_week(self):
        try:
            # Datumsobjekte zuerst
            today_dt = datetime.now()
            seven_days_ago_dt = today_dt - timedelta(days=7)
            today = today_dt.strftime("%Y-%m-%d")
            seven_days_ago = seven_days_ago_dt.strftime("%Y-%m-%d")

            db_manager = getattr(self.app, 'music_db_manager', None)
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    results = self._fetch_top10_week(cursor, seven_days_ago, today)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    results = self._fetch_top10_week(c, seven_days_ago, today)

            self._build_top10_popup(
                title="Top 10 Songs – Letzte 7 Tage",
                label_text=f"Top 10 von {seven_days_ago_dt.strftime('%d.%m.%Y')} bis {today_dt.strftime('%d.%m.%Y')}",
                results=results
            )
        except Exception:
            print("❌ Fehler bei Top-10 Woche:")
            import traceback
            traceback.print_exc()

    def _fetch_top10_month(self, cursor, thirty_days_ago, today):
        """Holt Top 10 Songs der letzten 30 Tage"""
        cursor.execute("""
            SELECT
                artist,
                song_or_title,
                COUNT(*) AS freq
            FROM (
                SELECT
                    COALESCE(artist,'')             AS artist,
                    COALESCE(song, title, '')       AS song_or_title,
                    LOWER(COALESCE(artist,''))      AS a_lower,
                    LOWER(COALESCE(song, title,'')) AS s_lower
                FROM music_log
                WHERE DATE(COALESCE(ts_local, timestamp)) BETWEEN ? AND ?
            )
            GROUP BY artist, song_or_title
            ORDER BY freq DESC
            LIMIT 10
        """, (thirty_days_ago, today))  # ← Strings direkt!
        return cursor.fetchall()

    def show_top10_month(self):
        try:
            # ✅ Wie bei show_top10_week()
            today_dt = datetime.now()
            thirty_days_ago_dt = today_dt - timedelta(days=30)
            today = today_dt.strftime("%Y-%m-%d")
            thirty_days_ago = thirty_days_ago_dt.strftime("%Y-%m-%d")

            db_manager = getattr(self.app, 'music_db_manager', None)
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    results = self._fetch_top10_month(cursor, thirty_days_ago, today)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    results = self._fetch_top10_month(c, thirty_days_ago, today)

            self._build_top10_popup(
                title="Top 10 Songs – Letzte 30 Tage",
                label_text=f"Top 10 von {thirty_days_ago_dt.strftime('%d.%m.%Y')} bis {today_dt.strftime('%d.%m.%Y')}",
                results=results
            )
                
        except Exception:
            print("❌ Fehler bei Top-10 Monat:")
            import traceback
            traceback.print_exc()

    def _fetch_top10_gesamt(self, cursor):
        """Top 10 meistgespielte Einzel-Songs (artist+song-Paar), für Standard-Ansicht."""
        cursor.execute("""
            SELECT
                MIN(artist)         AS artist,
                MIN(song_or_title)  AS song,
                COUNT(*)            AS freq
            FROM (
                SELECT
                    COALESCE(artist, '')             AS artist,
                    COALESCE(song, title, '')        AS song_or_title,
                    LOWER(COALESCE(artist, ''))      AS a_lower,
                    LOWER(COALESCE(song, title, '')) AS s_lower
                FROM music_log
            )
            GROUP BY a_lower, s_lower
            ORDER BY freq DESC
            LIMIT 10
        """)
        return cursor.fetchall()

    def _fetch_top10_gesamt_by_artist(self, cursor):
        """Top 10 Artists nach Gesamtspielzahl aller ihrer Songs.
        Hat ein Artist mehrere verschiedene Songs → song_display = 'div'.
        """
        cursor.execute("""
            SELECT
                MIN(artist)                                                    AS artist_display,
                CASE WHEN COUNT(DISTINCT s_lower) > 1
                     THEN 'div'
                     ELSE MIN(song_or_title)
                END                                                            AS song_display,
                COUNT(*)                                                       AS total_freq
            FROM (
                SELECT
                    COALESCE(artist, '')             AS artist,
                    COALESCE(song, title, '')        AS song_or_title,
                    LOWER(COALESCE(artist, ''))      AS a_lower,
                    LOWER(COALESCE(song, title, '')) AS s_lower
                FROM music_log
            )
            GROUP BY a_lower
            ORDER BY total_freq DESC
            LIMIT 10
        """)
        return cursor.fetchall()

    def _fetch_top10_gesamt_by_song(self, cursor):
        """Top 10 Song-Titel nach Gesamtspielzahl über alle Artists.
        Wurde ein Titel von mehreren Artists gespielt → artist_display = 'div'.
        """
        cursor.execute("""
            SELECT
                CASE WHEN COUNT(DISTINCT a_lower) > 1
                     THEN 'div'
                     ELSE MIN(artist)
                END                                                            AS artist_display,
                MIN(song_or_title)                                             AS song_display,
                COUNT(*)                                                       AS total_freq
            FROM (
                SELECT
                    COALESCE(artist, '')             AS artist,
                    COALESCE(song, title, '')        AS song_or_title,
                    LOWER(COALESCE(artist, ''))      AS a_lower,
                    LOWER(COALESCE(song, title, '')) AS s_lower
                FROM music_log
            )
            GROUP BY s_lower
            ORDER BY total_freq DESC
            LIMIT 10
        """)
        return cursor.fetchall()

    def _run_gesamt_query(self, fetch_fn):
        """Hilfsmethode: führt eine der drei Gesamt-Abfragen aus und gibt Rows zurück."""
        db_manager = getattr(self.app, 'music_db_manager', None)
        if db_manager:
            with db_manager.get_cursor() as (conn, cursor):
                return fetch_fn(cursor)
        else:
            db_path = self.get_db_path()
            with sqlite3.connect(db_path) as conn:
                return fetch_fn(conn.cursor())

    def show_top10_gesamt(self):
        try:
            # Standard-Ansicht: Top 10 Einzel-Songs
            results = self._run_gesamt_query(self._fetch_top10_gesamt)
            self._build_top10_gesamt_popup(
                title="Top 10 Songs – Gesamte Datenbank",
                label_text="Top 10 Songs – Alle Einträge",
                results=results
            )
        except Exception:
            print("❌ Fehler bei Top-10-Anzeige:")
            import traceback; traceback.print_exc()


    def _fetch_distinct_senders(self, cursor):
        """Holt alle distinct Sender aus der DB (sortiert)"""
        cursor.execute("""
            SELECT DISTINCT TRIM(sender)
            FROM music_log
            WHERE sender IS NOT NULL AND TRIM(sender) <> ''
            ORDER BY LOWER(TRIM(sender))
        """)
        return cursor.fetchall()

    def refresh_sender_combobox(self):
        """Lädt alle Sender aus der DB in die Combobox (distinct, a–z)."""
        try:
            db_manager = getattr(self.app, 'music_db_manager', None)

            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    rows = self._fetch_distinct_senders(cursor)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    rows = self._fetch_distinct_senders(c)

            senders = [row[0] for row in rows]
            self.Sender_dropbox['values'] = senders
            
        except Exception:
            print("❌ Fehler bei refresh_sender_combobox():")
            import traceback
            traceback.print_exc()

    def refresh_genre_combobox(self, genres: set[str]):
        """Befüllt die Genre-Combobox mit allen in der Treeview vorkommenden Genres."""
        try:
            # Falls Combobox noch nicht gebaut wurde (z.B. sehr frühe Aufrufe): abbrechen
            if not hasattr(self, "genre_combobox"):
                return

            genre_list = sorted({g.strip() for g in genres if g and str(g).strip()})
            self.genre_combobox['values'] = genre_list

            # Auswahl zurücksetzen, wenn aktueller Wert nicht mehr existiert
            current = (self.genre_combobox.get() or "").strip()
            if current not in genre_list:
                self.genre_combobox.set("")
        except Exception:
            print("❌ Fehler bei refresh_genre_combobox():")
            import traceback; traceback.print_exc()

    def _fetch_songs_by_genre(self, cursor, genre):
        """Holt alle Songs für ein bestimmtes Genre"""
        cursor.execute("""
            SELECT
                sender,
                artist,
                COALESCE(song, title) AS song
            FROM music_log
            WHERE genre = ?
            ORDER BY sender, artist, song
        """, (genre,))
        return cursor.fetchall()

    def show_genre_catalog(self, event=None):
        """Popup: alle Songs mit dem gewählten Genre (Sender, Artist, Song)."""
        genre = (self.genre_combobox.get() or "").strip()
        if not genre:
            return
        try:
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    rows = self._fetch_songs_by_genre(cursor, genre)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    rows = self._fetch_songs_by_genre(c, genre)

            if not rows:
                messagebox.showinfo("Keine Daten", f"Für das Genre „{genre}“ wurden keine Einträge gefunden.")
                return

            win = Toplevel(self)
            win.title(f"Genre: {genre}")
            win.geometry("700x420")
            win.configure(bg='lightblue')

            Label(
                win,
                text=f"Alle Titel mit Genre: {genre}",
                bg='lightblue',
                font=("Helvetica", 12, "bold")
            ).pack(pady=10)

            container = ttk.Frame(win)
            container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            container.grid_rowconfigure(0, weight=1)
            container.grid_columnconfigure(0, weight=1)
            tree = ttk.Treeview(
                container,
                columns=("Sender", "Artist", "Song"),
                show="headings",
                height=16
            )

            tree.heading("Sender", text="Sender")
            tree.heading("Artist", text="Artist")
            tree.heading("Song",   text="Song")
            tree.column("Sender", width=180, anchor="w")
            tree.column("Artist", width=200, anchor="w")
            tree.column("Song",   width=260, anchor="w")

            vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)

            tree.grid(row=0, column=0, sticky=tk.NSEW)
            vsb.grid(row=0, column=1, sticky=tk.NS)

            for sender, artist, song in rows:
                tree.insert(
                    "",
                    tk.END,
                    values=(
                        (sender or "").strip(),
                        (artist or "").strip(),
                        (song or "").strip()
                    )
                )

            # Popup etwas „modal“ wirken lassen
            win.transient(self.winfo_toplevel())
            win.grab_set()
            win.focus_force()

        except Exception:
            print("❌ Fehler bei show_genre_catalog():")
            import traceback; traceback.print_exc()

    def _fetch_show_sender(self, cursor, sender):
        """Holt alle Songs für einen bestimmten Sender"""
        cursor.execute("""
            SELECT
                MIN(artist)                           AS artist,
                MIN(song_or_title)                    AS song,
                MIN(genre)                            AS genre,
                COUNT(*)                              AS freq,
                MAX(ts_disp)                          AS last_ts
            FROM (
                SELECT
                    COALESCE(artist,'')               AS artist,
                    COALESCE(song, title, '')         AS song_or_title,
                    COALESCE(genre, '')               AS genre,
                    COALESCE(ts_local, timestamp)     AS ts_disp,
                    LOWER(COALESCE(artist,''))        AS a_lower,
                    LOWER(COALESCE(song, title,''))   AS s_lower
                FROM music_log
                WHERE sender = ?
            )
            GROUP BY a_lower, s_lower
            ORDER BY freq DESC, datetime(last_ts) DESC
        """, (sender,))
        return cursor.fetchall()

    def show_sender_catalog(self, event=None):
        """Popup: alle (Artist, Song, Genre) des gewählten Senders – mit Häufigkeit & zuletzt gespielt."""
        try:
            self.search_controller.close_virtual_keyboard()
        except Exception:
            pass
        
        sender = (self.Sender_dropbox.get() or "").strip()
        if not sender:
            sender = (getattr(self.app.state, "stats_selected_sender", None)
                    or getattr(self.app.state, "AktuellerSender", "") or "").strip()
        if not sender:
            return
        try:
            self.app.state.stats_selected_sender = sender
        except Exception:
            pass
        try:
            db_manager = getattr(self.app, 'music_db_manager', None)
            
            if db_manager:
                with db_manager.get_cursor() as (conn, cursor):
                    rows = self._fetch_show_sender(cursor, sender)
            else:
                db_path = self.get_db_path()
                with sqlite3.connect(db_path) as conn:
                    c = conn.cursor()
                    rows = self._fetch_show_sender(c, sender)

            if not rows:
                messagebox.showinfo("Keine Daten", f"Für „{sender}“ wurden keine Einträge gefunden.")
                return

            win = Toplevel(self)
            win.title(f"Sender: {sender} – Artists & Titel")
            win.geometry("740x420")
            win.configure(bg='lightblue')
            Label(win, text=f"Alle Artists & Titel für: {sender}", bg='lightblue',
                font=("Helvetica", 12, "bold")).pack(pady=10)

            container = ttk.Frame(win)
            container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            container.grid_rowconfigure(0, weight=1)
            container.grid_columnconfigure(0, weight=1)
            tree = ttk.Treeview(
                container,
                columns=("Artist", "Song", "Häufigkeit", "Zuletzt"),
                show="headings",
                height=16
            )

            win.transient(self.winfo_toplevel())
            win.grab_set()
            win.focus_force()
            self._bind_mousewheel_cluster(tree, tree, container, win)
            tree.heading("Artist", text="Artist")
            tree.heading("Song", text="Song")
            tree.heading("Häufigkeit", text="Häufigkeit")
            tree.heading("Zuletzt", text="Zuletzt gespielt")
            tree.column("Artist", width=200, anchor="w")
            tree.column("Song", width=260, anchor="w")
            tree.column("Häufigkeit", width=100, anchor="center")
            tree.column("Zuletzt", width=160, anchor="center")

            vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)

            tree.grid(row=0, column=0, sticky=tk.NSEW)
            vsb.grid(row=0, column=1, sticky=tk.NS)

            for artist, song, genre, freq, last_ts in rows:
                # Zuletzt nach EU-Format umwandeln, wenn möglich
                last_str = last_ts or ""
                try:
                    dt = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
                    last_str = dt.strftime("%d.%m.%Y %H:%M:%S")
                except Exception:
                    pass
                tree.insert("", tk.END, values=((artist or "").upper(), (song or "").upper(), freq, last_str))

        except Exception:
            print("❌ Fehler bei show_sender_catalog():")
            import traceback; traceback.print_exc()

    def _on_mousewheel(self, event, tv):
        # Windows/Mac: event.delta ±120; Linux: nutzen wir Button-4/5 unten
        if event.delta != 0:
            tv.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"  # verhindert, dass andere Widgets scrollen

    def _bind_mousewheel(self, tv):
        # Fokus beim Überfahren setzen (wichtig unter X11/Wayland)
        tv.bind("<Enter>", lambda e: tv.focus_set())

        # Windows/Mac
        tv.bind("<MouseWheel>", partial(self._on_mousewheel, tv=tv))

        # Linux/X11 (Raspberry Pi): Button 4/5 sind Scroll up/down
        tv.bind("<Button-4>", lambda e: (tv.yview_scroll(-1, "units"), "break")[1])
        tv.bind("<Button-5>", lambda e: (tv.yview_scroll( 1, "units"), "break")[1])

    def _bind_mousewheel_cluster(self, scroll_target, *widgets):
        """Binde Mausrad-Events an mehrere Widgets und leite sie an scroll_target (Treeview) weiter."""
        # Beim Betreten Fokus auf das Ziel-Treeview setzen (fix für X11/Wayland)
        for w in widgets:
            w.bind("<Enter>", lambda e, tv=scroll_target: tv.focus_set())
            # Windows/macOS
            w.bind("<MouseWheel>", lambda e, tv=scroll_target: self._on_mousewheel(e, tv))
            # Linux/X11
            w.bind("<Button-4>", lambda e, tv=scroll_target: (tv.yview_scroll(-1, "units"), "break")[1])
            w.bind("<Button-5>", lambda e, tv=scroll_target: (tv.yview_scroll( 1, "units"), "break")[1])

    def activate(self):
        """Wird beim Aufruf der Seite automatisch aufgerufen."""
        self.load_data()

    def on_first_activate(self):
        pass

    def on_reactivate(self):
        pass

    def search_and_close_keyboard(self):
        try:
            self.search_controller.close_virtual_keyboard()
        except Exception:
            pass
        self.search_entries()

    def on_page_hide(self):
        """Wird beim Verlassen der Page aufgerufen"""
        # Lokale Bilder freigeben
        # (ImageManager cached global, aber wir können Keys freigeben)
        
        # Optional: Nur wenn Page-spezifische Bilder geladen wurden
        # die NICHT in anderen Pages gebraucht werden
        pass  # Meist nicht nötig, da global gecacht

class SearchController:
    def __init__(self, app):
        self.app  = app
        self.active_entry = None  # merkt das aktuell fokussierte Eingabefeld
        self._configure_bind_id: str | None = None

# ------------------Keyboard------------------------------
    def create_frame_keyboard(self):
        # Bereits offen? -> nichts tun
        if getattr(self, "virtual_keyboard", None) and self.virtual_keyboard.winfo_exists():
            # Beim erneuten Öffnen nur neu positionieren
            self._place_keyboard()
            return
        # 1) Keyboard Frame definieren
        self.virtual_keyboard = tk.Frame(self.app, bd=2, relief="raised", bg="#f9f9f9")

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
            btn = tk.Button(self.virtual_keyboard, text=key, width=1)
            btn.config(command=lambda b=btn: self.click(b)) # Button selbst übergeben
            btn.grid(row=row_val, column=col_val, padx=1, pady=1)
            self.buttons.append(btn)
            col_val += 1
            if col_val > 10:
                col_val = 0
                row_val += 1

        self.shift_button  = tk.Button(self.virtual_keyboard, text="⇧", width=1, command=self.shift)
        self.shift_button.grid(row=row_val, column=0, padx=1, pady=1, sticky=tk.W)

        self.delete_button = tk.Button(self.virtual_keyboard, text="⌫", width=1, command=self.delete_last_character)
        self.delete_button.grid(row=row_val, column=1, padx=1, pady=1, sticky=tk.W)

        self.return_button = tk.Button(self.virtual_keyboard, text="⏎", width=1, command=self.press_return)
        self.return_button.grid(row=row_val, column=10, padx=1, pady=1, sticky=tk.W)

        # 3) Overlay-Position bestimmen & anzeigen
        self._place_keyboard()

        # 4) Bei Fenster-/Layoutänderung neu positionieren (Binding einmalig setzen/überschreiben)
        if self._configure_bind_id is None:
            self._configure_bind_id = self.app.bind("<Configure>", lambda e: self._place_keyboard())

    def press_return(self):
        """Entspricht Enter: Keyboard schließen + Suche starten."""
        try:
            self.app.search_and_close_keyboard()
        except Exception:
            # Fallback, falls Helfer nicht vorhanden:
            try:
                self.close_virtual_keyboard()
            except Exception:
                pass
            try:
                self.app.search_entries()
            except Exception:
                pass
        return "break"

    def _place_keyboard(self):
        if not getattr(self, "virtual_keyboard", None):
            return
        self.app.update_idletasks()

        info = self.app.tree_frame # Anker
        base_x = info.winfo_x() + 60
        base_y = info.winfo_y() + 45

        kb_w = self.virtual_keyboard.winfo_reqwidth()
        kb_h = self.virtual_keyboard.winfo_reqheight()

        page_w = self.app.winfo_width()
        page_h = self.app.winfo_height()

        x = base_x
        if x + kb_w > page_w:
            x = max(0, page_w - kb_w - 5)
        y = base_y
        if y + kb_h > page_h:
            y = max(0, page_h - kb_h - 5)

        self.virtual_keyboard.place(x=x, y=y)
        self.virtual_keyboard.lift()

    def open_virtual_keyboard(self):
        self.create_frame_keyboard() # erstellt (oder repositioniert) + zeigt an

    def close_virtual_keyboard(self):
        vk = getattr(self, "virtual_keyboard", None)
        if vk and vk.winfo_exists():
            vk.destroy()
        self.virtual_keyboard = None
        if self._configure_bind_id is not None:
            try:
                self.app.unbind("<Configure>", self._configure_bind_id)
            finally:
                self._configure_bind_id = None

    def delete_last_character(self):
        """Löscht Selektion oder das letzte Zeichen vor dem Cursor im aktiven Entry."""
        w = self.active_entry
        if w is None:
            # versuche Fokus-Widget
            fg = self.app.focus_get()
            if fg in (self.app.artist_entry, self.app.song_entry):
                w = fg
                self.active_entry = w
            else:
                w = self.app.artist_entry # Fallback
                self.active_entry = w
        try:
            # Wenn etwas selektiert ist -> selektierten Bereich entfernen
            try:
                w.delete("sel.first", "sel.last")
                return
            except tk.TclError:
                pass
            # Sonst: Zeichen vor dem Cursor löschen
            idx = w.index(tk.INSERT)
            if idx > 0:
                w.delete(idx - 1)
        except Exception:
            pass

    def shift(self): # Button "Shift"
        self.lowercase = not self.lowercase
        for i, button in enumerate(self.buttons):
            key = self.keys[i]
            if self.lowercase:
                button.config(text=key.lower())
            else:
                button.config(text=key)

    def focus_field(self, widget):
        """Merkt das aktive Entry und zeigt das Keyboard an."""
        self.active_entry = widget
        try:
            widget.focus_set()
        except Exception:
            pass
        self.open_virtual_keyboard()

    def click(self, button):
        """Fügt den aktuell auf dem Button sichtbaren Text in das aktive Entry ein."""
        text = button.cget("text")
        w = self.active_entry
        if w is None:
            # Fallback: nimm Artist-Entry
            w = self.app.artist_entry
            self.active_entry = w
        try:
            # vorhandene Selektion löschen
            try:
                w.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            # an Cursorposition einfügen
            w.insert(tk.INSERT, text)
            w.focus_set()
        except Exception:
            pass

class SenderData:
    def __init__(self, app):
        self.app = app          # Page01-Instanz
        # self.app.app           → App (tk.Tk), enthält listener_db_manager

    # ------------------------------------------------------------------
    # DB-Hilfsmethode
    # ------------------------------------------------------------------
    def _run_query(self, fetch_fn, date_from: str | None = None):
        """
        Führt eine der drei Abfragen gegen listener_stats.sqlite aus.
        date_from: ISO-Datum "2026-04-10" oder None für Gesamt.
        """
        db = getattr(self.app.app, 'listener_db_manager', None)
        if db is None:
            print("[SenderData] listener_db_manager nicht verfügbar")
            return []
        try:
            with db.get_cursor() as (conn, cursor):
                return fetch_fn(cursor, date_from)
        except Exception as e:
            print(f"[SenderData] DB-Fehler: {e}")
            return []

    # ------------------------------------------------------------------
    # WHERE-Klausel Hilfsmethode
    # ------------------------------------------------------------------
    @staticmethod
    def _date_filter(date_from: str | None) -> tuple:
        """Gibt WHERE-Klausel und Parameter-Liste zurück."""
        if date_from:
            return "WHERE DATE(ts_start) >= ?", [date_from]
        return "", []

    # ------------------------------------------------------------------
    # Drei separate SQL-Abfragen — je eine pro Sort-Modus
    # ------------------------------------------------------------------
    def _fetch_by_anzahl(self, cursor, date_from=None):
        """Standard: nach Anzahl Senderwahl absteigend."""
        where, params = self._date_filter(date_from)
        cursor.execute(f"""
            SELECT
                sender,
                COALESCE(SUM(duration_sec), 0)  AS total_sec,
                COUNT(*)                         AS anzahl
            FROM sender_log
            {where}
            GROUP BY LOWER(sender)
            ORDER BY anzahl DESC
        """, params)
        return cursor.fetchall()

    def _fetch_by_hoerdauer(self, cursor, date_from=None):
        """Nach Gesamthördauer absteigend."""
        where, params = self._date_filter(date_from)
        cursor.execute(f"""
            SELECT
                sender,
                COALESCE(SUM(duration_sec), 0)  AS total_sec,
                COUNT(*)                         AS anzahl
            FROM sender_log
            {where}
            GROUP BY LOWER(sender)
            ORDER BY total_sec DESC
        """, params)
        return cursor.fetchall()

    def _fetch_by_sender(self, cursor, date_from=None):
        """Alphabetisch nach Sendername."""
        where, params = self._date_filter(date_from)
        cursor.execute(f"""
            SELECT
                sender,
                COALESCE(SUM(duration_sec), 0)  AS total_sec,
                COUNT(*)                         AS anzahl
            FROM sender_log
            {where}
            GROUP BY LOWER(sender)
            ORDER BY LOWER(sender) ASC
        """, params)
        return cursor.fetchall()

    # ------------------------------------------------------------------
    # Hilfsfunktion: Sekunden → "h:mm h" bzw. "m:ss min"
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_duration(total_sec: int) -> str:
        if total_sec <= 0:
            return "0:00"
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h}:{m:02d} h"
        return f"{m}:{s:02d} min"

    # ------------------------------------------------------------------
    # Popup mit Zeitfilter-Buttons und sortierbaren Spalten
    # ------------------------------------------------------------------
    def _build_sender_stats_popup(self):
        """
        Öffnet das Senderstatistik-Fenster.

        Zeitfilter:   Heute | Woche | Monat | Gesamt  (Buttons)
        Spalten:      Sender  |  Hördauer  |  Anzahl Auswahl
        Standard:     Gesamt, nach Anzahl Auswahl absteigend.

        Sort-Klick:
          Sender ↓         → alphabetisch A→Z
          Hördauer ↓       → Gesamtdauer absteigend
          Anzahl Auswahl ↓ → Auswahlhäufigkeit absteigend
          Aktive Spalte zeigt ↓, alle anderen "-".
        """
        # Zustand: aktiver Zeitraum und aktive Sort-Spalte
        state = {"period": "gesamt", "sort_col": None}

        def get_date_from(period: str) -> str | None:
            """Berechnet Startdatum für den gewählten Zeitraum."""
            today = datetime.now()
            if period == "heute":
                return today.strftime("%Y-%m-%d")
            elif period == "woche":
                return (today - timedelta(days=7)).strftime("%Y-%m-%d")
            elif period == "monat":
                return (today - timedelta(days=30)).strftime("%Y-%m-%d")
            return None  # gesamt

        def refresh():
            """Lädt Daten neu — mit aktivem Zeitraum und aktiver Sortierung."""
            date_from = get_date_from(state["period"])
            col = state["sort_col"]
            if col == "Sender":
                rows = self._run_query(self._fetch_by_sender, date_from)
            elif col == "Hördauer":
                rows = self._run_query(self._fetch_by_hoerdauer, date_from)
            else:  # Anzahl Auswahl (Standard)
                rows = self._run_query(self._fetch_by_anzahl, date_from)
            populate(rows)
            label_map = {
                "heute": "Heute",
                "woche": "Letzte 7 Tage",
                "monat": "Letzte 30 Tage",
                "gesamt": "Gesamt"
            }
            status_var.set(
                f"Zeitraum: {label_map[state['period']]}  |  {len(rows)} Sender"
            )

        def on_period(period: str):
            """Filter-Button geklickt: Zustand aktualisieren + neu laden."""
            state["period"] = period
            for p, btn in period_buttons.items():
                btn.config(
                    relief="sunken" if p == period else "raised",
                    bg="#7eb8d4"    if p == period else "lightblue"
                )
            refresh()

        def sort_by(col):
            """Spalten-Heading geklickt: Sortierung + Heading-Symbole setzen."""
            state["sort_col"] = col
            update_headings(col)
            refresh()

        # ---- Fenster ----
        win = Toplevel(self.app)
        win.title("Senderstatistik")
        win.geometry("560x440")
        win.configure(bg='lightblue')

        Label(
            win,
            text="Senderstatistik – Hördauer & Auswahlhäufigkeit",
            bg='lightblue', font=("Helvetica", 12, "bold")
        ).pack(pady=(10, 4))

        # ---- Zeitfilter-Buttons ----
        btn_frame = tk.Frame(win, bg='lightblue')
        btn_frame.pack(pady=(0, 6))

        period_buttons: dict = {}
        for label, period in (
            ("Heute",  "heute"),
            ("Woche",  "woche"),
            ("Monat",  "monat"),
            ("Gesamt", "gesamt"),
        ):
            b = tk.Button(
                btn_frame, text=label, width=8,
                bg='lightblue', relief="raised",
                command=lambda p=period: on_period(p)
            )
            b.pack(side="left", padx=4)
            period_buttons[period] = b

        # ---- Treeview-Container ----
        container = ttk.Frame(win)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        COLS = ("Sender", "Hördauer", "Anzahl Auswahl")
        tree = ttk.Treeview(container, columns=COLS, show="headings")
        vsb  = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0,  column=1, sticky=tk.NS)

        tree.column("Sender",         width=220, anchor="w")
        tree.column("Hördauer",       width=130, anchor="center")
        tree.column("Anzahl Auswahl", width=130, anchor="center")

        # ---- Statusleiste ----
        status_var = tk.StringVar(value="")
        tk.Label(
            win, textvariable=status_var,
            bg='lightblue', font=("Helvetica", 9), anchor="w"
        ).pack(fill="x", padx=12, pady=(0, 6))

        # ---- Innere Hilfsfunktionen ----
        def populate(rows):
            """Treeview aus DB-Rows befüllen (sender, total_sec, anzahl)."""
            tree.delete(*tree.get_children())
            for sender, total_sec, anzahl in rows:
                tree.insert("", tk.END, values=(
                    (sender or "").strip(),
                    self._fmt_duration(int(total_sec or 0)),
                    anzahl
                ))

        def update_headings(active_col):
            for col in COLS:
                sym = "↓" if col == active_col else "-"
                tree.heading(col, text=f"{col}  {sym}",
                             command=lambda c=col: sort_by(c))
                
        def on_row_select(event):
            """Doppelklick oder Enter auf eine Zeile → Sender direkt tunen."""
            sel = tree.selection()
            if not sel:
                return
            sender_name = tree.item(sel[0])["values"][0]  # erste Spalte = Sendername
            app = self.app.app                             # App (tk.Tk)
            sender_names = getattr(getattr(app, 'state', None), 'Sender_Name', [])
            # Sender-Index bestimmen (case-insensitiv)
            name_lower = str(sender_name).strip().casefold()
            matches = [i for i, n in enumerate(sender_names)
                    if str(n).strip().casefold() == name_lower]
            if not matches:
                print(f"[SenderData] Sender '{sender_name}' nicht in Senderliste gefunden")
                return
            idx = matches[0]
            print(f"[SenderData] Tune → {sender_name} (idx={idx})")
            app.dispatcher.submit(lambda i=idx: app.tune_service(i), key="tune")
            win.destroy()  # Popup schliessen nach Senderwahl

        # ---- Mausrad-Scrolling (Pi-kompatibel) ----
        tree.bind("<Enter>",      lambda e: tree.focus_set())
        tree.bind("<MouseWheel>", lambda e: tree.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))
        tree.bind("<Button-4>",   lambda e: tree.yview_scroll(-1, "units"))
        tree.bind("<Button-5>",   lambda e: tree.yview_scroll( 1, "units"))

        # ---- Initial: Headings setzen + Gesamt laden ----
        update_headings(None)
        on_period("gesamt")  # setzt Button-Optik + lädt Daten + Statusleiste

        win.transient(self.app.winfo_toplevel())
        win.focus_force()
        tree.bind("<Double-1>", on_row_select)
        tree.bind("<Return>",   on_row_select)

__all__ = ["Page01"]