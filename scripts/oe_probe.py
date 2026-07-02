#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/oe_probe.py  –  Diagnose CMD 0xB5 / 0xC1 an Swiss Pop+
===============================================================
Testet CMD 0xB5 (SRC=0 und SRC=1) sowie CMD 0xC1 in allen Zuständen:
  1. Ohne gestarteten Dienst
  2. Mit gestartetem Dienst (kein I2S → kein Audio), STCINT gequittiert
  3. Mit gestartetem Dienst + I2S (für den Fall, dass Dekoder aktiv sein muss)

Die rohen Reply-Bytes werden immer ausgegeben, auch wenn ERR gesetzt ist
(ERR-Check wird für die Diagnose überbrückt).

Ausführen:
  sudo ~/my_venv_314/bin/python3 scripts/oe_probe.py
"""
from __future__ import annotations

import sys
import time
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402

DB_PATH               = PROJECT_DIR / "assets/DB/dab_scans.sqlite"
SWISS_POP_IDX         = 70          # si4689_idx Swiss Pop+, Kanal 12C
TEST_CHANNEL          = "12C"
FIC_MIN               = 90
LOCK_TIMEOUT          = 10.0
PROP_DAB_ANN_ENABLE   = 0xB700      # DAB_ANNOUNCEMENT_ENABLE


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_svc(idx: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT name, channel, service_id, component_id "
        "FROM si4689_datenbank WHERE si4689_idx=?", (idx,)
    ).fetchone()
    conn.close()
    return dict(r) if r else None


def wait_for_lock(radio: Si4689, timeout: float = LOCK_TIMEOUT) -> tuple[bool, dict]:
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= FIC_MIN:
            return True, last
        time.sleep(0.3)
    return False, last


def read_status0(radio: Si4689) -> int:
    """STATUS0 ohne Kommando lesen (5-Byte Dummy-Read)."""
    resp = radio._spi.xfer2([0x00, 0x00, 0x00, 0x00, 0x00])
    return resp[1]


def raw_query(radio: Si4689, cmd_bytes: list[int], n_reply: int = 24) -> list[int]:
    """Kommando senden, Antwort lesen – OHNE ERR-Check (Diagnosezweck).

    Wartet vor und nach dem Senden auf CTS, ignoriert dabei ERR-Bit.
    Gibt n_reply Reply-Bytes zurück (Dummy-Byte wird entfernt).
    """
    radio._wait_cts(check_err=False)            # auf CTS warten, ERR ignorieren
    radio._spi.xfer2(cmd_bytes)                 # Kommando senden
    radio._wait_cts(check_err=False)            # auf CTS nach Verarbeitung warten
    raw = radio._spi.xfer2([0x00] * (n_reply + 1))
    return raw[1:]


def probe(radio: Si4689, label: str, cmd_bytes: list[int]) -> dict:
    """Einen Rohabfrage-Durchlauf protokollieren und Ergebnis zurückgeben."""
    print(f"\n  ── {label} ──")
    s0_before = read_status0(radio)
    print(f"  STATUS0 vor Senden : 0x{s0_before:02X}  "
          f"CTS={int(bool(s0_before & 0x80))} ERR={int(bool(s0_before & 0x40))} "
          f"STCINT={int(bool(s0_before & 0x01))}")
    print(f"  Sende   : {' '.join(f'{b:02X}' for b in cmd_bytes)}")

    reply = raw_query(radio, cmd_bytes, n_reply=24)

    s0 = reply[0]
    err = bool(s0 & 0x40)
    print(f"  Reply   : {' '.join(f'{b:02X}' for b in reply)}")
    print(f"  STATUS0 : 0x{s0:02X}  "
          f"CTS={int(bool(s0 & 0x80))} ERR={int(err)} "
          f"STCINT={int(bool(s0 & 0x01))}")

    result = {"err": err, "reply": reply}
    if not err:
        cmd = cmd_bytes[0]
        if cmd == 0xB5:
            num_ids = reply[4]
            asu     = reply[6] | (reply[7] << 8)
            eids    = [reply[8 + i*2] | (reply[9 + i*2] << 8)
                       for i in range(min(num_ids, 8)) if 9 + i*2 < len(reply)]
            print(f"  NUM_IDS={num_ids}  ASU=0x{asu:04X}  EIDs={[hex(e) for e in eids]}")
            result.update({"num_ids": num_ids, "asu": asu, "eids": eids})
        elif cmd == 0xC1:
            size     = reply[4] | (reply[5] << 8)
            num_eids = reply[6]
            eids     = [reply[8 + i*2] | (reply[9 + i*2] << 8)
                        for i in range(min(num_eids, 8)) if 9 + i*2 < len(reply)]
            print(f"  SIZE={size}  NUM_EIDS={num_eids}  EIDs={[hex(e) for e in eids]}")
            result.update({"size": size, "num_eids": num_eids, "eids": eids})
    else:
        print("  → ERR gesetzt (Kommando abgelehnt)")
    return result


def safe_close(radio: Si4689) -> None:
    try:
        if radio._spi is not None:
            radio._spi.close()
            radio._spi = None
    except Exception:
        pass
    radio._opened = False


# ---------------------------------------------------------------------------
def main() -> int:
    svc = load_svc(SWISS_POP_IDX)
    if not svc:
        print("Swiss Pop+ nicht in DB!"); return 1
    sid = int(svc["service_id"])
    cid = int(svc["component_id"])
    log(f"Swiss Pop+: SID=0x{sid:X} CID={cid} Kanal={svc['channel']}")

    sid_le = list(sid.to_bytes(4, "little"))

    radio = Si4689(verbose=False)
    try:
        log("Init Si4689 (DAB) …")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.set_dab_freq_list()

        log(f"Tune auf {TEST_CHANNEL} …")
        radio.dab_tune(TEST_CHANNEL)
        locked, sig = wait_for_lock(radio)
        if not locked:
            log(f"Kein Lock! FIC={sig.get('fic_quality')}%"); return 1
        log(f"Lock: FIC={sig['fic_quality']}%  RSSI={sig['rssi']} dBm")

        # =====================================================================
        print("\n" + "="*60)
        print("PHASE 1: Kein Dienst gestartet")
        print("="*60)
        probe(radio, "0xB5 SRC=0 (cur-ensemble FIG 0/18)", [0xB5, 0x00, 0x00, 0x00] + sid_le)
        probe(radio, "0xB5 SRC=1 (OE FIG 0/25)",          [0xB5, 0x01, 0x00, 0x00] + sid_le)
        probe(radio, "0xC1 OE Services",                    [0xC1, 0x00, 0x00, 0x00] + sid_le)

        # =====================================================================
        print("\n" + "="*60)
        print("PHASE 2: Dienst gestartet, KEIN I2S")
        print("="*60)
        log(f"dab_start_service(SID=0x{sid:X}, CID={cid}) …")
        radio.dab_start_service(sid, cid)
        radio.set_property(PROP_DAB_ANN_ENABLE, 0x07FF)   # alle Announcement-Typen
        time.sleep(2.0)
        radio.dab_digrad_status(stc_ack=True)              # STCINT quittieren
        s0 = read_status0(radio)
        log(f"Nach Start+Settle: STATUS0=0x{s0:02X}  STCINT={int(bool(s0 & 0x01))}")

        probe(radio, "0xB5 SRC=0 (cur-ensemble FIG 0/18)", [0xB5, 0x00, 0x00, 0x00] + sid_le)
        probe(radio, "0xB5 SRC=1 (OE FIG 0/25)",          [0xB5, 0x01, 0x00, 0x00] + sid_le)
        probe(radio, "0xC1 OE Services",                    [0xC1, 0x00, 0x00, 0x00] + sid_le)

        # =====================================================================
        print("\n" + "="*60)
        print("PHASE 3: Dienst gestartet + I2S konfiguriert")
        print("="*60)
        radio.configure_i2s(master=False)
        time.sleep(1.0)
        s0 = read_status0(radio)
        log(f"Nach I2S-Config: STATUS0=0x{s0:02X}")

        probe(radio, "0xB5 SRC=0 (cur-ensemble FIG 0/18)", [0xB5, 0x00, 0x00, 0x00] + sid_le)
        probe(radio, "0xB5 SRC=1 (OE FIG 0/25)",          [0xB5, 0x01, 0x00, 0x00] + sid_le)
        probe(radio, "0xC1 OE Services",                    [0xC1, 0x00, 0x00, 0x00] + sid_le)

        # Noch ein zweiter SRF-1-Dienst aus dem selben Ensemble testen (idx=63: SRF 1 BE FR VS+)
        svc2 = load_svc(63)
        if svc2:
            sid2_le = list(int(svc2["service_id"]).to_bytes(4, "little"))
            print(f"\n  [Zusatz: {svc2['name']} SID=0x{int(svc2['service_id']):X}]")
            probe(radio, f"0xB5 SRC=0 {svc2['name']}", [0xB5, 0x00, 0x00, 0x00] + sid2_le)
            probe(radio, f"0xB5 SRC=1 {svc2['name']}", [0xB5, 0x01, 0x00, 0x00] + sid2_le)

        try:
            radio.dab_stop_service(sid, cid)
        except Exception:
            pass

    except KeyboardInterrupt:
        log("Abbruch.")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        safe_close(radio)
        log("SPI geschlossen (GPIO unverändert).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
