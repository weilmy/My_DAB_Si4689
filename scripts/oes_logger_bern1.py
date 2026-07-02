#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oes_logger_bern1.py  –  Standalone-Langzeit-Logger für den OES-Fall (c)
=======================================================================

Zweck
-----
BERN1 (Kanal 8B) hat drei TA-Varianten:
  (a) Sprecher liest ohne ANNO        -> technisch nicht erkennbar, ignorieren
  (b) redaktionelle TA, in-band       -> ANNO + 0xB6 mit Ziel = BERN1, kein Umschalten
  (c) wichtige Durchsage (OES)        -> ANNO + 0xB6 mit Ziel = ENERGY (anderes
                                         Ensemble, 7D). DAS ist der ungetestete Fall.

Dieses Skript schaltet NICHTS um und schreibt NICHTS in die DB. Es tunt fest auf
BERN1 (8B), hört mit (Audio bleibt an), und protokolliert bei jeder ANNO-Flanke
das vollständige 0xB6 (DAB_GET_ANNOUNCEMENT_INFO) mit Zeitstempel.

Robustheit (v2): Ein einzelner SPI-/Kommandofehler beendet den Logger NIE mehr.
0xB6 wird einmal an der Flanke gelesen plus wenige begrenzte Nachproben – nicht
mehr bei jedem Poll (das provozierte einen ERR, sobald eine kurze Durchsage
zwischen zwei Polls endete).

Heartbeat-reduziert: Routine-Polls (ANNO=0) sind still; ausgegeben werden nur
ANNO-Start/-Stopp, 0xB6-Inhalte/-Änderungen und alle paar Minuten ein Lebenszeichen
mit Poll-Zähler und aktuellem ANNO-Pegel (= bestätigte Stille).

Start (im Projektordner /home/weilmy/My_DAB_Si4689/):
    sudo ~/my_venv_314/bin/python3 oes_logger_bern1.py
    sudo ~/my_venv_314/bin/python3 oes_logger_bern1.py "BERN1"   # Name optional

Am besten in tmux laufen lassen, damit er das Abmelden überlebt.
Beenden mit Strg+C – sauberer Shutdown über Manager.close().
"""

import os
import sys
import time
import signal
import sqlite3
import datetime
from typing import Optional

# --- Projektwurzel in den Importpfad (Skript liegt im Projektordner) --------
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hardware.si4689_init import Si4689Manager   # noqa: E402

# ===========================================================================
# Konfiguration
# ===========================================================================
STATION_NAME   = sys.argv[1] if len(sys.argv) > 1 else "BERN1"  # Name in der DB
DB_REL         = "assets/DB/dab_scans.sqlite"
LOGFILE        = os.path.join(ROOT, "oes_bern1_log.txt")
POLL_INTERVAL  = 0.5     # s – feiner als 1 Hz, um kurze Durchsagen nicht zu verpassen
HEARTBEAT_MIN  = 10      # alle N Minuten ein Lebenszeichen ins Terminal
RESAMPLE_COUNT = 6       # Anzahl Nachproben nach der Rising-Edge (6 × 0,5 s ≈ 3 s)
ASW_TRAFFIC    = 0x0002  # ASW-Bit b1 = Road Traffic flash (TS 101 756, Tab. 14)

# ===========================================================================
# Logging
# ===========================================================================
def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

_logf = open(LOGFILE, "a", encoding="utf-8", buffering=1)  # line-buffered

def log(msg: str, to_term: bool = True) -> None:
    line = f"[{_ts()}] {msg}"
    _logf.write(line + "\n")
    if to_term:
        print(line, flush=True)

# ===========================================================================
# BERN1-Tune-Parameter aus der DB lesen (NUR lesen)
# ===========================================================================
def load_station(name: str) -> dict:
    db = DB_REL if os.path.isabs(DB_REL) else os.path.join(ROOT, DB_REL)
    if not os.path.exists(db):
        log(f"FEHLER: DB nicht gefunden: {db}  – zuerst einen Scan durchführen.")
        sys.exit(1)
    con = sqlite3.connect(db, timeout=5)
    try:
        row = con.execute(
            "SELECT name, channel, freq_index, service_id, component_id "
            "FROM si4689_datenbank WHERE name LIKE ? "
            "ORDER BY si4689_idx LIMIT 1;",
            (f"%{name}%",),
        ).fetchone()
    finally:
        con.close()
    if not row:
        log(f"FEHLER: '{name}' nicht in si4689_datenbank gefunden.")
        sys.exit(1)
    return {"name": row[0], "channel": row[1], "freq_index": row[2],
            "sid": row[3], "cid": row[4]}

# ===========================================================================
# 0xB6  DAB_GET_ANNOUNCEMENT_INFO  – roh lesen + parsen, FEHLERTOLERANT
# ===========================================================================
def read_b6(radio) -> Optional[dict]:
    """Liest 0xB6 einmal. Gibt bei JEDEM Fehler (z.B. ERR, weil die Durchsage
    gerade endete) None zurück, statt zu werfen – der Logger läuft dann weiter."""
    try:
        radio._write_command([0xB6, 0x00])
        r = radio._read_reply(16)
    except Exception:
        return None     # z.B. ERR 0xC0, weil keine Durchsage mehr aktiv – Aufrufer entscheidet
    src       = r[7] & 0x03
    anno_stat = (r[7] >> 3) & 0x01
    asw       = r[8]  | (r[9]  << 8)
    id1       = r[10] | (r[11] << 8)     # Ziel-SID (Träger)
    id2       = r[12] | (r[13] << 8)     # Ziel-CID / Subch
    raw       = " ".join(f"{b:02X}" for b in r)
    return {"src": src, "anno_stat": anno_stat, "asw": asw,
            "id1": id1, "id2": id2, "raw": raw}

def classify(info: dict, bern1_sid: int) -> str:
    if not (info["asw"] & ASW_TRAFFIC):
        return "kein Road-Traffic-Bit gesetzt (anderer Announcement-Typ)"
    # src: 0 = lokal (eigenes Ensemble), 1 = OE (anderes Ensemble)
    if info["src"] == 0 and (info["id1"] in (0, bern1_sid)):
        return "(b) IN-BAND – Ziel = BERN1, KEIN Umschalten nötig"
    return (f"(c) OES !!  src={info['src']}  Ziel-SID=0x{info['id1']:04X}  "
            f"≠ BERN1 → Cross-Channel-Umschalten (z.B. ENERGY/7D) nötig")

def log_b6(info: dict, bern1_sid: int, prefix: str = "0xB6") -> None:
    log(f"{prefix}  src={info['src']}  anno_stat={info['anno_stat']}  "
        f"ASW=0x{info['asw']:04X}  ID1=0x{info['id1']:04X}  ID2=0x{info['id2']:04X}")
    log(f"      RAW16: {info['raw']}")
    log(f"   → {classify(info, bern1_sid)}")

# ===========================================================================
# Hauptprogramm
# ===========================================================================
def main() -> None:
    log("=" * 72)
    log("OES-Logger v2 – Fall (c) auf BERN1 untersuchen")

    st = load_station(STATION_NAME)
    log(f"Station: {st['name']}  Kanal={st['channel']}  "
        f"SID=0x{st['sid']:04X}  CID=0x{st['cid']:X}  freq_index={st['freq_index']}")

    # --- Chip hochfahren (Firmware laden, Frontend konfigurieren) -----------
    si = Si4689Manager(verbose=False)
    if not si.initialize():
        log("FEHLER: Si4689Manager.initialize() fehlgeschlagen. Abbruch.")
        sys.exit(1)
    radio = si._radio

    try:
        # --- Tunen + Service starten (bis zu 2 Versuche wie in der App) -----
        ok = False
        for attempt in range(2):
            si.dab_tune(st["channel"])
            ok = si.dab_start_service(st["sid"], st["cid"])
            if ok:
                break
            log(f"FIC-Timeout, Re-Tune {st['channel']} … (Versuch {attempt + 2})")
        if not ok:
            log("FEHLER: dab_start_service fehlgeschlagen (FIC?). Abbruch.")
            return
        si.amp_enable(True)
        log(f"Auf {st['name']} ({st['channel']}) – Audio aktiv.")

        if si.enable_announcements():
            log("enable_announcements() OK – warte auf Durchsagen …")
        else:
            log("WARNUNG: enable_announcements() meldete keinen Erfolg.")

        # --- Poll-Schleife --------------------------------------------------
        bern1_sid     = st["sid"]
        prev_anno     = False
        last_sig      = None
        resample_left = 0           # >0 = nach Rising-Edge noch nachproben
        poll_count    = 0
        last_beat     = time.monotonic()
        running       = {"go": True}

        def _stop(*_):
            running["go"] = False
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        while running["go"]:
            # Gesamter Schleifenkörper fehlertolerant: nichts darf den Logger killen.
            try:
                try:
                    evt  = radio.dab_get_event_status(ack=True)
                    anno = bool(evt.get("anno"))
                except Exception as e:
                    log(f"event_status Fehler: {e}")
                    time.sleep(POLL_INTERVAL)
                    continue

                rising  = anno and not prev_anno
                falling = (not anno) and prev_anno

                if rising:
                    log("──────────── ANNO START ────────────")
                    info = read_b6(radio)
                    if info is not None:
                        last_sig = (info["src"], info["asw"], info["id1"], info["id2"])
                        log_b6(info, bern1_sid, prefix="0xB6")
                    else:
                        log("   0xB6 an der Flanke nicht lesbar (ungewöhnlich).")
                    resample_left = RESAMPLE_COUNT     # OES-Ziel evtl. verzögert

                elif anno and resample_left > 0:
                    # Begrenzte Nachproben für ein evtl. verzögertes OES-Ziel.
                    # Sobald 0xB6 nicht mehr lesbar ist (Durchsage-Inhalt weg),
                    # einmal sauber vermerken und abbrechen – keine Wiederholzeilen.
                    resample_left -= 1
                    info = read_b6(radio)
                    if info is None:
                        log("   Nachproben beendet – 0xB6 nicht mehr verfügbar "
                            "(Durchsage-Inhalt weg, ok).")
                        resample_left = 0
                    else:
                        sig = (info["src"], info["asw"], info["id1"], info["id2"])
                        if sig != last_sig:
                            last_sig = sig
                            log_b6(info, bern1_sid, prefix="0xB6 ÄNDERUNG")

                if falling:
                    log("──────────── ANNO STOPP ────────────")
                    last_sig      = None
                    resample_left = 0

                prev_anno = anno
                poll_count += 1

                now = time.monotonic()
                if (now - last_beat) >= HEARTBEAT_MIN * 60:
                    last_beat = now
                    log(f"… läuft – {poll_count} Polls/{HEARTBEAT_MIN}min, "
                        f"anno aktuell={int(anno)}, keine OES-Durchsage.")
                    poll_count = 0

            except Exception as e:
                # Auffangnetz für alles Unerwartete – weiterlaufen statt sterben.
                log(f"Schleifenfehler (ignoriert, weiter): {e}")

            time.sleep(POLL_INTERVAL)

    finally:
        log("Beende – sauberer Shutdown (Manager.close).")
        try:
            si.close()
        except Exception as e:
            log(f"close() Fehler: {e}")
        _logf.close()


if __name__ == "__main__":
    main()