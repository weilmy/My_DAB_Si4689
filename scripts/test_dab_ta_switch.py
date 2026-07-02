#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dab_ta_switch.py   (Variante A)
====================================

Standalone-Test: Verkehrsdurchsage (TA) auf dem Si4689 erkennen und das
Audio innerhalb des Ensembles auf den Announcement-Service umschalten –
analog zum Verhalten des Keystone T5A, der bei TA selbst auf "Radio SRF 1"
schaltet.

Sender (beide auf Kanal 12C / Ensemble SRG SSR D01):
    Swiss Pop+        si4689_idx 70   (Musik / Normalbetrieb)
    SRF 1 BE FR VS+   si4689_idx 63   (Announcement-Service / TA-Ziel)

Designentscheidung (Variante A):
  * Trigger ist das ANNO-Flag (Event-Status RESP5 Bit4) – sauber und stabil.
  * Qualifiziert durch Queue-Prüfung auf SRC=0 (aktuelles Ensemble), damit
    FM-/Fremd-Ensemble-Durchsagen NICHT zum Umschalten führen.
  * Bei TA: stop Swiss Pop+, start SRF 1 (+ optional Lautstärke-Boost).
    Bei TA-Ende (ANNO=0): zurück auf Swiss Pop+ und Lautstärke.
  * Reiner Service-Wechsel im selben Ensemble – kein Retune.

Ausführen:
    sudo ~/my_venv_314/bin/python3 test_dab_ta_switch.py

Beenden: Strg-C (schaltet zurück auf Swiss Pop+ und schliesst SPI).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
DRIVER_DIR = Path("/home/weilmy/My_DAB_Si4689/hardware")
DB_PATH    = Path("/home/weilmy/My_DAB_Si4689/assets/DB/dab_scans.sqlite")

TEST_CHANNEL      = "12C"          # SRG SSR D01
SWISS_POP_IDX     = 70             # Normalbetrieb
SRF1_IDX          = 63             # TA-Ziel (SRF 1 BE FR VS+)
EXPECTED_ENSEMBLE = "SRG SSR D01"

# Fallback-SID/CID, falls DB-Lookup fehlschlägt (0 = ungenutzt)
FALLBACK_SWISS_SID, FALLBACK_SWISS_CID = 0x42F1, 0x10
FALLBACK_SRF1_SID,  FALLBACK_SRF1_CID  = 0, 0

POLL_INTERVAL  = 3.0               # Sekunden
LOCK_TIMEOUT   = 8.0
FIC_MIN        = 90

NORMAL_VOLUME  = 48                # 0..63
TA_VOLUME      = 60                # Boost während TA; = NORMAL_VOLUME -> kein Boost
MAX_QUEUE_READ = 8                 # max. 0xB6-Lesungen pro Poll (Queue-Scan)

# DAB_ANNOUNCEMENT_ENABLE (0xB700): hier ALLE Typen, da SRG kein TRAFFIC-Bit
# setzt (siehe Messung). 0x07FF = alle 11 Typen.
ANNO_TYPES = 0x07FF
PROP_DAB_ANNOUNCEMENT_ENABLE    = 0xB700
PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
ANNO_INTEN_BIT = 0x0010

ASW_NAMES = ["ALARM", "TRAFFIC", "TRANSPORT", "WARNING", "NEWS", "WEATHER",
             "EVENT", "SPECIAL", "PROGRAM", "SPORT", "FINANCIAL"]
SRC_NAMES = {0: "current ensemble", 1: "other ensemble", 2: "FM", 3: "reserved"}

sys.path.insert(0, str(DRIVER_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ===========================================================================
# DB + Roh-Kommandos
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
    return {"name": row["name"], "channel": row["channel"],
            "sid": int(row["service_id"]), "cid": int(row["component_id"])}


def read_event_flags(radio: Si4689) -> dict:
    """DAB_GET_EVENT_STATUS (0xB3) ohne Quittierung; liefert ANNO-Bits."""
    radio._write_command([0xB3, 0x00])
    r = radio._read_reply(9)
    return {"annoint": bool(r[4] & 0x10), "anno_active": bool(r[5] & 0x10)}


def ack_events(radio: Si4689) -> None:
    radio._write_command([0xB3, 0x01])
    radio._read_reply(9)


def read_announcement_info(radio: Si4689) -> dict:
    """DAB_GET_ANNOUNCEMENT_INFO (0xB6), Offsets verifiziert gegen AN649."""
    radio._write_command([0xB6])
    r = radio._read_reply(16)
    return {
        "q_size":    r[5] & 0x1F,
        "cluster":   r[6],
        "anno_stat": bool(r[7] & 0x08),
        "src":       r[7] & 0x03,
        "asw":       r[8] | (r[9] << 8),
        "id1":       r[10] | (r[11] << 8),
        "id2":       r[12] | (r[13] << 8),
    }


def decode_asw(asw: int) -> str:
    f = [n for i, n in enumerate(ASW_NAMES) if asw & (1 << i)]
    return ", ".join(f) if f else "—"


def probe_current_ensemble(radio: Si4689, k: int):
    """Liest bis zu k Queue-Einträge und sucht eine aktive Durchsage im
    AKTUELLEN Ensemble (SRC=0, ANNO_STAT=1).

    Rückgabe: (found_src0, sample_info, n_read). Bricht ab, sobald ein
    SRC=0-Eintrag gefunden wird oder sich Einträge wiederholen.
    """
    found = False
    sample = None
    last_key = None
    n = 0
    for _ in range(k):
        info = read_announcement_info(radio)
        n += 1
        if sample is None:
            sample = info
        if info["src"] == 0 and info["anno_stat"]:
            return True, info, n
        key = (info["src"], info["anno_stat"], info["cluster"], info["asw"])
        if key == last_key:           # Queue erschöpft/zyklisch
            break
        last_key = key
    return found, sample, n


def wait_for_lock(radio: Si4689, timeout: float, fic_min: int) -> dict:
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= fic_min:
            return last
        time.sleep(0.25)
    raise TimeoutError(f"Kein Lock nach {timeout:.0f}s "
                       f"(acq={last.get('acq')}, FIC={last.get('fic_quality')}%).")


# ===========================================================================
# Hauptablauf
# ===========================================================================
def main() -> int:
    # --- Senderdaten laden ---
    sp = load_service(DB_PATH, SWISS_POP_IDX)
    sf = load_service(DB_PATH, SRF1_IDX)

    swiss_sid = sp["sid"] if sp else FALLBACK_SWISS_SID
    swiss_cid = sp["cid"] if sp else FALLBACK_SWISS_CID
    srf1_sid  = sf["sid"] if sf else FALLBACK_SRF1_SID
    srf1_cid  = sf["cid"] if sf else FALLBACK_SRF1_CID

    if not swiss_sid:
        log("FEHLER: Swiss Pop+ nicht auffindbar (DB + Fallback leer).")
        return 1
    srf1_ok = bool(srf1_sid)
    log(f"Swiss Pop+: SID=0x{swiss_sid:X} CID=0x{swiss_cid:X}"
        f"{'  ('+sp['name']+')' if sp else '  (Fallback)'}")
    if srf1_ok:
        log(f"SRF 1     : SID=0x{srf1_sid:X} CID=0x{srf1_cid:X}"
            f"{'  ('+sf['name']+')' if sf else '  (Fallback)'}")
    else:
        log("WARNUNG: SRF 1 nicht auffindbar – es wird NICHT umgeschaltet, "
            "nur protokolliert.")

    radio = Si4689(verbose=True)
    cur_sid, cur_cid = swiss_sid, swiss_cid
    on_srf1 = False

    try:
        log("Initialisiere Si4689 (DAB) …")
        radio.open(); radio.reset(); radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.configure_i2s(master=False)
        radio.set_volume(NORMAL_VOLUME)

        radio.set_dab_freq_list()
        log(f"Tune auf Kanal {TEST_CHANNEL} …")
        radio.dab_tune(TEST_CHANNEL)
        sig = wait_for_lock(radio, LOCK_TIMEOUT, FIC_MIN)
        log(f"Lock OK: RSSI={sig['rssi']} FIC={sig['fic_quality']}% CNR={sig['cnr']}")

        radio.dab_start_service(swiss_sid, swiss_cid)
        log("Swiss Pop+ läuft.")

        radio.set_property(PROP_DAB_ANNOUNCEMENT_ENABLE, ANNO_TYPES)
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, ANNO_INTEN_BIT)
        log(f"Announcements aktiviert (ENABLE=0x{ANNO_TYPES:04X}). "
            f"Polling alle {POLL_INTERVAL:.0f}s – warte auf TA (Strg-C beendet) …")

        # ===================================================================
        while True:
            ev  = read_event_flags(radio)
            sig = radio.get_dab_signal_strength()
            anno = ev["anno_active"]
            tag  = "SRF1" if on_srf1 else "POP"
            log(f"… FIC={sig['fic_quality']:>3}% RSSI={sig['rssi']:>3} "
                f"| ANNO={int(anno)} | spielt={tag} SID=0x{cur_sid:X}")

            if anno:
                found_src0, sample, nread = probe_current_ensemble(radio, MAX_QUEUE_READ)
                ack_events(radio)
                if sample:
                    log(f"  Queue({nread}): SRC={sample['src']}"
                        f"({SRC_NAMES.get(sample['src'],'?')}) "
                        f"stat={'START' if sample['anno_stat'] else 'STOP'} "
                        f"cluster=0x{sample['cluster']:02X} "
                        f"ASW=0x{sample['asw']:04X}[{decode_asw(sample['asw'])}] "
                        f"q_size={sample['q_size']}")

                if found_src0 and srf1_ok and not on_srf1:
                    radio.dab_stop_service(cur_sid, cur_cid)
                    radio.dab_start_service(srf1_sid, srf1_cid)
                    cur_sid, cur_cid = srf1_sid, srf1_cid
                    on_srf1 = True
                    if TA_VOLUME != NORMAL_VOLUME:
                        radio.set_volume(TA_VOLUME)
                    log(f"  >> TA: umgeschaltet auf SRF 1 (SID=0x{srf1_sid:X}), "
                        f"Lautstärke={TA_VOLUME}.")
                elif found_src0 and not srf1_ok:
                    log("  >> TA im aktuellen Ensemble erkannt, aber SRF 1 "
                        "unbekannt – nicht umgeschaltet.")

            # --- TA-Ende: zurück auf Swiss Pop+ ---
            if not anno and on_srf1:
                radio.dab_stop_service(cur_sid, cur_cid)
                radio.dab_start_service(swiss_sid, swiss_cid)
                cur_sid, cur_cid = swiss_sid, swiss_cid
                on_srf1 = False
                if TA_VOLUME != NORMAL_VOLUME:
                    radio.set_volume(NORMAL_VOLUME)
                log(f"  << TA-Ende: zurück auf Swiss Pop+, Lautstärke={NORMAL_VOLUME}.")

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
                log("Cleanup: zurück auf Swiss Pop+.")
        except Exception:
            pass
        try:
            radio.close(); log("SPI geschlossen.")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())