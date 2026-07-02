#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/ta_sniffer_bern1.py  (v3 – interrupt-getrieben)
=======================================================
Eigenständiger TA-Sniffer für BERN1, der den INT-Pin des Si4689 nutzt –
exakt wie der TA-Pfad der App (FALLING-Edge -> dab_get_event_status -> annoint).

Warum Interrupt statt Polling:
  - bildet den Produktionspfad der App 1:1 ab (Standalone-Validierung),
  - triggert auf das STICKY annoint-Bit (DEVNTINT), also die sensitivste Quelle
    -> klärt endgültig, ob OE-Durchsagen (src=1) überhaupt ein Event auslösen,
  - sparsamer (kein 1-Hz-SPI-Dauerpoll).

Interrupt-Konfiguration (wie App, aber nur DAB-Events, kein DLS):
  INT_CTL_ENABLE (0x0000)           = 0x2000  (Bit13 DEVNTIEN)
  DAB_EVENT_INTERRUPT_SOURCE(0xB300)= 0x0010  (Bit4  ANNO_INTEN)
  DAB_ANNOUNCEMENT_ENABLE (0xB700)  = 0x07FF  (alle Typen)

ISR-Pfad: FALLING-Edge -> Event setzen (kein SPI im Callback) -> Hauptthread
liest dab_get_event_status(ack=False) [annoint?], quittiert mit ack=True und
leert die Announcement-Queue (0xB6). Ein 1-s-Pegel-Check der INT-Leitung dient
als Sicherheitsnetz, falls eine Flanke verpasst wurde.

Bei src=1 (OE) wird wie gehabt kurz ins Ziel-Ensemble gehoppt.

Schreibt NICHT in die DB, ändert weder Treiber noch App.
Ausführen (läuft bis Strg-C):
  sudo ~/my_venv_314/bin/python3 scripts/ta_sniffer_bern1.py
"""
from __future__ import annotations

import sys
import time
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import RPi.GPIO as GPIO  # noqa: E402
from hardware.si4689_driver import (  # noqa: E402
    Si4689, PROP_INT_CTL_ENABLE, PROP_DAB_EVENT_INTERRUPT_SOURCE,
)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
DB_PATH        = PROJECT_DIR / "assets/DB/dab_scans.sqlite"
LOG_PATH       = PROJECT_DIR / "scripts/ta_sniffer_bern1.log"
TARGET_NAME    = "Swiss Pop+"
HEARTBEAT_SEC  = 300
MAX_MINUTES    = 0            # 0 = unbegrenzt (bis Strg-C)
DEBUG          = False

PROP_ANN_EN    = 0xB700
INT_CTL_VAL    = 0x2000       # DEVNTIEN (Bit13) – nur DAB-Events, kein DLS
EVENT_SRC_VAL  = 0x0010       # ANNO_INTEN (Bit4)
ANN_ENABLE     = 0x07FF

FIC_MIN        = 90
LOCK_TIMEOUT   = 10.0
HOP_TO_OE      = True
HOP_LOCK_TO    = 8.0
HOP_DWELL      = 2.0
HOP_READS      = 8

_ANN_NAMES = ["Alarm", "Traffic", "Transport", "Warning", "News", "Weather",
              "Event", "Special", "Programme", "Sport", "Financial"]

# INT-Ereignis aus dem GPIO-Callback an den Hauptthread
_irq = threading.Event()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_logf = open(LOG_PATH, "a", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")


def dbg(msg: str) -> None:
    if DEBUG:
        log(msg)


def ann_types(mask: int) -> str:
    bits = [n for i, n in enumerate(_ANN_NAMES) if mask & (1 << i)]
    return ",".join(bits) if bits else "-"


def src_name(src: int) -> str:
    return {0: "eigenes Ensemble", 1: "anderes Ensemble (OE)",
            2: "FM", 3: "reserved"}.get(src, "?")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def load_db() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT si4689_idx, name, channel, ensemble, freq_index, "
        "service_id, component_id FROM si4689_datenbank "
        "ORDER BY freq_index, si4689_idx"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def name_for_sid16(rows: list[dict], channel: str, sid16: int) -> str:
    for r in rows:
        if r["channel"] == channel and (int(r["service_id"]) & 0xFFFF) == sid16:
            return r["name"]
    return f"SID16=0x{sid16:04X}"


# ---------------------------------------------------------------------------
# Hardware-Hilfen
# ---------------------------------------------------------------------------
def wait_for_lock(radio: Si4689, timeout: float) -> tuple[bool, dict]:
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + timeout
    sig: dict = {}
    while time.monotonic() < deadline:
        sig = radio.get_dab_signal_strength()
        if sig.get("acq") and sig.get("fic_quality", 0) >= FIC_MIN:
            return True, sig
        time.sleep(0.3)
    return False, sig


def read_anno(radio: Si4689) -> dict:
    """DAB_GET_ANNOUNCEMENT_INFO (0xB6) lesen/dekodieren (AN649, Cmd 0xB6)."""
    radio._write_command([0xB6, 0x00])
    r = radio._read_reply(16)
    return {
        "q_ovfl":      bool(r[4] & 0x01),
        "q_size":      r[5] & 0x1F,
        "cluster":     r[6],
        "src":         r[7] & 0x03,
        "region_flag": bool((r[7] >> 2) & 0x01),
        "anno_stat":   (r[7] >> 3) & 0x01,
        "asw":         r[8] | (r[9] << 8),
        "id1":         r[10] | (r[11] << 8),
        "id2":         r[12] | (r[13] << 8),
        "regionid1":   r[14],
        "regionid2":   r[15],
    }


def drain_anno(radio: Si4689) -> list[dict]:
    """Announcement-Queue leeren. ERR (leere Queue) -> 'fertig'."""
    out: list[dict] = []
    for _ in range(12):
        try:
            ev = read_anno(radio)
        except Exception as exc:
            dbg(f"  0xB6: {exc} (Queue leer)")
            break
        if ev["asw"] == 0 and ev["q_size"] == 0:
            break
        out.append(ev)
        if ev["q_size"] == 0:
            break
    return out


def safe_close_spi(radio: Si4689) -> None:
    try:
        if getattr(radio, "_spi", None) is not None:
            radio._spi.close()
            radio._spi = None
    except Exception:
        pass
    radio._opened = False


def tune_lock_start(radio: Si4689, channel: str, sid: int, cid: int,
                    timeout: float) -> bool:
    radio.dab_tune(channel)
    locked, sig = wait_for_lock(radio, timeout)
    if not locked:
        log(f"  Kein Lock auf {channel} (FIC={sig.get('fic_quality')}%).")
        return False
    radio.dab_start_service(sid, cid)
    return True


def _on_int(channel: int) -> None:
    """GPIO-Callback (RPi.GPIO-Thread): KEIN SPI – nur Event setzen."""
    _irq.set()


# ---------------------------------------------------------------------------
# Volvo-Hop: bei OES kurz ins Ziel-Ensemble, dortigen Zielsender auflösen
# ---------------------------------------------------------------------------
def resolve_oe_target(radio: Si4689, rows: list[dict],
                      eid_map: dict[int, str], target_eid: int,
                      bern1: dict) -> None:
    target_ch = eid_map.get(target_eid)
    if not target_ch:
        log(f"  -> Ziel-EId 0x{target_eid:04X} nicht in Kanalliste; kein Hop.")
        return
    if not HOP_TO_OE:
        log(f"  -> Ziel-Ensemble {target_ch} (Hop deaktiviert).")
        return

    log(f"  -> HOP nach {target_ch} (EId 0x{target_eid:04X}) ...")
    seed = next((r for r in rows if r["channel"] == target_ch), None)
    try:
        if seed is None or not tune_lock_start(
                radio, target_ch, int(seed["service_id"]),
                int(seed["component_id"]), HOP_LOCK_TO):
            log(f"  -> Hop nach {target_ch} fehlgeschlagen (kein Lock).")
            return
        radio.dab_get_event_status(ack=True)
        found = False
        for _ in range(HOP_READS):
            time.sleep(HOP_DWELL)
            try:
                st = radio.dab_get_event_status(ack=True)
            except Exception:
                continue
            if not (st.get("anno") or st.get("annoint")):
                continue
            for ev in drain_anno(radio):
                if ev["src"] == 0 and ev["asw"]:
                    tgt = name_for_sid16(rows, target_ch, ev["id1"])
                    log(f"  *** ZIELSENDER: {tgt}  (SID16=0x{ev['id1']:04X} "
                        f"comp=0x{ev['id2']:04X} clus=0x{ev['cluster']:02X} "
                        f"typ={ann_types(ev['asw'])})")
                    found = True
                    break
            if found:
                break
        if not found:
            log(f"  -> Auf {target_ch} keine aktive Durchsage erfasst (Timing).")
    except Exception as exc:
        log(f"  -> Hop-Fehler: {exc}")
    finally:
        try:
            tune_lock_start(radio, bern1["channel"], int(bern1["service_id"]),
                            int(bern1["component_id"]), LOCK_TIMEOUT)
            radio.dab_get_event_status(ack=True)
            log(f"  -> zurück auf {TARGET_NAME}.")
        except Exception as exc:
            log(f"  -> Rückkehr zu {TARGET_NAME} fehlgeschlagen: {exc}")
        _irq.clear()


def handle_irq(radio: Si4689, rows: list[dict], eid_map: dict[int, str],
               bern1: dict, seen: set) -> None:
    """ISR im Hauptthread: Event triagieren, quittieren, Queue auswerten."""
    try:
        evt = radio.dab_get_event_status(ack=False)
    except Exception as exc:
        dbg(f"0xB3 (no-ack) Fehler: {exc}")
        return
    annoint = bool(evt.get("annoint"))
    anno = bool(evt.get("anno"))
    if not (annoint or anno):
        dbg("IRQ ohne annoint/anno (spurious).")
        return

    try:
        evt2 = radio.dab_get_event_status(ack=True)   # quittieren + re-arm
        anno = bool(evt2.get("anno", anno))
    except Exception as exc:
        dbg(f"0xB3 (ack) Fehler: {exc}")

    log(f"IRQ: annoint={annoint} anno={anno}")

    for ev in drain_anno(radio):
        if ev["asw"] == 0:
            continue
        key = (ev["cluster"], ev["src"], ev["anno_stat"], ev["id1"], ev["id2"])
        if key in seen:
            continue
        seen.add(key)

        stat = "START" if ev["anno_stat"] else "STOP "
        log(f"ANNO {stat} | cluster=0x{ev['cluster']:02X} "
            f"| src={ev['src']} ({src_name(ev['src'])}) "
            f"| typ={ann_types(ev['asw'])} "
            f"| ID1=0x{ev['id1']:04X} ID2=0x{ev['id2']:04X}"
            + (" | OVERFLOW" if ev["q_ovfl"] else ""))

        if ev["anno_stat"] == 0:
            seen.clear()                 # Episode beendet -> Signaturen frei
            continue
        if ev["src"] == 0:
            tgt = name_for_sid16(rows, bern1["channel"], ev["id1"])
            log(f"  *** ZIELSENDER (lokal in {bern1['channel']}): "
                f"{tgt}  (comp=0x{ev['id2']:04X})")
        elif ev["src"] == 1:
            resolve_oe_target(radio, rows, eid_map, ev["id1"], bern1)
            seen.clear()


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
def main() -> int:
    rows = load_db()
    # bern1 = next((r for r in rows if r["name"].strip() == TARGET_NAME), None)
    bern1 = next((r for r in rows if r["si4689_idx"] == 70), None)
    if not bern1:
        log(f"FEHLER: '{TARGET_NAME}' nicht in der DB gefunden.")
        return 1
    log(f"Ziel: {TARGET_NAME}  Kanal={bern1['channel']}  "
        f"SID=0x{int(bern1['service_id']):X}  Comp=0x{int(bern1['component_id']):X}")

    radio = Si4689(verbose=False)
    int_registered = False
    try:
        log("Initialisiere Si4689 (DAB) ...")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.set_dab_freq_list()

        # Interrupt-Quellen freischalten (wie App, aber nur DAB-Events)
        radio.set_property(PROP_ANN_EN, ANN_ENABLE)
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, EVENT_SRC_VAL)
        radio.set_property(PROP_INT_CTL_ENABLE, INT_CTL_VAL)
        log(f"INT-Quellen: INT_CTL=0x{INT_CTL_VAL:04X} "
            f"EVENT_SRC=0x{EVENT_SRC_VAL:04X} ANN_EN=0x{ANN_ENABLE:04X}")

        log("Baue EId->Kanal-Map ...")
        eid_map: dict[int, str] = {}
        for ch in dict.fromkeys(r["channel"] for r in rows):
            radio.dab_tune(ch)
            locked, _ = wait_for_lock(radio, 6.0)
            if not locked:
                log(f"  {ch}: kein Lock (übersprungen)")
                continue
            try:
                ens = radio.dab_get_ensemble_info()
                eid_map[ens["eid"]] = ch
                log(f"  {ch}: EId=0x{ens['eid']:04X} '{ens['label']}'")
            except Exception as exc:
                log(f"  {ch}: Ensemble-Info Fehler: {exc}")

        log(f"Stelle auf {TARGET_NAME} und starte Dienst ...")
        if not tune_lock_start(radio, bern1["channel"], int(bern1["service_id"]),
                               int(bern1["component_id"]), LOCK_TIMEOUT):
            log("FEHLER: BERN1 lockt nicht – Abbruch.")
            return 1
        radio.dab_get_event_status(ack=True)        # Rest-Sticky löschen -> INT HIGH

        # GPIO-Interrupt registrieren
        try:
            GPIO.setup(radio.int_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            try:
                GPIO.remove_event_detect(radio.int_pin)
            except Exception:
                pass
            GPIO.add_event_detect(radio.int_pin, GPIO.FALLING,
                                  callback=_on_int, bouncetime=50)
            int_registered = True
            level = "HIGH" if GPIO.input(radio.int_pin) else "LOW(!)"
            log(f"INT aktiv: FALLING auf GPIO{radio.int_pin} (Pegel jetzt {level}).")
        except Exception as exc:
            log(f"add_event_detect fehlgeschlagen ({exc}) -> Pegel-Polling 1 Hz.")

        sig = radio.get_dab_signal_strength()
        log(f"Lock OK: FIC={sig['fic_quality']}%  RSSI={sig['rssi']} dBuV. "
            f"Warte auf Durchsage (INT/annoint). Strg-C zum Beenden.")

        # ===== Hauptschleife: auf INT warten, Pegel-Check als Sicherheitsnetz =====
        t_start = time.monotonic()
        t_hb = t_start
        seen: set = set()
        while True:
            fired = _irq.wait(timeout=1.0)
            _irq.clear()
            now = time.monotonic()

            if MAX_MINUTES and (now - t_start) > MAX_MINUTES * 60:
                log(f"Laufzeitlimit {MAX_MINUTES} min erreicht – Ende.")
                break

            if now - t_hb >= HEARTBEAT_SEC:
                t_hb = now
                try:
                    s = radio.get_dab_signal_strength()
                    acf = radio.get_acf_status()
                    log(f"... aktiv. FIC={s['fic_quality']}% RSSI={s['rssi']}dBuV "
                        f"audio={acf['audio_dbfs']}dBFS "
                        f"mute={'JA' if acf['soft_muting'] else 'nein'}")
                except Exception:
                    log("... aktiv (Status-Read übersprungen).")

            # INT pending? (Callback ODER Pegel LOW als Sicherheitsnetz)
            pending = fired
            try:
                if GPIO.input(radio.int_pin) == 0:     # LOW = aktiv
                    pending = True
            except Exception:
                pass
            if not pending:
                continue

            handle_irq(radio, rows, eid_map, bern1, seen)

    except KeyboardInterrupt:
        log("Beendet durch Benutzer (Strg-C).")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if int_registered:
            try:
                GPIO.remove_event_detect(radio.int_pin)
            except Exception:
                pass
        try:
            radio.dab_stop_service(int(bern1["service_id"]),
                                   int(bern1["component_id"]))
        except Exception:
            pass
        safe_close_spi(radio)
        log("SPI geschlossen (GPIO-Pins unverändert, kein cleanup).")
        _logf.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())