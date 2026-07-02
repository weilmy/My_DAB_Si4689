#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dab_ta_verify.py   (Weg 2 / Variante B + robuster Service-Wechsel)
======================================================================

Variante B: Bei ANNO-Flanke hörbar auf SRF 1 schalten (normale Lautstärke);
sobald die DLS von SRF 1 eine Durchsage bestätigt -> Lautstärke-Boost.
Bei ANNO=0 zurück auf Swiss Pop+. Kein Muten, kein Songtitel-Abbruch
(der Vorlauf einer echten TA enthält oft einen Song).

NEU – robuster Service-Wechsel gegen den sporadischen 0xC1:
    switch_service() macht denselben Ablauf wie main.py:
      1. dab_stop_service(aktuell)
      2. kurze Pause
      3. dab_digrad_status(stc_ack=True)   – pending STCINT quittieren
      4. auf FIC-Qualität >= FIC_MIN warten
      5. dab_start_service(ziel) mit Retry bei 0xC1
    Schlägt der Start fehl, wird der Vordienst wiederhergestellt.
    Alle Wechsel sind exception-sicher (Rückgabe True/False, kein Crash).

Die Hauptschleife fängt zusätzlich jede unerwartete Exception pro Poll ab
und läuft weiter – ein Radio darf an einem Wackler nicht sterben.

Ausführen:
    sudo ~/my_venv_314/bin/python3 test_dab_ta_verify.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

DRIVER_DIR = Path("/home/weilmy/My_DAB_Si4689/hardware")
DB_PATH    = Path("/home/weilmy/My_DAB_Si4689/assets/DB/dab_scans.sqlite")

TEST_CHANNEL  = "12C"
SWISS_POP_IDX = 70
SRF1_IDX      = 63
FALLBACK_SWISS_SID, FALLBACK_SWISS_CID = 0x42F1, 0x10
FALLBACK_SRF1_SID,  FALLBACK_SRF1_CID  = 0x44B1, 0x02

POLL_INTERVAL  = 1.0
HEARTBEAT_EVERY = 300.0        # Sek.: Lebenszeichen im Ruhezustand (sonst still)
VERIFY_TIMEOUT = 12.0          # max. Wartezeit auf Marker, sonst Fehlalarm
LOCK_TIMEOUT   = 8.0
FIC_MIN        = 90
NORMAL_VOLUME  = 48
TA_VOLUME      = 60

# Robuster Service-Wechsel
SWITCH_SETTLE  = 0.15          # Pause nach dem Stop
SWITCH_ATTEMPTS = 3            # Start-Versuche bei 0xC1
SWITCH_RETRY_WAIT = 0.4        # Pause zwischen den Versuchen
SWITCH_FIC_TIMEOUT = 5.0       # Warten auf FIC (im selben Ensemble schnell)

ANNO_TYPES = 0x07FF
PROP_DAB_ANNOUNCEMENT_ENABLE    = 0xB700
PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
ANNO_INTEN_BIT = 0x0010

TA_MARKERS = ["srf 1", "srf1.ch", "studio@srf1", "regionaljournal"]
CONTENT_MARKERS = [
    "verkehrshinweis", "verkehrsmeldung", "verkehrsfunk", "verkehrslage",
    "stau auf", "unfall auf", "vollsperrung", "rückstau",
    "info trafic", "circulation",
    "informazione traffico", "ingorgo",
]

sys.path.insert(0, str(DRIVER_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def has_ta_markers(dls: str) -> bool:
    if not dls:
        return False
    low = dls.casefold()
    return (any(m in low for m in CONTENT_MARKERS)
            or any(m in low for m in TA_MARKERS))


def looks_like_song_title(text: str) -> bool:
    if not text:
        return False
    s = text.strip()
    parts = s.split(" - ")
    if len(parts) != 2:
        return False
    a, t = parts[0].strip(), parts[1].strip()
    if not (2 <= len(a) <= 80 and 2 <= len(t) <= 80):
        return False
    if "@" in s or "www." in s.lower() or ".ch" in s.lower():
        return False
    return True


def load_service(idx: int):
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT name, service_id, component_id "
            "FROM si4689_datenbank WHERE si4689_idx = ?", (idx,)).fetchone()
        con.close()
    except Exception as exc:
        log(f"DB-Fehler (idx {idx}): {exc}")
        return None
    if row is None:
        return None
    return {"name": row["name"], "sid": int(row["service_id"]),
            "cid": int(row["component_id"])}


def read_anno(radio: Si4689) -> bool:
    radio._write_command([0xB3, 0x00])
    r = radio._read_reply(9)
    return bool(r[5] & 0x10)


def flush_dls(radio: Si4689, settle_s: float = 0.3,
              passes: int = 2, max_iter: int = 15) -> None:
    time.sleep(settle_s)
    for _ in range(passes):
        drained = 0
        for _ in range(max_iter):
            try:
                st = radio.get_digital_service_data(status_only=True, ack=False)
                if not st.get("packet_ready") and not st.get("buffer_count"):
                    break
                radio.get_digital_service_data(status_only=False, ack=True)
                drained += 1
            except Exception:
                break
        if drained == 0:
            break
        time.sleep(0.1)


def wait_for_lock(radio: Si4689) -> dict:
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + LOCK_TIMEOUT
    last = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= FIC_MIN:
            return last
        time.sleep(0.25)
    raise TimeoutError("Kein Lock.")


def _is_0xc1(exc: Exception) -> bool:
    return "0xc1" in str(exc).lower()


# ===========================================================================
def main() -> int:
    sp = load_service(SWISS_POP_IDX)
    sf = load_service(SRF1_IDX)
    swiss = (sp["sid"] if sp else FALLBACK_SWISS_SID,
             sp["cid"] if sp else FALLBACK_SWISS_CID)
    srf1  = (sf["sid"] if sf else FALLBACK_SRF1_SID,
             sf["cid"] if sf else FALLBACK_SRF1_CID)
    log(f"Swiss Pop+ SID=0x{swiss[0]:X}/CID=0x{swiss[1]:X} | "
        f"SRF 1 SID=0x{srf1[0]:X}/CID=0x{srf1[1]:X}")

    radio = Si4689(verbose=True)
    cur = swiss
    mode = "POP"                 # POP | SRF1_WAIT | SRF1_TA
    suppressed = False
    prev_anno = False
    wait_start = 0.0
    last_hb = None             # (anno, mode, suppressed) der letzten Heartbeat-Zeile
    last_hb_time = 0.0

    # --- Hilfen, die self/radio/cur kapseln ---------------------------------
    def _wait_fic(timeout: float) -> None:
        deadline = time.monotonic() + timeout
        try:
            radio.dab_digrad_status(stc_ack=True)      # STCINT quittieren
        except Exception:
            pass
        while time.monotonic() < deadline:
            try:
                sig = radio.get_dab_signal_strength()
                if sig.get("acq") and sig.get("fic_quality", 0) >= FIC_MIN:
                    return
            except Exception:
                pass
            time.sleep(0.1)

    def _try_start(target, label) -> bool:
        for i in range(1, SWITCH_ATTEMPTS + 1):
            try:
                radio.dab_start_service(target[0], target[1])
                return True
            except Exception as exc:
                if _is_0xc1(exc) and i < SWITCH_ATTEMPTS:
                    log(f"   [start {label}: 0xC1, Versuch {i}/{SWITCH_ATTEMPTS}"
                        f" – STC-Ack + warte …]")
                    try:
                        radio.dab_digrad_status(stc_ack=True)
                    except Exception:
                        pass
                    time.sleep(SWITCH_RETRY_WAIT)
                    continue
                log(f"   [start {label} fehlgeschlagen: {exc}]")
                return False
        return False

    def switch_service(target, label) -> bool:
        """Robuster Wechsel: stop -> STC-Ack -> FIC -> start (Retry bei 0xC1).
        Bei Misserfolg wird der Vordienst wiederhergestellt. Wirft nie."""
        nonlocal cur
        if cur == target:
            return True
        origin = cur
        try:
            radio.dab_stop_service(origin[0], origin[1])
        except Exception as exc:
            log(f"   [stop fehlgeschlagen: {exc}]")
        time.sleep(SWITCH_SETTLE)
        _wait_fic(SWITCH_FIC_TIMEOUT)
        if _try_start(target, label):
            cur = target
            log(f"   [Service -> {label} SID=0x{target[0]:X}]")
            return True
        # Fehlschlag: Vordienst wiederherstellen, damit Audio weiterläuft
        log(f"   [Wechsel auf {label} fehlgeschlagen – stelle Vordienst her]")
        _wait_fic(SWITCH_FIC_TIMEOUT)
        if _try_start(origin, "ORIGIN"):
            cur = origin
        return False

    def safe_volume(v: int) -> None:
        try:
            radio.set_volume(v)
        except Exception as exc:
            log(f"   [set_volume({v}) Fehler: {exc}]")

    def go_pop(reason: str, suppress: bool = False) -> None:
        nonlocal mode, suppressed
        if switch_service(swiss, "POP"):
            safe_volume(NORMAL_VOLUME)
            mode = "POP"
            if suppress:
                suppressed = True
            log(f"<< {reason}")
        else:
            log("   [Rückschalten auf Pop fehlgeschlagen – Versuch nächste Runde]")

    # --- Init ---------------------------------------------------------------
    try:
        log("Init Si4689 (DAB) …")
        radio.open(); radio.reset(); radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.configure_i2s(master=False)
        safe_volume(NORMAL_VOLUME)
        radio.set_dab_freq_list()
        radio.dab_tune(TEST_CHANNEL)
        sig = wait_for_lock(radio)
        log(f"Lock OK: FIC={sig['fic_quality']}% RSSI={sig['rssi']}")
        radio.dab_start_service(*swiss)
        radio.set_property(PROP_DAB_ANNOUNCEMENT_ENABLE, ANNO_TYPES)
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, ANNO_INTEN_BIT)
        log("Swiss Pop+ läuft. Variante B aktiv (SRF 1 hörbar, Boost per DLS).")

        # --- Hauptschleife --------------------------------------------------
        while True:
            try:
                anno = read_anno(radio)
            except Exception as exc:
                log(f"   [read_anno Fehler: {exc} – überspringe Poll]")
                time.sleep(POLL_INTERVAL)
                continue

            try:
                rising  = anno and not prev_anno
                falling = (not anno) and prev_anno

                # Heartbeat nur bei Zustandswechsel oder alle HEARTBEAT_EVERY s,
                # damit der Ruhezustand das Terminal nicht flutet. Alle echten
                # Ereignisse (>>, <<, DLS, Service, Fehler) werden weiter geloggt.
                now_t = time.monotonic()
                state = (anno, mode, suppressed)
                if state != last_hb or (now_t - last_hb_time) >= HEARTBEAT_EVERY:
                    log(f"… ANNO={int(anno)} mode={mode}"
                        f"{' [gesperrt]' if suppressed else ''} cur=0x{cur[0]:X}")
                    last_hb = state
                    last_hb_time = now_t

                if falling:
                    suppressed = False

                # --- ANNO-Flanke: hörbar auf SRF 1 ---
                if rising and mode == "POP" and not suppressed:
                    if switch_service(srf1, "SRF1"):
                        safe_volume(NORMAL_VOLUME)
                        flush_dls(radio)
                        mode = "SRF1_WAIT"
                        wait_start = time.monotonic()
                        log(">> ANNO↑ – hörbar auf SRF 1, warte auf Marker …")
                    else:
                        suppressed = True
                        log("   [Wechsel auf SRF 1 misslang – bleibe Pop, gesperrt]")

                # --- Warten auf Marker (Vorlauf oder Fehlalarm) ---
                elif mode == "SRF1_WAIT":
                    if not anno:
                        go_pop("ANNO endete vor Bestätigung – zurück auf Pop.")
                    else:
                        dls = radio.get_dls_text(attempts=4, timeout=0.8)
                        if has_ta_markers(dls):
                            safe_volume(TA_VOLUME)
                            mode = "SRF1_TA"
                            log(f"   DLS='{dls}'")
                            log(">> ECHTE TA bestätigt – Boost, bleibe SRF 1.")
                        elif time.monotonic() - wait_start >= VERIFY_TIMEOUT:
                            go_pop(f"Fehlalarm (kein Marker in {VERIFY_TIMEOUT:.0f}s)"
                                   f" – zurück auf Pop, gesperrt.", suppress=True)
                        else:
                            hint = " [Song/Vorlauf]" if looks_like_song_title(dls) else ""
                            log(f"   DLS='{dls}'{hint} -> warte auf Marker")

                # --- Bestätigte TA läuft ---
                elif mode == "SRF1_TA":
                    if not anno:
                        go_pop("TA-Ende (ANNO=0) – zurück auf Swiss Pop+.")

                prev_anno = anno
            except Exception as exc:
                log(f"   [Poll-Fehler: {exc} – Schleife läuft weiter]")
                prev_anno = anno

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log("Abbruch durch Benutzer.")
    except Exception as exc:
        log(f"FATALER FEHLER (Setup): {exc}")
        import traceback; traceback.print_exc()
        return 1
    finally:
        try:
            if cur != swiss:
                switch_service(swiss, "POP")
            safe_volume(NORMAL_VOLUME)
        except Exception:
            pass
        try:
            radio.close(); log("SPI geschlossen.")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())