#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/oe_classifier_ai.py
========================
Klassifiziert jeden Sender in dab_scans.sqlite nach Durchsage-Unterstuetzung
und ermittelt – soweit offline ueberhaupt moeglich – das Ziel der Umschaltung.

TA_Typ (passend zum DB-Schema si4689_datenbank):
  TA_direct : Durchsage wird im EIGENEN Ensemble getragen  (FIG 0/18, ASu-Bit b1)
  OES       : Durchsage wird in einem ANDEREN Ensemble getragen (FIG 0/25)
  No_TA     : keine Verkehrsdurchsage-Unterstuetzung

Was sich offline bestimmen laesst und was nicht (EN 300 401, Kap. 8.1.6):
  - TA-Support (FIG 0/18 / FIG 0/25) ist STATISCH und sofort nach FIC-Lock lesbar.
  - Der konkrete TRAEGER (Ziel-Service) ist DYNAMISCH: FIG 0/19 (SubChId) bzw.
    FIG 0/26 (Target SId) existieren nur WAEHREND einer aktiven Durchsage und
    werden dann ueber CMD 0xB6 geliefert. Daher:
      * OES       -> Ziel-ENSEMBLE ist offline bekannt (EId aus FIG 0/25),
                     aufgeloest gegen die im Scan gesammelte EId->Kanal-Map.
                     Ziel-Service bleibt "(Laufzeit)".
      * TA_direct -> nur Cluster-Geschwister als KANDIDATEN; der echte Traeger
                     kommt zur Laufzeit aus 0xB6 (wie im laufenden TA-Feature).

Read-only: kein Audio, kein dab_start_service, KEIN GPIO.cleanup()
(Verstaerker-Pin-Zustand bleibt unangetastet).

Ausfuehren:
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
FIC_MIN      = 90      # Mindest-FIC-Qualitaet in %
LOCK_TIMEOUT = 10.0    # Sekunden auf Ensemble-Lock warten
POLL_SLEEP   = 0.3     # Polling-Intervall in Sekunden
ANN_SETTLE   = 4.0     # Nachlauf nach Lock, bis FIG 0/18/0/25 dekodiert sind

# ASu-Bitmaske: b1 = Road Traffic flash (TS 101 756, Tabelle 14)
ASU_TRAFFIC  = 0x0002

# Klassifikations-Labels (Praezedenz: TA_direct > OES > No_TA)
TA_DIRECT = "TA_direct"
TA_OES    = "OES"
TA_NONE   = "No_TA"
TA_NA     = "n/a"

# ASu-Bitnamen fuer die Detail-Ausgabe (TS 101 756, Tabelle 14, b0..b10)
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
    return ",".join(bits) if bits else "-"


def load_db() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT si4689_idx, name, channel, ensemble, freq_index, service_id "
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
        if last.get("acq") and last.get("fic_quality", 0) >= FIC_MIN:
            return True, last
        time.sleep(POLL_SLEEP)
    return False, last


def classify(local_ta: bool, oe_ann: bool) -> str:
    # local TA reicht aus -> Vorrang; OES nur, wenn keine lokale TA vorhanden.
    if local_ta:
        return TA_DIRECT
    if oe_ann:
        return TA_OES
    return TA_NONE


def safe_close_spi(radio: Si4689) -> None:
    """Nur SPI-FD schliessen – KEIN amp_enable(False), KEIN GPIO.cleanup()."""
    try:
        if getattr(radio, "_spi", None) is not None:
            radio._spi.close()
            radio._spi = None
    except Exception:
        pass
    radio._opened = False


# ---------------------------------------------------------------------------
# Pro Dienst: TA-Support einlesen (rein lesend, kein Dienststart noetig)
# ---------------------------------------------------------------------------
def probe_service(radio: Si4689, sid: int) -> dict:
    """Liest FIG 0/18 (SRC=0), FIG 0/25 (SRC=1) und FIG 0/24 (0xC1) fuer eine SID.

    Rueckgabe:
      local_ta     : bool        Traffic-Bit in FIG 0/18 gesetzt
      cluster_ids  : list[int]   Cluster-IDs aus FIG 0/18
      asu0         : int         ASu-Wort des eigenen Ensembles (FIG 0/18)
      oe_ann       : bool        OE-Announcement mit Traffic-Bit (FIG 0/25)
      oe_ann_eids  : list[int]   Ziel-EIds der OE-Durchsage
      asu1         : int         ASu-Wort der OE-Durchsage (FIG 0/25)
      oe_serv_eids : list[int]   EIds aus Service-Following (FIG 0/24)
    """
    out = {
        "local_ta": False, "cluster_ids": [], "asu0": 0,
        "oe_ann": False, "oe_ann_eids": [], "asu1": 0,
        "oe_serv_eids": [],
    }

    # FIG 0/18 – Durchsage im eigenen Ensemble
    try:
        a0 = radio.get_announcement_support_info(sid, src=0)
        out["asu0"] = a0["asu"]
        out["cluster_ids"] = a0["cluster_ids"]
        out["local_ta"] = bool(a0["asu"] & ASU_TRAFFIC)
    except Exception as exc:
        log(f"    0xB5 SRC=0 [SID=0x{sid:X}]: {exc}")

    # FIG 0/25 – Durchsage in anderem Ensemble
    try:
        a1 = radio.get_announcement_support_info(sid, src=1)
        out["asu1"] = a1["asu"]
        out["oe_ann_eids"] = a1["eids"]
        out["oe_ann"] = a1["num_ids"] > 0 and bool(a1["asu"] & ASU_TRAFFIC)
    except Exception as exc:
        log(f"    0xB5 SRC=1 [SID=0x{sid:X}]: {exc}")

    # FIG 0/24 – Service-Following (rein informativ, kein TA)
    try:
        oe = radio.get_oe_services_info(sid)
        if oe["num_eids"] > 0:
            out["oe_serv_eids"] = oe["eids"]
    except Exception as exc:
        log(f"    0xC1 [SID=0x{sid:X}]: {exc}")

    return out


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
def main() -> int:
    rows = load_db()
    log(f"DB: {len(rows)} Sender geladen aus {DB_PATH}")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["channel"], row["freq_index"])].append(row)

    # eid_map: EId -> (channel, ensemble-label) ; ueber alle Kanaele gesammelt
    eid_map: dict[int, tuple[str, str]] = {}
    # probes: si4689_idx -> probe-dict (+ row-Felder), fuer spaetere Aufloesung
    probes: dict[int, dict] = {}
    # cluster_by_channel: channel -> {cluster_id: [si4689_idx, ...]}
    cluster_by_channel: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))

    radio = Si4689(verbose=False)
    try:
        log("Initialisiere Si4689 (DAB, kein Audio) ...")
        radio.open()
        radio.reset()
        radio.power_up()
        radio.load_firmware_auto(mode="dab")
        radio.configure_dab_frontend()
        radio.set_dab_freq_list()

        # ===== Durchlauf: pro Ensemble einmal tunen, alle Dienste abfragen =====
        for (channel, freq_index), services in sorted(groups.items(),
                                                       key=lambda x: x[0][1] or 0):
            log(f"\nKanal {channel} (freq_index={freq_index}): {len(services)} Sender")
            radio.dab_tune(channel)
            locked, sig = wait_for_lock(radio)

            if not locked:
                log(f"  Kein Lock nach {LOCK_TIMEOUT:.0f}s "
                    f"(acq={sig.get('acq')}, FIC={sig.get('fic_quality')}%) -> n/a")
                for svc in services:
                    probes[svc["si4689_idx"]] = {"row": svc, "na": True}
                continue

            log(f"  Lock OK: RSSI={sig['rssi']:+} dBm  "
                f"FIC={sig['fic_quality']}%  SNR={sig['snr']} dB")

            # EId des Kanals fuer die spaetere OES-Ziel-Aufloesung merken
            try:
                ens = radio.dab_get_ensemble_info()
                eid_map[ens["eid"]] = (channel, ens["label"])
                log(f"  Ensemble: EId=0x{ens['eid']:04X}  '{ens['label']}'")
            except Exception as exc:
                log(f"  0xB4 (Ensemble-Info): {exc}")

            time.sleep(ANN_SETTLE)   # FIG 0/18 / 0/25 fertig dekodieren lassen

            for svc in services:
                idx  = svc["si4689_idx"]
                sid  = int(svc["service_id"])
                name = svc["name"]
                p = probe_service(radio, sid)
                p["row"] = svc
                p["na"]  = False
                probes[idx] = p

                for cid in p["cluster_ids"]:
                    cluster_by_channel[channel][cid].append(idx)

                ta_typ = classify(p["local_ta"], p["oe_ann"])
                detail = []
                if p["asu0"]:
                    detail.append(f"FIG18 ASu=0x{p['asu0']:04X}[{asu_str(p['asu0'])}]"
                                  f" clus={[hex(c) for c in p['cluster_ids']]}")
                if p["oe_ann_eids"]:
                    detail.append("FIG25 EIds="
                                  + ",".join(f"0x{e:04X}" for e in p["oe_ann_eids"]))
                if p["oe_serv_eids"]:
                    detail.append("FIG24 EIds="
                                  + ",".join(f"0x{e:04X}" for e in p["oe_serv_eids"]))
                log(f"  [{idx:>3}] {name:<26} -> {ta_typ}"
                    + (f"   ({' | '.join(detail)})" if detail else ""))

    except KeyboardInterrupt:
        log("\nAbbruch durch Benutzer.")
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        safe_close_spi(radio)
        log("SPI geschlossen (GPIO-Pins unveraendert).")

    # ===== Ziele aufloesen und Ergebnis-Tabelle bauen =====
    def resolve_eids(eids: list[int]) -> str:
        parts = []
        for e in eids:
            if e in eid_map:
                ch, lbl = eid_map[e]
                parts.append(f"{ch} ({lbl})" if lbl else ch)
            else:
                parts.append(f"EId 0x{e:04X}")
        return ", ".join(dict.fromkeys(parts)) if parts else "-"

    results: list[tuple] = []
    for idx in sorted(probes):
        p   = probes[idx]
        row = p["row"]
        name     = row["name"]
        ensemble = row["ensemble"] or ""

        if p.get("na"):
            results.append((idx, name, ensemble, TA_NA, "-", "-", "-"))
            continue

        ta_typ = classify(p["local_ta"], p["oe_ann"])

        ziel_ens = "-"
        ziel_srv = "-"
        if ta_typ == TA_DIRECT:
            ziel_ens = f"{row['channel']} ({ensemble})" if ensemble else row["channel"]
            # Cluster-Geschwister als Kandidaten (echter Traeger -> Laufzeit/0xB6)
            cand: list[str] = []
            cmap = cluster_by_channel.get(row["channel"], {})
            for cid in p["cluster_ids"]:
                for other_idx in cmap.get(cid, []):
                    if other_idx != idx and other_idx in probes:
                        cand.append(probes[other_idx]["row"]["name"])
            ziel_srv = "~" + ", ~".join(dict.fromkeys(cand)) if cand else "(Laufzeit)"
        elif ta_typ == TA_OES:
            ziel_ens = resolve_eids(p["oe_ann_eids"])
            ziel_srv = "(Laufzeit)"

        oe_serv = resolve_eids(p["oe_serv_eids"]) if p["oe_serv_eids"] else "-"
        results.append((idx, name, ensemble, ta_typ, ziel_ens, ziel_srv, oe_serv))

    if not results:
        log("Keine Ergebnisse – Tabelle leer.")
        return 0

    headers = ["Idx", "Name", "Ensemble", "TA_Typ",
               "Ziel-Ensemble", "Ziel-Service", "OE-Serv"]
    print("\n" + "=" * 110)
    try:
        from tabulate import tabulate  # type: ignore
        print(tabulate(results, headers=headers, tablefmt="simple"))
    except ImportError:
        widths = (4, 24, 14, 10, 22, 22, 14)
        sep = " | "
        header = sep.join(c.ljust(w) for c, w in zip(headers, widths))
        print(header)
        print("-" * len(header))
        for r in results:
            print(sep.join(str(v).ljust(w) for v, w in zip(r, widths)))

    print("\nLegende:")
    print("  TA_direct = Durchsage im eigenen Ensemble (FIG 0/18). Traeger -> Laufzeit (0xB6).")
    print("  OES       = Durchsage in anderem Ensemble (FIG 0/25). Ziel-Ensemble offline bekannt.")
    print("  ~Name     = Cluster-Geschwister (Kandidat, nicht der garantierte Traeger).")
    print("  OE-Serv   = Service-Following (FIG 0/24), informativ, keine Durchsage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())