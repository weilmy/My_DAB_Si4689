#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_patches.py
=================

Prüft die beiden Treiber-Patches in si4689_driver.py:

  Patch 1 – get_part_info():
      Opcode 0x08, Offsets RESP4/5/8/9, Part-Nummer DEZIMAL.
      Erwartet: part_str = "Si4689", romid = 0 (ROM00).

  Patch 2 – _wait_cts():
      Liest 5 Statusbytes und wirft bei REPOFERR/CMDOFERR (STATUS3 & 0x0C).

Ablauf:
  Stufe A  Quelltext-Check (ohne Hardware): sind die Patches eingespielt?
  Stufe B  Hardware-Test: get_part_info() lesen + 200 Normalbefehle ohne
           Fehlauslösung von Patch 2.
  Stufe C  (optional) Overflow provozieren: SPI-Takt stufenweise erhöhen und
           prüfen, ob Patch 2 wirklich greift. Best-effort – kann je nach
           Pi5-Teiler/Verdrahtung folgenlos bleiben.

Ausführen:
    sudo ~/my_venv_314/bin/python3 verify_patches.py
    sudo ~/my_venv_314/bin/python3 verify_patches.py provoke   # inkl. Stufe C
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

DRIVER_DIR  = Path("/home/weilmy/My_DAB_Si4689/hardware")
DRIVER_FILE = DRIVER_DIR / "si4689_driver.py"

TEST_CHANNEL  = "8B"          # BERN1, bekannt-guter Sender (nur als Last)
NORMAL_ROUNDS = 200           # Normalbefehle für die Fehlauslösungs-Prüfung
EXPECTED_PART = 4689          # Si4689
PROVOKE_SPEEDS = [40_000_000, 50_000_000, 62_000_000, 80_000_000, 100_000_000]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ===========================================================================
# Stufe A – Quelltext-Check (keine Hardware nötig)
# ===========================================================================
def source_check() -> bool:
    log("=== Stufe A: Quelltext-Check ===")
    if not DRIVER_FILE.exists():
        log(f"  FEHLER: {DRIVER_FILE} nicht gefunden.")
        return False
    src = DRIVER_FILE.read_text(encoding="utf-8", errors="ignore")

    import re
    checks = {
        "Patch1 Opcode 0x08":
            bool(re.search(r"CMD_GET_PART_INFO\s*=\s*0x08", src)),
        "Patch1 Parse reply[9] (10 Bytes)":
            ("_read_reply(10)" in src and "reply[9]" in src),
        "Patch1 Part dezimal":
            ('f"Si{part}"' in src and 'f"Si{part:04X}"' not in src),
        "Patch2 5-Byte-Status-Read":
            ("0x00, 0x00, 0x00, 0x00, 0x00" in src),
        "Patch2 Overflow-Maske 0x0C":
            ("status3" in src and "0x0C" in src),
    }

    all_ok = True
    for name, ok in checks.items():
        log(f"  [{'OK ' if ok else 'XX '}] {name}")
        all_ok = all_ok and ok
    log(f"  -> {'alle Patches im Quelltext gefunden' if all_ok else 'PATCH(ES) FEHLEN'}")
    return all_ok


# ===========================================================================
# Stufe B/C – Hardware
# ===========================================================================
def hardware_check(provoke: bool) -> int:
    sys.path.insert(0, str(DRIVER_DIR))
    from hardware.si4689_driver import Si4689  # noqa: E402

    rc = 0
    radio = Si4689(verbose=True)
    try:
        log("=== Stufe B: Hardware-Test ===")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.configure_i2s(master=False)

        # --- Patch 1: get_part_info() ---
        info = radio.get_part_info()
        log(f"  get_part_info(): part_str={info['part_str']} "
            f"part={info['part']} romid={info['romid']} chiprev={info['chiprev']}")
        if info["part"] == EXPECTED_PART and info["part_str"] == f"Si{EXPECTED_PART}":
            log(f"  [OK ] Patch 1: Si{EXPECTED_PART} korrekt gelesen.")
        else:
            log(f"  [XX ] Patch 1: erwartet Si{EXPECTED_PART}, "
                f"erhalten {info['part_str']} (Patch evtl. nicht eingespielt).")
            rc = 2

        # --- Patch 2 Regressions-Check: keine Fehlauslösung im Normalbetrieb ---
        radio.set_dab_freq_list()
        radio.dab_tune(TEST_CHANNEL)
        radio.dab_digrad_status(stc_ack=True)
        time.sleep(0.5)

        log(f"  {NORMAL_ROUNDS} Normalbefehle – darf NICHT 'SPI-Überlauf' werfen …")
        false_trips = 0
        for i in range(NORMAL_ROUNDS):
            try:
                radio.get_part_info()
                radio.get_property(0xB201)        # DAB_VALID_RSSI_THRESHOLD
                radio.dab_digrad_status(stc_ack=False)
            except RuntimeError as exc:
                if "Überlauf" in str(exc):
                    false_trips += 1
                    log(f"    !! Fehlauslösung in Runde {i}: {exc}")
                else:
                    raise
        if false_trips == 0:
            log("  [OK ] Patch 2: keine Fehlauslösung im Normalbetrieb.")
        else:
            log(f"  [XX ] Patch 2: {false_trips} Fehlauslösungen – zu streng/fehlerhaft.")
            rc = 2

        # --- Stufe C: Overflow provozieren (optional) ---
        if provoke:
            log("=== Stufe C: Overflow provozieren (best-effort) ===")
            triggered_at = None
            for spd in PROVOKE_SPEEDS:
                radio._spi.max_speed_hz = spd
                log(f"  Teste {spd/1e6:.1f} MHz …")
                try:
                    for _ in range(50):
                        radio.get_part_info()       # post-send _wait_cts prüft STATUS3
                except RuntimeError as exc:
                    if "Überlauf" in str(exc):
                        triggered_at = spd
                        log(f"  [OK ] Patch 2 ausgelöst bei {spd/1e6:.1f} MHz: {exc}")
                        break
                    raise
                except Exception as exc:
                    log(f"  (Abbruch bei {spd/1e6:.1f} MHz: {exc})")
                    break
            # sichere Rate wiederherstellen
            radio._spi.max_speed_hz = 2_000_000
            if triggered_at is None:
                log("  [--] Kein Overflow provozierbar – Chip/Pi5 toleriert die "
                    "Raten oder der Takt wird heruntergeteilt. Nicht aussagekräftig.")

        log("=" * 56)
        log(f"GESAMT: {'ALLES OK ✓' if rc == 0 else 'FEHLER ✗ – siehe oben'}")
        log("=" * 56)

    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        try:
            radio.close()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    provoke = len(sys.argv) > 1 and sys.argv[1].lower() == "provoke"
    src_ok = source_check()
    if not src_ok:
        log("Hinweis: Quelltext-Check unvollständig – Hardware-Test läuft trotzdem,")
        log("         zeigt dann aber den Stand VOR dem Patch.")
    raise SystemExit(hardware_check(provoke))