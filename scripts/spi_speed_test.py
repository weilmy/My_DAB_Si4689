#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spi_speed_test.py
=================

Prüft, ob der Si4689 bei einer gegebenen Host-SPI-Taktrate (z.B. 30 MHz)
STABIL kommuniziert. Zwei unabhängige Kriterien:

  1) Der Chip selbst: STATUS3-Bits REPOFERR (0x08) und CMDOFERR (0x04).
     Diese werden laut AN649 gesetzt, wenn die SPI-Taktrate für den
     internen Daten-Arbiter/Speicher zu hoch ist. Ein einziger Treffer
     = zu schnell.

  2) Readback-Konsistenz: feste, bekannte Antworten (GET_PART_INFO,
     GET_FUNC_INFO, GET_PROPERTY) werden tausendfach gelesen und Byte
     für Byte mit einer Referenz verglichen. Jede Abweichung = Korruption.

Der Test läuft unter REALISTISCHER Last: nach Boot wird auf einen Sender
getunt (DAB-Decode aktiv) und in jeder Iteration zusätzlich
DAB_DIGRAD_STATUS gepollt – genau die Situation, in der Overflow-Fehler
auftreten.

Ausführen (Testrate als Argument, Default 30 MHz):
    sudo ~/my_venv_314/bin/python3 spi_speed_test.py 30000000
    sudo ~/my_venv_314/bin/python3 spi_speed_test.py  2000000   # Kontrolllauf

Empfehlung: 30-MHz-Lauf und 2-MHz-Lauf vergleichen. 2 MHz MUSS fehlerfrei
sein; ist es das nicht, liegt das Problem nicht an der Taktrate.

Hinweis: Das Skript bootet absichtlich bei einer sicheren Rate (BOOT_SPEED)
und schaltet erst für den Lauftest auf die Testrate um. So wird die
LAUFZEIT-Kommunikation isoliert geprüft (der Treiber bootet ohnehin mit
seiner Default-Rate). Wer Boot@Testrate prüfen will, setzt BOOT_SPEED = None.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

DRIVER_DIR = Path("/home/weilmy/My_DAB_Si4689/hardware")
sys.path.insert(0, str(DRIVER_DIR))
from hardware.si4689_driver import Si4689  # noqa: E402

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
TEST_SPEED  = 30_000_000          # per Argument überschreibbar
BOOT_SPEED  = 2_000_000           # sichere Rate für Firmware-Load; None = Testrate
TEST_CHANNEL = "8B"               # BERN1 / SMC D03 BE-FR (Ihr bekannt-guter Sender)
ITERATIONS  = 20_000              # Anzahl Lese-Runden im Hammer-Test
REPORT_EVERY = 2_000              # Zwischenbericht-Intervall

# Korrigierte Opcodes (AN649) – unabhängig von evtl. falschen Treiber-Konstanten
CMD_RD_REPLY      = 0x00
CMD_GET_PART_INFO = 0x08          # ACHTUNG: Treiber hat fälschlich 0x02
CMD_GET_FUNC_INFO = 0x12
CMD_GET_PROPERTY  = 0x14
CMD_DAB_DIGRAD    = 0xB2

# STATUS3-Fehlerbits (4. Statusbyte, reply[3])
S3_REPOFERR = 0x08                # Antwort-Überlauf  -> SPI zu schnell
S3_CMDOFERR = 0x04                # Kommando-Überlauf -> SPI zu schnell
S3_ARBERR   = 0x02
S3_ERRNR    = 0x01
S3_OVERFLOW = S3_REPOFERR | S3_CMDOFERR


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Roh-Kommunikation OHNE Exception (damit Fehler GEZÄHLT statt geworfen werden)
# ---------------------------------------------------------------------------
def _wait_cts(radio: Si4689, timeout: float = 1.0):
    """Wartet auf CTS, gibt STATUS0 zurück; None bei Timeout. Wirft NICHT."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = radio._spi.xfer2([0x00, 0x00])[1]
        if s & 0x80:
            return s
        time.sleep(0.0002)
    return None


def cmd(radio: Si4689, data, nresp: int):
    """Kommando senden, Antwort lesen. Gibt (reply_bytes, status0) zurück.

    status0 ist None bei CTS-Timeout. reply enthält STATUS0..3 + Daten.
    Wirft KEINE Exception – Fehler werden vom Aufrufer ausgewertet.
    """
    _wait_cts(radio)                                   # pre
    radio._spi.xfer2(list(data))
    s0 = _wait_cts(radio)                              # post (ERR nicht werfen)
    reply = radio._spi.xfer2([0x00] * (nresp + 1))[1:]
    return reply, s0


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
def run(test_speed: int) -> int:
    radio = Si4689(spi_speed_hz=(BOOT_SPEED or test_speed), verbose=True)
    try:
        log(f"=== SPI-Stabilitätstest @ {test_speed/1e6:.3f} MHz ===")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.configure_i2s(master=False)

        # Auf Testrate umschalten (nach sicherem Boot)
        if BOOT_SPEED:
            radio._spi.max_speed_hz = test_speed
            log(f"Host-SPI-Takt auf {test_speed/1e6:.3f} MHz gesetzt "
                f"(angefordert; tatsächlicher Pi5-Teiler kann abweichen).")

        radio.set_dab_freq_list()
        radio.dab_tune(TEST_CHANNEL)

        # Lock abwarten (STC quittieren + FIC)
        radio.dab_digrad_status(stc_ack=True)
        deadline = time.monotonic() + 8.0
        sig = {}
        while time.monotonic() < deadline:
            sig = radio.get_dab_signal_strength()
            if sig["acq"] and sig["fic_quality"] >= 90:
                break
            time.sleep(0.25)
        log(f"Lock: acq={sig.get('acq')} FIC={sig.get('fic_quality')}% "
            f"RSSI={sig.get('rssi')} -> Last für den Arbiter aktiv.")

        # --- Referenzwerte einmalig erfassen ---
        ref_part, _ = cmd(radio, [CMD_GET_PART_INFO, 0x00], 10)
        ref_func, _ = cmd(radio, [CMD_GET_FUNC_INFO, 0x00], 12)
        ref_prop, _ = cmd(radio, [CMD_GET_PROPERTY, 0x00, 0x01, 0xB2], 8)  # 0xB201
        log(f"Referenz Part-Info Bytes: {[hex(b) for b in ref_part[4:9]]}")
        log(f"Starte Hammer-Test: {ITERATIONS} Runden …")

        # --- Zähler ---
        n = 0
        mismatches = 0
        overflow = 0
        err_cmd = 0
        cts_timeouts = 0
        other_s3 = 0

        def check_status3(reply, s0):
            nonlocal overflow, err_cmd, cts_timeouts, other_s3
            if s0 is None:
                cts_timeouts += 1
                return
            if s0 & 0x40:
                err_cmd += 1
            s3 = reply[3]
            if s3 & S3_OVERFLOW:
                overflow += 1
                log(f"  !! OVERFLOW: STATUS3=0x{s3:02X} "
                    f"(REPOFERR={int(bool(s3 & S3_REPOFERR))} "
                    f"CMDOFERR={int(bool(s3 & S3_CMDOFERR))})")
            elif s3 & (S3_ARBERR | S3_ERRNR):
                other_s3 += 1

        t0 = time.monotonic()
        for n in range(1, ITERATIONS + 1):
            r1, s1 = cmd(radio, [CMD_GET_PART_INFO, 0x00], 10)
            check_status3(r1, s1)
            if r1[4:] != ref_part[4:]:
                mismatches += 1

            r2, s2 = cmd(radio, [CMD_GET_FUNC_INFO, 0x00], 12)
            check_status3(r2, s2)
            if r2[4:] != ref_func[4:]:
                mismatches += 1

            r3, s3 = cmd(radio, [CMD_GET_PROPERTY, 0x00, 0x01, 0xB2], 8)
            check_status3(r3, s3)
            if r3[4:] != ref_prop[4:]:
                mismatches += 1

            # zusätzliche realistische Last: Signalstatus pollen (variabel,
            # daher nur STATUS3 prüfen, kein Byte-Vergleich)
            r4, s4 = cmd(radio, [CMD_DAB_DIGRAD, 0x00], 16)
            check_status3(r4, s4)

            if n % REPORT_EVERY == 0:
                log(f"  {n:>6}/{ITERATIONS}  mism={mismatches} "
                    f"ovfl={overflow} err={err_cmd} cts_to={cts_timeouts}")

        dt = time.monotonic() - t0
        log("=" * 56)
        log(f"ERGEBNIS @ {test_speed/1e6:.3f} MHz  ({n} Runden, {dt:.1f}s)")
        log(f"  Readback-Mismatches : {mismatches}")
        log(f"  Overflow (REP/CMD)  : {overflow}   <- 'SPI zu schnell'")
        log(f"  ERR_CMD (STATUS0)   : {err_cmd}")
        log(f"  CTS-Timeouts        : {cts_timeouts}")
        log(f"  Sonstige STATUS3    : {other_s3}")
        passed = (mismatches == 0 and overflow == 0 and err_cmd == 0
                  and cts_timeouts == 0)
        log(f"  >>> {'STABIL ✓' if passed else 'INSTABIL ✗ – Rate senken'}")
        log("=" * 56)
        return 0 if passed else 2

    except Exception as exc:
        log(f"FEHLER beim Setup: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        try:
            radio.close()
        except Exception:
            pass


if __name__ == "__main__":
    speed = int(sys.argv[1]) if len(sys.argv) > 1 else TEST_SPEED
    raise SystemExit(run(speed))