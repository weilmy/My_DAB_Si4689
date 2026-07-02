#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# db_connection_pool.py
"""
SQLite Connection Pool für DAB-Radio-Projekt

Verwaltet einen Pool von wiederverwendbaren SQLite-Connections,
um den Overhead von connect()/close() zu vermeiden.

Verwendung:
    # main.py: In class App(tk.Tk):
    self.scan_pool  = SQLiteConnectionPool("assets/DB/dab_scans.sqlite", pool_size=5)
    self.music_pool = SQLiteConnectionPool("assets/DB/music_data.sqlite", pool_size=3)
"""

import sqlite3
import threading
from contextlib import contextmanager


class SQLiteConnectionPool:
    """
    Thread-sicherer Connection-Pool für SQLite-Datenbanken.
    
    Vorteile:
    - Vermeidet Connection-Overhead (10-20ms pro connect/close)
    - Thread-sicher
    - Automatisches Cleanup
    - Context-Manager-Support
    """
    
    def __init__(
        self,
        db_path: str,
        pool_size: int = 5,
        timeout: float = 10.0,
        check_same_thread: bool = False
    ):
        """
        Args:
            db_path: Pfad zur SQLite-Datenbank
            pool_size: Maximale Anzahl gecachter Connections
            timeout: SQLite busy timeout in Sekunden
            check_same_thread: False erlaubt Multi-Threading (Standard bei SQLite)
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self.check_same_thread = check_same_thread

        # ▼▼▼ DEBUG: Zeige, welche Pools erstellt werden ▼▼▼
        print(f"[Pool] ✨ Neuer Pool erstellt: {db_path}")
        import traceback
        traceback.print_stack(limit=5)
        # ▲▲▲
        
        self._pool = []
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,      # Connection aus Pool
            "misses": 0,    # Neue Connection erstellt
            "returns": 0,   # Connection zurückgegeben
            "creates": 0,   # Connections erstellt
            "closes": 0     # Connections geschlossen
        }
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Holt eine Connection aus dem Pool (oder erstellt eine neue).
        Returns:
            sqlite3.Connection: Verwendbare Connection
        """
        with self._lock:
            if self._pool:
                self._stats["hits"] += 1
                return self._pool.pop()
            else:
                self._stats["misses"] += 1
                self._stats["creates"] += 1
                return self._create_connection()
    
    def return_connection(self, conn: sqlite3.Connection) -> None:
        """
        Gibt eine Connection an den Pool zurück.
        Args:
            conn: Die zurückzugebende Connection
        """
        if conn is None:
            return
        
        with self._lock:
            self._stats["returns"] += 1
            
            # Pool voll? → Connection schließen
            if len(self._pool) >= self.pool_size:
                self._close_connection(conn)
                return
            try:
                conn.rollback()
            except Exception:
                pass
            self._pool.append(conn)
    
    @contextmanager
    def connection(self):
        """
        Context Manager für automatisches Return.
        Verwendung:
            with pool.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("...")
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            self.return_connection(conn)
    
    def close_all(self) -> None:
        """
        Schließt alle Connections im Pool.
        Sollte beim Herunterfahren der Applikation aufgerufen werden.
        """
        with self._lock:
            for conn in self._pool:
                self._close_connection(conn)
            self._pool.clear()
            print(f"[Pool] {self.db_path}: Alle Connections geschlossen")
    
    def get_stats(self) -> dict:
        """
        Gibt Pool-Statistiken zurück.
        Returns:
            dict: {"hits": int, "misses": int, "hit_rate": float, ...}
        """
        with self._lock:
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total_requests * 100
                if total_requests > 0
                else 0.0
            )
            
            return {
                **self._stats,
                "pool_size": len(self._pool),
                "hit_rate": round(hit_rate, 1),
                "total_requests": total_requests
            }
    
    def print_stats(self) -> None:
        """Gibt Pool-Statistiken auf der Konsole aus."""
        stats = self.get_stats()
        print(f"\n[Pool-Stats] {self.db_path}")
        print(f"  Requests:   {stats['total_requests']}")
        print(f"  Hits:       {stats['hits']} ({stats['hit_rate']}%)")
        print(f"  Misses:     {stats['misses']}")
        print(f"  Returns:    {stats['returns']}")
        print(f"  Created:    {stats['creates']}")
        print(f"  Closed:     {stats['closes']}")
        print(f"  Pool-Size:  {stats['pool_size']}/{self.pool_size}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """Erstellt eine neue SQLite-Connection mit optimalen Settings."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.timeout,
            isolation_level=None,  # Autocommit-Modus
            check_same_thread=self.check_same_thread
        )
        
        # Performance-Pragmas
        conn.execute("PRAGMA journal_mode=WAL")      # Write-Ahead-Log
        conn.execute("PRAGMA synchronous=NORMAL")    # Balance Performance/Safety
        conn.execute("PRAGMA temp_store=MEMORY")     # Temp-Tables im RAM
        conn.execute("PRAGMA cache_size=-10000")     # 10 MB Cache
        
        return conn
    
    def _close_connection(self, conn: sqlite3.Connection) -> None:
        """Schließt eine Connection sicher."""
        try:
            conn.close()
            self._stats["closes"] += 1
        except Exception as e:
            print(f"[Pool] Fehler beim Schließen: {e}")


# ----- Verwendungsbeispiele -----
def example_usage():
    """Zeigt typische Verwendungsmuster."""
    
    # 1. Pool erstellen
    pool = SQLiteConnectionPool(
        "assets/DB/dab_scans.sqlite",
        pool_size=5
    )
    
    # 2a. Manuelle Verwendung
    conn = pool.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM t4b_datenbank")
        count = cursor.fetchone()[0]
        print(f"Sender in DB: {count}")
    finally:
        pool.return_connection(conn)
    
    # 2b. Mit Context Manager (empfohlen!)
    with pool.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM t4b_datenbank LIMIT 5")
        for row in cursor.fetchall():
            print(f"  - {row[0]}")
    
    # 3. Statistiken anzeigen
    pool.print_stats()
    
    # 4. Beim Shutdown
    pool.close_all()


__all__ = ["SQLiteConnectionPool"]

if __name__ == "__main__":
    example_usage()