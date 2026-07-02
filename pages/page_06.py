#!/usr/bin/env python3
# ('my_venv_314':venv)

# -*- coding: utf-8 -*-

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

import os
import sqlite3
import threading
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple
from PIL import Image, ImageTk
from .base_page import BasePage


class Page06(BasePage):
    """
    Page06: Klickbare DAB-Senderkarte (Schweiz)

    - Header (25px), Karte (430px), Statuszeile (25px) -> 800x480 Layout
    - Klick auf Logo-BBox ruft app.tune_service(si4689_idx) auf
    - Regions (name, si4689_idx, bbox) werden direkt aus si4689_datenbank gelesen
    """

    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app = controller
        self._ui_built = False
        self.configure(bg="#E7EF76")

    # ==== BasePage Lifecycle ====

    def on_first_activate(self):
        """Nur beim allerersten echten Anzeigen der Seite."""
        if not self._ui_built:
            self.build_gui()
            self._ui_built = True
        self._reload_map_async()

    def on_reactivate(self):
        """Bei jedem späteren Umschalten zur Seite."""
        self._reload_map_async()

    def build_gui(self):
        self.configure(bg="#E7EF76")

        # ----- GRID Grundlayout -----
        self.grid_rowconfigure(0, minsize=25, weight=0)
        self.grid_rowconfigure(1, minsize=420, weight=1)
        self.grid_rowconfigure(2, minsize=25, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # ----- Header -----
        self.header_frame = tk.Frame(self, height=25, bg="#E7EF76")
        self.header_frame.grid(row=0, column=0, sticky=tk.NSEW)
        self.header_frame.grid_propagate(False)

        self.label = tk.Label(
            self.header_frame,
            text="DAB Senderkarte Schweiz",
            bg="#E7EF76",
            fg="black",
            font=("Helvetica", 25),
        )
        self.label.pack(fill="both", expand=True)

        # ----- Map -----
        self.map_frame = tk.Frame(self, height=420, bg="black")
        self.map_frame.grid(row=1, column=0, sticky="nsew")
        self.map_frame.grid_propagate(False)

        self.map_widget = SenderMapWidget(
            self.map_frame,
            app=self.app,
            on_select=self._on_region_selected,
            debug_right_click=False,  # True => Rechtsklick druckt Bild-Koordinaten
            bg="black",
        )
        self.map_widget.pack(fill="both", expand=True)

        # ----- Status -----
        self.status_frame = tk.Frame(self, height=25, bg="#E7EF76")
        self.status_frame.grid(row=2, column=0, sticky="nsew")
        self.status_frame.grid_propagate(False)

        self.status_var = tk.StringVar(value="Status: bereit")
        self.status_label = tk.Label(
            self.status_frame,
            textvariable=self.status_var,
            bg="#E7EF76",
            fg="black",
            font=("Arial", 12),
            anchor="w",
            padx=10,
        )
        self.status_label.pack(fill="both", expand=True)

    def _on_region_selected(self, region: Dict[str, Any]) -> None:
        """Wird beim Klick auf ein Logo aufgerufen (UI-thread-sicher)."""
        name = region.get("name", "Unbekannt")
        sid  = region.get("service_id", "?")
        self.app.gui_batcher.schedule_update(
            lambda: self.status_var.set(f"Status: wähle {name} (IDX {sid})")
        )

    # ------------------------------------------------------------------
    # Async-Reload: DB → SenderMapWidget (kein JSON mehr)
    # ------------------------------------------------------------------

    def _reload_map_async(self) -> None:
        """Startet DB-Reload des Kartenwidgets in einem Hintergrund-Thread."""
        threading.Thread(target=self._reload_map_worker, daemon=True).start()

    def _reload_map_worker(self) -> None:
        def _apply():
            try:
                self.map_widget.reload_regions()
                count = len(self.map_widget.regions_with_bbox())
                self.status_var.set(
                    f"Status: Karte geladen – {count} Sender mit bbox"
                )
            except Exception as exc:
                self.status_var.set(f"Status: Ladefehler: {type(exc).__name__}: {exc}")

        self.app.gui_batcher.schedule_update(_apply)

    def on_page_hide(self):
        """Wird beim Verlassen der Page aufgerufen."""
        pass  # Meist nicht nötig, da global gecacht


# ===========================================================================
#  SenderMapWidget
# ===========================================================================

class SenderMapWidget(tk.Frame):
    """
    Klickbare Senderkarte (Canvas + Hintergrundbild).

    - Hintergrundbild wird proportional skaliert (Aspect Ratio bleibt).
    - Klick-Regions werden direkt aus si4689_datenbank gelesen:
        si4689_idx  → service_id  (wird an app.tune_service() übergeben)
        name        → Anzeigename
        bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y → [x1, y1, x2, y2]
          (Koordinaten in ORIGINALBILD-Pixeln)
    - Sender ohne bbox (alle vier Werte NULL oder 0) sind nicht klickbar.
    - Bei Klick: ruft app.tune_service(si4689_idx) auf (in Thread),
      optional zusätzlich on_select(region_dict).
    """

    def __init__(
        self,
        parent: tk.Misc,
        app: Any,
        on_select: Optional[Callable[[Dict[str, Any]], None]] = None,
        debug_right_click: bool = False,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self.app = app
        self.on_select = on_select
        self.debug_right_click = debug_right_click

        self._photo: Optional[Any] = None
        self._img_original: Optional[Image.Image] = None
        self._regions: List[Dict[str, Any]] = []

        # Transformationsdaten Canvas <-> Bild
        self._scale   = 1.0
        self._off_x   = 0
        self._off_y   = 0
        self._drawn_w = 1
        self._drawn_h = 1

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # Hover/Highlight
        self._hover_name: Optional[str]  = None
        self._hover_rect_id: Optional[int]  = None
        self._hover_label_id: Optional[int] = None

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>",    self._on_motion)
        self.canvas.bind("<Button-1>",  self._on_click)
        if self.debug_right_click:
            self.canvas.bind("<Button-3>", self._debug_print_img_coords)

        self.load_image()
        self.reload_regions()
        self._redraw()

    # ------------------------------------------------------------------
    # Pfad-Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_project_path(rel_or_abs: str) -> str:
        """Absolute oder projekt-relative Pfade auflösen."""
        if os.path.isabs(rel_or_abs):
            return rel_or_abs
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        return os.path.abspath(os.path.join(root, rel_or_abs))

    def _resolve_dab_scan_db_path(self) -> str:
        cfg = getattr(self.app, "config_data", {}) or {}
        rel = cfg.get("dab_scan_db", "assets/DB/dab_scans.sqlite")
        if os.path.isabs(rel):
            path = rel
        else:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            path = os.path.abspath(os.path.join(root, rel))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ------------------------------------------------------------------
    # Bild laden
    # ------------------------------------------------------------------

    def load_image(self) -> None:
        image_path = (getattr(self.app, "config_data", {}) or {}).get("Sender_CH_Karte")
        if not image_path:
            raise ValueError("Config-Key 'Sender_CH_Karte' fehlt (Pfad zur Karte).")
        image_path = self._resolve_project_path(str(image_path))
        self._img_original = Image.open(image_path).convert("RGBA")

    # ------------------------------------------------------------------
    # Regions direkt aus DB laden (kein JSON mehr)
    # ------------------------------------------------------------------

    def reload_regions(self) -> None:
        """
        Liest Sender-Regions direkt aus si4689_datenbank.

        Spalten: si4689_idx, name, bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y
          bbox_x_x = x1  (oben links,   x-Koordinate)
          bbox_x_y = y1  (oben links,   y-Koordinate)
          bbox_y_x = x2  (unten rechts, x-Koordinate)
          bbox_y_y = y2  (unten rechts, y-Koordinate)

        Sender ohne vollständige bbox (NULL oder alle 0) werden ohne
        'bbox'-Key gespeichert und sind auf der Karte nicht klickbar.
        """
        db_path = self._resolve_dab_scan_db_path()
        regions: List[Dict[str, Any]] = []

        try:
            con = sqlite3.connect(db_path, timeout=10)
            try:
                cur = con.cursor()
                cur.execute("""
                    SELECT si4689_idx, name,
                           bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y
                    FROM   si4689_datenbank
                    ORDER  BY si4689_idx ASC
                """)
                for row in cur.fetchall():
                    si4689_idx, name, x1, y1, x2, y2 = row
                    if si4689_idx is None:
                        continue

                    entry: Dict[str, Any] = {
                        "name":       str(name or ""),
                        "service_id": int(si4689_idx),   # si4689_idx = Tune-Index
                    }

                    # bbox nur setzen wenn alle vier Werte vorhanden und != 0
                    if None not in (x1, y1, x2, y2):
                        bbox = [int(x1), int(y1), int(x2), int(y2)]
                        if not all(v == 0 for v in bbox):
                            entry["bbox"] = bbox

                    regions.append(entry)
            finally:
                con.close()

        except Exception as exc:
            print(f"❌ SenderMapWidget.reload_regions() DB-Fehler: {exc}")

        self._regions = regions

    def regions_with_bbox(self) -> List[Dict[str, Any]]:
        """Gibt nur Regions zurück, die eine gültige bbox haben."""
        return [r for r in self._regions if "bbox" in r]

    # ------------------------------------------------------------------
    # Zeichnen
    # ------------------------------------------------------------------

    def _on_resize(self, _evt=None):
        self._redraw()

    def _redraw(self):
        if self._img_original is None:
            return

        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())

        iw, ih = self._img_original.size
        scale = min(cw / iw, ch / ih)

        self._scale   = scale
        self._drawn_w = max(1, int(iw * scale))
        self._drawn_h = max(1, int(ih * scale))
        self._off_x   = (cw - self._drawn_w) // 2
        self._off_y   = (ch - self._drawn_h) // 2

        img_scaled = self._img_original.resize(
            (self._drawn_w, self._drawn_h), Image.LANCZOS
        )
        self._photo = ImageTk.PhotoImage(img_scaled)

        self.canvas.delete("all")
        self.canvas.create_image(
            self._off_x, self._off_y, image=self._photo, anchor="nw"
        )

        # Hover neu zeichnen (falls aktiv)
        self._hover_rect_id  = None
        self._hover_label_id = None
        if self._hover_name:
            region = next(
                (r for r in self._regions if r.get("name") == self._hover_name),
                None,
            )
            if region:
                self._draw_hover(region, None)

    # ------------------------------------------------------------------
    # Koordinaten-Umrechnung & Hit-Test
    # ------------------------------------------------------------------

    def _canvas_to_img_coords(self, cx: int, cy: int) -> Tuple[float, float]:
        """Canvas-Koordinaten → Originalbild-Pixel."""
        x = (cx - self._off_x) / self._scale
        y = (cy - self._off_y) / self._scale
        return x, y

    def _hit_test(self, img_x: float, img_y: float) -> Optional[Dict[str, Any]]:
        """
        In-Memory-Suche für Hover-Effekte (benutzt self._regions).
        Koordinatenregel:
            img_x >= bbox_x_x  (x1, oben links  x)
            img_x <= bbox_y_x  (x2, unten rechts x)
            img_y >= bbox_x_y  (y1, oben links  y)
            img_y <= bbox_y_y  (y2, unten rechts y)
        """
        for r in self._regions:
            bbox = r.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                continue
            try:
                if all(int(v) == 0 for v in bbox):
                    continue
            except Exception:
                continue
            # bbox = [x1, y1, x2, y2]  →  [bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y]
            x1, y1, x2, y2 = bbox
            if x1 <= img_x <= x2 and y1 <= img_y <= y2:
                return r
        return None

    def _db_find_idx_at(
        self, img_x: float, img_y: float
    ) -> Optional[Tuple[int, str]]:
        """
        Direkte DB-Abfrage (DatabaseManager) beim Klick-Ereignis.

        Sucht die Zeile in si4689_datenbank, deren bbox den Klickpunkt enthält:

            a  = img_x  (Mausklick x im Originalbild)
            b  = img_y  (Mausklick y im Originalbild)

            Bedingung pro Zeile z:
                a >= z.bbox_x_x   (oben links  x)
                a <= z.bbox_y_x   (unten rechts x)
                b >= z.bbox_x_y   (oben links  y)
                b <= z.bbox_y_y   (unten rechts y)

        Beispiel RADIO 24  (bbox_x_x=478, bbox_x_y=133, bbox_y_x=500, bbox_y_y=151):
            Klick x=493 → 493 >= 478  und  493 <= 500  ✓
            Klick y=144 → 144 >= 133  und  144 <= 151  ✓
            → Treffer, si4689_idx dieser Zeile wird zurückgegeben.

        Rückgabe: (si4689_idx, name) oder None wenn kein Treffer.
        """
        db_manager = getattr(self.app, "scan_db_manager", None)

        # --- Primär: DatabaseManager ---
        if db_manager is not None:
            try:
                with db_manager.get_cursor() as (conn, cursor):
                    cursor.execute(
                        """
                        SELECT si4689_idx, name
                        FROM   si4689_datenbank
                        WHERE  :a >= bbox_x_x    -- Klick-x rechts von linker Kante
                          AND  :a <= bbox_y_x    -- Klick-x links  von rechter Kante
                          AND  :b >= bbox_x_y    -- Klick-y unter  oberer Kante
                          AND  :b <= bbox_y_y    -- Klick-y über   unterer Kante
                        LIMIT 1
                        """,
                        {"a": img_x, "b": img_y},
                    )
                    row = cursor.fetchone()
                    if row:
                        return int(row[0]), str(row[1] or "")
                    return None
            except Exception as exc:
                print(f"❌ _db_find_idx_at() DB-Fehler: {exc}")

        # --- Fallback: In-Memory (self._regions) ---
        region = self._hit_test(img_x, img_y)
        if region:
            return region.get("service_id"), region.get("name", "")
        return None

    # ------------------------------------------------------------------
    # Event-Handler
    # ------------------------------------------------------------------

    def _on_click(self, event):
        """
        Klick-Ablauf:
          1. Canvas-Koordinaten → Originalbild-Pixel umrechnen
          2. DB abfragen: welcher Sender liegt unter dem Klickpunkt?
          3. on_select()-Callback aufrufen (Statuszeile)
          4. app.tune_service(si4689_idx) in Hintergrund-Thread starten
        """
        # 1) Koordinaten umrechnen
        img_x, img_y = self._canvas_to_img_coords(event.x, event.y)

        # 2) DB-Suche
        result = self._db_find_idx_at(img_x, img_y)
        if result is None:
            return                          # Klick ausserhalb aller Logos

        si4689_idx, name = result

        # 3) UI-Callback (z.B. Statuszeile aktualisieren)
        if self.on_select:
            try:
                self.on_select({"name": name, "service_id": si4689_idx})
            except Exception as exc:
                print(f"⚠️  on_select() Fehler: {exc}")

        # 4) Sender wählen (IO-blockierend → eigener Thread)
        def worker():
            try:
                self.app.tune_service(si4689_idx)
            except Exception as exc:
                print(f"❌ tune_service({si4689_idx}) fehlgeschlagen: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_motion(self, event):
        img_x, img_y = self._canvas_to_img_coords(event.x, event.y)
        region = self._hit_test(img_x, img_y)

        name = region.get("name") if region else None
        if name == self._hover_name:
            return

        self._hover_name = name
        self._clear_hover()

        if region:
            self._draw_hover(region, event)

    def _draw_hover(self, region: Dict[str, Any], event):
        bbox = region.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            return
        try:
            if all(int(v) == 0 for v in bbox):
                return
        except Exception:
            return
        x1, y1, x2, y2 = bbox

        sx1 = int(x1 * self._scale + self._off_x)
        sy1 = int(y1 * self._scale + self._off_y)
        sx2 = int(x2 * self._scale + self._off_x)
        sy2 = int(y2 * self._scale + self._off_y)

        self._hover_rect_id = self.canvas.create_rectangle(
            sx1, sy1, sx2, sy2, outline="black", width=2
        )

        if event and region.get("name"):
            lx = event.x + 12
            ly = event.y + 12
            self._hover_label_id = self.canvas.create_text(
                lx, ly,
                anchor="nw",
                text=str(region["name"]),
                fill="#3F0E71",
                font=("Arial", 14, "bold"),
            )

    def _clear_hover(self):
        if self._hover_rect_id:
            self.canvas.delete(self._hover_rect_id)
            self._hover_rect_id = None
        if self._hover_label_id:
            self.canvas.delete(self._hover_label_id)
            self._hover_label_id = None

    def _debug_print_img_coords(self, event):
        img_x, img_y = self._canvas_to_img_coords(event.x, event.y)
        print(f"Bild-Koordinaten: x={img_x:.1f}, y={img_y:.1f}")


__all__ = ["Page06"]