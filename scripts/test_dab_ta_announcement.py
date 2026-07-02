#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dab_ta_announcement.py
===========================

Standalone-Testskript: DAB-Verkehrsdurchsagen (TA / Road Traffic Flash)
auf dem Si4689 erkennen und – sofern die Durchsage im AKTUELLEN Ensemble
liegt (SRC=0) – das Audio automatisch darauf umschalten und nach Ende
wieder zurück.

Testsender (Schweiz):
    Swiss Pop+   |   Ensemble: SRG SSR D01   |   Kanal: 12C (227.360 MHz)
    Si4689-idx 70 (in dab_scans.sqlite)

Zweck: vor der GUI-Integration validieren, was der lokale Multiplex bei
einer Verkehrsdurchsage tatsächlich signalisiert (SRC, ASW, ID1/ID2,
ANNO_STAT). Reines Polling im 3-Sekunden-Takt (INT-Leitung GPIO23 wird
NICHT genutzt).

Ausführen:
    sudo ~/my_venv_314/bin/python3 test_dab_ta_announcement.py

Beenden: Strg-C  (schaltet sauber auf Swiss Pop+ zurück und schliesst SPI)

Hinweis zu den ROH-Kommandos:
    DAB_GET_ANNOUNCEMENT_INFO (0xB6) und die ANNO-Bits aus
    DAB_GET_EVENT_STATUS (0xB3) sind im aktuellen si4689_driver.py noch
    nicht enthalten. Sie werden hier direkt über _write_command/_read_reply
    angesprochen – die Byte-Parser unten lassen sich 1:1 in den Treiber
    übernehmen.
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

TEST_CHANNEL   = "12C"     # Swiss Pop+ / SRG SSR D01
SWISS_POP_IDX  = 70        # si4689_idx in si4689_datenbank
EXPECTED_ENSEMBLE = "SRG SSR D01"

# Fallback, falls der DB-Lookup fehlschlägt: hier SID/CID von Swiss Pop+ eintragen.
# (Werte als reine Integer; 0 = "unbekannt, bitte aus DB lesen")
FALLBACK_SID = 0
FALLBACK_CID = 0

POLL_INTERVAL = 3.0        # Sekunden – reines Polling, wie gewünscht
LOCK_TIMEOUT  = 8.0        # max. Wartezeit auf FIC-Lock
FIC_MIN       = 90         # FIC-Qualität in %, bevor dab_start_service zulässig ist

# DAB_ANNOUNCEMENT_ENABLE (0xB700): nur Road Traffic Flash (Bit 1).
# Weitere Bits bei Bedarf dazu-ODERn:
#   Bit0=ALARM 0x001, Bit1=TRAFFIC 0x002, Bit2=TRANSPORT 0x004,
#   Bit3=WARNING 0x008, Bit4=NEWS 0x010, Bit5=WEATHER 0x020, ...
ANNO_TYPES = 0x0002

# Property-/Bit-Konstanten (AN649)
PROP_DAB_ANNOUNCEMENT_ENABLE = 0xB700
PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
ANNO_INTEN_BIT = 0x0010    # Bit 4 in DAB_EVENT_INTERRUPT_SOURCE

# ASW/ASU-Bitnamen (TS 101 756, Tabelle 14) – nur für lesbare Ausgabe
ASW_NAMES = [
    "ALARM", "TRAFFIC", "TRANSPORT", "WARNING", "NEWS", "WEATHER",
    "EVENT", "SPECIAL", "PROGRAM", "SPORT", "FINANCIAL",
]
SRC_NAMES = {0: "current ensemble", 1: "other ensemble", 2: "FM", 3: "reserved"}

# ---------------------------------------------------------------------------
# Treiber importieren
# ---------------------------------------------------------------------------
sys.path.insert(0, str(DRIVER_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402


# ===========================================================================
# Hilfsfunktionen
# ===========================================================================
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_service_from_db(db_path: Path, idx: int):
    """SID/CID/Name/Kanal von Swiss Pop+ aus der SQLite-DB lesen.

    Rückgabe: dict oder None.
    """
    if not db_path.exists():
        log(f"DB nicht gefunden: {db_path}")
        return None
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT name, channel, service_id, component_id "
            "FROM si4689_datenbank WHERE si4689_idx = ?",
            (idx,),
        ).fetchone()
        con.close()
    except Exception as exc:
        log(f"DB-Lesefehler: {exc}")
        return None
    if row is None:
        return None
    return {
        "name": row["name"],
        "channel": row["channel"],
        "sid": int(row["service_id"]),
        "cid": int(row["component_id"]),
    }


def read_event_flags(radio: Si4689) -> dict:
    """DAB_GET_EVENT_STATUS (0xB3) roh lesen, OHNE Quittierung.

    Wichtig: parst die ANNO-Bits, die der Treiber (noch) nicht liefert.
        RESP4 (reply[4]) Bit4 = ANNOINT      – sticky: ein Event liegt vor
        RESP5 (reply[5]) Bit4 = ANNO         – mind. eine Durchsage ist AKTIV
    """
    radio._write_command([0xB3, 0x00])      # EVENT_ACK = 0 -> nur lesen
    r = radio._read_reply(9)
    return {
        "annoint":     bool(r[4] & 0x10),
        "anno_active": bool(r[5] & 0x10),
        "srvlist":     bool(r[4] & 0x01),
    }


def ack_events(radio: Si4689) -> None:
    """DAB_GET_EVENT_STATUS (0xB3) mit EVENT_ACK=1 – sticky-Flags löschen."""
    radio._write_command([0xB3, 0x01])
    radio._read_reply(9)                    # Antwort verwerfen


def read_announcement_info(radio: Si4689) -> dict:
    """DAB_GET_ANNOUNCEMENT_INFO (0xB6) roh lesen und parsen.

    Byte-Layout (AN649, Cmd 0xB6 Response):
        reply[4]  RESP4  Bit0 = ANNO_Q_OVFL
        reply[5]  RESP5  Bits[4:0] = ANNO_Q_SIZE
        reply[6]  RESP6  CLUSTER_ID
        reply[7]  RESP7  Bit3=ANNO_STAT  Bit2=REGION_FLAG  Bits[1:0]=SRC
        reply[8]  RESP8  ASW[7:0]
        reply[9]  RESP9  ASW[15:8]
        reply[10] RESP10 ID1[7:0]
        reply[11] RESP11 ID1[15:8]
        reply[12] RESP12 ID2[7:0]
        reply[13] RESP13 ID2[15:8]
        reply[14] RESP14 REGIONID1
        reply[15] RESP15 REGIONID2
    """
    radio._write_command([0xB6])
    r = radio._read_reply(16)
    return {
        "q_ovfl":      bool(r[4] & 0x01),
        "q_size":      r[5] & 0x1F,
        "cluster_id":  r[6],
        "anno_stat":   bool(r[7] & 0x08),   # True = gestartet, False = gestoppt
        "region_flag": bool(r[7] & 0x04),
        "src":         r[7] & 0x03,
        "asw":         r[8] | (r[9] << 8),
        "id1":         r[10] | (r[11] << 8),
        "id2":         r[12] | (r[13] << 8),
        "regionid1":   r[14],
        "regionid2":   r[15],
    }


def decode_asw(asw: int) -> str:
    flags = [name for i, name in enumerate(ASW_NAMES) if asw & (1 << i)]
    return ", ".join(flags) if flags else "—"


def wait_for_lock(radio: Si4689, timeout: float, fic_min: int) -> dict:
    """Pending STCINT quittieren und auf FIC-Lock warten.

    Gibt das letzte Status-Dict zurück. Wirft TimeoutError bei Fehlschlag.
    """
    radio.dab_digrad_status(stc_ack=True)   # pending STCINT zuerst quittieren
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= fic_min:
            return last
        time.sleep(0.25)
    raise TimeoutError(
        f"Kein Lock nach {timeout:.0f}s "
        f"(acq={last.get('acq')}, FIC={last.get('fic_quality')}%)."
    )


# ===========================================================================
# Hauptablauf
# ===========================================================================
def main() -> int:
    # --- Swiss Pop+ SID/CID bestimmen ---
    svc = load_service_from_db(DB_PATH, SWISS_POP_IDX)
    if svc:
        swiss_sid, swiss_cid = svc["sid"], svc["cid"]
        log(f"DB: idx {SWISS_POP_IDX} = {svc['name']} "
            f"(Kanal {svc['channel']}, SID=0x{swiss_sid:X}, CID=0x{swiss_cid:X})")
    elif FALLBACK_SID:
        swiss_sid, swiss_cid = FALLBACK_SID, FALLBACK_CID
        log(f"DB-Lookup leer – verwende Fallback "
            f"SID=0x{swiss_sid:X}, CID=0x{swiss_cid:X}")
    else:
        log("FEHLER: Swiss Pop+ weder in DB noch als Fallback gefunden. "
            "Bitte FALLBACK_SID/FALLBACK_CID setzen.")
        return 1

    radio = Si4689(verbose=True)
    cur_sid, cur_cid = swiss_sid, swiss_cid
    switched = False

    try:
        # --- Initialisierung (DAB) ---
        log("Initialisiere Si4689 (DAB) …")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()      # setzt u.a. EVENT_INTERRUPT_SOURCE = 0
        radio.configure_i2s(master=False)   # I2S-Slave (Audio -> Pi -> ALSA)
        radio.set_volume(50)

        # --- Tunen ---
        radio.set_dab_freq_list()
        log(f"Tune auf Kanal {TEST_CHANNEL} …")
        radio.dab_tune(TEST_CHANNEL)

        sig = wait_for_lock(radio, LOCK_TIMEOUT, FIC_MIN)
        log(f"Lock OK: RSSI={sig['rssi']} SNR={sig['snr']} "
            f"FIC={sig['fic_quality']}% CNR={sig['cnr']}")

        ens = radio.dab_get_ensemble_info()
        log(f"Ensemble: '{ens['label']}' (EID=0x{ens['eid']:04X})")
        if EXPECTED_ENSEMBLE not in ens["label"]:
            log(f"WARNUNG: erwartet '{EXPECTED_ENSEMBLE}', empfangen "
                f"'{ens['label']}' – ggf. falscher Kanal/Standort.")

        # --- Swiss Pop+ starten ---
        radio.dab_start_service(swiss_sid, swiss_cid)
        log("Swiss Pop+ läuft.")

        # --- Announcement-Empfang aktivieren ---
        radio.set_property(PROP_DAB_ANNOUNCEMENT_ENABLE, ANNO_TYPES)
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, ANNO_INTEN_BIT)
        log(f"Announcements aktiviert: ENABLE=0x{ANNO_TYPES:04X} "
            f"(nur TRAFFIC), ANNO_INTEN gesetzt.")
        log(f"Polling alle {POLL_INTERVAL:.0f}s – warte auf Verkehrsdurchsage "
            f"(Strg-C zum Beenden) …")

        # ===================================================================
        # Poll-Schleife
        # ===================================================================
        while True:
            ev  = read_event_flags(radio)
            sig = radio.get_dab_signal_strength()
            tag = "TA-AKTIV" if ev["anno_active"] else "—"
            log(f"… RSSI={sig['rssi']:>4} FIC={sig['fic_quality']:>3}% "
                f"| ANNOINT={int(ev['annoint'])} ANNO={int(ev['anno_active'])} "
                f"[{tag}] | playing SID=0x{cur_sid:X}")

            # --- Neues Announcement-Event ausgewertet ---
            if ev["annoint"] or ev["anno_active"]:
                info = read_announcement_info(radio)
                is_traffic = bool(info["asw"] & 0x0002)
                log(f"  Announcement: stat={'START' if info['anno_stat'] else 'STOP'} "
                    f"SRC={info['src']}({SRC_NAMES.get(info['src'],'?')}) "
                    f"cluster=0x{info['cluster_id']:02X} "
                    f"ASW=0x{info['asw']:04X} [{decode_asw(info['asw'])}] "
                    f"ID1=0x{info['id1']:04X} ID2=0x{info['id2']:04X} "
                    f"q_size={info['q_size']} ovfl={int(info['q_ovfl'])}")

                # --- Umschalt-Logik: nur Verkehr, nur Start, nur nicht-umgeschaltet ---
                if info["anno_stat"] and is_traffic and not switched:
                    if info["src"] == 0:
                        # Current ensemble: ID1=Service-ID, ID2=Component-ID
                        t_sid, t_cid = info["id1"], info["id2"]
                        if (t_sid, t_cid) != (cur_sid, cur_cid):
                            radio.dab_stop_service(cur_sid, cur_cid)
                            radio.dab_start_service(t_sid, t_cid)
                            cur_sid, cur_cid = t_sid, t_cid
                            switched = True
                            log(f"  >> UMGESCHALTET auf Verkehrsdurchsage "
                                f"(SID=0x{t_sid:X}, CID=0x{t_cid:X}).")
                        else:
                            log("  >> Durchsage läuft auf dem aktuellen Dienst – "
                                "kein Umschalten nötig.")
                    elif info["src"] == 1:
                        log(f"  >> TA in ANDEREM Ensemble (EID=0x{info['id1']:04X}, "
                            f"cluster=0x{info['id2']:04X}). DAB-Retune nötig – "
                            "in diesem Test NICHT durchgeführt (nur protokolliert).")
                    elif info["src"] == 2:
                        log("  >> TA via FM – per Vorgabe ausgeschlossen, übersprungen.")

                ack_events(radio)   # sticky ANNOINT löschen

            # --- Sicherheitsnetz: keine Durchsage mehr aktiv -> zurückschalten ---
            if switched and not ev["anno_active"]:
                radio.dab_stop_service(cur_sid, cur_cid)
                radio.dab_start_service(swiss_sid, swiss_cid)
                cur_sid, cur_cid = swiss_sid, swiss_cid
                switched = False
                log("  << Durchsage beendet – zurück auf Swiss Pop+.")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log("Abbruch durch Benutzer.")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Falls noch auf einer Durchsage: zurück auf Swiss Pop+ (best effort)
        try:
            if switched:
                radio.dab_stop_service(cur_sid, cur_cid)
                radio.dab_start_service(swiss_sid, swiss_cid)
                log("Cleanup: zurück auf Swiss Pop+.")
        except Exception:
            pass
        try:
            radio.close()   # close() ruft KEIN GPIO.cleanup() auf
            log("SPI geschlossen.")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())