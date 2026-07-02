#!/usr/bin/env python3
"""
Standalone-Test: ASTRA Verkehrsmeldungen via opentransportdata.swiss
Projekt: My_DAB_Si4689
Ausführen: ~/my_venv_314/bin/python3 test_traffic.py
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import re
from collections import Counter

# ── Konfiguration ─────────────────────────────────────────────────────────────
API_TOKEN = "eyJvcmciOiI2NDA2NTFhNTIyZmEwNTAwMDEyOWJiZTEiLCJpZCI6IjM2OGUzM2NiZjJmNDRlNDA5NzdlZmEzYTI1OTE0Mzg0IiwiaCI6Im11cm11cjEyOCJ9"
API_URL   = "https://api.opentransportdata.swiss/TDP/Soap_Datex2/TrafficSituations/Pull"

# Zeitfenster: Meldungen der letzten N Tage + zukünftige
FILTER_TAGE = 7   # für Test grosszügig; in GUI später auf 7 reduzieren

NS = {
    "env": "http://schemas.xmlsoap.org/soap/envelope/",
    "dx":  "http://datex2.eu/schema/2/2_0",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

SOAP_BODY = '''<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
                   xmlns:dx223="http://datex2.eu/schema/2/2_0">
  <SOAP-ENV:Body>
    <dx223:d2LogicalModel modelBaseVersion="2">
      <dx223:exchange>
        <dx223:supplierIdentification>
          <dx223:country>ch</dx223:country>
          <dx223:nationalIdentifier>DAB_Radio_Pi</dx223:nationalIdentifier>
        </dx223:supplierIdentification>
      </dx223:exchange>
    </dx223:d2LogicalModel>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>'''

HEADERS = {
    "Authorization":   f"Bearer {API_TOKEN}",
    "SOAPAction":      "http://opentransportdata.swiss/TDP/Soap_Datex2/Pull/v1/pullTrafficMessages",
    "Content-Type":    "text/xml; charset=utf-8",
    "User-Agent":      "DAB_Radio_Pi/1.0",
    "Accept-Encoding": "gzip, deflate",
}

# ── Typen ─────────────────────────────────────────────────────────────────────
TYP_MAP = {
    "AbnormalTraffic":    ("🚗", "Verkehr"),
    "Accident":           ("🚨", "Unfall"),
    "MaintenanceWorks":   ("🔧", "Unterhalt"),
    "ConstructionWorks":  ("🏗️", "Baustelle"),
    "GeneralObstruction": ("⚠️", "Hindernis"),
    "WeatherRelatedRoad": ("❄️", "Witterung"),
}
HAUPTTYPEN = set(TYP_MAP.keys())

# ── Regionsfilter ─────────────────────────────────────────────────────────────

# A1 Bern → Härkingen (relevante Anschlüsse und Verzweigungen)
A1_ABSCHNITT = [
    "Bern-Forsthaus", "Bern-Bethlehem", "Bern-Brünnen", "Bern-Bümpliz",
    "Bern-Neufeld", "Weyermannshaus", "Wankdorf",
    "Grauholz", "Schönbühl", "Kirchberg",
    "Wangen an der Aare", "Niederbipp", "Härkingen",
    "Oensingen", "Rothrist", "Wiggertal",
    "Luterbach", "Kriegstetten", "Gunzgen", "Lindenrain",
]

# A2 Härkingen → Basel
A2_ABSCHNITT = [
    "Härkingen", "Egerkingen",
    "Belchen", "Eptingen", "Läufelfingen",
    "Liestal", "Pratteln", "Augst", "Arisdorf",
    "Basel-Badischer", "Basel-Ost", "Hagnau",
]
# NEU: explizit ausschliessen wenn "Luzern" im Text vorkommt

# Übrige Autobahnen (ganzer Abschnitt relevant)
WEITERE_AUTOBAHNEN = {"A5", "A6", "A8", "A12"}

# Kantons-/Gemeindestrassen Bern-Agglomeration
BERN_AGGLOMERATION = [
    "Bern", "Berne", "Köniz", "Liebefeld", "Niederwangen",
    "Gümligen", "Worb", "Ostermundigen", "Bolligen", "Ittigen",
    "Zollikofen", "Belp", "Thun", "Spiez", "Steffisburg",
    "Interlaken", "Wilderswil", "Burgdorf", "Langnau",
    "Münchenbuchsee", "Schüpfen", "Wankdorf", "Bümpliz",
    "Brünnen", "Bethlehem", "Neufeld", "Schönbühl", "Hünibach",
]


def in_region(ort: str, kommentar: str) -> bool:
    text = f"{ort} {kommentar}"

    # A1: nur Bern–Härkingen-Abschnitt
    if re.search(r'\bA1\b', text):
        for o in A1_ABSCHNITT:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True
        return False   # A1 ohne passenden Anschluss → ablehnen

    # A2: nur Härkingen–Basel, NICHT Luzerner Seite
    if re.search(r'\bA2\b', text):
        if re.search(r'\bLuzern\b', text, re.IGNORECASE):
            return False   # A2 Luzern-Seite → ablehnen
        for o in A2_ABSCHNITT:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True
        return False

    # A5, A6, A8, A12: ganzer Abschnitt
    for ab in WEITERE_AUTOBAHNEN:
        if re.search(rf'\b{ab}\b', text):
            return True

    # Bern-Agglomeration Kantonsstrassen
    for o in BERN_AGGLOMERATION:
        if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
            return True

    return False

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────
def fmt_dt(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso_str[:16]

def get_text(elem, xpath):
    e = elem.find(xpath, NS)
    return (e.text or "").strip() if e is not None else ""

def parse_kommentar(rec):
    for val in rec.iter(f"{{{NS['dx']}}}value"):
        t = (val.text or "").strip()
        if t and not t.startswith("(("):
            return t
    return ""

def bereinigen(ort):
    """GPS-Anhang (#lon;lat) und Switzerland-Suffix entfernen."""
    ort = re.sub(r'#[-\d.]+;[-\d.]+', '', ort)
    ort = re.sub(r',?\s*Switzerland\b', '', ort)
    return ort.strip()

def parse_ort(kommentar):
    m = re.search(r'Freigegeben:\s*(.+?)(?:\s{2,}|Sachlage:|$)', kommentar)
    if m:
        return bereinigen(m.group(1))
    m = re.search(r'(.+?)\s+Ortschaft\s+', kommentar)
    if m:
        return bereinigen(m.group(1))
    m = re.search(r'^(.+?)\s+Sachlage:', kommentar)
    if m:
        return bereinigen(m.group(1))
    return ""

def parse_sachlage(kommentar):
    m = re.search(r'Sachlage:\s*(.+?)(?:\s{2,}|Ursache:|Dauer:|$)', kommentar)
    return m.group(1).strip() if m else ""

def ist_im_zeitfenster(rec):
    t = get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallStartTime")
    if not t:
        return True
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        grenze = datetime.now(timezone.utc) - timedelta(days=FILTER_TAGE)
        return dt >= grenze
    except Exception:
        return True

# ── API-Zugriff & Parsing ─────────────────────────────────────────────────────
def fetch_raw():
    resp = requests.post(API_URL, headers=HEADERS,
                         data=SOAP_BODY.encode("utf-8"),
                         timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.content

def parse_xml(raw):
    root = ET.fromstring(raw)
    fault = root.find(".//env:Fault", NS)
    if fault is not None:
        raise RuntimeError(f"SOAP-Fault: {fault.findtext('faultstring', '')}")
    return root

def extrahiere_meldungen(root, nur_zeitfenster=True, nur_region=True):
    """
    Pro Situation: nur den ersten HAUPTTYP-Record verwenden (kein Duplikat).
    Gibt sortierte Liste zurück (neueste zuerst).
    """
    gesehene_sits = set()
    ergebnis = []

    for sit in root.findall(".//dx:situation", NS):
        sit_id = sit.get("id", "?")
        if sit_id in gesehene_sits:
            continue

        for rec in sit.findall("dx:situationRecord", NS):
            xsi_type = rec.get(f"{{{NS['xsi']}}}type", "")
            typ = xsi_type.replace("dx223:", "")
            if typ not in HAUPTTYPEN:
                continue

            if nur_zeitfenster and not ist_im_zeitfenster(rec):
                break  # nächste Situation

            kommentar = parse_kommentar(rec)
            ort       = parse_ort(kommentar)
            sachlage  = parse_sachlage(kommentar)

            if nur_region and not in_region(ort, kommentar):
                break  # nächste Situation

            t_start_raw = get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallStartTime")
            t_end_raw   = get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallEndTime")
            symbol, label = TYP_MAP.get(typ, ("ℹ️", typ))

            ergebnis.append({
                "id":        sit_id,
                "typ":       typ,
                "label":     label,
                "symbol":    symbol,
                "ort":       ort or "—",
                "sachlage":  sachlage,
                "start":     fmt_dt(t_start_raw),
                "end":       fmt_dt(t_end_raw),
                "start_raw": t_start_raw,
                "info":      kommentar[:250],
            })
            gesehene_sits.add(sit_id)
            break  # nur erster Record pro Situation

    # Sortierung: zukünftige zuerst, dann neueste
    def sort_key(m):
        try:
            return datetime.fromisoformat(m["start_raw"].replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    ergebnis.sort(key=sort_key, reverse=True)
    return ergebnis

# ── Ausgabe ───────────────────────────────────────────────────────────────────
def main():
    print(f"{'='*68}")
    print(f"  ASTRA Verkehrsmeldungen — Region Bern/CH-Autobahnen/A5")
    print(f"  Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"{'='*68}")
    print("  Abfrage läuft …")

    raw  = fetch_raw()
    print(f"  {len(raw)//1024} KB empfangen")
    root = parse_xml(raw)

    alle_sits = root.findall(".//dx:situation", NS)
    print(f"  {len(alle_sits)} Situationen total im XML")

    # Alle Meldungen im Zeitfenster, ohne Regionsfilter — für Statistik
    alle_zeitfenster = extrahiere_meldungen(root, nur_zeitfenster=True, nur_region=False)
    print(f"  {len(alle_zeitfenster)} Meldungen im Zeitfenster ({FILTER_TAGE} Tage)")

    # Regionsgefiltert
    regional = extrahiere_meldungen(root, nur_zeitfenster=True, nur_region=True)
    print(f"  {len(regional)} Meldungen in Ihrer Region\n")

    if not regional:
        print("  → Keine regionalen Meldungen gefunden.")
        print("    Tipp: FILTER_TAGE erhöhen oder REGION_MUSTER erweitern.")
    else:
        for m in regional:
            print(f"  {m['symbol']} {m['label']:<12} {m['ort']}")
            if m['sachlage']:
                print(f"     Sachlage : {m['sachlage']}")
            print(f"     Von/Bis  : {m['start']}  →  {m['end']}")
            print()

    print(f"{'='*68}")
    typen = Counter(m['label'] for m in alle_zeitfenster)
    print("  Typen im Zeitfenster (alle Regionen):")
    for label, n in typen.most_common():
        print(f"    {label:<16}: {n}")

if __name__ == "__main__":
    main()