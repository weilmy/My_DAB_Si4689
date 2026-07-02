#!/usr/bin/env python3
# LASTFM_API_KEY = "d8d29e956ef2cf6c0d3c22cb6fb492b1"
import tkinter as tk
import tkinter.font as tkfont

from collections import OrderedDict
from threading import Lock
import requests
import json
from pathlib import Path
import logging
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

# ---- Helpers für Fancy "Chip" Button ----
def _hex_to_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch*2 for ch in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _rgb_to_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(v))) for v in rgb)

def _mix(c1, c2, t):
    r1,g1,b1 = _hex_to_rgb(c1); r2,g2,b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((r1+(r2-r1)*t, g1+(g2-g1)*t, b1+(b2-b1)*t))

def _luma(c):
    r,g,b = _hex_to_rgb(c); return 0.2126*r + 0.7152*g + 0.0722*b


class ChipButton(tk.Canvas):
    """Canvas-basierter 'Chip/Pill'-Button mit Hover/Press, Disable/Enable, config(text=...)."""
    def __init__(self, parent, text, command, base_bg, **kw):
        super().__init__(parent, highlightthickness=0, bd=0, bg=base_bg, width=1, height=1, **kw)
        self._cmd = command
        self._text = str(text)
        self._enabled = True

        self.font = tkfont.nametofont("TkDefaultFont").copy()
        self.font.configure(size=10, weight="bold")

        self.pad_x, self.pad_y, self.radius = 14, 6, 12
        self.configure(cursor="hand2")

        self.set_base_bg(base_bg, redraw=False)
        self._draw(self.bg_idle)

        # Interaktion
        self.bind("<Enter>", lambda e: self._redraw(self.bg_hover) if self._enabled else None)
        self.bind("<Leave>", lambda e: self._redraw(self.bg_idle)  if self._enabled else None)
        self.bind("<ButtonPress-1>", lambda e: self._redraw(self.bg_press, down=True) if self._enabled else None)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", self._activate)
        self.bind("<Return>", self._activate)
        self.bind("<FocusIn>",  lambda e: self._redraw(self.bg_hover) if self._enabled else None)
        self.bind("<FocusOut>", lambda e: self._redraw(self.bg_idle)  if self._enabled else None)

    # ---- Public API ----
    def set_base_bg(self, base_bg: str, redraw: bool = True):
        base_hex = self._normalize_color(base_bg)
        self.bg_panel   = base_hex
        self.bg_idle    = _mix(base_hex, "#FFFFFF", 0.65)
        self.bg_hover   = _mix(base_hex, "#FFFFFF", 0.80)
        self.bg_press   = _mix(base_hex, "#000000", 0.10)
        self.border_col = _mix(base_hex, "#000000", 0.25)
        self.text_col   = "#111111" if _luma(self.bg_idle) > 140 else "#F5F5F5"
        self.configure(bg=base_hex)
        if redraw:
            self._draw(self.bg_idle)

    def set_enabled(self, enabled: bool = True):
        self._enabled = bool(enabled)
        fill = self.bg_idle if self._enabled else _mix(self.bg_idle, "#000000", 0.20)
        self._redraw(fill)
        self.configure(cursor="hand2" if self._enabled else "arrow")

    # ttk-Kompatibilität: state(["disabled"]) / state(["!disabled"])
    def state(self, ops):
        if not ops: return
        if any(op == "disabled" for op in ops):
            self.set_enabled(False)
        if any(op == "!disabled" for op in ops):
            self.set_enabled(True)

    # Text zur Laufzeit ändern (für deinen Reset-Workflow)
    def set_text(self, text: str):
        self._text = str(text)
        self._redraw(self.bg_idle if self._enabled else _mix(self.bg_idle, "#000000", 0.20))

    # config(text="...") kompatibel machen
    def config(self, **kw):
        if "text" in kw:
            self.set_text(kw.pop("text"))
        return super().config(**kw)
    configure = config

    # ---- Internals ----
    def _normalize_color(self, c: str) -> str:
        try:
            if isinstance(c, str) and c.startswith("#"):
                h = c.lstrip("#")
                if len(h) == 3: h = "".join(ch*2 for ch in h)
                if len(h) == 6: return "#" + h.lower()
            r,g,b = self.winfo_rgb(c)
            return "#{:02x}{:02x}{:02x}".format(r//257, g//257, b//257)
        except Exception:
            return "#cccccc"

    def _txt_w(self, s): return self.font.measure(s)

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
               x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self, fill):
        w = self._txt_w(self._text) + self.pad_x*2
        h = self.font.metrics("linespace") + self.pad_y*2
        self.config(width=w+2, height=h+2)
        self.delete("all")
        self._rounded_rect(2, 2, w, h, self.radius, fill=_mix(self.bg_panel, "#000000", 0.12), outline="")
        self._rounded_rect(1, 1, w-1, h-1, self.radius, fill=fill, outline=self.border_col)
        self.create_text((w//2, h//2), text=self._text, font=self.font, fill=self.text_col)

    def _redraw(self, fill, down=False):
        self._draw(fill)
        self.move("all", 1 if down else 0, 1 if down else 0)

    def _release(self, e):
        if not self._enabled: return
        self._redraw(self.bg_hover)
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._activate(e)

    def _activate(self, e=None):
        if self._enabled and callable(self._cmd):
            self._cmd()


class Cover_url:
    """
    Cover-URL und Genre-Suche mit LRU-Cache. 
    Nutzt mehrere Quellen: iTunes (schnell), MusicBrainz, Last.fm (zuverlässiger). 
    """
    _cover_url_cache: "OrderedDict[str, str]" = OrderedDict()
    _cover_cache_cap = 200
    _genre_cache: "OrderedDict[str, str]" = OrderedDict()
    _genre_cache_cap = 200
    _lock = Lock()

    # MusicBrainz & Last.fm Config
    MB_BASE_URL = "https://musicbrainz.org/ws/2"
    MB_USER_AGENT = "MySmartDABRadio/1.0 (Raspberry Pi; kontakt: bachmann.willy@bluewin.ch)"
    LASTFM_BASE_URL = "http://ws.audioscrobbler.com/2.0/"
    LASTFM_API_KEY = "d8d29e956ef2cf6c0d3c22cb6fb492b1"

    @classmethod
    def _cache_get(cls, cache: "OrderedDict[str, str]", key: str) -> str | None:
        try:
            with cls._lock:
                value = cache. get(key)
                if value is not None:
                    cache.move_to_end(key)
                return value
        except Exception:
            return None

    @classmethod
    def _cache_put(cls, cache: "OrderedDict[str, str]", cap: int, key: str, value: str) -> None:
        try:
            with cls._lock:
                cache[key] = value
                cache.move_to_end(key)
                while len(cache) > cap:
                    cache.popitem(last=False)
        except Exception:
            pass

    # ============================================================
    # COVER-URL FUNKTIONEN
    # ============================================================

    @classmethod
    def fetch_cover_url(cls, artist: str, song: str) -> str | None:
        """
        Holt die Cover-URL von iTunes für Artist + Song.
        Nebenbei wird (falls vorhanden) auch das Genre in den Genre-Cache geschrieben.
        """
        if not artist or not song:
            return None
        key = f"{artist}|{song}".casefold().strip()
        cached = cls._cache_get(cls._cover_url_cache, key)
        if cached:
            return cached
        try:
            url = "https://itunes.apple.com/search"
            params = {"term": f"{artist} {song}", "media": "music", "limit": 1}
            print(f"💽 iTunes: {artist} - {song}")
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data. get("resultCount", 0) == 0:
                print("💽 iTunes: keine Treffer")
                return None
            result = data["results"][0]
            cover = result.get("artworkUrl100")
            if not cover:
                return None
            cover = cover.replace("100x100bb", "600x600bb")
            cls._cache_put(cls._cover_url_cache, cls._cover_cache_cap, key, cover)

            # Genre gleich mit-cachen, falls vorhanden
            genre = result.get("primaryGenreName")
            if genre:
                cls._cache_put(cls._genre_cache, cls._genre_cache_cap, key, genre. strip())
            return cover
        except Exception as e:
            print(f"💽 Fehler bei iTunes-Request: {e}")
            return None

    # ============================================================
    # GENRE-FUNKTIONEN (Multi-Source)
    # ============================================================

    @classmethod
    def fetch_genre_url(cls, artist: str, song: str) -> str | None:
        """
        Liefert das Genre für Artist + Song über mehrere Quellen. 
        Optimierte Fallback-Strategie: MusicBrainz → Last.fm → iTunes
        (Qualität vor Geschwindigkeit, aber mit Cache für Performance)
        """
        if not artist or not song:
            return None
        key = f"{artist}|{song}".casefold(). strip()
        # 1) Cache prüfen (schnellste Option)
        cached = cls._cache_get(cls._genre_cache, key)
        if cached:
            #print(f"  💾 Genre aus Cache: {cached}")
            return cached
        print(f"🔍 Genre-Suche: {artist} - {song}")

        # 2) MusicBrainz ZUERST (beste Qualität, kostenlos, keine Auth)
        genre = cls._fetch_genre_musicbrainz(artist, song)
        if genre:
            print(f"✅ Quelle: MusicBrainz")
            cls._cache_put(cls._genre_cache, cls._genre_cache_cap, key, genre)
            return genre

        # 3) Last.fm ZWEITE (kostenlos mit API-Key, gute Qualität)
        if cls.LASTFM_API_KEY:
            genre = cls._fetch_genre_lastfm(artist, song)
            if genre:
                print(f"✅ Quelle: Last.fm")
                cls._cache_put(cls._genre_cache, cls._genre_cache_cap, key, genre)
                return genre

        # 4) iTunes als letzter Fallback (schnell, aber grobe Genres)
        genre = cls._fetch_genre_itunes(artist, song)
        if genre:
            print(f"✅ Quelle: iTunes")
            cls._cache_put(cls._genre_cache, cls._genre_cache_cap, key, genre)
            return genre

        #print(f"⚠️  Keine Genre gefunden für: {artist} - {song}")
        return None

    @classmethod
    def _fetch_genre_lastfm(cls, artist: str, song: str) -> str | None:
        """
        Last.fm Genre-Abfrage mit 3-fach Fallback-Strategie:
        1. Track-Tags (beste Qualität für populäre Songs)
        2.  Artist-Tags aus track.getInfo (quick fallback)
        3. Artist. getInfo (separate API-Call, aber zuverlässig)
        
        API-Key: https://www.last.fm/api/account/create
        """
        if not cls. LASTFM_API_KEY:
            return None
        try:
            # ============================================================
            # STRATEGIE 1 + 2: track. getInfo abrufen (1 API-Call)
            # ============================================================
            params = {
                "method": "track.getInfo",
                "artist": artist,
                "track": song,
                "api_key": cls.  LASTFM_API_KEY,
                "format": "json"
            }
            #print(f"  🎶 Last.fm: {artist} - {song}")
            resp = requests.get(cls. LASTFM_BASE_URL, params=params, timeout=5)
            resp. raise_for_status()
            data = resp.json()

            if "track" not in data:
                #print(f"    ℹ️  Last.fm: Track nicht gefunden")
                return None
            track = data["track"]
            
            # Strategie 1: Track-Tags versuchen
            if "toptags" in track:
                tags = track["toptags"].  get("tag", [])
                if isinstance(tags, list) and tags:
                    genres = [t["name"] for t in tags[:2]]
                    genre_str = " / ".join(genres)
                    #print(f"    ✅ Last.fm-Track-Tags: {genre_str}")
                    return genre_str
                elif isinstance(tags, dict) and tags.get("name"):
                    #print(f"    ✅ Last.fm-Track-Tag: {tags['name']}")
                    return tags["name"]
            
            # Strategie 2: Artist-Tags aus track.getInfo als Fallback
            if "artist" in track and isinstance(track["artist"], dict):
                artist_data = track["artist"]
                if "tags" in artist_data:
                    artist_tags = artist_data["tags"]. get("tag", [])
                    if isinstance(artist_tags, list) and artist_tags:
                        genres = [t["name"] for t in artist_tags[:2]]
                        genre_str = " / ".join(genres)
                        #print(f"    ✅ Last.fm-Artist-Tags (Fallback): {genre_str}")
                        return genre_str
                    elif isinstance(artist_tags, dict) and artist_tags.get("name"):
                        #print(f"    ✅ Last. fm-Artist-Tag (Fallback): {artist_tags['name']}")
                        return artist_tags["name"]
            
            # ============================================================
            # STRATEGIE 3: artist.getInfo (separate API-Call als letztes Resort)
            # ============================================================
            try:
                artist_params = {
                    "method": "artist.getInfo",
                    "artist": artist,
                    "api_key": cls.LASTFM_API_KEY,
                    "format": "json"
                }
                artist_resp = requests.get(cls.LASTFM_BASE_URL, params=artist_params, timeout=5)
                artist_resp.raise_for_status()
                artist_full_data = artist_resp.json()
                
                if "artist" in artist_full_data:
                    artist_info = artist_full_data["artist"]
                    if "tags" in artist_info:
                        artist_full_tags = artist_info["tags"].get("tag", [])
                        if isinstance(artist_full_tags, list) and artist_full_tags:
                            genres = [t["name"] for t in artist_full_tags[:2]]
                            genre_str = " / ".join(genres)
                            #print(f"    ✅ Last.fm-Artist. getInfo: {genre_str}")
                            return genre_str
                        elif isinstance(artist_full_tags, dict) and artist_full_tags.get("name"):
                            #print(f"    ✅ Last.fm-Artist.getInfo: {artist_full_tags['name']}")
                            return artist_full_tags["name"]
            except Exception:
                pass  # Fallback schlägt fehl - weitermachen
            
            #print(f"    ℹ️  Last.fm: Keine Tags gefunden (alle Strategien fehlgeschlagen)")
            return None
        except requests.exceptions. Timeout:
            #print(f"    ⏱️  Last.fm: Timeout")
            return None
        except Exception:
            #print(f"    ⚠️  Last.fm-Fehler: {e}")
            return None

    @classmethod
    def _fetch_genre_itunes(cls, artist: str, song: str) -> str | None:
        """iTunes Genre-Abfrage"""
        try:
            url = "https://itunes.apple.com/search"
            params = {"term": f"{artist} {song}", "media": "music", "limit": 1}
            print(f"  📱 iTunes: {artist} - {song}")
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get("resultCount", 0) == 0:
                return None

            result = data["results"][0]
            genre = result.get("primaryGenreName")
            if genre:
                #print(f"    ✅ iTunes-Genre: {genre}")
                return genre. strip()
            return None
        except Exception:
            #print(f"    ⚠️  iTunes-Fehler: {e}")
            return None

    @classmethod
    def _fetch_genre_musicbrainz(cls, artist: str, song: str) -> str | None:
        """
        MusicBrainz Genre-Abfrage (kostenlos, keine Auth). 
        Gibt das primäre Genre zurück oder kombiniert mehrere Tags.
        """
        try:
            headers = {"User-Agent": cls. MB_USER_AGENT}
            # 1) Recording suchen
            params = {
                "query": f'artist:"{artist}" recording:"{song}"',
                "fmt": "json",
                "limit": 1
            }
            #print(f"  🎵 MusicBrainz: {artist} - {song}")
            resp = requests.get(
                f"{cls.MB_BASE_URL}/recording",
                params=params,
                headers=headers,
                timeout=5
            )
            resp. raise_for_status()
            data = resp.json()
            if not data.get("recordings"):
                #print(f"    ℹ️  MusicBrainz: Keine Recordings gefunden")
                return None
            recording = data["recordings"][0]
            recording_id = recording["id"]

            # 2) Genre-Tags vom Recording holen
            tags_resp = requests.get(
                f"{cls.MB_BASE_URL}/recording/{recording_id}",
                params={"inc": "genres+tags+ratings", "fmt": "json"},
                headers=headers,
                timeout=5
            )
            tags_resp.raise_for_status()
            tags_data = tags_resp.json()
            genres = []

            # Genre aus Genre-Feld (neu in MusicBrainz)
            if "genres" in tags_data:
                genres = [g["name"] for g in tags_data. get("genres", [])]

            # Von Tags als Fallback
            if not genres and "tags" in tags_data:
                tags = tags_data. get("tags", [])
                if isinstance(tags, list):
                    genres = [t["name"] for t in tags[:3]]

            if genres:
                genre_str = " / ".join(genres[:2])  # Top 2 Genres kombinieren
                #print(f"    ✅ MusicBrainz-Genres: {genre_str}")
                return genre_str
            #print(f"    ℹ️  MusicBrainz: Keine Genres gefunden")
            return None

        except requests.exceptions. Timeout:
            #print(f"    ⏱️  MusicBrainz: Timeout")
            return None
        except Exception:
            #print(f"    ⚠️  MusicBrainz-Fehler: {e}")
            return None

    @classmethod
    def _detect_holiday_genre(cls, artist: str, song: str) -> str | None:
        """
        Erkennt Christmas/Holiday-Songs anhand des Titles.
        Fallback für Sender wie FLASHBACK FM.
        """
        title_lower = f"{artist} {song}".lower()
        holiday_keywords = [
            "christmas", "xmas", "noel", "weihnacht", "santa",
            "sleigh", "jingle", "carol", "holiday", "festive",
            "merry", "season", "winter", "advent"
        ]
        
        if any(kw in title_lower for kw in holiday_keywords):
            print(f"  🎄 Holiday-Erkennung: {artist} - {song}")
            return "Holiday / Christmas"
        return None

# ============================================================
# Artist-Biografien (MusicBrainz + Wikipedia)
# ============================================================

MB_BASE_URL = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = (
    "MySmartDABRadio/1.0 "
    "(Raspberry Pi5; kontakt: bachmann.willy@bluewin.ch)"
)

# bevorzugte Wikipedia-Sprachen für die Biografie
DEFAULT_LANG_PREFERENCE = ["de", "en"]
log = logging.getLogger("dab_artist_bio")

def shorten_text(text: str, max_chars: int = 320) -> str:
    """
    Vereinfacht den Text (Mehrfachspaces entfernen) und kürzt ihn
    auf max_chars, möglichst an einem Satzende. 
    """
    text = " ". join(text.split())
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(".", 0, max_chars)
    if cutoff == -1:
        cutoff = max_chars
    return text[:cutoff].rstrip() + " …"


class MusicBrainzClient:
    """
    Kleiner Client für die MusicBrainz-API, um Artist zu suchen.
    """
    def __init__(self, base_url: str = MB_BASE_URL, user_agent: str = MB_USER_AGENT):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _sleep_polite(self) -> None:
        """
        MusicBrainz bittet um max. 1 Request pro Sekunde.
        """
        time.sleep(1.1)

    def search_artist(
        self,
        artist_name: str,
        recording_title: Optional[str] = None,
        limit: int = 5,
    ) -> Optional[Dict]:
        """
        Sucht Artist bei MusicBrainz.
        Optional kann ein Aufnahmetitel (track_title) mitgegeben werden,
        um die Suche einzuengen.
        """
        query_parts: List[str] = [f'artist:"{artist_name}"']
        if recording_title:
            query_parts.append(f'recording:"{recording_title}"')

        query = " AND ".join(query_parts)
        params = {"query": query, "fmt": "json", "limit": str(limit)}
        url = f"{self.base_url}/artist"

        #log.debug("MusicBrainz search URL: %s", url)
        #log.debug("MusicBrainz search params: %r", params)

        resp = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._sleep_polite()
        artists = data.get("artists", [])
        if not artists:
            #log.warning("MusicBrainz: Keine Artists gefunden für %r", artist_name)
            return None
        best = artists[0]
        #log.debug("Best artist match: %r", best)
        return best


class WikiClient:
    """
    Holt die Zusammenfassung (Summary) einer Artist-Seite von Wikipedia.
    """
    def __init__(self, user_agent: str = MB_USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json",
            }
        )

    def _get_summary_direct(
        self,
        lang: str,
        title: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Fragt die Wikipedia-REST-API direkt mit einem Titel an.
        Gibt (summary, page_url) zurück.
        """
        encoded_title = urllib.parse.quote(title.replace(" ", "_"))
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
        #log.debug("Wikipedia summary URL (direct): %s", url)

        try:
            resp = self.session.get(url, timeout=10)
        except requests.RequestException:
            #log.error("Fehler bei Anfrage an Wikipedia (direct): %s", e)
            return None, None
        if resp.status_code != 200:
            #log.warning("Wikipedia summary (direct) nicht gefunden (status %s) für %s:%s", resp.status_code, lang, title,)
            return None, None

        data = resp.json()
        summary = data.get("extract")
        content_urls = data.get("content_urls", {})
        desktop = content_urls.get("desktop", {})
        page_url = desktop.get("page")

        if not page_url:
            page_url = f"https://{lang}.wikipedia.org/wiki/{encoded_title}"
        if not summary:
            return None, page_url
        return summary, page_url

    def _search_title(self, lang: str, query: str) -> Optional[str]:
        """
        Falls der direkte Titel nicht klappt, per Wikipedia-Suche den
        besten Seitentitel ermitteln.
        """
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1,
        }
        #log.debug("Wikipedia search URL: %s params=%r", url, params)

        try:
            resp = self.session.get(url, params=params, timeout=10)
        except requests.RequestException:
            #log.error("Fehler bei Wikipedia-Suche: %s", e)
            return None
        if resp.status_code != 200:
            #log.warning("Wikipedia-Suche fehlgeschlagen (status %s) für %s:%s", resp.status_code, lang, query,)
            return None
        data = resp.json()
        hits = data.get("query", {}).get("search", [])
        if not hits:
            #log.info("Wikipedia-Suche: keine Treffer für %s:%r", lang, query)
            return None
        title = hits[0].get("title")
        #log.debug("Wikipedia-Suche: bester Treffer für %s ist Titel %r", query, title)
        return title

    def find_best_summary(
        self,
        lang_preference: List[str],
        artist_name: str,
    ) -> Optional[Tuple[str, str, str, str]]:
        """
        Versucht in den bevorzugten Sprachen nacheinander,
        eine gute Zusammenfassung zu finden.
        Rückgabe: (lang, wiki_title, summary, page_url) oder None.
        """
        for lang in lang_preference:
            # 1) Direkter Titel-Versuch (z.B. "The Beatles")
            summary, page_url = self._get_summary_direct(lang, artist_name)
            if summary:
                return lang, artist_name, summary, page_url or ""

            # 2) Falls das nicht klappt: Suche nach dem besten Titel
            title = self._search_title(lang, artist_name)
            if not title:
                continue
            summary, page_url = self._get_summary_direct(lang, title)
            if summary:
                return lang, title, summary, page_url or ""
        return None


class Bio_url:
    """
    High-Level-API für dein Projekt:
    Bio_url.fetch_artist_bio(artist_n) -> kurzer Biografie-Text (short_summary)

    Verwendung in main_page.py:
        self.app.state.artist_bio = Bio_url.fetch_artist_bio(artist_n)
    """
    _cache: "OrderedDict[str, str]" = OrderedDict()
    _cache_cap = 200
    _lock = Lock()
    _mb_client: Optional[MusicBrainzClient] = None
    _wiki_client: Optional[WikiClient] = None

    # --------------------------------------------------------
    # interner Cache (damit ein Artist nicht dauernd neu geladen wird)
    # --------------------------------------------------------
    @classmethod
    def _cache_get(cls, key: str) -> str:
        try:
            with cls._lock:
                val = cls._cache.get(key)
                if val is not None:
                    cls._cache.move_to_end(key)
                return val or ""
        except Exception:
            return ""

    @classmethod
    def _cache_put(cls, key: str, value: str) -> None:
        if not value:
            return
        try:
            with cls._lock:
                cls._cache[key] = value
                cls._cache.move_to_end(key)
                while len(cls._cache) > cls._cache_cap:
                    cls._cache.popitem(last=False)
        except Exception:
            pass

    @classmethod
    def _get_clients(cls) -> Tuple[MusicBrainzClient, WikiClient]:
        if cls._mb_client is None:
            cls._mb_client = MusicBrainzClient()
        if cls._wiki_client is None:
            cls._wiki_client = WikiClient()
        return cls._mb_client, cls._wiki_client

    # --------------------------------------------------------
    # Öffentliche API für dein Projekt
    # --------------------------------------------------------
    @classmethod
    def fetch_artist_bio(
        cls,
        artist_name: str,
        track_title: Optional[str] = None,
        lang_preference: Optional[List[str]] = None,
    ) -> str:
        """
        Holt eine kurze Biografie zu einem Artist-Namen.
        Rückgabe:
            - kurzer Text (max ~320 Zeichen) oder
            - "" (leerer String), wenn nichts gefunden wurde
        """
        if not artist_name:
            #log.warning("fetch_artist_bio: leerer Artistname")
            return ""

        # etwas aufräumen
        artist_name = " ".join(artist_name.split()).strip()
        if not artist_name:
            #log.warning("fetch_artist_bio: Artistname nur Whitespace")
            return ""
        langs = lang_preference or DEFAULT_LANG_PREFERENCE
        track_norm = (track_title or "").strip()
        lang_key = ",".join(langs)
        cache_key = f"{artist_name.casefold()}|{track_norm.casefold()}|{lang_key.casefold()}"
        cached = cls._cache_get(cache_key)
        if cached:
            #log.debug("Artist-Bio aus Cache: %r (%r)", artist_name, cache_key)
            return cached

        langs = lang_preference or DEFAULT_LANG_PREFERENCE
        mb, wiki = cls._get_clients()

        #log.info("Suche Artist bei MusicBrainz: %s (Titel: %s)", artist_name, track_title,)

        # Schritt 1: Artist bei MusicBrainz suchen
        try:
            mb_artist = mb.search_artist(artist_name, recording_title=track_title)
        except requests.RequestException:
            #log.error("Fehler bei Anfrage an MusicBrainz: %s", e)
            mb_artist = None
        if mb_artist:
            mb_artist_name = mb_artist.get("name", artist_name)
            #log.debug("MusicBrainz-Treffer: %s (%s)", mb_artist_name, mb_artist.get("id", ""),)
        else:
            #log.warning("MusicBrainz-Suche ergab keinen Treffer, nehme reinen Namen %r.", artist_name)
            mb_artist_name = artist_name

        # Schritt 2: Wikipedia-Zusammenfassung ermitteln
        best = wiki.find_best_summary(langs, mb_artist_name)
        if not best:
            #log.error("Keine Wikipedia-Zusammenfassung für %r gefunden.", mb_artist_name)
            return ""

        lang, wiki_title, summary, page_url = best
        #log.info("Artist-Bio gefunden: lang=%s, wiki_title=%r, url=%s", lang, wiki_title, page_url)

        short = shorten_text(summary, max_chars=320)
        cls._cache_put(cache_key, short)
        return short


class FMStationLookup:
    """
    Einfache Lookup-Klasse für FM-Sender aus JSON:
      [
        {"freq_mhz": 97.7, "name": "...", "short": "...", "region": "..." },
        ...
      ]
    """
    def __init__(self, json_path: str | Path):
        self._json_path = str(json_path)
        self.stations: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            p = Path(self._json_path)
            if not p.is_absolute():
                p = p.expanduser()
            with p.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"[FMStationLookup] Konnte FM-Stationen nicht laden ({self._json_path}): {e}")
            self.stations = []
            return
        stations: list[dict] = []
        for entry in raw:
            try:
                freq_mhz = float(entry.get("freq_mhz"))
            except (TypeError, ValueError):
                continue
            stations.append(
                {
                    "freq_mhz": freq_mhz,
                    "name": (entry.get("name") or "").strip(),
                    "short": (entry.get("short") or "").strip(),
                    "region": (entry.get("region") or "").strip(),
                }
            )
        self.stations = stations


    def find_by_freq(self, freq_mhz: float, tolerance_khz: float = 50.0):
        """
        Gibt den am nächsten passenden Sender zur angegebenen Frequenz zurück
        oder None, wenn nichts innerhalb der Toleranz liegt.
        tolerance_khz: erlaubte Abweichung in kHz (Standard: ±50 kHz).
        """
        if not self.stations:
            return None

        freq_khz = int(round(float(freq_mhz) * 1000.0))
        best = None
        best_delta = None

        for st in self.stations:
            st_khz = int(round(st["freq_mhz"] * 1000.0))
            delta = abs(st_khz - freq_khz)
            if best is None or delta < best_delta:
                best = st
                best_delta = delta

        if best is not None and best_delta is not None and best_delta <= tolerance_khz:
            return best
        return None

__all__ = ["ChipButton", "Cover_url", "Bio_url", "FMStationLookup"]
