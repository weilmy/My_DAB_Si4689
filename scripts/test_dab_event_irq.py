#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dab_event_irq.py
=====================

Standalone-Nachweis: Eine DAB-Verkehrsdurchsage (Announcement) auf Kanal 12C
bringt den Si4689 dazu, GPIO 23 (INTB) physisch auf LOW zu ziehen. Danach
wird DAB_GET_EVENT_STATUS (0xB3) mit EVENT_ACK gelesen und ANNO START /
ANNO STOPP mit Zeitstempel geloggt.

Voraussetzung:
  - main.py muss gestoppt sein (exklusiver SPI + GPIO 23 frei)
  - Ausführen als: sudo ~/my_venv_314/bin/python3 test_dab_event_irq.py

Testsender: SRF 1 BE FR VS+
  si4689_idx=63  SID=0x44B1  CID=0x02  Kanal=12C  Ensemble: SRG SSR D01
"""

from __future__ import annotations

import datetime
import sqlite3
import sys
import time
from pathlib import Path

# gpiod: System-Paket (python3-libgpiod), nicht im venv.
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, "/home/weilmy/My_DAB_Si4689")

import gpiod
from gpiod.line import Bias, Direction, Edge, Value

from hardware.si4689_driver import Si4689

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
DB_PATH         = Path("/home/weilmy/My_DAB_Si4689/assets/DB/dab_scans.sqlite")
TEST_CHANNEL    = "12C"
SRF1_IDX        = 63           # si4689_idx in dab_scans.sqlite
SRF1_SID        = 0x44B1       # Fallback falls DB nicht verfügbar
SRF1_CID        = 0x02         # Fallback

INT_LINE_OFFSET = 23           # BCM-Offset GPIO23 (INTB-Pin)
LOCK_TIMEOUT    = 15.0         # Sekunden, max. Wartezeit auf FIC-Lock
FIC_MIN         = 90           # FIC-Qualität [%] für Lock-OK
CHUNK_S         = 30           # wait_edge_events-Fenster pro Iteration [s]

# DAB_ANNOUNCEMENT_ENABLE (0xB700): alle 11 Typen.
# SRG setzt das reine TRAFFIC-Bit (Bit1) nicht immer, daher 0x07FF.
ANNO_TYPES = 0x07FF

# Properties (AN649)
PROP_INT_CTL_ENABLE             = 0x0000
PROP_DAB_EVENT_INTERRUPT_SOURCE = 0xB300
PROP_DAB_ANNOUNCEMENT_ENABLE   = 0xB700

DEVNTIEN   = 0x2000   # INT_CTL_ENABLE Bit13 – routet DEVNTINT → INTB-Pin
ANNO_INTEN = 0x0010   # DAB_EVENT_INTERRUPT_SOURCE Bit4 – ANNO → DEVNTINT


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", flush=True)


def load_service(db_path: Path, idx: int) -> dict | None:
    if not db_path.exists():
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
        log(f"DB-Fehler: {exc}")
        return None
    if row is None:
        return None
    return {
        "name":    row["name"],
        "channel": row["channel"],
        "sid":     int(row["service_id"]),
        "cid":     int(row["component_id"]),
    }


def find_gpiochip(line_offset: int) -> str | None:
    """Sucht /dev/gpiochipX, dessen Leitung *line_offset* 'GPIOxx' heisst."""
    expected = f"GPIO{line_offset}"
    for dev in sorted(Path("/dev").glob("gpiochip*")):
        if not gpiod.is_gpiochip_device(str(dev)):
            continue
        try:
            with gpiod.Chip(str(dev)) as chip:
                info = chip.get_info()
                if line_offset >= info.num_lines:
                    continue
                linfo = chip.get_line_info(line_offset)
                log(f"    {dev.name} ({info.label}): "
                    f"Leitung {line_offset} = '{linfo.name}'")
                if linfo.name == expected:
                    return str(dev)
        except Exception:
            pass
    return None


def wait_for_lock(radio: Si4689, timeout: float, fic_min: int) -> dict:
    radio.dab_digrad_status(stc_ack=True)   # pending STCINT quittieren
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last.get("acq") and last.get("fic_quality", 0) >= fic_min:
            return last
        time.sleep(0.25)
    raise TimeoutError(
        f"Kein FIC-Lock nach {timeout:.0f}s "
        f"(acq={last.get('acq')}, FIC={last.get('fic_quality')}%)."
    )


def print_event_status(ev: dict, source: str) -> None:
    annoint = ev.get("annoint", False)   # RESP4 Bit4: sticky, EVENT-Bit
    anno    = ev.get("anno",    False)   # RESP5 Bit4: Pegel, TRUE=aktiv
    status  = "ANNO START" if anno else "ANNO STOPP"
    log(f"  [{source}] DAB_GET_EVENT_STATUS (mit ACK):")
    log(f"         annoint (RESP4 Bit4, sticky) = {annoint}")
    log(f"         anno    (RESP5 Bit4, Pegel)  = {anno}")
    log(f"         *** {status} ***")
    extra = [k for k in ("srvlist", "audio", "mute", "blk_error", "blk_loss")
             if ev.get(k)]
    if extra:
        log(f"         weitere Flags: {', '.join(extra)}")


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 66)
    print("  test_dab_event_irq.py  –  Si4689 ANNO-IRQ (GPIO 23) Nachweis")
    print("=" * 66)

    # [1] Sender aus DB laden ------------------------------------------------
    svc = load_service(DB_PATH, SRF1_IDX)
    if svc:
        sid, cid = svc["sid"], svc["cid"]
        log(f"[1] DB idx={SRF1_IDX}: {svc['name']}  "
            f"Kanal={svc['channel']}  SID=0x{sid:04X}  CID=0x{cid:02X}")
    else:
        sid, cid = SRF1_SID, SRF1_CID
        log(f"[1] DB-Lookup leer – Fallback SID=0x{sid:04X} CID=0x{cid:02X}")

    # [2] GPIO-Chip ermitteln ------------------------------------------------
    log(f"[2] GPIO-Chip-Suche für GPIO{INT_LINE_OFFSET} …")
    chip_path = find_gpiochip(INT_LINE_OFFSET)
    if chip_path is None:
        log(f"    FEHLER: Kein GPIO-Chip mit Label 'GPIO{INT_LINE_OFFSET}' gefunden.")
        return 1
    log(f"    → {chip_path} ✓")

    # [3] Si4689 initialisieren ----------------------------------------------
    log("[3] Si4689 initialisieren (DAB-Firmware) …")
    radio = Si4689(verbose=False)
    req = None
    try:
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()      # setzt DAB_EVENT_INTERRUPT_SOURCE=0
        radio.configure_i2s(master=False)
        radio.set_volume(40)
        log("    Hardware-Init ✓")

        radio.set_dab_freq_list()
        log(f"    Tune → {TEST_CHANNEL} …")
        radio.dab_tune(TEST_CHANNEL)

        log(f"    Warte auf FIC-Lock (max. {LOCK_TIMEOUT:.0f}s, FIC≥{FIC_MIN}%) …")
        sig = wait_for_lock(radio, LOCK_TIMEOUT, FIC_MIN)
        log(f"    Lock ✓  RSSI={sig['rssi']} dBm  SNR={sig['snr']} dB  "
            f"FIC={sig['fic_quality']}%  CNR={sig['cnr']} dB")

        try:
            ens = radio.dab_get_ensemble_info()
            log(f"    Ensemble: '{ens['label']}'  EID=0x{ens['eid']:04X}")
        except Exception:
            pass

        log(f"    START_DIGITAL_SERVICE SID=0x{sid:04X} CID=0x{cid:02X} …")
        radio.dab_start_service(sid, cid)
        log("    Service läuft ✓")

        # [4] gpiod – Leitung anfordern --------------------------------------
        log(f"[4] gpiod: Leitung {INT_LINE_OFFSET} auf {chip_path} anfordern …")
        # Sicherheitshalber aus RPi.GPIO freigeben, falls noch belegt
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup(INT_LINE_OFFSET)
        except Exception:
            pass
        req = gpiod.request_lines(
            chip_path,
            consumer="test_dab_event_irq",
            config={
                INT_LINE_OFFSET: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                    edge_detection=Edge.FALLING,
                )
            },
        )
        log(f"    Leitung {INT_LINE_OFFSET} angefordert ✓")

        # [5] Ruhepegel vor Property-Set ------------------------------------
        idle_val = req.get_value(INT_LINE_OFFSET)
        idle_str = "HIGH" if idle_val == Value.ACTIVE else "LOW"
        warn     = "" if idle_val == Value.ACTIVE else "  ← WARNUNG: Pin schon LOW!"
        log(f"[5] GPIO{INT_LINE_OFFSET} Ruhepegel (vor INT-Enable): {idle_str}{warn}")

        # [6] Interrupt-Properties setzen -----------------------------------
        log("[6] Interrupt-Properties setzen …")
        radio.set_property(PROP_DAB_ANNOUNCEMENT_ENABLE, ANNO_TYPES)
        log(f"    DAB_ANNOUNCEMENT_ENABLE  (0x{PROP_DAB_ANNOUNCEMENT_ENABLE:04X}) "
            f"= 0x{ANNO_TYPES:04X}  (alle Typen)")
        radio.set_property(PROP_DAB_EVENT_INTERRUPT_SOURCE, ANNO_INTEN)
        log(f"    DAB_EVENT_INTERRUPT_SOURCE (0x{PROP_DAB_EVENT_INTERRUPT_SOURCE:04X}) "
            f"= 0x{ANNO_INTEN:04X}  (ANNO_INTEN Bit4)")
        radio.set_property(PROP_INT_CTL_ENABLE, DEVNTIEN)
        log(f"    INT_CTL_ENABLE           (0x{PROP_INT_CTL_ENABLE:04X}) "
            f"= 0x{DEVNTIEN:04X}  (DEVNTIEN Bit13)")
        log("    Properties gesetzt ✓")

        # Sofort-Check: Pin schon LOW (Event war schon gepuffert)?
        after_val = req.get_value(INT_LINE_OFFSET)
        if after_val != Value.ACTIVE:
            log(f"    GPIO{INT_LINE_OFFSET} ist sofort nach Enable LOW "
                "– Event war bereits gepuffert (Sofortauslösung).")
            ev = radio.dab_get_event_status(ack=True)
            print_event_status(ev, "SOFORT")
            return 0

        # [7] Event-Schleife (kein Gesamt-Timeout – Ctrl-C zum Beenden) --------
        log("[7] Warte auf ANNO-Event – kein Timeout, Ctrl-C zum Beenden.")
        log("    Hinweis: SRF 1 sendet Durchsagen typisch zu Verkehrszeiten.")
        t_start = time.monotonic()

        while True:
            got = req.wait_edge_events(
                timeout=datetime.timedelta(seconds=CHUNK_S)
            )

            if not got:
                elapsed = int(time.monotonic() - t_start)
                log(f"    … warte (bisher {elapsed // 60}:{elapsed % 60:02d} min)")
                continue

            # Falling Edge empfangen
            t_edge = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log(f"*** GPIO{INT_LINE_OFFSET} FALLING EDGE @ {t_edge} ***")
            events = req.read_edge_events()
            log(f"    {len(events)} Edge-Event(s) im Buffer")

            ev = radio.dab_get_event_status(ack=True)
            print_event_status(ev, "IRQ")

    except KeyboardInterrupt:
        log("Abbruch durch Benutzer (Ctrl-C).")
    except TimeoutError as exc:
        log(f"FEHLER: {exc}")
        return 1
    except Exception as exc:
        log(f"UNERWARTETER FEHLER: {exc}")
        raise
    finally:
        if req is not None:
            try:
                req.release()
            except Exception:
                pass
        try:
            radio.close()
            log("Si4689 geschlossen ✓")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
