#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager - KORRIGIERT
Problem: sqlite3.Connection unterstützt keine weak references
Lösung: Normale Liste mit Connection-IDs statt weakref
"""

import sqlite3
import contextlib
import tracemalloc
import gc
from typing import Generator, Optional, List, Tuple
from dataclasses import dataclass
import threading


@dataclass
class ConnectionStats:
    """Statistiken für DB-Verbindungen"""
    total_connections: int = 0
    total_queries: int = 0
    active_connections: int = 0
    failed_connections: int = 0
    total_rows_fetched: int = 0


class DatabaseManager:
    """
    Connection-Pool-Manager für SQLite mit Memory-Leak-Prävention
    """

    def __init__(
            self,
            db_path: str,
            enable_memory_tracking: bool = True,
            memory_check_interval: int = 100,
            check_same_thread: bool = False,
            verbose: bool = False
    ):
        self.db_path = db_path
        self._verbose = verbose
        self._check_same_thread = check_same_thread
        self._connection_ids: List[int] = []  # Track Connection-IDs
        self._lock = threading.Lock()
        self.stats = ConnectionStats()

        # Memory-Tracking
        self._memory_tracking = enable_memory_tracking
        self._memory_check_interval = memory_check_interval
        self._start_snapshot: Optional[tracemalloc.Snapshot] = None

        if self._memory_tracking:
            if not tracemalloc.is_tracing():
                tracemalloc.start(10)
            self._start_snapshot = tracemalloc.take_snapshot()

            if self._verbose:
                print(f"✅ DatabaseManager für '{db_path}' initialisiert")

    @contextlib.contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context Manager für sichere DB-Verbindung"""
        conn = None
        conn_id = None

        try:
            # Verbindung öffnen
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=self._check_same_thread,
                timeout=10.0
            )

            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")

            # Tracking
            conn_id = id(conn)
            with self._lock:
                self.stats.total_connections += 1
                self.stats.active_connections += 1
                self._connection_ids.append(conn_id)

            if self._verbose:
                print(f"📂 DB-Verbindung geöffnet (aktiv: {self.stats.active_connections})")

            yield conn

        except sqlite3.Error as e:
            with self._lock:
                self.stats.failed_connections += 1
            print(f"❌ DB-Verbindungsfehler: {e}")
            raise

        finally:
            # KORREKTUR: Sauberes Cleanup
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    print(f"⚠️ Fehler beim Schließen: {e}")
                finally:
                    del conn  # Explizit freigeben

            # Connection-ID aufräumen (WICHTIG!)
            if conn_id is not None:
                with self._lock:
                    # Entferne ALLE Vorkommen (falls doppelt getrackt)
                    self._connection_ids = [
                        cid for cid in self._connection_ids
                        if cid != conn_id
                    ]
                    self.stats.active_connections = len(self._connection_ids)

            if self._verbose:
                print(f"📂 DB-Verbindung geschlossen (aktiv: {self.stats.active_connections})")

    @contextlib.contextmanager
    def get_cursor(self) -> Generator[Tuple[sqlite3.Connection, sqlite3.Cursor], None, None]:
        """
        Context Manager für Verbindung + Cursor

        Usage:
            with db_manager.get_cursor() as (conn, cursor):
                cursor.execute("SELECT ...")
                rows = cursor.fetchall()
                conn.commit()
        Yields:
            Tuple[Connection, Cursor]
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield conn, cursor
            finally:
                try:
                    cursor.close()
                except:
                    pass
                finally:
                    del cursor

    def execute_query(
            self,
            query: str,
            params: Optional[tuple] = None,
            fetch: str = "all"
    ) -> Optional[List]:
        """
        Convenience-Methode für einfache Queries
        """
        try:
            with self.get_cursor() as (conn, cursor):
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                with self._lock:
                    self.stats.total_queries += 1
                if fetch == "all":
                    result = cursor.fetchall()
                    with self._lock:
                        self.stats.total_rows_fetched += len(result)
                    return result
                elif fetch == "one":
                    result = cursor.fetchone()
                    if result:
                        with self._lock:
                            self.stats.total_rows_fetched += 1
                    return result
                elif fetch == "many":
                    result = cursor.fetchmany(100)
                    with self._lock:
                        self.stats.total_rows_fetched += len(result)
                    return result
                else:  # "none"
                    conn.commit()
                    return None

        except sqlite3.Error as e:
            print(f"❌ Query-Fehler: {e}")
            print(f"   Query: {query}")
            if params:
                print(f"   Params: {params}")
            return None

    def insert(self, table: str, data: dict) -> Optional[int]:
        """Convenience für INSERT"""
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        try:
            with self.get_cursor() as (conn, cursor):
                cursor.execute(query, tuple(data.values()))
                conn.commit()
                with self._lock:
                    self.stats.total_queries += 1
                return cursor.lastrowid

        except sqlite3.Error as e:
            print(f"❌ INSERT-Fehler: {e}")
            return None

    def update(self, table: str, data: dict, where: str, where_params: tuple) -> bool:
        """Convenience für UPDATE"""
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"
        params = tuple(data.values()) + where_params

        try:
            with self.get_cursor() as (conn, cursor):
                cursor.execute(query, params)
                conn.commit()
                with self._lock:
                    self.stats.total_queries += 1
                return True

        except sqlite3.Error as e:
            print(f"❌ UPDATE-Fehler: {e}")
            return False

    def _check_memory_growth(self):
        """Prüft Memory-Wachstum"""
        if not self._memory_tracking or self._start_snapshot is None:
            return
        try:
            gc.collect()
            current = tracemalloc.take_snapshot()
            top_stats = current.compare_to(self._start_snapshot, 'lineno')
            sqlite_stats = [
                s for s in top_stats
                if '/sqlite3' in str(s) and s.size_diff > 50 * 1024
            ]

            if sqlite_stats:
                print(f"\n💾 DB Memory-Check (nach {self.stats.total_connections} Verbindungen):")
                for stat in sqlite_stats[:3]:
                    print(f"  📈 {stat}")

                # Warnung bei zu vielen offenen IDs
                alive = len(self._connection_ids)
                if alive > 5:
                    print(f"  ⚠️ WARNUNG: {alive} Connection-IDs noch getrackt!")
        except Exception as e:
            print(f"⚠️ Memory-Check Fehler: {e}")

    def _count_alive_connections(self) -> int:
        """Zählt getracktes Connection-IDs"""
        with self._lock:
            return len(self._connection_ids)

    def get_stats_dict(self) -> dict:
        """Stats als Dict"""
        return {
            'db_path': self.db_path,
            'total_connections': self.stats.total_connections,
            'active_connections': self.stats.active_connections,
            'tracked_ids': len(self._connection_ids),
            'failed_connections': self.stats.failed_connections,
            'total_queries': self.stats.total_queries,
            'total_rows': self.stats.total_rows_fetched,
        }

    def print_stats(self):
        """Stats ausgeben"""
        stats = self.get_stats_dict()
        print("\n📊 DatabaseManager Statistiken:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

    def cleanup(self):
        """Cleanup beim Shutdown"""
        print("🧹 DatabaseManager Cleanup...")

        if self.stats.active_connections > 0:
            print(f"  ⚠️ {self.stats.active_connections} Connections noch aktiv!")
        tracked = len(self._connection_ids)
        if tracked > 0:
            print(f"  ⚠️ {tracked} Connection-IDs noch getrackt!")
        self.print_stats()
        if self._memory_tracking:
            self._check_memory_growth()
        self._connection_ids.clear()


# ============================================================
# USAGE EXAMPLE
# ============================================================

def main():
    import tempfile
    import os

    print("🧪 Testing DatabaseManager (KORRIGIERT)...")

    temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    temp_db.close()
    db_path = temp_db.name

    try:
        db_manager = DatabaseManager(
            db_path=db_path,
            enable_memory_tracking=True,
            memory_check_interval=10,
            verbose=True
        )

        # Tabelle erstellen
        with db_manager.get_cursor() as (conn, cursor):
            cursor.execute("""
                           CREATE TABLE IF NOT EXISTS test
                           (
                               id
                               INTEGER
                               PRIMARY
                               KEY,
                               name
                               TEXT
                           )
                           """)
            conn.commit()

        print("\n📝 Test: 20 INSERTs...")
        for i in range(20):
            row_id = db_manager.insert('test', {'name': f'Item {i}'})
            if i == 0:
                print(f"  First insert: ID={row_id}")

        print("\n📝 Test: SELECT...")
        rows = db_manager.execute_query("SELECT * FROM test LIMIT 5")
        print(f"  Gefunden: {len(rows) if rows else 0} Zeilen")

        print("\n" + "=" * 60)
        db_manager.cleanup()
        print("=" * 60)

    finally:
        try:
            os.unlink(db_path)
        except:
            pass
    print("\n✅ Test abgeschlossen (keine weakref Fehler!)")


__all__ = ["DatabaseManager"]

if __name__ == "__main__":
    main()