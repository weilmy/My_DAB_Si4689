#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ta_controller.py  –  Verkehrsdurchsage (Traffic Announcement) für My_DAB_Si4689
==============================================================================

Enthält die gesamte TA-spezifische Logik des Si4689-Projekts, getrennt vom
Radio-Treiber und von der GUI

Zwei Bausteine:

1. ``TaController`` – der REINE Zustandsautomat. Er fasst KEINE Hardware an
   und kennt kein Tkinter. Er wird im Sekundentakt mit ``tick(...)`` gefüttert
   (ANNO-Flag + DLS-Texte) und gibt eine ``TaAction`` zurück, die die App
   ausführt (Service wechseln, Lautstärke setzen, Fenster öffnen/schließen).
   Dadurch bleibt der gesamte SPI-/Threading-Teil in der App, wo er
   serialisiert ist, und die Logik bleibt testbar.

2. ``TaWindow`` – das Toplevel-Fenster, das während einer bestätigten
   Durchsage angezeigt wird. 
"""

from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["TaController", "TaAction", "TaWindow"]

# ===========================================================================
# TaAction – das, was die App nach jedem tick() ausführen soll
# ===========================================================================
@dataclass
class TaAction:
    """Ergebnis eines tick(). ``kind`` sagt der App, was zu tun ist.

    kind:
    "none"   – nichts tun
    "start"  – Durchsage aktiv: ggf. auf Träger umschalten, Fenster + TA-Lautstärke
    "back"   – zurück auf den Heimsender: Lautstärke restaurieren, Fenster schließen

    ``reason`` (nur bei "back"): "ta_end" | "false_alarm" | "anno_end" | "channel_left"
    """
    kind: str = "none"
    reason: str = ""

# ===========================================================================
# TaController – reiner Zustandsautomat (kein SPI, kein Tk)
# ===========================================================================
class TaController:
    def __init__(self,
                 *,
                 settle_time: float = 3.0,
                 rising_ticks: int = 2):
        self.settle_time = settle_time   # Beruhigungszeit nach TA-Ende (ANNO-Flackern)
        self.rising_ticks = max(1, rising_ticks)  # ANNO muss so oft in Folge 1 sein

        self.state: str = "HOME_STATION"
        self._prev_anno: bool = False
        self._suppressed: bool = False   # nach Fehlalarm bis ANNO=0 gesperrt
        self._settle_until: float = 0.0  # bis hierhin keine neue ANNO-Flanke annehmen
        self._anno_high_count: int = 0   # aufeinanderfolgende ANNO=1-Polls (Entprellung)

    @property
    def active(self) -> bool:
        return self.state == "TA_ACTIVE"

    def reset(self) -> None:
        """Zurück in den Ruhezustand (z.B. wenn der Nutzer den Sender wechselt).

        Setzt KEIN _prev_anno zurück: läuft die aktuelle Durchsage noch
        (ANNO=1), wird sie nicht sofort erneut gegriffen – erst eine neue
        ANNO-Flanke triggert wieder.
        """
        self.state = "HOME_STATION"
        self._suppressed = False
        self._settle_until = 0.0
        self._anno_high_count = 0

    def cancel(self, now: Optional[float] = None) -> None:
        """Nutzer-Abbruch: wie reset(), aber unterdrückt sofortige Wiedererkennung.

        Solange ANNO noch high ist (_suppressed=True), wird keine neue TA
        ausgelöst. Erst nach dem ANNO-Abfall (fallende Flanke) und nach
        Ablauf der settle_time ist eine neue Erkennung möglich.
        """
        if now is None:
            now = time.monotonic()
        self.state = "HOME_STATION"
        self._suppressed = True
        self._settle_until = now + self.settle_time
        self._anno_high_count = 0

    # ----- Kern -----------------------------------------------------------
    def tick(self,
                *,
                anno: bool,
                now: Optional[float] = None) -> TaAction:
            """``anno`` = Verkehrsdurchsage AKTIV (von der App aus 0xB6 ASW-Traffic + anno-Level
            bestimmt). Kein DLS, keine Kanal-Beschränkung mehr."""
            if now is None:
                now = time.monotonic()

            falling = (not anno) and self._prev_anno
            self._prev_anno = anno
            self._anno_high_count = self._anno_high_count + 1 if anno else 0
            if falling:
                self._suppressed = False

            if self.state == "HOME_STATION":
                if (self._anno_high_count >= self.rising_ticks
                        and not self._suppressed
                        and now >= self._settle_until):
                    self.state = "TA_ACTIVE"
                    return TaAction("start")

            elif self.state == "TA_ACTIVE":
                if not anno:
                    self.state = "HOME_STATION"
                    self._settle_until = now + self.settle_time
                    return TaAction("back", "ta_end")

            return TaAction("none")

# ===========================================================================
# TaWindow – Toplevel-Fenster während bestätigter Durchsage (Optik wie T5A)
# ===========================================================================
class TaWindow:
    """Fenster, das bei bestätigter Durchsage angezeigt wird.

    WICHTIG: Tkinter ist nicht thread-sicher. Erzeugen und Schließen dieses
    Fensters MUSS im Tk-Hauptthread erfolgen (in der App z.B. via
    ``self.after(0, ...)``).

    Die Buttons sind vorhanden, aber NOCH NICHT verdrahtet (Callbacks
    optional, Default = None -> Button tut nichts). Die Funktion bauen wir
    später ein.
    """

    BG         = "#000000"
    FRAME      = "#5a5a5a"
    HEADER_FG  = "#cc2200"
    LABEL_FG   = "#aaaaaa"
    STATION_FG = "#e0e0e0"
    BTN_BG     = "#2a2a2a"
    BTN_FG     = "#cccccc"
    BTN_ACTIVE = "#3a3a3a"

    WIDTH  = 500
    HEIGHT = 320

    def __init__(self,
                 parent: tk.Misc,
                 station: str = "SRF 1",
                 on_louder:  Optional[Callable[[], None]] = None,
                 on_quieter: Optional[Callable[[], None]] = None,
                 on_cancel:  Optional[Callable[[], None]] = None):
        self.parent = parent
        self.station = station
        self._on_louder = on_louder
        self._on_quieter = on_quieter
        self._on_cancel = on_cancel

        self.win = tk.Toplevel(parent)
        self.win.title("Verkehrsdurchsage")
        self.win.configure(bg=self.BG,
                           highlightthickness=2,
                           highlightbackground=self.FRAME)
        self.win.resizable(False, False)
        self.win.transient(parent)              # gehört zum Hauptfenster
        # Schließen-X / ESC -> Abbrechen-Callback (noch unverdrahtet)
        self.win.protocol("WM_DELETE_WINDOW", self._handle_cancel)
        self.win.bind("<Escape>", lambda e: self._handle_cancel())

        self._build_layout()
        self._center_now()
        try:
            self.win.after(50, self._center_now)
        except tk.TclError:
            pass
        try:
            self.win.lift()
        except tk.TclError:
            pass

    # ----- Layout ---------------------------------------------------------
    def _build_layout(self) -> None:
        container = tk.Frame(self.win, bg=self.BG)
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)

        header = tk.Label(container, text="Verkehrsdurchsage",
                          fg=self.HEADER_FG, bg=self.BG,
                          font=("Calibri", 22, "bold"))
        header.pack(pady=(8, 4))

        station = tk.Label(container, text=self.station,
                           fg=self.STATION_FG, bg=self.BG,
                           font=("Calibri", 14))
        station.pack(pady=(0, 14))

        sep = tk.Frame(container, bg=self.FRAME, height=1)
        sep.pack(fill=tk.X, padx=4, pady=(0, 14))

        vol_label = tk.Label(container, text="Lautstärke",
                             fg=self.LABEL_FG, bg=self.BG,
                             font=("Calibri", 13, "bold"))
        vol_label.pack(anchor="w", padx=4, pady=(0, 8))

        btn_row = tk.Frame(container, bg=self.BG)
        btn_row.pack(anchor="w", padx=4, pady=(0, 18))

        self.btn_quieter = tk.Button(
            btn_row, text="Leiser", width=10,
            bg=self.BTN_BG, fg=self.BTN_FG,
            activebackground=self.BTN_ACTIVE, activeforeground=self.BTN_FG,
            relief=tk.RAISED, borderwidth=1, font=("Calibri", 12),
            command=self._handle_quieter)           # noch ohne Funktion
        self.btn_quieter.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_louder = tk.Button(
            btn_row, text="Lauter", width=10,
            bg=self.BTN_BG, fg=self.BTN_FG,
            activebackground=self.BTN_ACTIVE, activeforeground=self.BTN_FG,
            relief=tk.RAISED, borderwidth=1, font=("Calibri", 12),
            command=self._handle_louder)            # noch ohne Funktion
        self.btn_louder.pack(side=tk.LEFT)

        sep2 = tk.Frame(container, bg=self.FRAME, height=1)
        sep2.pack(fill=tk.X, padx=4, pady=(6, 12))

        cancel_row = tk.Frame(container, bg=self.BG)
        cancel_row.pack(fill=tk.X, padx=4)
        self.btn_cancel = tk.Button(
            cancel_row, text="Abbrechen", width=12,
            bg=self.BTN_BG, fg=self.BTN_FG,
            activebackground=self.BTN_ACTIVE, activeforeground=self.BTN_FG,
            relief=tk.RAISED, borderwidth=1, font=("Calibri", 12),
            command=self._handle_cancel)            # noch ohne Funktion
        self.btn_cancel.pack(side=tk.RIGHT)

    def _center_now(self) -> None:
        if not (self.win and self.win.winfo_exists()):
            return
        try:
            self.parent.update_idletasks()
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            px = self.parent.winfo_rootx()
            py = self.parent.winfo_rooty()
            if pw < 100 or ph < 100:
                pw = self.parent.winfo_screenwidth()
                ph = self.parent.winfo_screenheight()
                px = py = 0
            x = px + max(0, (pw - self.WIDTH) // 2)
            y = py + max(0, (ph - self.HEIGHT) // 2)
            self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except tk.TclError:
            try:
                self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}")
            except tk.TclError:
                pass

    # ----- Button-Handler ------------------------------------------------
    def _handle_louder(self) -> None:
        if self._on_louder:
            self._on_louder()

    def _handle_quieter(self) -> None:
        if self._on_quieter:
            self._on_quieter()

    def _handle_cancel(self) -> None:
        if self._on_cancel:
            self._on_cancel()

    # ----- Steuerung durch die App ----------------------------------------
    def close(self) -> None:
        """Fenster schließen (im Tk-Hauptthread aufrufen)."""
        try:
            if self.win and self.win.winfo_exists():
                self.win.destroy()
        except tk.TclError:
            pass
        finally:
            self.win = None

    def is_open(self) -> bool:
        try:
            return bool(self.win and self.win.winfo_exists())
        except tk.TclError:
            return False
        
