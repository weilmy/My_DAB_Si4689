#!/usr/bin/env python3
# ('my_venv_314':venv)
# -*- coding: utf-8 -*-
#
# Standalone-Debug-Skript: ASTRA Datex2-API abrufen und alle Meldungen ausgeben.
# Ausführen aus dem Projektverzeichnis:
#   python scripts/astra_debug.py
#   python scripts/astra_debug.py --alle      (alle Typen, nicht nur Unfälle)
#   python scripts/astra_debug.py --xml       (rohe XML zusätzlich in "/home/weilmy/astra_raw.xml" speichern)

import sys
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/home/weilmy/My_DAB_Si4689")
from assets.epg_config import API_TOKEN, API_URL

# ── Optionen ──────────────────────────────────────────────────────────────────
NUR_UNFAELLE = "--alle" not in sys.argv
SPEICHERE_XML = "--xml"  in sys.argv
XML_PFAD = "/home/weilmy/astra_raw.xml"

# ── Konstanten (identisch mit page_08.py) ────────────────────────────────────
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

HAUPTTYPEN = {
    "AbnormalTraffic", "Accident", "MaintenanceWorks",
    "ConstructionWorks", "GeneralObstruction", "WeatherRelatedRoad",
}

FILTER_TAGE = 7

A1_ABSCHNITT = [
    "Bern-Forsthaus", "Bern-Bethlehem", "Bern-Brünnen", "Bern-Bümpliz",
    "Bern-Neufeld", "Weyermannshaus", "Wankdorf",
    "Grauholz", "Schönbühl", "Kirchberg",
    "Wangen an der Aare", "Niederbipp", "Härkingen",
    "Oensingen", "Rothrist", "Wiggertal",
    "Luterbach", "Kriegstetten", "Gunzgen", "Lindenrain",
]
A2_ABSCHNITT = [
    "Härkingen", "Egerkingen",
    "Belchen", "Eptingen", "Läufelfingen",
    "Liestal", "Pratteln", "Augst", "Arisdorf",
    "Basel-Badischer", "Basel-Ost", "Hagnau",
]
A35_ABSCHNITT = [
    "Saint-Louis", "Hésingue", "Bartenheim", "Sierentz", "Rixheim",
    "Mulhouse", "Mülhausen", "Wittenheim", "Kingersheim", "Illzach",
    "Colmar", "Kolmar", "Sélestat", "Schlettstadt",
    "Erstein", "Illkirch-Graffenstaden", "Illkirch",
    "Strasbourg", "Straßburg", "Strassburg",
]
WEITERE_AUTOBAHNEN = {"A5", "A6", "A8", "A12"}
BERN_AGGLOMERATION = [
    "Bern", "Berne", "Köniz", "Liebefeld", "Niederwangen",
    "Gümligen", "Worb", "Ostermundigen", "Bolligen", "Ittigen",
    "Zollikofen", "Belp", "Interlaken", "Burgdorf", "Langnau",
    "Münchenbuchsee", "Wankdorf", "Bümpliz", "Brünnen", "Bethlehem", "Neufeld", "Schönbühl", 
    "Muri bei Bern", "Rubigen", "Münsingen", "Kiesen", "Heimberg", "Thun", "Steffisburg",
]

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────
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
    ort = re.sub(r'#[-\d.]+;[-\d.]+', '', ort)
    ort = re.sub(r',?\s*Switzerland\b', '', ort)
    ort = re.sub(r'^Aufgehoben:\s*', '', ort)
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
    # Fallback für Unfälle: plain Adresse "Strasse, PLZ Ort" → letztes Komma-Teil
    b = bereinigen(kommentar)
    if b and ',' in b:
        teile = [t.strip() for t in b.split(',') if t.strip()]
        letzter = re.sub(r'^\d{4,5}\s+', '', teile[-1])
        return letzter if letzter else b
    return b

def in_region(ort, kommentar):
    text = f"{ort} {kommentar}"
    if re.search(r'\bA1\b', text):
        for o in A1_ABSCHNITT:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True, "A1"
        return False, "A1 (Ort nicht in Liste)"
    if re.search(r'\bA2\b', text):
        if re.search(r'\bLuzern\b', text, re.IGNORECASE):
            return False, "A2 (Luzern-Ausschluss)"
        for o in A2_ABSCHNITT:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True, "A2"
        return False, "A2 (Ort nicht in Liste)"
    if re.search(r'\bA35\b', text):
        for o in A35_ABSCHNITT:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True, "A35"
        return False, "A35 (Ort nicht in Liste)"
    for ab in WEITERE_AUTOBAHNEN:
        if re.search(rf'\b{ab}\b', text):
            return True, ab
    for o in BERN_AGGLOMERATION:
        if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
            return True, f"Agglomeration ({o})"
    return False, "kein Treffer"

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

def ist_aufgehoben(kommentar, ort):
    return kommentar.startswith("Aufgehoben:") or ort.startswith("Aufgehoben:")

# ── API abrufen ───────────────────────────────────────────────────────────────
print(f"Abruf: {API_URL}")
print(f"Zeit:  {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
print()

resp = requests.post(API_URL, headers=HEADERS,
                     data=SOAP_BODY.encode("utf-8"),
                     timeout=30, allow_redirects=True)
resp.raise_for_status()

if SPEICHERE_XML:
    with open(XML_PFAD, "wb") as f:
        f.write(resp.content)
    print(f"XML gespeichert: {XML_PFAD}  ({len(resp.content)} Bytes)\n")

root = ET.fromstring(resp.content)
fault = root.find(".//env:Fault", NS)
if fault is not None:
    sys.exit(f"SOAP-Fault: {fault.findtext('faultstring', '')}")

# ── Alle Situationen durchsuchen ──────────────────────────────────────────────
situations = root.findall(".//dx:situation", NS)
print(f"Situationen total: {len(situations)}")
print("=" * 72)

unfaelle_gesamt   = 0
unfaelle_region   = 0
andere_gesamt     = 0
andere_region     = 0

for sit in situations:
    sit_id = sit.get("id", "?")
    for rec in sit.findall("dx:situationRecord", NS):
        xsi_type = rec.get(f"{{{NS['xsi']}}}type", "")
        typ = xsi_type.replace("dx223:", "")
        if typ not in HAUPTTYPEN:
            continue

        ist_unfall = (typ == "Accident")
        if NUR_UNFAELLE and not ist_unfall:
            continue

        kommentar = parse_kommentar(rec)
        ort       = parse_ort(kommentar)
        zeitok    = ist_im_zeitfenster(rec)
        aufgeh    = ist_aufgehoben(kommentar, ort)
        in_reg, grund = in_region(ort, kommentar)

        if ist_unfall:
            unfaelle_gesamt += 1
            if in_reg:
                unfaelle_region += 1
        else:
            andere_gesamt += 1
            if in_reg:
                andere_region += 1

        # Ausgabe
        t_start = get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallStartTime")
        print(f"TYP      : {typ}")
        print(f"ID       : {sit_id}")
        print(f"Start    : {t_start}")
        print(f"Ort      : {ort or '(leer)'}")
        print(f"Region   : {'✓  ' + grund if in_reg else '✗  ' + grund}")
        print(f"Zeitfenst: {'✓' if zeitok  else '✗ (zu alt)'}")
        print(f"Aufgehob : {'JA' if aufgeh  else 'nein'}")
        # Kommentar auf 200 Zeichen kürzen
        k = kommentar.replace("\n", " ")
        print(f"Kommentar: {k[:200]}{'…' if len(k) > 200 else ''}")
        print("-" * 72)

# ── Zusammenfassung ───────────────────────────────────────────────────────────
print()
if NUR_UNFAELLE:
    print(f"Unfälle (Accident) total:    {unfaelle_gesamt}")
    print(f"Unfälle im Suchgebiet:       {unfaelle_region}")
    print()
    print("Tipp: '--alle' zeigt alle Meldungstypen, '--xml' speichert die rohe XML.")
else:
    print(f"Unfälle total / im Gebiet:   {unfaelle_gesamt} / {unfaelle_region}")
    print(f"Andere  total / im Gebiet:   {andere_gesamt} / {andere_region}")
    print()
    print("Tipp: '--xml' speichert die rohe XML nach /tmp/astra_raw.xml.")
