#!/usr/bin/env python3
"""
EPG Manager für DAB Radio Projekt
Unterstützt Radio UND TV!
"""

import requests
import threading
import time
from datetime import datetime
from pathlib import Path


class EPGManager:
    """EPG Manager mit OAuth2 und Caching für Radio + TV"""

    TOKEN_URL = "https://api.srgssr.ch/oauth/v1/accesstoken?grant_type=client_credentials"
    EPG_BASE_URL = "https://api.srgssr.ch/epg/v3"
    TOKEN_EXPIRY_BUFFER = 300
    EPG_CACHE_DURATION = 900

    def __init__(self, client_id, client_secret, cache_dir="epg_cache"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

        self.access_token = None
        self.token_expires_at = None
        self.token_lock = threading.Lock()

        self.epg_cache = {}
        self.epg_cache_lock = threading.Lock()

        self.session = requests.Session()
        print(f"✅ EPG Manager initialisiert (Radio + TV)")

    def _get_oauth_token(self):
        """Holt neuen OAuth2 Token"""
        try:
            response = self.session.post(
                self.TOKEN_URL,
                auth=(self.client_id, self.client_secret),
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                token = data.get('access_token')
                expires_in = data.get('expires_in', 3600)

                self.access_token = token
                self.token_expires_at = time.time() + expires_in

                print(f"🔐 OAuth2 Token erhalten (gültig {expires_in}s)")
                return token
            else:
                print(f"❌ Token-Fehler: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Token-Exception: {e}")
            return None

    def get_access_token(self):
        """Liefert gültigen Token (cached oder neu)"""
        with self.token_lock:
            if self.access_token and self.token_expires_at:
                if self.token_expires_at - time.time() > self.TOKEN_EXPIRY_BUFFER:
                    return self.access_token

            return self._get_oauth_token()

    def _get_cached_epg(self, cache_key):
        """Holt EPG aus Cache, wenn gültig"""
        with self.epg_cache_lock:
            if cache_key in self.epg_cache:
                cached_data, cached_time = self.epg_cache[cache_key]

                if time.time() - cached_time < self.EPG_CACHE_DURATION:
                    # print(f"📦 EPG aus Cache: {cache_key}")
                    return cached_data
                else:
                    del self.epg_cache[cache_key]
        return None

    def _cache_epg(self, cache_key, data):
        """Speichert EPG im Cache"""
        with self.epg_cache_lock:
            self.epg_cache[cache_key] = (data, time.time())

    def _parse_datetime(self, datetime_str):
        """
        Parst Datums-String aus API und konvertiert UTC in lokale Zeit

        Die SRGSSR API liefert Zeiten in UTC (mit 'Z' suffix).
        Wir konvertieren diese in lokale Schweizer Zeit (MEZ/MESZ).
        """
        if not datetime_str:
            return None

        try:
            from datetime import timezone

            # Entferne Timezone-Suffix für Parsing
            dt_str = datetime_str.replace('Z', '').replace('+00:00', '').replace('+01:00', '')

            # Versuche verschiedene Formate zu parsen
            dt_utc = None
            for fmt in [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f',
                '%Y-%m-%dT%H:%M:%S.%f',
            ]:
                try:
                    dt_utc = datetime.strptime(dt_str, fmt)
                    break
                except ValueError:
                    continue

            if dt_utc is None:
                # Fallback auf fromisoformat
                dt_utc = datetime.fromisoformat(dt_str)

            # Markiere als UTC-Zeit
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

            # Konvertiere in lokale Zeitzone
            dt_local = dt_utc.astimezone()

            # Gib naive datetime zurück (ohne Timezone-Info)
            return dt_local.replace(tzinfo=None)

        except Exception as e:
            print(f"⚠️ Datum-Parse-Fehler: {datetime_str} - {e}")
            return None

    def get_epg(self, business_unit='srf', broadcast_type='radio',
                station_name='srf-3', date=None, use_cache=True):
        """
        Holt EPG-Daten

        Args:
            business_unit: 'srf', 'rts', 'rsi'
            broadcast_type: 'radio' oder 'tv'
            station_name: z.B. 'srf-3' (Radio) oder 'srf-1' (TV)
            date: 'YYYY-MM-DD' oder None für heute
            use_cache: Cache verwenden
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        cache_key = f"{business_unit}_{broadcast_type}_{station_name}_{date}"

        if use_cache:
            cached = self._get_cached_epg(cache_key)
            if cached is not None:
                return cached

        token = self.get_access_token()
        if not token:
            print(f"❌ Kein Token verfügbar")
            return None

        url = f"{self.EPG_BASE_URL}/{business_unit}/{broadcast_type}/stations/{station_name}"
        headers = {'Authorization': f'Bearer {token}'}
        params = {'date': date}

        try:
            response = self.session.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()

                if use_cache:
                    self._cache_epg(cache_key, data)

                if isinstance(data, dict):
                    programs = data.get('programs', data.get('data', []))
                    count = len(programs)
                else:
                    count = len(data) if isinstance(data, list) else "?"

                media_icon = "📻" if broadcast_type == 'radio' else "📺"
                print(
                    f"{media_icon} EPG: {station_name} ({date}) - {count} Programme")  # >-----------Ausgabe in Terminal----------
                return data

            elif response.status_code == 401:
                print(f"⚠️ Token ungültig, erneuere...")
                self.access_token = None
                token = self.get_access_token()

                if token:
                    headers['Authorization'] = f'Bearer {token}'
                    response = self.session.get(url, headers=headers, params=params, timeout=10)

                    if response.status_code == 200:
                        data = response.json()
                        if use_cache:
                            self._cache_epg(cache_key, data)
                        return data

            print(f"❌ EPG-Fehler: HTTP {response.status_code}")
            return None

        except Exception as e:
            print(f"❌ EPG-Exception: {e}")
            return None

    def get_current_program(self, business_unit='srf', broadcast_type='radio',
                            station_name='srf-3'):
        """Holt aktuell laufendes Programm (mit Fallback auf letztes Programm)"""
        epg_data = self.get_epg(business_unit, broadcast_type, station_name)

        if not epg_data:
            print("⚠️ Keine EPG-Daten erhalten")
            return None

        if isinstance(epg_data, dict):
            programs = epg_data.get('programs', epg_data.get('data', []))
        else:
            programs = epg_data

        if not programs:
            print("⚠️ Keine Programme in EPG-Daten")
            return None

        now = datetime.now()
        print(f"🕐 Aktuelle Zeit: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        last_started = None
        last_started_time = None

        for idx, program in enumerate(programs):
            date_times = program.get('dateTimes', {})
            start_str = date_times.get('startTime')
            end_str = date_times.get('endTime')

            if start_str and end_str:
                start_time = self._parse_datetime(start_str)
                end_time = self._parse_datetime(end_str)

                if start_time and end_time:
                    if idx < 3:
                        print(
                            f"   Programm {idx + 1}: {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')} - {program.get('title', 'Unbekannt')}")

                    if start_time <= now < end_time:
                        print(f"✅ Aktuelles Programm gefunden: {program.get('title', 'Unbekannt')}")
                        return program

                    if start_time <= now:
                        if last_started_time is None or start_time > last_started_time:
                            last_started = program
                            last_started_time = start_time

        if last_started:
            print(f"📻 Letztes gestartetes Programm: {last_started.get('title', 'Unbekannt')}")
            print(f"   (Gestartet um: {last_started_time.strftime('%H:%M')})")
            return last_started

        print("⚠️ Kein Programm gefunden")
        return None

    def format_program(self, program):
        """Formatiert Programm für Anzeige"""
        if not program:
            return None

        date_times = program.get('dateTimes', {})
        start_str = date_times.get('startTime', '')
        end_str = date_times.get('endTime', '')

        start_dt = self._parse_datetime(start_str)
        end_dt = self._parse_datetime(end_str)

        if start_dt and end_dt:
            time_str = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
        else:
            time_str = "??:??-??:??"

        short_desc = program.get('shortDescription', '')
        long_desc = program.get('longDescription', '')

        title = program.get('title', 'Unbekannt')
        if not short_desc or short_desc.strip().lower() == title.strip().lower():
            description = long_desc if long_desc else short_desc
        else:
            description = short_desc

        return {
            'time': time_str,
            'title': title,
            'description': description,
            'is_live': program.get('live', program.get('isLive', False)),
            'has_subtitle': program.get('hasSubtitle', False),
            'duration': date_times.get('duration', ''),
            'raw': program
        }

    def clear_cache(self):
        """Löscht EPG-Cache"""
        with self.epg_cache_lock:
            self.epg_cache.clear()
        print("🗑️ EPG-Cache geleert")


# ============================================================================
# RADIO SENDER
# ============================================================================

SRF_RADIO_STATIONS = {
    'SRF 1': 'srf-1',
    'SRF 2 Kultur': 'srf-2',
    'SRF 3': 'srf-3',
    'SRF 4 News': 'srf-4',
    'SRF Musikwelle': 'srf-musikwelle',
    'SRF Virus': 'srf-virus',
}

RTS_RADIO_STATIONS = {
    'La 1ère': 'LA1ERE',
    'Espace 2': 'ESPACE2',
    'Couleur 3': 'COULEUR3',
    'Option Musique': 'OPTION_MUSIQUE',
    'RTS Info': 'RTSINFO',
}

RSI_RADIO_STATIONS = {
    'Rete Uno': 'rete-uno',
    'Rete Due': 'rete-due',
    'Rete Tre': 'rete-tre',
}

# ============================================================================
# TV SENDER
# ============================================================================

SRF_TV_STATIONS = {
    'SRF 1': 'srf-1',
    'SRF zwei': 'srf-2',
    'SRF info': 'srf-info',
}

RTS_TV_STATIONS = {
    'RTS Un': 'RTS_UN',
    'RTS Deux': 'RTS_DEUX',
}

RSI_TV_STATIONS = {
    'RSI LA 1': 'rsi-la-1',
    'RSI LA 2': 'rsi-la-2',
}


def main():
    import sys

    print("\n" + "=" * 80)
    print("📻📺 EPG Manager - Test (Radio + TV)")
    print("=" * 80 + "\n")

    if len(sys.argv) < 3:
        print("Usage: python epg_manager.py CLIENT_ID CLIENT_SECRET")
        sys.exit(1)

    client_id = sys.argv[1]
    client_secret = sys.argv[2]

    epg = EPGManager(client_id, client_secret)

    # Test Radio
    print("\n📻 Test: SRF 3 Radio")
    print("-" * 80)
    current = epg.get_current_program('srf', 'radio', 'srf-3')
    if current:
        fmt = epg.format_program(current)
        print(f"⏰ {fmt['time']}")
        print(f"🎵 {fmt['title']}")

    # Test TV
    print("\n\n📺 Test: SRF 1 TV")
    print("-" * 80)
    current_tv = epg.get_current_program('srf', 'tv', 'srf-1')
    if current_tv:
        fmt_tv = epg.format_program(current_tv)
        print(f"⏰ {fmt_tv['time']}")
        print(f"📺 {fmt_tv['title']}")
        if fmt_tv['description']:
            print(f"💬 {fmt_tv['description'][:100]}")

    print("\n\n" + "=" * 80)
    print("✅ Tests abgeschlossen")
    print("=" * 80 + "\n")


__all__ = ["EPGManager"]

if __name__ == "__main__":
    main()