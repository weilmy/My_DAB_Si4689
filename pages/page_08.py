#!/usr/bin/env python3
# ('my_venv_314':venv)
# -*- coding: utf-8 -*-

# Raspberry Pi5
# RaspiAudio DAB HAT mit Skyworks DAB Controller Si4689
# HifiBerry DAC+ADC Pro HW 1.0.1 mit Controller PCM5122 und PCM1863
# 10.1" 1280×800 HDMI IPS LCD Monitor Display for Raspberry Pi 4B
# Stereo Amplifier Adafruit MAX98306

import tkinter as tk

from datetime import datetime, timezone, timedelta
import threading
import requests
import xml.etree.ElementTree as ET
import re

from .base_page import BasePage
from assets.epg_config import API_TOKEN, API_URL

# ── Farben & Schriften ────────────────────────────────────────────────────────
BG          = "#3288FF"
FG          = "white"
FG_GRAU     = "#C0D8FF"
FG_ROT      = "#FF4444"
FG_GELB     = "#FFE066"
FONT_TITEL  = ("Helvetica", 20, "bold")
FONT_NORMAL = ("Arial", 12)
FONT_KLEIN  = ("Arial", 10)
FONT_ORT    = ("Arial", 12, "bold")
FONT_SACHE  = ("Arial", 11)
FONT_DATUM  = ("Arial", 10)
ROW_BG_1    = "#2070DD"
ROW_BG_2    = "#1A5FCC"
ROW_SEP     = "#4AA0FF"


class Page08(BasePage):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)
        self.controller = controller
        self.app = controller
        self._api = api_manager(controller)

        self.configure(bg=BG)
        self.bg_color = BG

        self._meldungen   = []      # alle geladenen Meldungen
        self._seite       = 0       # aktuelle Anzeigeseite (0-basiert)
        self._pro_seite   = 5       # Meldungen pro Seite
        self._laden_aktiv = False   # Busy-Flag
        self._after_id    = None    # für auto-refresh

        self.build_gui()

    # ── GUI aufbauen ──────────────────────────────────────────────────────────
    def build_gui(self):
        self.load_images()
        self.create_frames()

    def load_images(self):
        cfg     = self.app.config_data
        img_mgr = self.app.image_manager
        self.traffic_stau      = img_mgr.load_image('main_stau',      cfg["stau"],      resize=(28, 28))
        self.traffic_baustelle = img_mgr.load_image('main_baustelle', cfg["baustelle"], resize=(28, 28))
        self.traffic_unfall    = img_mgr.load_image('main_unfall',    cfg["unfall"],    resize=(28, 28))
        self.command_vorher    = img_mgr.load_image('main_vorher',    cfg["vorher"],    resize=(28, 28))
        self.command_naechste  = img_mgr.load_image('main_naechste',  cfg["naechste"],  resize=(28, 28))

    def _get_bild(self, key):
        return {
            "stau":      self.traffic_stau,
            "baustelle": self.traffic_baustelle,
            "unfall":    self.traffic_unfall,
        }.get(key, self.traffic_stau)

    def create_frames(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Titelzeile ────────────────────────────────────────────────────────
        frm_titel = tk.Frame(self, bg=BG)
        frm_titel.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        frm_titel.columnconfigure(0, weight=1)

        tk.Label(frm_titel,
                 text="ASTRA VERKEHRSMELDUNGEN",
                 bg=BG, fg=FG,
                 font=FONT_TITEL,
                 anchor="center"
                 ).grid(row=0, column=0, sticky="ew")

        self.lbl_stand = tk.Label(frm_titel,
                                  text="Stand: —",
                                  bg=BG, fg=FG_GRAU,
                                  font=FONT_KLEIN,
                                  anchor="e")
        self.lbl_stand.grid(row=0, column=1, sticky="e", padx=(10, 40))

        # ── Untertitel / Region ───────────────────────────────────────────────
        tk.Label(self,
                 text="Region Bern  ·  A1 Bern–Härkingen  ·  A2 Härkingen–Basel  ·  De-A5  ·  Fr-A35 Basel–Strassburg",
                 bg=BG, fg=FG_GRAU,
                 font=FONT_KLEIN,
                 anchor="w"
                 ).grid(row=1, column=0, sticky="w", padx=110, pady=(0, 4))

        # ── Meldungsbereich ───────────────────────────────────────────────────
        self.frm_meldungen = tk.Frame(self, bg=BG)
        self.frm_meldungen.grid(row=2, column=0, sticky="nsew", padx=8)
        self.frm_meldungen.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        # Platzhalter-Zeilen (werden in _zeige_seite() befüllt)
        self._zeilen = []
        for i in range(self._pro_seite):
            bg = ROW_BG_1 if i % 2 == 0 else ROW_BG_2
            # Trennlinie oben
            sep = tk.Frame(self.frm_meldungen, bg=ROW_SEP, height=1)
            sep.grid(row=i * 3, column=0, columnspan=3, sticky="ew")

            # Icon-Spalte
            lbl_icon = tk.Label(self.frm_meldungen,
                                bg=bg, width=40, anchor="center")
            lbl_icon.grid(row=i * 3 + 1, column=0,
                          rowspan=2, sticky="nsew", padx=(2, 4), pady=2)

            # Ort + Sachlage (Zeile 1)
            lbl_ort = tk.Label(self.frm_meldungen,
                               bg=bg, fg=FG,
                               font=FONT_ORT,
                               anchor="w",
                               wraplength=580)
            lbl_ort.grid(row=i * 3 + 1, column=1, sticky="ew", padx=2)

            # Datum (Zeile 1, rechts)
            lbl_datum = tk.Label(self.frm_meldungen,
                                 bg=bg, fg=FG_GRAU,
                                 font=FONT_DATUM,
                                 anchor="e", width=12)
            lbl_datum.grid(row=i * 3 + 1, column=2, sticky="e", padx=(0, 2))

            # Detail (Zeile 2)
            lbl_detail = tk.Label(self.frm_meldungen,
                                  bg=bg, fg=FG_GRAU,
                                  font=FONT_SACHE,
                                  anchor="w")
            lbl_detail.grid(row=i * 3 + 2, column=1,
                            columnspan=2, sticky="ew", padx=2, pady=(0, 3))

            self._zeilen.append((lbl_icon, lbl_ort, lbl_datum, lbl_detail, bg))

        # Abschluss-Trennlinie
        tk.Frame(self.frm_meldungen, bg=ROW_SEP, height=1).grid(
            row=self._pro_seite * 3, column=0, columnspan=3, sticky="ew")

        # ── Statuszeile / Buttons ─────────────────────────────────────────────
        frm_unten = tk.Frame(self, bg=BG)
        frm_unten.grid(row=3, column=0, sticky="ew", padx=8, pady=6)
        frm_unten.columnconfigure(1, weight=1)

        # ◀ Vorherige Seite
        self.btn_vorher = tk.Button(frm_unten,
                                    image=self.command_vorher,
                                    bg=BG, activebackground=BG,
                                    bd=0, relief="flat", cursor="hand2",
                                    command=self._seite_zurueck)
        self.btn_vorher.grid(row=0, column=0, padx=(200, 8))

        # Aktualisieren
        self.btn_refresh = tk.Button(frm_unten,
                                     text="Aktualisieren",
                                     bg="#1A5FCC", fg=FG,
                                     activebackground="#0040AA",
                                     font=FONT_NORMAL,
                                     bd=0, relief="flat",
                                     padx=12, pady=4,
                                     cursor="hand2",
                                     command=self._starte_laden)
        self.btn_refresh.grid(row=0, column=1)

        # ▶ Nächste Seite
        self.btn_naechste = tk.Button(frm_unten,
                                      image=self.command_naechste,
                                      bg=BG, activebackground=BG,
                                      bd=0, relief="flat", cursor="hand2",
                                      command=self._seite_vor)
        self.btn_naechste.grid(row=0, column=2, padx=(8, 200))

        # Stand-Label (Mitte)
        self.lbl_seite = tk.Label(frm_unten,
                                  text="",
                                  bg=BG, fg=FG_GRAU,
                                  font=FONT_KLEIN)
        self.lbl_seite.grid(row=0, column=3, padx=8)

    # ── Seitensteuerung ───────────────────────────────────────────────────────
    def _seite_vor(self):
        max_seite = max(0, (len(self._meldungen) - 1) // self._pro_seite)
        if self._seite < max_seite:
            self._seite += 1
            self._zeige_seite()

    def _seite_zurueck(self):
        if self._seite > 0:
            self._seite -= 1
            self._zeige_seite()

    def _zeige_seite(self):
        start = self._seite * self._pro_seite
        sichtbar = self._meldungen[start: start + self._pro_seite]

        for i, (lbl_icon, lbl_ort, lbl_datum, lbl_detail, bg) in enumerate(self._zeilen):
            if i < len(sichtbar):
                m = sichtbar[i]
                lbl_icon.config(image=self._get_bild(m["bild_key"]))
                lbl_ort.config(
                    text=f"{m['ort']}   {m['sachlage']}" if m["sachlage"] else m["ort"])
                lbl_datum.config(text=m["start"])
                lbl_detail.config(text=m["detail"])
                # sichtbar
                lbl_icon.grid()
                lbl_ort.grid()
                lbl_datum.grid()
                lbl_detail.grid()
            else:
                # leere Zeile ausblenden
                lbl_icon.config(image="", text="")
                lbl_ort.config(text="")
                lbl_datum.config(text="")
                lbl_detail.config(text="")

        max_seite = max(1, (len(self._meldungen) - 1) // self._pro_seite + 1)
        self.lbl_seite.config(
            text=f"Seite {self._seite + 1} / {max_seite}   ({len(self._meldungen)} Meldungen)")

        # Buttons en/disable
        self.btn_vorher.config(state="normal" if self._seite > 0 else "disabled")
        self.btn_naechste.config(
            state="normal" if self._seite < max_seite - 1 else "disabled")

    # ── Daten laden (Thread) ──────────────────────────────────────────────────
    def _starte_laden(self):
        if self._laden_aktiv:
            return
        self._laden_aktiv = True
        self.btn_refresh.config(text="Lädt …", state="disabled")
        self.lbl_stand.config(text="Abruf läuft …", fg=FG_GELB)
        threading.Thread(target=self._lade_thread, daemon=True).start()

    def _lade_thread(self):
        try:
            meldungen = self._api.lade_meldungen()
            self.after(0, lambda m=meldungen: self._laden_fertig(m, None))
        except Exception as e:
            self.after(0, lambda err=str(e): self._laden_fertig(None, err))

    def _laden_fertig(self, meldungen, fehler):
        self._laden_aktiv = False
        self.btn_refresh.config(text="Aktualisieren", state="normal")
        jetzt = datetime.now().strftime("%d.%m. %H:%M")

        if fehler:
            self.lbl_stand.config(
                text=f"Fehler: {fehler[:60]}", fg=FG_ROT)
        else:
            self._meldungen = meldungen
            self._seite = 0
            self._zeige_seite()
            self.lbl_stand.config(
                text=f"Stand: {jetzt}", fg=FG_GRAU)

        # Auto-Refresh planen
        self._plane_autorefresh()

    def _plane_autorefresh(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(api_manager.AUTO_REFRESH_MIN * 60 * 1000, self._starte_laden)

    # ── BasePage-Hooks ────────────────────────────────────────────────────────
    # activate() wird NICHT überschrieben — BasePage.activate() steuert
    # die Erst/Wiederaktivierung und ruft on_first_activate() / on_reactivate().

    # App-Start         → __init__() + build_gui()    → kein API-Aufruf
    # 1. Seitenaufruf   → BasePage.activate()
    #                     → on_first_activate()        → _starte_laden()
    # Weiterer Aufruf   → BasePage.activate()
    #                     → on_reactivate()            → laden nur wenn _meldungen leer
    # Auto-Refresh      → alle 5 Min via after()       → _starte_laden()

    def on_first_activate(self):
        """Beim allerersten Seitenaufruf: Daten von API laden."""
        self._starte_laden()

    def on_reactivate(self):
        """Bei erneutem Seitenaufruf: nur laden wenn noch keine Daten vorhanden."""
        if not self._meldungen:
            self.lbl_seite.config(text="Keine Meldungen")
            self.btn_vorher.config(state="disabled")
            self.btn_naechste.config(state="disabled")
            return
        max_seite = (len(self._meldungen) - 1) // self._pro_seite + 1

    def shutdown(self):
        """App-Beendigung: Auto-Refresh-Timer stoppen."""
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None



class api_manager:
    def __init__(self, app):
        self.app = app

    # ── API Daten ────────────────────────────────────────────────────────
    FILTER_TAGE      = 7
    AUTO_REFRESH_MIN = 5          # Automatische Aktualisierung alle 5 Minuten

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

    TYP_MAP = {
        "AbnormalTraffic":    "stau",
        "Accident":           "unfall",
        "MaintenanceWorks":   "baustelle",
        "ConstructionWorks":  "baustelle",
        "GeneralObstruction": "stau",
        "WeatherRelatedRoad": "stau",
    }
    HAUPTTYPEN = set(TYP_MAP.keys())

    SACHLAGE_KURZ = {
        "Verkehrsbehinderung Baustelle": "Baustelle",
        "Verkehrsbehinderung":           "Behinderung",
        "stockender Verkehr":            "Stockend",
        "Stau":                          "Stau",
        "Gefahr":                        "Gefahr",
    }

    # ── Regionsfilter ─────────────────────────────────────────────────────────
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
    WEITERE_AUTOBAHNEN = {"A5", "A6", "A8", "A12"}
    A35_ABSCHNITT = [
        "Saint-Louis", "Hésingue", "Bartenheim", "Sierentz", "Rixheim",
        "Mulhouse", "Mülhausen", "Wittenheim", "Kingersheim", "Illzach",
        "Colmar", "Kolmar", "Sélestat", "Schlettstadt",
        "Erstein", "Illkirch-Graffenstaden", "Illkirch",
        "Strasbourg", "Straßburg", "Strassburg",
    ]
    BERN_AGGLOMERATION = [
    "Bern", "Berne", "Köniz", "Liebefeld", "Niederwangen",
    "Gümligen", "Worb", "Ostermundigen", "Bolligen", "Ittigen",
    "Zollikofen", "Belp", "Interlaken", "Burgdorf", "Langnau",
    "Münchenbuchsee", "Wankdorf", "Bümpliz", "Brünnen", "Bethlehem", "Neufeld", "Schönbühl", 
    "Muri bei Bern", "Rubigen", "Münsingen", "Kiesen", "Heimberg", "Thun", "Steffisburg",
    ]

    # ── Parser-Hilfsfunktionen ────────────────────────────────────────────────
    def bild_key_aus_text(self, sachlage, kommentar):
        """Überschreibt den XML-basierten Icon-Key anhand von Meldungstext."""
        text = (sachlage + " " + kommentar).lower()
        if "baustelle" in text:
            return "baustelle"
        if "unfall" in text:
            return "unfall"
        if "stau" in text or "behinderung" in text:
            return "stau"
        return None

    def in_region(self, ort: str, kommentar: str) -> bool:
        text = f"{ort} {kommentar}"
        if re.search(r'\bA1\b', text):
            for o in self.A1_ABSCHNITT:
                if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                    return True
            return False
        if re.search(r'\bA2\b', text):
            if re.search(r'\bLuzern\b', text, re.IGNORECASE):
                return False
            for o in self.A2_ABSCHNITT:
                if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                    return True
            return False
        if re.search(r'\bA35\b', text):
            for o in self.A35_ABSCHNITT:
                if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                    return True
            return False
        for ab in self.WEITERE_AUTOBAHNEN:
            if re.search(rf'\b{ab}\b', text):
                return True
        for o in self.BERN_AGGLOMERATION:
            if re.search(rf'\b{re.escape(o)}\b', text, re.IGNORECASE):
                return True
        return False

    def fmt_dt_kurz(self, iso_str):
        if not iso_str:
            return "—"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%d.%m. %H:%M")
        except Exception:
            return iso_str[:10]

    def get_text(self, elem, xpath):
        e = elem.find(xpath, self.NS)
        return (e.text or "").strip() if e is not None else ""

    def parse_kommentar(self, rec):
        for val in rec.iter(f"{{{self.NS['dx']}}}value"):
            t = (val.text or "").strip()
            if t and not t.startswith("(("):
                return t
        return ""

    def bereinigen(self, ort):
        ort = re.sub(r'#[-\d.]+;[-\d.]+', '', ort)
        ort = re.sub(r',?\s*Switzerland\b', '', ort)
        ort = re.sub(r'^Aufgehoben:\s*', '', ort)
        return ort.strip()

    def parse_ort(self, kommentar):
        m = re.search(r'Freigegeben:\s*(.+?)(?:\s{2,}|Sachlage:|$)', kommentar)
        if m:
            return self.bereinigen(m.group(1))
        m = re.search(r'(.+?)\s+Ortschaft\s+', kommentar)
        if m:
            return self.bereinigen(m.group(1))
        m = re.search(r'^(.+?)\s+Sachlage:', kommentar)
        if m:
            return self.bereinigen(m.group(1))
        # Fallback für Unfälle: plain Adresse "Strasse, PLZ Ort" → letztes Komma-Teil
        bereinigt = self.bereinigen(kommentar)
        if bereinigt and ',' in bereinigt:
            teile = [t.strip() for t in bereinigt.split(',') if t.strip()]
            letzter = re.sub(r'^\d{4,5}\s+', '', teile[-1])
            return letzter if letzter else bereinigt
        return bereinigt

    def parse_sachlage_kurz(self, kommentar):
        m = re.search(r'Sachlage:\s*(.+?)(?:\s{2,}|Ursache:|Dauer:|Verkehrsführung:|Länge|Zusatz|$)',
                      kommentar)
        if not m:
            return ""
        sachlage = m.group(1).strip()
        for lang, kurz in self.SACHLAGE_KURZ.items():
            if sachlage.startswith(lang):
                return kurz
        return sachlage[:30]

    def parse_verkehrsfuehrung(self, kommentar):
        """Extrahiert Verkehrsführung für Zeile 2 der Meldung."""
        m = re.search(r'Verkehrsführung:\s*(.+?)(?:\s{2,}|Zusatz|Dauer:|Länge \[|$)', kommentar)
        if m:
            vf = m.group(1).strip()
            vf = vf.replace("Fahrbahnverengung auf einen Fahrstreifen verengt", "1 Spur")
            vf = vf.replace("Fahrbahnverengung auf zwei Fahrstreifen verengt", "2 Spuren")
            vf = vf.replace("Fahrbahnverengung", "Verengung")
            vf = vf.replace("Verkehr wird über die Gegenfahrbahn geleitet", "Gegenfahrbahn")
            vf = vf.replace("veränderte Verkehrsführung", "verändert")
            vf = vf.replace("Pannenstreifen gesperrt", "Pannenstr. gesperrt")
            vf = vf.replace("Pannenstreifen blockiert", "Pannenstr. blockiert")
            vf = vf.replace("wechselseitige Verkehrsführung", "wechselseitig")
            lm = re.search(r'Länge \[km\]\s*([\d.]+)', kommentar)
            if lm:
                vf += f"  {lm.group(1)} km"
            return vf[:55]
        lm = re.search(r'Länge \[km\]\s*([\d.]+)', kommentar)
        if lm:
            return f"{lm.group(1)} km"
        return ""

    def ist_aufgehoben(self, kommentar, ort):
        return kommentar.startswith("Aufgehoben:") or ort.startswith("Aufgehoben:")

    def ist_im_zeitfenster(self, rec):
        t = self.get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallStartTime")
        if not t:
            return True
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            grenze = datetime.now(timezone.utc) - timedelta(days=self.FILTER_TAGE)
            return dt >= grenze
        except Exception:
            return True

    # ── Daten holen & parsen ──────────────────────────────────────────────────
    def lade_meldungen(self):
        """Holt Daten von API und gibt Liste von Dicts zurück. Wirft Exception bei Fehler."""
        resp = requests.post(API_URL, headers=self.HEADERS,
                             data=self.SOAP_BODY.encode("utf-8"),
                             timeout=30, allow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        fault = root.find(".//env:Fault", self.NS)
        if fault is not None:
            raise RuntimeError(f"SOAP-Fault: {fault.findtext('faultstring', '')}")

        gesehene = set()
        ergebnis = []
        for sit in root.findall(".//dx:situation", self.NS):
            sit_id = sit.get("id", "?")
            if sit_id in gesehene:
                continue
            for rec in sit.findall("dx:situationRecord", self.NS):
                xsi_type = rec.get(f"{{{self.NS['xsi']}}}type", "")
                typ = xsi_type.replace("dx223:", "")
                if typ not in self.HAUPTTYPEN:
                    continue
                if not self.ist_im_zeitfenster(rec):
                    continue

                kommentar = self.parse_kommentar(rec)
                ort       = self.parse_ort(kommentar)

                if self.ist_aufgehoben(kommentar, ort):
                    continue

                if not self.in_region(ort, kommentar):
                    continue

                t_start  = self.fmt_dt_kurz(
                    self.get_text(rec, "dx:validity/dx:validityTimeSpecification/dx:overallStartTime"))
                sachlage = self.parse_sachlage_kurz(kommentar)

                # Text darf XML-Typ nur upgraden (unfall > baustelle > stau), nie downgraden
                _RANG     = {"stau": 0, "baustelle": 1, "unfall": 2}
                bild_xml  = self.TYP_MAP.get(typ, "stau")
                bild_text = self.bild_key_aus_text(sachlage, kommentar)
                bild = bild_text if (bild_text and _RANG[bild_text] >= _RANG[bild_xml]) else bild_xml

                ergebnis.append({
                    "bild_key": bild,
                    "ort":      ort or "—",
                    "sachlage": sachlage,
                    "detail":   self.parse_verkehrsfuehrung(kommentar),
                    "start":    t_start,
                })
                gesehene.add(sit_id)
                break

        # Sortierung: neueste zuerst
        ergebnis.sort(key=lambda x: x["start"], reverse=True)
        return ergebnis


__all__ = ["Page08"]