#!/usr/bin/env python3
# ('my_venv_314':venv)
# base_page.py

import tkinter as tk

class BasePage(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.app = controller
        self._first_activation = True

    def activate(self):
        """Wird beim Seitenwechsel aufgerufen. Entscheidet zwischen Erst- und Wiederaktivierung."""
        if self._first_activation:
            self._first_activation = False
            self.on_first_activate()
        else:
            self.on_reactivate()

    def on_first_activate(self):
        """Nur beim allerersten Aufrufen der Seite (durch main.py)."""
        pass

    def on_reactivate(self):
        """Bei jedem späteren Umschalten zu dieser Seite."""
        pass

__all__ = ["BasePage"]
