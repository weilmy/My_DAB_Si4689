#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bbox_editor.py  –  Bbox-Editor für si4689_datenbank
=====================================================
Externes Hilfsskript (läuft unabhängig von der Hauptapplikation).

Zweck:
    - Alle Sender aus si4689_datenbank anzeigen
    - bbox-Werte (x1, y1, x2, y2) pro Sender erfassen/bearbeiten
    - Werte in die Spalten bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y schreiben

Koordinaten-Schema (Originalbild-Pixel der CH-Karte):
    bbox_x_x = x1  (oben links,   x)
    bbox_x_y = y1  (oben links,   y)
    bbox_y_x = x2  (unten rechts, x)
    bbox_y_y = y2  (unten rechts, y)

Massenimport aus sender_map_regions.json:
    Beim Start wird optional eine sender_map_regions.json eingelesen
    und die bbox-Werte automatisch übernommen (Name-Matching).

Start:
    python bbox_editor.py
    python bbox_editor.py --db /pfad/zur/dab_scans.sqlite
    python bbox_editor.py --db /pfad/zur/dab_scans.sqlite --json /pfad/sender_map_regions.json
"""

import argparse
import json
import os
import sqlite3
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple


# ===========================================================================
#  Standard-Pfade
# ===========================================================================

_DEFAULT_DB   = os.path.join(
    os.path.dirname(__file__), "assets", "DB", "dab_scans.sqlite"
)
_DEFAULT_JSON = os.path.join(
    os.path.dirname(__file__), "assets", "jsons", "sender_map_regions.json"
)


# ===========================================================================
#  DB-Zugriff
# ===========================================================================

def _connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        messagebox.showerror(
            "Datenbank nicht gefunden",
            f"Datenbank nicht gefunden:\n{db_path}\n\n"
            "Bitte zuerst einen DAB-Scan durchführen.",
        )
        sys.exit(1)
    return sqlite3.connect(db_path, timeout=10, isolation_level=None)


def _ensure_bbox_columns(con: sqlite3.Connection) -> None:
    """Stellt sicher, dass alle bbox-Spalten vorhanden sind."""
    cur = con.cursor()
    cur.execute("PRAGMA table_info(si4689_datenbank)")
    existing = {row[1] for row in cur.fetchall()}
    for col in ("bbox_x_x", "bbox_x_y", "bbox_y_x", "bbox_y_y"):
        if col not in existing:
            cur.execute(
                f"ALTER TABLE si4689_datenbank ADD COLUMN {col} INTEGER;"
            )
    con.commit()


def fetch_rows(con: sqlite3.Connection) -> List[Dict]:
    cur = con.cursor()
    cur.execute("""
        SELECT si4689_idx, name, channel, ensemble,
               bbox_x_x, bbox_x_y, bbox_y_x, bbox_y_y
        FROM   si4689_datenbank
        ORDER  BY si4689_idx ASC
    """)
    out = []
    for row in cur.fetchall():
        idx, name, channel, ensemble, x1, y1, x2, y2 = row
        out.append({
            "si4689_idx": idx,
            "name":       name or "",
            "channel":    channel or "",
            "ensemble":   ensemble or "",
            "bbox_x_x":   x1,
            "bbox_x_y":   y1,
            "bbox_y_x":   x2,
            "bbox_y_y":   y2,
        })
    return out


def save_bbox(
    con: sqlite3.Connection,
    si4689_idx: int,
    x1: Optional[int],
    y1: Optional[int],
    x2: Optional[int],
    y2: Optional[int],
) -> None:
    cur = con.cursor()
    cur.execute(
        """
        UPDATE si4689_datenbank
        SET    bbox_x_x = ?,
               bbox_x_y = ?,
               bbox_y_x = ?,
               bbox_y_y = ?
        WHERE  si4689_idx = ?
        """,
        (x1, y1, x2, y2, si4689_idx),
    )
    con.commit()


# ===========================================================================
#  JSON-Import (sender_map_regions.json)
# ===========================================================================

def _norm(s: str) -> str:
    """Robust normalisieren für Name-Matching."""
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(ch for ch in s.strip().casefold() if ch.isalnum())


def load_bbox_from_json(json_path: str) -> Dict[str, List[int]]:
    """
    Liest bbox aus sender_map_regions.json.
    Rückgabe: {norm_name: [x1, y1, x2, y2]}
    """
    if not os.path.exists(json_path):
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result: Dict[str, List[int]] = {}
    for r in data:
        bbox = r.get("bbox")
        name = str(r.get("name", "") or "")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        try:
            bbox_int = [int(v) for v in bbox]
        except (TypeError, ValueError):
            continue
        if all(v == 0 for v in bbox_int):
            continue
        key = _norm(name)
        if key and key not in result:
            result[key] = bbox_int
    return result


def apply_json_to_db(
    con: sqlite3.Connection,
    json_path: str,
) -> Tuple[int, int]:
    """
    Importiert bbox-Werte aus JSON in die DB (nur Sender ohne bbox).
    Rückgabe: (übernommen, fehlend)
    """
    bbox_map = load_bbox_from_json(json_path)
    if not bbox_map:
        return 0, 0

    rows = fetch_rows(con)
    uebernommen = 0
    fehlend = 0

    for row in rows:
        key = _norm(row["name"])
        bbox = bbox_map.get(key)
        if bbox:
            save_bbox(con, row["si4689_idx"], bbox[0], bbox[1], bbox[2], bbox[3])
            uebernommen += 1
        else:
            fehlend += 1

    return uebernommen, fehlend


# ===========================================================================
#  GUI
# ===========================================================================

class BboxEditor(tk.Tk):
    """Hauptfenster des Bbox-Editors."""

    # Treeview-Spalten
    _COLS = (
        "si4689_idx", "name", "channel", "ensemble",
        "bbox_x_x", "bbox_x_y", "bbox_y_x", "bbox_y_y",
    )
    _HEADINGS = (
        "IDX", "Name", "Kanal", "Ensemble",
        "x1 (bbox_x_x)", "y1 (bbox_x_y)", "x2 (bbox_y_x)", "y2 (bbox_y_y)",
    )
    _WIDTHS = (45, 220, 55, 160, 100, 100, 100, 100)

    def __init__(self, db_path: str, json_path: Optional[str] = None):
        super().__init__()
        self.title(f"bbox-Editor  –  {os.path.basename(db_path)}")
        self.geometry("1000x600")
        self.resizable(True, True)

        self.db_path   = db_path
        self.json_path = json_path
        self.con       = _connect(db_path)
        _ensure_bbox_columns(self.con)

        self._rows: List[Dict] = []
        self._sel_idx: Optional[int] = None   # si4689_idx des gewählten Eintrags

        self._build_ui()
        self._load()

        # JSON-Massenimport beim Start (wenn angegeben)
        if self.json_path and os.path.exists(self.json_path):
            self._json_import_silent()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI aufbauen
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.configure(bg="#2B2B2B")

        # ---------- Toolbar ----------
        toolbar = tk.Frame(self, bg="#3C3C3C", pady=4)
        toolbar.pack(fill="x", side="top")

        btn_kw = dict(bg="#555", fg="white", activebackground="#777",
                      font=("Arial", 10), relief="flat", padx=8, pady=4)

        tk.Button(toolbar, text="🔄  Neu laden",
                  command=self._load, **btn_kw).pack(side="left", padx=4)

        tk.Button(toolbar, text="📥  JSON importieren",
                  command=self._json_import_dialog, **btn_kw).pack(side="left", padx=4)

        tk.Button(toolbar, text="🗑  bbox löschen",
                  command=self._clear_selected_bbox, **btn_kw).pack(side="left", padx=4)

        tk.Button(toolbar, text="💾  Alle speichern",
                  command=self._save_all, **btn_kw).pack(side="right", padx=4)

        self._status_var = tk.StringVar(value="Bereit.")
        tk.Label(toolbar, textvariable=self._status_var,
                 bg="#3C3C3C", fg="#AAFFAA",
                 font=("Arial", 10)).pack(side="right", padx=12)

        # ---------- Treeview ----------
        tree_frame = tk.Frame(self, bg="#2B2B2B")
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(4, 0))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                        background="#1E1E1E", foreground="white",
                        fieldbackground="#1E1E1E", rowheight=22,
                        font=("Consolas", 10))
        style.configure("Treeview.Heading",
                        background="#444", foreground="white",
                        font=("Arial", 10, "bold"))
        style.map("Treeview", background=[("selected", "#0057A8")])

        self.tree = ttk.Treeview(
            tree_frame,
            columns=self._COLS,
            show="headings",
            selectmode="browse",
        )

        for col, heading, width in zip(self._COLS, self._HEADINGS, self._WIDTHS):
            self.tree.heading(col, text=heading,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, minwidth=40, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # ---------- Eingabe-Bereich ----------
        edit_frame = tk.LabelFrame(
            self, text=" bbox bearbeiten (Originalbild-Pixel) ",
            bg="#2B2B2B", fg="#CCCCCC",
            font=("Arial", 11, "bold"),
            padx=10, pady=8,
        )
        edit_frame.pack(fill="x", padx=6, pady=6)

        lbl_kw = dict(bg="#2B2B2B", fg="#CCCCCC", font=("Arial", 11))
        ent_kw = dict(width=7, font=("Consolas", 12),
                      bg="#1E1E1E", fg="white", insertbackground="white",
                      relief="flat", bd=2)

        # Sender-Info
        self._info_var = tk.StringVar(value="— kein Sender gewählt —")
        tk.Label(edit_frame, textvariable=self._info_var,
                 bg="#2B2B2B", fg="#FFD700",
                 font=("Arial", 11, "bold")).grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(0, 6))

        # x1
        tk.Label(edit_frame, text="x1  (bbox_x_x)", **lbl_kw).grid(
            row=1, column=0, sticky="e", padx=(0, 4))
        self._e_x1 = tk.Entry(edit_frame, **ent_kw)
        self._e_x1.grid(row=1, column=1, padx=4)

        # y1
        tk.Label(edit_frame, text="y1  (bbox_x_y)", **lbl_kw).grid(
            row=1, column=2, sticky="e", padx=(12, 4))
        self._e_y1 = tk.Entry(edit_frame, **ent_kw)
        self._e_y1.grid(row=1, column=3, padx=4)

        # x2
        tk.Label(edit_frame, text="x2  (bbox_y_x)", **lbl_kw).grid(
            row=1, column=4, sticky="e", padx=(12, 4))
        self._e_x2 = tk.Entry(edit_frame, **ent_kw)
        self._e_x2.grid(row=1, column=5, padx=4)

        # y2
        tk.Label(edit_frame, text="y2  (bbox_y_y)", **lbl_kw).grid(
            row=1, column=6, sticky="e", padx=(12, 4))
        self._e_y2 = tk.Entry(edit_frame, **ent_kw)
        self._e_y2.grid(row=1, column=7, padx=4)

        # Legende
        tk.Label(
            edit_frame,
            text="  →  oben links: (x1, y1)     unten rechts: (x2, y2)"
                 "     [Koordinaten im Originalbildformat der CH-Karte]",
            bg="#2B2B2B", fg="#888888", font=("Arial", 9),
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(4, 0))

        # Speichern-Button
        tk.Button(
            edit_frame,
            text="💾  Diesen Sender speichern  (Enter)",
            command=self._save_selected,
            bg="#0057A8", fg="white",
            activebackground="#0077CC",
            font=("Arial", 11, "bold"),
            relief="flat", padx=10, pady=4,
        ).grid(row=1, column=8, padx=(20, 0), sticky="nsew")

        self.bind("<Return>", lambda _e: self._save_selected())

    # ------------------------------------------------------------------
    # Daten laden / anzeigen
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._rows = fetch_rows(self.con)
        self._refresh_tree(self._rows)
        bbox_count = sum(
            1 for r in self._rows
            if all(r.get(k) is not None for k in
                   ("bbox_x_x", "bbox_x_y", "bbox_y_x", "bbox_y_y"))
        )
        self._status("Geladen: "
                     f"{len(self._rows)} Sender, "
                     f"{bbox_count} mit bbox")

    def _refresh_tree(self, rows: List[Dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            vals = (
                r["si4689_idx"],
                r["name"],
                r["channel"],
                r["ensemble"],
                r["bbox_x_x"] if r["bbox_x_x"] is not None else "",
                r["bbox_x_y"] if r["bbox_x_y"] is not None else "",
                r["bbox_y_x"] if r["bbox_y_x"] is not None else "",
                r["bbox_y_y"] if r["bbox_y_y"] is not None else "",
            )
            tag = "hasbbox" if all(
                r.get(k) is not None
                for k in ("bbox_x_x", "bbox_x_y", "bbox_y_x", "bbox_y_y")
            ) else ""
            self.tree.insert("", "end", iid=str(r["si4689_idx"]),
                             values=vals, tags=(tag,))
        self.tree.tag_configure("hasbbox", foreground="#88FF88")

    # ------------------------------------------------------------------
    # Auswahl
    # ------------------------------------------------------------------

    def _on_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        row = next(
            (r for r in self._rows if str(r["si4689_idx"]) == iid), None
        )
        if row is None:
            return
        self._sel_idx = row["si4689_idx"]

        self._info_var.set(
            f"IDX {row['si4689_idx']}  –  {row['name']}"
            f"  [{row['channel']}  /  {row['ensemble']}]"
        )

        def _set(entry: tk.Entry, val) -> None:
            entry.delete(0, "end")
            if val is not None:
                entry.insert(0, str(val))

        _set(self._e_x1, row["bbox_x_x"])
        _set(self._e_y1, row["bbox_x_y"])
        _set(self._e_x2, row["bbox_y_x"])
        _set(self._e_y2, row["bbox_y_y"])

    # ------------------------------------------------------------------
    # Speichern
    # ------------------------------------------------------------------

    def _parse_int_or_none(self, entry: tk.Entry) -> Optional[int]:
        txt = entry.get().strip()
        if txt == "":
            return None
        try:
            return int(txt)
        except ValueError:
            raise ValueError(f"Ungültiger Wert: '{txt}' (muss eine ganze Zahl sein)")

    def _save_selected(self) -> None:
        if self._sel_idx is None:
            messagebox.showwarning("Kein Sender", "Bitte zuerst einen Sender auswählen.")
            return
        try:
            x1 = self._parse_int_or_none(self._e_x1)
            y1 = self._parse_int_or_none(self._e_y1)
            x2 = self._parse_int_or_none(self._e_x2)
            y2 = self._parse_int_or_none(self._e_y2)
        except ValueError as exc:
            messagebox.showerror("Eingabefehler", str(exc))
            return

        save_bbox(self.con, self._sel_idx, x1, y1, x2, y2)

        # Row im Speicher aktualisieren
        for r in self._rows:
            if r["si4689_idx"] == self._sel_idx:
                r["bbox_x_x"] = x1
                r["bbox_x_y"] = y1
                r["bbox_y_x"] = x2
                r["bbox_y_y"] = y2
                break

        # Treeview-Eintrag aktualisieren
        iid = str(self._sel_idx)
        row = next(r for r in self._rows if r["si4689_idx"] == self._sel_idx)
        self.tree.item(iid, values=(
            row["si4689_idx"], row["name"], row["channel"], row["ensemble"],
            x1 if x1 is not None else "",
            y1 if y1 is not None else "",
            x2 if x2 is not None else "",
            y2 if y2 is not None else "",
        ))
        has_all = all(v is not None for v in (x1, y1, x2, y2))
        self.tree.item(iid, tags=("hasbbox",) if has_all else ())

        self._status(f"✓  IDX {self._sel_idx}  –  bbox gespeichert: "
                     f"[{x1}, {y1}, {x2}, {y2}]")

        # Nächsten Sender auswählen (bequemes Durcharbeiten der Liste)
        children = self.tree.get_children()
        idx_list = list(children)
        try:
            pos = idx_list.index(iid)
            next_iid = idx_list[pos + 1] if pos + 1 < len(idx_list) else None
        except ValueError:
            next_iid = None
        if next_iid:
            self.tree.selection_set(next_iid)
            self.tree.see(next_iid)
            self._on_select()

    def _save_all(self) -> None:
        """Alle Einträge im Speicher in die DB schreiben (für Massenänderungen)."""
        count = 0
        for r in self._rows:
            save_bbox(
                self.con, r["si4689_idx"],
                r["bbox_x_x"], r["bbox_x_y"],
                r["bbox_y_x"], r["bbox_y_y"],
            )
            count += 1
        self._status(f"✓  {count} Einträge gespeichert.")

    def _clear_selected_bbox(self) -> None:
        if self._sel_idx is None:
            messagebox.showwarning("Kein Sender", "Bitte zuerst einen Sender auswählen.")
            return
        save_bbox(self.con, self._sel_idx, None, None, None, None)
        for e in (self._e_x1, self._e_y1, self._e_x2, self._e_y2):
            e.delete(0, "end")
        for r in self._rows:
            if r["si4689_idx"] == self._sel_idx:
                r.update(bbox_x_x=None, bbox_x_y=None, bbox_y_x=None, bbox_y_y=None)
                break
        self._refresh_tree(self._rows)
        self._status(f"✓  IDX {self._sel_idx}: bbox gelöscht.")

    # ------------------------------------------------------------------
    # JSON-Import
    # ------------------------------------------------------------------

    def _json_import_silent(self) -> None:
        """Beim Start: JSON-Import ohne Dialog."""
        uebernommen, fehlend = apply_json_to_db(self.con, self.json_path)
        self._load()
        self._status(
            f"JSON-Import: {uebernommen} bbox übernommen, {fehlend} fehlend"
        )

    def _json_import_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="sender_map_regions.json wählen",
            filetypes=[("JSON", "*.json"), ("Alle", "*.*")],
            initialdir=os.path.dirname(self.json_path or _DEFAULT_JSON),
        )
        if not path:
            return
        if not messagebox.askyesno(
            "JSON importieren",
            f"bbox-Werte aus\n{path}\nin die Datenbank importieren?\n\n"
            "(Nur Sender mit passendem Namen werden aktualisiert.)",
        ):
            return
        uebernommen, fehlend = apply_json_to_db(self.con, path)
        self._load()
        self._status(
            f"JSON-Import: {uebernommen} bbox übernommen, {fehlend} fehlend"
        )

    # ------------------------------------------------------------------
    # Sortierung
    # ------------------------------------------------------------------

    def _sort_by(self, col: str) -> None:
        reverse = getattr(self, "_sort_reverse", False)
        try:
            self._rows.sort(
                key=lambda r: (r.get(col) is None, r.get(col) or 0),
                reverse=reverse,
            )
        except TypeError:
            self._rows.sort(
                key=lambda r: str(r.get(col) or ""),
                reverse=reverse,
            )
        self._sort_reverse = not reverse
        self._refresh_tree(self._rows)

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    def _status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _on_close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass
        self.destroy()


# ===========================================================================
#  main
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="bbox-Editor für si4689_datenbank"
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"Pfad zur SQLite-Datenbank (Standard: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--json",
        default=None,
        help="Pfad zur sender_map_regions.json für automatischen Import beim Start",
    )
    args = parser.parse_args()

    app = BboxEditor(db_path=args.db, json_path=args.json)
    app.mainloop()


if __name__ == "__main__":
    main()