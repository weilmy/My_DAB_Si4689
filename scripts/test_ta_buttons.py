#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ta_buttons.py  –  Simulation der TA-Fenster-Buttons
=========================================================
Öffnet ein echtes TaWindow ohne Hardware/App.
Testet _ta_louder, _ta_quieter, _ta_cancel_for_user.

Starten:  python3 test_ta_buttons.py
"""

import tkinter as tk
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Optional, cast

import importlib.util as _ilu, os as _os, sys as _sys
_spec = _ilu.spec_from_file_location(
    "ta_controller",
    _os.path.join(_os.path.dirname(__file__), "utils", "ta_controller.py"),
)
_mod = _ilu.module_from_spec(_spec)
_sys.modules["ta_controller"] = _mod
_spec.loader.exec_module(_mod)
TaWindow = _mod.TaWindow


# ---------------------------------------------------------------------------
# Minimaler AppState (nur die für TA relevanten Felder)
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    AktuelleLautstaerke_DAB: int = 55   # Heimlautstärke
    TA_Lautstaerke_DAB:      int = 65   # aktuelle TA-Lautstärke


# ---------------------------------------------------------------------------
# Minimale Audio-Codec-Attrappe
# ---------------------------------------------------------------------------
class FakeAudioCodec:
    def set_volume_amixer(self, vol: int) -> None:
        print(f"  [amixer] set_volume → {vol}")


# ---------------------------------------------------------------------------
# Simulations-App (enthält die drei Methoden 1:1 wie in main.py)
# ---------------------------------------------------------------------------
class TaSimApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("TA-Simulation")
        self.configure(bg="#111111")
        self.geometry("600x200")

        self.state_      = AppState()          # 'state' ist in tk.Tk belegt → state_
        self.audio_codec = FakeAudioCodec()
        self._ta_window  = None
        self._ta_active  = True                # so als wäre TA gerade aktiv
        self._ta_home    = None
        self._ta_save_volume_after_id = None

        # Temporäre JSON-Datei für den Persistierungstest
        self._json_file  = os.path.join(tempfile.gettempdir(), "ta_sim_state.json")
        self.config_data = {"dab_state_file": self._json_file}

        self._build_ui()
        # TA-Fenster sofort öffnen
        self.after(100, self._ta_open_window)

    # ----- Hilfs-UI --------------------------------------------------------
    def _build_ui(self) -> None:
        lbl = tk.Label(self, text="TA-Button-Simulation", fg="#cc2200", bg="#111111",
                       font=("Calibri", 16, "bold"))
        lbl.pack(pady=10)

        self._vol_var = tk.StringVar()
        self._home_var = tk.StringVar()
        self._update_labels()

        tk.Label(self, textvariable=self._vol_var,  fg="#aaaaaa", bg="#111111",
                 font=("Calibri", 13)).pack()
        tk.Label(self, textvariable=self._home_var, fg="#aaaaaa", bg="#111111",
                 font=("Calibri", 13)).pack()

    def _update_labels(self) -> None:
        self._vol_var.set(
            f"TA-Lautstärke:    {self.state_.TA_Lautstaerke_DAB}")
        self._home_var.set(
            f"Heim-Lautstärke:  {self.state_.AktuelleLautstaerke_DAB}")
        self.after(200, self._update_labels)

    # ----- volume_service-Attrappe ----------------------------------------
    def volume_service(self, vol: int) -> None:
        print(f"  [volume_service] → {vol}")

    # ----- TA-Fenster ------------------------------------------------------
    def _ta_open_window(self) -> None:
        if self._ta_window and self._ta_window.is_open():
            return
        self._ta_window = TaWindow(
            self,
            station="SRF 1 (Simulation)",
            on_louder=self._ta_louder,
            on_quieter=self._ta_quieter,
            on_cancel=self._ta_cancel_for_user,
        )
        print("✅ TaWindow geöffnet — bitte Buttons testen.")

    def _ta_close_window(self) -> None:
        if self._ta_window:
            self._ta_window.close()
        self._ta_window = None

    # ----- Die drei zu testenden Methoden (1:1 wie in main.py) ------------
    def _ta_louder(self) -> None:
        new_vol = min(100, int(self.state_.TA_Lautstaerke_DAB) + 5)
        self.state_.TA_Lautstaerke_DAB = new_vol
        try:
            self.audio_codec.set_volume_amixer(new_vol)
        except Exception as e:
            print(f"[TA] _ta_louder Fehler: {e}")
        print(f"🔊 TA-Lautstärke: +5 → {new_vol}")
        self._ta_save_volume_debounced()

    def _ta_quieter(self) -> None:
        new_vol = max(0, int(self.state_.TA_Lautstaerke_DAB) - 5)
        self.state_.TA_Lautstaerke_DAB = new_vol
        try:
            self.audio_codec.set_volume_amixer(new_vol)
        except Exception as e:
            print(f"[TA] _ta_quieter Fehler: {e}")
        print(f"🔉 TA-Lautstärke: −5 → {new_vol}")
        self._ta_save_volume_debounced()

    def _ta_cancel_for_user(self) -> None:
        self.after(0, self._ta_close_window)
        self.volume_service(self.state_.AktuelleLautstaerke_DAB)
        print(f" 🚦 ✓ Lautstärke wiederhergestellt: {self.state_.AktuelleLautstaerke_DAB}")
        self._ta_active = False
        self._ta_home = None
        print("🚫 TA abgebrochen – Simulation beendet.")
        self.after(200, self.destroy)

    def _ta_save_volume_debounced(self) -> None:
        aid = getattr(self, "_ta_save_volume_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(cast(str, aid))
            except tk.TclError:
                pass

        def do_save():
            self._ta_save_volume_after_id = None
            try:
                try:
                    with open(self._json_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    state = {}
                ta_block = state.setdefault("traffic_announcement", {})
                ta_block["ta_volume"] = int(self.state_.TA_Lautstaerke_DAB)
                with open(self._json_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=4, ensure_ascii=False)
                print(f"💾 TA-Lautstärke gespeichert: {ta_block['ta_volume']}  "
                      f"(→ {self._json_file})")
            except Exception as e:
                print(f"[TA] JSON-Save Fehler: {e}")

        self._ta_save_volume_after_id = self.after(2000, do_save)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  TA-Button-Simulation")
    print("  Heim-Lautstärke : 55   TA-Lautstärke : 65")
    print("  Lauter/Leiser   : ändert TA-Lautstärke ±5")
    print("  Abbrechen       : restauriert Heim-Lautstärke")
    print("  JSON-Save       : erfolgt 2 s nach letztem Klick")
    print("=" * 55)
    app = TaSimApp()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        print("\nSimulation abgebrochen.")
