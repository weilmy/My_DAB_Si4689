#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/oe_classifier.py
========================
Read-only DAB-Scan: klassifiziert alle Sender aus dab_scans.sqlite nach
Announcement-Unterstützung.

Tabelle:  Idx | Name | Ensemble | TA-lokal | OE-Ann | OE-Serv

Pro Kanal (nach FIC-Lock >= FIC_MIN):
  1. CMD 0xB4 → EID in {eid → channel}-Map ablegen
  2. Pro Service drei Reads:
     a) 0xB5 SRC=0 (FIG 0/18): TA-lokal wenn ASU-Bit b1 (Traffic) gesetzt;
        Cluster-IDs = RESP8+ (16-Bit LE, NUM_IDS Stück)
     b) 0xB5 SRC=1 (FIG 0/25): OE-Ann wenn NUM_IDS>0; fremde EIDs merken
     c) 0xC1  (FIG 0/24):       OE-Serv wenn NUM_EIDS>0; EIDs merken

Nach dem Scan: OE-Ann/OE-Serv-EIDs gegen {eid → channel} auflösen.

ACHTUNG TA-lokal: Cluster-ID stammt aus FIG 0/18 und gibt nur die Cluster-
Zugehörigkeit an. Der konkrete Träger-SubChId ist erst zur Laufzeit via
FIG 0/19 / CMD 0xB6 verfügbar und wird hier NICHT aufgelöst.

Kein dab_start_service, kein Audio, kein GPIO.cleanup().

Ausführen:
  sudo ~/my_venv_314/bin/python3 scripts/oe_classifier.py
"""
from __future__ import annotations

import sys
import time
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from hardware.si4689_driver import Si4689  # noqa: E402

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
DB_PATH      = PROJECT_DIR / "assets/DB/dab_scans.sqlite"
FIC_MIN      = 90     # Mindest-FIC-Qualität in %
LOCK_TIMEOUT = 10.0   # Sekunden auf Ensemble-Lock warten
POLL_SLEEP   = 0.3    # Polling-Intervall in Sekunden

# ASU-Bitnamen (TS 101 756, Tab. 14) – Index = Bitnummer
_ASU_NAMES = [
    "Alarm", "Traffic", "Transport", "Warning", "News",
    "Weather", "Event", "Special", "Programme", "Sport", "Financial",
]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def asu_str(asu: int) -> str:
    bits = [n for i, n in enumerate(_ASU_NAMES) if asu & (1 << i)]
    return ",".join(bits) if bits else "–"


def load_db() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT si4689_idx, name, channel, ensemble, freq_index, "
        "service_id, component_id "
        "FROM si4689_datenbank ORDER BY freq_index, si4689_idx"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def wait_for_lock(radio: Si4689, timeout: float = LOCK_TIMEOUT) -> tuple[bool, dict]:
    """STC quittieren, dann auf FIC-Lock >= FIC_MIN pollen."""
    radio.dab_digrad_status(stc_ack=True)
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = radio.get_dab_signal_strength()
        if last["acq"] and last["fic_quality"] >= FIC_MIN:
            return True, last
        time.sleep(POLL_SLEEP)
    return False, last


def safe_close_spi(radio: Si4689) -> None:
    """Nur SPI-FD schließen – kein amp_enable(False), kein GPIO.cleanup()."""
    try:
        if radio._spi is not None:
            radio._spi.close()
            radio._spi = None
    except Exception:
        pass
    radio._opened = False


def ta_lokal_col(
    asu: int, num_ids: int, cluster_ids: list[int],
    peer_idxs: list[int] | None = None,
) -> str:
    """TA-lokal-Spalte: 'Traffic (clus 41=[70])*' wenn Traffic-Bit gesetzt;
    '–' sonst. peer_idxs = Idxs anderer Dienste im selben Ensemble + Cluster."""
    if not (num_ids > 0 and asu & (1 << 1)):
        return "–"
    clus = ",".join(f"{c:02X}" for c in cluster_ids) if cluster_ids else "?"
    peer_str = "=[" + ",".join(str(p) for p in peer_idxs) + "]" if peer_idxs else ""
    return f"Traffic (clus {clus}{peer_str})*"


def resolve_eids(eids: list[int], eid_map: dict[int, str]) -> str:
    """EIDs gegen {eid → channel} auflösen; unbekannte als '0xXXXX' darstellen."""
    if not eids:
        return "–"
    parts = []
    for eid in eids:
        parts.append(f"→ {eid_map[eid]}" if eid in eid_map else f"→ 0x{eid:04X}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
def main() -> int:
    rows = load_db()
    log(f"DB: {len(rows)} Sender geladen.")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["channel"], row["freq_index"])].append(row)

    # EID → Channel-Name, wird während des Scans aufgebaut
    eid_map: dict[int, str] = {}

    # Rohdaten aller Services; EID-Auflösung erst nach dem Scan
    # {si4689_idx: {"name", "ensemble", "ta_col", "oe_ann_eids", "oe_serv_eids", "locked"}}
    raw: dict[int, dict] = {}

    radio = Si4689(verbose=False)
    try:
        log("Initialisiere Si4689 (DAB, kein Audio) …")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.set_dab_freq_list()

        for (channel, freq_index), services in sorted(
            groups.items(), key=lambda x: x[0][1]
        ):
            log(f"\nKanal {channel} (freq_index={freq_index}): {len(services)} Sender")

            radio.dab_tune(channel)
            locked, sig = wait_for_lock(radio)

            if not locked:
                log(
                    f"  Kein Lock nach {LOCK_TIMEOUT:.0f}s "
                    f"(acq={sig.get('acq')}, FIC={sig.get('fic_quality')}%) → übersprungen"
                )
                for svc in services:
                    raw[svc["si4689_idx"]] = {
                        "name": svc["name"], "ensemble": svc["ensemble"],
                        "ta_col": "n/a", "oe_ann_eids": [], "oe_serv_eids": [],
                        "locked": False,
                    }
                continue

            log(
                f"  Lock OK: RSSI={sig['rssi']:+} dBm  "
                f"FIC={sig['fic_quality']}%  SNR={sig['snr']} dB"
            )

            # CMD 0xB4: EID des aktuellen Ensembles lesen
            try:
                ens = radio.dab_get_ensemble_info()
                eid_map[ens["eid"]] = channel
                log(f"  EID=0x{ens['eid']:04X}  Label={ens['label']!r}")
            except Exception as exc:
                log(f"  0xB4 Fehler: {exc}")

            for svc in services:
                idx  = svc["si4689_idx"]
                sid  = int(svc["service_id"])
                name = svc["name"]

                # --- a) 0xB5 SRC=0 (FIG 0/18): TA-lokal + Cluster-IDs ---
                asu = 0; num_ids = 0; cluster_ids: list[int] = []
                try:
                    b5_0        = radio.get_announcement_support_info(sid, src=0)
                    asu         = b5_0["asu"]
                    num_ids     = b5_0["num_ids"]
                    cluster_ids = b5_0["cluster_ids"]   # 8-Bit Cluster-IDs
                    b5_0_note   = (
                        f"ASU=0x{asu:04X}[{asu_str(asu)}]"
                        f" clus=[{','.join(f'0x{c:02X}' for c in cluster_ids)}]"
                        if num_ids > 0 else "ok(num_ids=0)"
                    )
                except Exception:
                    b5_0_note = "ERR"

                # --- b) 0xB5 SRC=1 (FIG 0/25): OE-Ann + fremde Ziel-EIDs ---
                oe_ann_eids: list[int] = []
                try:
                    b5_1        = radio.get_announcement_support_info(sid, src=1)
                    oe_ann_eids = b5_1["eids"] if b5_1["num_ids"] > 0 else []
                    b5_1_note   = (
                        f"EIDs=[{','.join(f'0x{e:04X}' for e in oe_ann_eids)}]"
                        if oe_ann_eids else "ok(–)"
                    )
                except Exception:
                    b5_1_note = "ERR"

                # --- c) 0xC1 (FIG 0/24): OE-Serv + EIDs ---
                oe_serv_eids: list[int] = []
                try:
                    c1           = radio.get_oe_services_info(sid)
                    oe_serv_eids = c1["eids"] if c1["num_eids"] > 0 else []
                    c1_note      = (
                        f"EIDs=[{','.join(f'0x{e:04X}' for e in oe_serv_eids)}]"
                        if oe_serv_eids else "ok(–)"
                    )
                except Exception:
                    c1_note = "ERR"

                log(
                    f"  [{idx:>3}] {name:<26}"
                    f"  B5_0:{b5_0_note}"
                    f"  B5_1:{b5_1_note}"
                    f"  C1:{c1_note}"
                )

                raw[idx] = {
                    "name":          name,
                    "ensemble":      svc["ensemble"],
                    "channel":       channel,
                    "asu":           asu,
                    "num_ids":       num_ids,
                    "cluster_ids":   cluster_ids,
                    "oe_ann_eids":   oe_ann_eids,
                    "oe_serv_eids":  oe_serv_eids,
                    "locked":        True,
                }

    except KeyboardInterrupt:
        log("\nAbbruch durch Benutzer.")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        safe_close_spi(radio)
        log("SPI geschlossen (GPIO-Pins unverändert).")

    if not raw:
        log("Keine Ergebnisse – Tabelle leer.")
        return 0

    # ---------------------------------------------------------------------------
    # EID-Auflösung + Cluster-Peer-Map
    # ---------------------------------------------------------------------------
    log(f"\nEID-Map: { {f'0x{k:04X}': v for k, v in sorted(eid_map.items())} }")

    # (channel, cluster_id) → [idx, ...] — nur Traffic-Dienste (ASU bit1 + num_ids>0)
    cluster_peer_map: dict[tuple, list[int]] = defaultdict(list)
    for idx, r in raw.items():
        if r["locked"] and r["asu"] & (1 << 1) and r["num_ids"] > 0:
            for cid in r["cluster_ids"]:
                cluster_peer_map[(r["channel"], cid)].append(idx)

    results: list[tuple] = []
    for row in load_db():       # original sort: freq_index, si4689_idx
        idx = row["si4689_idx"]
        if idx not in raw:
            continue
        r = raw[idx]
        if not r["locked"]:
            results.append((idx, r["name"], r["ensemble"], "n/a", "n/a", "n/a"))
        else:
            peer_idxs: list[int] = []
            for cid in r["cluster_ids"]:
                for p in cluster_peer_map.get((r["channel"], cid), []):
                    if p != idx:
                        peer_idxs.append(p)
            peer_idxs = list(dict.fromkeys(peer_idxs))
            ta_col = ta_lokal_col(r["asu"], r["num_ids"], r["cluster_ids"], peer_idxs or None)
            results.append((
                idx,
                r["name"],
                r["ensemble"],
                ta_col,
                resolve_eids(r["oe_ann_eids"],  eid_map),
                resolve_eids(r["oe_serv_eids"], eid_map),
            ))

    # ---------------------------------------------------------------------------
    # Ergebnis-Tabelle
    # ---------------------------------------------------------------------------
    headers = ["Idx", "Name", "Ensemble", "TA-lokal", "OE-Ann", "OE-Serv"]
    print("\n" + "=" * 110)
    try:
        from tabulate import tabulate  # type: ignore
        print(tabulate(results, headers=headers, tablefmt="simple"))
    except ImportError:
        widths = (4, 26, 14, 22, 14, 14)
        sep    = " | "
        hdr    = sep.join(c.ljust(w) for c, w in zip(headers, widths))
        print(hdr)
        print("-" * len(hdr))
        for row in results:
            print(sep.join(str(v).ljust(w) for v, w in zip(row, widths)))

    print(
        "\n* TA-lokal: Cluster-ID aus FIG 0/18 (Cluster-Zugehörigkeit)."
        " Träger-SubChId erst zur Laufzeit via FIG 0/19 / CMD 0xB6 verfügbar."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
