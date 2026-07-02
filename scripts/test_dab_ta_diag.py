#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dab_ta_diag.py   (Diagnose-Variante)
=========================================

Wie test_dab_ta_switch.py, aber für den Timing-/ASW-Vergleich mit dem T5A:

  * Poll im SEKUNDENTAKT (POLL_INTERVAL = 1.0).
  * Zeitstempel mit Millisekunden ([HH:MM:SS.mmm]) – identisch zum T5A,
    sobald Sie dort denselben Zeitstempel ergänzen.
  * Bei aktiver Announcement (ANNO=1) wird JEDER unterschiedliche
    Queue-Eintrag mit VOLLEM ASW geloggt – auch wenn nicht umgeschaltet wird.
  * "Harte" Bits (Alarm/Traffic/Warning/News) werden mit ★ hervorgehoben,
    damit Sie im Vergleich sofort sehen, ob im Moment des T5A-Umschaltens
    ein hartes Bit auftaucht.

Das Umschalten auf SRF 1 bleibt aktiv (SRC=0), damit der Parallelbetrieb
vergleichbar ist. SWITCH_ASW_FILTER ist vorbereitet, um später nur auf
bestimmte Bits umzuschalten – steht für diesen Lauf bewusst auf 0.

Ausführen:
    sudo ~/my_venv_314/bin/python3 test_dab_ta_diag.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
DRIVER_DIR = Path("/home/weilmy/My_DAB_Si4689/hardware")
DB_PATH    = Path("/home/weilmy/My_DAB_Si4689/assets/DB/dab_scans.sqlite")

TEST_CHANNEL  = "12C"
SWISS_POP_IDX = 70
SRF1_IDX      = 63

FALLBACK_SWISS_SID, FALLBACK_SWISS_CID = 0x42F1, 0x10
FALLBACK_SRF1_SID,  FALLBACK_SRF1_CID  = 0x44B1, 0x02

POLL_INTERVAL  = 1.0          # Sekundentakt
LOCK_TIMEOUT   = 8.0
FIC_MIN        = 90
NORMAL_VOLUME  = 48
TA_VOLUME      = 60
MAX_QUEUE_READ = 6            # Einträge pro Poll scannen

# Diagnose: harte Announcement-Typen hervorheben
HARD_BITS = 0x0001 | 0x0002 | 0x0008 | 0x0010   # Alarm|Traffic|Warning|News
# Umschalt-Filter: 0 = auf jede SRC=0-Announcement umschalten (wie bisher).
# Später z.B. = HARD_BITS setzen, um nur auf echte Durchsagen zu schalten.
SWITCH_ASW_FILTER = 0x0000

ANNO_TYPES = 0x07FF
PROP_DAB_ANNOUNCEMENT_ENABLE    = 0xB700
PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
ANNO_INTEN_BIT = 0x0010

ASW_NAMES = ["ALARM", "TRAFFIC", "TRANSPORT", "WARNING", "NEWS", "WEATHER",
             "EVENT", "SPECIAL", "PROGRAM", "SPORT", "FINANCIAL"]
SRC_NAMES = {0: "cur-ens", 1: "other-ens", 2: "FM", 3: "reserved"}

sys.path.insert(0, str(DRIVER_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402


def log(msg: str) -> None:
    # Millisekunden-Zeitstempel für den Timing-Vergleich mit dem T5A
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


# ===========================================================================
def load_service(db_path: Path, idx: int):
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT name, channel, service_id, component_id "
            "FROM si4689_datenbank WHERE si4689_idx = ?", (idx,)).fetchone()
        con.close()
    except Exception as exc:
        log(f"DB-Lesefehler (idx {idx}): {exc}")
        return None
    if row is None:
        return None
    return {"name": row["name"], "sid": int(row["service_id"]),
            "cid": int(row["component_id"])}


def read_event_flags(radio: Si4689) -> dict:
    radio._write_command([0xB3, 0x00])
    r = radio._read_reply(9)
    return {"annoint": bool(r[4] & 0x10), "anno_active": bool(r[5] & 0x10)}


def ack_events(radio: Si4689) -> None:
    radio._write_command([0xB3, 0x01])
    radio._read_reply(9)


def read_announcement_info(radio: Si4689) -> dict:
    radio._write_command([0xB6])
    r = radio._read_reply(16)
    return {
        "q_size":    r[5] & 0x1F,
        "cluster":   r[6],
        "anno_stat": bool(r[7] & 0x08),
        "src":       r[7] & 0x03,
        "asw":       r[8] | (r[9] << 8),
    }


def decode_asw(asw: int) -> str:
    f = [n for i, n in enumerate(ASW_NAMES) if asw & (1 << i)]
    return ",".join(f) if f else "—"


def scan_queue(radio: Si4689, k: int):
    """Liest bis zu k Einträge, sammelt UNTERSCHIEDLICHE Einträge.

    Rückgabe: (entries, found_src0). Bricht ab, wenn sich Einträge
    wiederholen (Queue zyklisch/erschöpft).
    """
    entries = []
    seen = set()
    found_src0 = False
    last_key = None
    for _ in range(k):
        info = read_announcement_info(radio)
        key = (info["src"], info["anno_stat"], info["cluster"], info["asw"])
        if key not in seen:
            seen.add(key)
            entries.append(info)
        if info["src"] == 0 and info["anno_stat"]:
            found_src0 = True
        if key == last_key:
            break
        last_key = key
    return entries, found_src0


def wait_for_lock(radio: Si4689, timeout: float, fic_min: int) -> dict:
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= fic_min:
            return last
        time.sleep(0.25)
    raise TimeoutError(f"Kein Lock nach {timeout:.0f}s.")


# ===========================================================================
def main() -> int:
    sp = load_service(DB_PATH, SWISS_POP_IDX)
    sf = load_service(DB_PATH, SRF1_IDX)
    swiss_sid = sp["sid"] if sp else FALLBACK_SWISS_SID
    swiss_cid = sp["cid"] if sp else FALLBACK_SWISS_CID
    srf1_sid  = sf["sid"] if sf else FALLBACK_SRF1_SID
    srf1_cid  = sf["cid"] if sf else FALLBACK_SRF1_CID
    srf1_ok   = bool(srf1_sid)
    log(f"Swiss Pop+ SID=0x{swiss_sid:X}/CID=0x{swiss_cid:X} | "
        f"SRF 1 SID=0x{srf1_sid:X}/CID=0x{srf1_cid:X}")

    radio = Si4689(verbose=True)
    cur_sid, cur_cid = swiss_sid, swiss_cid
    on_srf1 = False
    try:
        log("Init Si4689 (DAB) …")
        radio.open(); radio.reset(); radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.configure_i2s(master=False)
        radio.set_volume(NORMAL_VOLUME)
        radio.set_dab_freq_list()
        radio.dab_tune(TEST_CHANNEL)
        sig = wait_for_lock(radio, LOCK_TIMEOUT, FIC_MIN)
        log(f"Lock OK: FIC={sig['fic_quality']}% RSSI={sig['rssi']}")
        radio.dab_start_service(swiss_sid, swiss_cid)
        radio.set_property(PROP_DAB_ANNOUNCEMENT_ENABLE, ANNO_TYPES)
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, ANNO_INTEN_BIT)
        log(f"Swiss Pop+ läuft. Sekundentakt-Diagnose aktiv "
            f"(Strg-C beendet). Harte Bits = {decode_asw(HARD_BITS)}.")

        # ===================================================================
        while True:
            ev   = read_event_flags(radio)
            sig  = radio.get_dab_signal_strength()
            anno = ev["anno_active"]
            tag  = "SRF1" if on_srf1 else "POP "
            log(f"… FIC={sig['fic_quality']:>3}% RSSI={sig['rssi']:>3} "
                f"| ANNOINT={int(ev['annoint'])} ANNO={int(anno)} | {tag} 0x{cur_sid:X}")

            if anno:
                entries, found_src0 = scan_queue(radio, MAX_QUEUE_READ)
                ack_events(radio)
                hard_in_src0 = False
                for e in entries:
                    hard = e["asw"] & HARD_BITS
                    if e["src"] == 0 and e["anno_stat"] and hard:
                        hard_in_src0 = True
                    mark = f"  ★HARD[{decode_asw(hard)}]" if hard else ""
                    log(f"    SRC={e['src']}({SRC_NAMES.get(e['src'],'?')}) "
                        f"{'START' if e['anno_stat'] else 'STOP '} "
                        f"cl=0x{e['cluster']:02X} q={e['q_size']} "
                        f"ASW=0x{e['asw']:04X}[{decode_asw(e['asw'])}]{mark}")

                # Umschalt-Entscheidung
                relevant = found_src0 and (
                    SWITCH_ASW_FILTER == 0 or any(
                        (e["asw"] & SWITCH_ASW_FILTER)
                        for e in entries if e["src"] == 0 and e["anno_stat"]))
                if relevant and srf1_ok and not on_srf1:
                    radio.dab_stop_service(cur_sid, cur_cid)
                    radio.dab_start_service(srf1_sid, srf1_cid)
                    cur_sid, cur_cid = srf1_sid, srf1_cid
                    on_srf1 = True
                    if TA_VOLUME != NORMAL_VOLUME:
                        radio.set_volume(TA_VOLUME)
                    flag = " (mit HARD-Bit)" if hard_in_src0 else " (nur weiche Typen)"
                    log(f"  >> TA: umgeschaltet auf SRF 1{flag}.")

            if not anno and on_srf1:
                radio.dab_stop_service(cur_sid, cur_cid)
                radio.dab_start_service(swiss_sid, swiss_cid)
                cur_sid, cur_cid = swiss_sid, swiss_cid
                on_srf1 = False
                if TA_VOLUME != NORMAL_VOLUME:
                    radio.set_volume(NORMAL_VOLUME)
                log("  << TA-Ende: zurück auf Swiss Pop+.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log("Abbruch durch Benutzer.")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback; traceback.print_exc()
        return 1
    finally:
        try:
            if on_srf1:
                radio.dab_stop_service(cur_sid, cur_cid)
                radio.dab_start_service(swiss_sid, swiss_cid)
                radio.set_volume(NORMAL_VOLUME)
        except Exception:
            pass
        try:
            radio.close(); log("SPI geschlossen.")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())