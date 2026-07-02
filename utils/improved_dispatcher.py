#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verbesserte Dispatcher-Klasse mit Memory-Leak-Prävention
"""

import tracemalloc
import weakref
import gc
from concurrent.futures import ThreadPoolExecutor, Future, CancelledError
from typing import Dict, Optional, Callable
import time
import threading


class CancellationToken:
    """
    Token für abbruchfähige Tasks
    Ermöglicht Tasks zu prüfen, ob sie abgebrochen wurden
    """
    def __init__(self):
        self._cancelled = threading.Event()
    
    def cancel(self):
        """Markiert Task als abgebrochen"""
        self._cancelled.set()
    
    def is_cancelled(self) -> bool:
        """Prüft, ob Task abgebrochen wurde"""
        return self._cancelled.is_set()
    
    def check_cancelled(self):
        """Wirft Exception, wenn abgebrochen"""
        if self._cancelled.is_set():
            raise CancelledError("Task was cancelled")


def cancellable_task(func: Callable, token: CancellationToken, *args, **kwargs):
    """
    Wrapper für abbruchfähige Tasks
    Prüft vor Ausführung, ob Task abgebrochen wurde
    """
    # Sofort prüfen
    if token.is_cancelled():
        raise CancelledError("Task cancelled before execution")
    
    # Funktion ausführen
    try:
        return func(*args, **kwargs)
    except CancelledError:
        # Task wurde während Ausführung abgebrochen
        raise
    except Exception:
        # Andere Fehler durchreichen
        raise


class ImprovedDispatcher:
    """
    Features:
    - Thread-Pool-Dispatcher mit Memory-Leak-Schutz
    - Automatisches Cleanup alter Futures
    - Memory-Tracking mit tracemalloc
    - Weak References für besseres GC
    - Shutdown-Schutz
    - Detaillierte Statistiken
    """
    
    def __init__(
        self, 
        max_workers: int = 2,
        enable_memory_tracking: bool = True,
        memory_check_interval: int = 100,
        verbose: bool = False,
        enable_smart_cancellation: bool = True  # ← NEU!
    ):
        """
        Args:
            max_workers: Anzahl Worker-Threads
            enable_memory_tracking: tracemalloc aktivieren
            memory_check_interval: Nach X Calls Memory prüfen
            verbose: Debug-Ausgaben
        """
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="DispatcherWorker"
        )
        
        # Future-Verwaltung
        self._pending: Dict[str, Future] = {}
        self._future_refs: list[weakref.ref] = []  # Track für GC-Check
        
        # Statistiken
        self._submit_count = 0
        self._cancel_count = 0
        self._cleanup_count = 0
        
        # Memory-Tracking
        self._memory_tracking = enable_memory_tracking
        self._memory_check_interval = memory_check_interval
        self._start_snapshot: Optional[tracemalloc.Snapshot] = None
        self._last_memory_check = 0
        
        # Status
        self._is_shutdown = False
        self._verbose = verbose
        
        if self._memory_tracking:
            if not tracemalloc.is_tracing():
                tracemalloc.start(10)  # Track 10 Frames
            self._start_snapshot = tracemalloc.take_snapshot()
            
            if self._verbose:
                print("✅ Dispatcher Memory-Tracking aktiviert")
            
        # Cancellation-Tracking
        self._enable_smart_cancellation = enable_smart_cancellation
        self._cancellation_tokens: Dict[str, CancellationToken] = {}  # key → token
        self._cancel_count_smart = 0  # Statistik
        
        if self._verbose:
            print(f"✅ Dispatcher mit Smart-Cancellation: {enable_smart_cancellation}")
    
    def submit(
        self, 
        fn: Callable, 
        key: Optional[str] = None,
        cancellable: bool = True,  # ← NEU: Task abbruchfähig machen
        *args,
        **kwargs
    ) -> Optional[Future]:
        """
        Submitted eine Funktion an den Thread-Pool
        
        Args:
            fn: Auszuführende Funktion
            key: Optional eindeutiger Schlüssel
            cancellable: Wenn True, kann Task abgebrochen werden
            *args, **kwargs: Argumente für fn
            
        Returns:
            Future-Objekt oder None bei Fehler
        """
        if self._is_shutdown:
            if self._verbose:
                print("⚠️ Dispatcher ist bereits heruntergefahren - Task ignoriert")
            return None
        
        try:
            # ========== SMART CANCELLATION ==========
            cancellation_token = None
            
            if key and self._enable_smart_cancellation and cancellable:
                # Alte Task mit gleichem Key abbrechen
                if key in self._pending:
                    self._cancel_task_smart(key)
                
                # Neuen Cancellation-Token erstellen
                cancellation_token = CancellationToken()
                self._cancellation_tokens[key] = cancellation_token
            
            # Alte Future mit gleichem Key entfernen (wie bisher)
            elif key and key in self._pending:
                self._cancel_and_remove_future(key)
            
            # ========== TASK WRAPPING ==========
            if cancellation_token:
                # Task mit Cancellation-Support wrappen
                future = self._executor.submit(
                    cancellable_task,
                    fn,
                    cancellation_token,
                    *args,
                    **kwargs
                )
            else:
                # Normale Task
                future = self._executor.submit(fn, *args, **kwargs)
            
            # Future registrieren
            if key:
                self._pending[key] = future
                future.add_done_callback(lambda f: self._on_future_done(key, f))
            
            # Tracking
            self._future_refs.append(weakref.ref(future))
            self._submit_count += 1

            # Tote Weakrefs periodisch bereinigen
            if self._submit_count % 500 == 0:
                self._future_refs = [r for r in self._future_refs if r() is not None]

            # Periodischer Memory-Check
            if self._memory_tracking:
                if self._submit_count - self._last_memory_check >= self._memory_check_interval:
                    self._check_memory_growth()
                    self._last_memory_check = self._submit_count
            
            return future
            
        except Exception as e:
            print(f"❌ Dispatcher Submit-Fehler: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _cancel_task_smart(self, key: str):
        """
        Intelligentes Abbrechen einer Task
        
        1. Setzt Cancellation-Token (stoppt Task, wenn sie prüft)
        2. Versucht Future zu canceln (stoppt Task, wenn sie noch nicht läuft)
        3. Räumt auf
        """
        # Cancellation-Token setzen
        if key in self._cancellation_tokens:
            token = self._cancellation_tokens.pop(key)
            token.cancel()
            self._cancel_count_smart += 1
            
            if self._verbose:
                print(f"🔴 Task '{key}' cancelled (smart)")
        
        # Future auch canceln (falls noch nicht gestartet)
        if key in self._pending:
            old_future = self._pending.pop(key)
            
            if not old_future.done():
                cancelled = old_future.cancel()
                if cancelled and self._verbose:
                    print(f"🔴 Future '{key}' cancelled (before execution)")
            
            del old_future
            self._cleanup_count += 1


    def _cancel_and_remove_future(self, key: str):
        """Cancelt und entfernt eine Future sauber"""
        if key not in self._pending:
            return
        
        old_future = self._pending.pop(key)
        
        # Cancel, nur wenn noch nicht fertig
        if not old_future.done():
            cancelled = old_future.cancel()
            if cancelled:
                self._cancel_count += 1
        
        # Explizit löschen für schnelleres GC
        del old_future
        self._cleanup_count += 1
    
    def _on_future_done(self, key: str, future: Future):
        """
        Callback, wenn Future fertig ist
        Entfernt Future aus _pending für automatisches Cleanup
        """
        try:
            # Cancellation-Token aufräumen (NEU)
            if key in self._cancellation_tokens:
                self._cancellation_tokens.pop(key)

            # Aus pending entfernen (falls noch da)
            if key in self._pending and self._pending[key] is future:
                self._pending.pop(key)
                self._cleanup_count += 1
            
            # Exception-Handling
            try:
                exc = future.exception(timeout=0.1)
                if exc and not isinstance(exc, CancelledError):
                    print(f"⚠️ Future '{key}' Exception: {exc}")
            except CancelledError:                                   # ← KORRIGIERT
                # Normal - Task wurde abgebrochen
                if self._verbose:
                    print(f"ℹ️ Task '{key}' wurde erfolgreich abgebrochen")
            except Exception:                                        # ← OK
                pass
        except Exception as e:
            if self._verbose:
                print(f"⚠️ Fehler in _on_future_done: {e}")
    
    def _check_memory_growth(self):
        """Prüft Memory-Wachstum seit Start"""
        if not self._memory_tracking or self._start_snapshot is None:
            return
        
        try:
            # Garbage Collection vor Snapshot
            gc.collect()
            current = tracemalloc.take_snapshot()
            top_stats = current.compare_to(self._start_snapshot, 'lineno')
            significant_growth = [
                s for s in top_stats 
                if s.size_diff > 100 * 1024
            ]
            
            if significant_growth:
                print(f"\n🔍 Dispatcher Memory-Check (nach {self._submit_count} Calls):")
                for stat in significant_growth[:5]:
                    print(f"  📈 {stat}")
                
                # Warnung bei extremem Wachstum
                total_growth = sum(s.size_diff for s in top_stats)
                if total_growth > 10 * 1024 * 1024:  # > 10 MB
                    print(f"  ⚠️ WARNUNG: Gesamt-Wachstum {total_growth / 1024 / 1024:.2f} MB!")
                    self._detailed_leak_analysis()
            
        except Exception as e:
            print(f"⚠️ Memory-Check Fehler: {e}")
    
    def _detailed_leak_analysis(self):
        """Detaillierte Analyse bei Leak-Verdacht"""
        print("\n🔬 Detaillierte Dispatcher-Analyse:")
        print(f"  Pending Futures: {len(self._pending)}")
        alive_futures = sum(1 for ref in self._future_refs if ref() is not None)
        print(f"  Lebende Futures: {alive_futures}/{len(self._future_refs)}")
        
        # Statistiken
        print(f"  Submits: {self._submit_count}")
        print(f"  Cancels: {self._cancel_count}")
        print(f"  Cleanups: {self._cleanup_count}")
        
        expected_alive = len(self._pending) + 2
        if alive_futures > expected_alive:
            print(f"  ⚠️ LEAK-VERDACHT: {alive_futures - expected_alive} Futures zu viel!")

            try:
                snapshot = tracemalloc.take_snapshot()
                top = snapshot.statistics('traceback')
                if top:
                    biggest = top[0]
                    print(f"\n  Größter Verbraucher ({biggest.size / 1024:.1f} KB):")
                    for line in biggest.traceback.format()[:5]:
                        print(f"    {line}")
            except:
                pass
    
    def cancel_all(self):
        """Cancelt alle pending Tasks (mit Smart-Cancellation)"""
        print(f"🛑 Cancelling {len(self._pending)} pending tasks...")
        
        # Smart-Cancellation für alle Tokens
        if self._enable_smart_cancellation:
            for key in list(self._cancellation_tokens.keys()):
                token = self._cancellation_tokens.pop(key)
                token.cancel()
                if self._verbose:
                    print(f"🔴 Token '{key}' cancelled")
        
        # Futures canceln
        for key in list(self._pending.keys()):
            self._cancel_and_remove_future(key)
        
        self._pending.clear()
        self._cancellation_tokens.clear()  # ← NEU: Tokens auch clearen!
    
    def shutdown(self, wait: bool = True):
        """
        Fährt Dispatcher sauber herunter
        
        Args:
            wait: Auf laufende Tasks warten
            timeout: Max. Wartezeit in Sekunden
        """
        if self._is_shutdown:
            return
        
        print("🛑 Dispatcher Shutdown gestartet...")
        self._is_shutdown = True
        self.cancel_all()
        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=wait)
        
        # Cleanup
        self._pending.clear()
        self._future_refs.clear()
        
        # Final Memory-Report
        if self._memory_tracking:
            self._final_memory_report()
    
    def _final_memory_report(self):
        """Abschließender Memory-Report"""
        print("\n📊 Dispatcher Final Memory-Report:")
        print(f"  Total Submits: {self._submit_count}")
        print(f"  Total Cancels: {self._cancel_count}")
        print(f"  Total Cleanups: {self._cleanup_count}")
        gc.collect()
        
        alive = sum(1 for ref in self._future_refs if ref() is not None)
        print(f"  Lebende Futures: {alive}")
        if alive > 0:
            print(f"  ⚠️ {alive} Futures noch im Speicher (sollte 0 sein)")
        if tracemalloc.is_tracing() and self._start_snapshot:
            current = tracemalloc.take_snapshot()
            top = current.compare_to(self._start_snapshot, 'lineno')
            
            print("\n  Top Memory-Wachstum:")
            for stat in top[:5]:
                print(f"    {stat}")
            tracemalloc.stop()
    
    def get_stats(self) -> dict:
        """Gibt aktuelle Statistiken zurück"""
        gc.collect()
        alive_futures = sum(1 for ref in self._future_refs if ref() is not None)
        
        return {
            'pending': len(self._pending),
            'total_submits': self._submit_count,
            'total_cancels': self._cancel_count,
            'smart_cancels': self._cancel_count_smart,  # ← NEU
            'total_cleanups': self._cleanup_count,
            'alive_futures': alive_futures,
            'active_tokens': len(self._cancellation_tokens),  # ← NEU
            'is_shutdown': self._is_shutdown,
        }
    
    def __enter__(self):
        """Context Manager Support"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatisches Cleanup bei Context Manager"""
        self.shutdown(wait=True)
    
    def __del__(self):
        """Destruktor - Sicherheits-Cleanup"""
        if not self._is_shutdown:
            print("⚠️ Dispatcher wurde nicht explizit heruntergefahren!")
            self.shutdown(wait=False)




__all__ = ["ImprovedDispatcher"]

# ============================================================
# USAGE EXAMPLE
# ============================================================

if __name__ == "__main__":
    import time
    import random
    
    print("🧪 Testing ImprovedDispatcher...")
    
    def sample_task(task_id: int, sleep_time: float = 0.01):
        """Beispiel-Task"""
        time.sleep(sleep_time)
        return f"Task {task_id} completed"
    
    # Test 1: Normale Verwendung
    print("\n📝 Test 1: Normale Verwendung")
    with ImprovedDispatcher(max_workers=2, verbose=True) as dispatcher:
        
        # 100 Tasks submitten
        for i in range(100):
            dispatcher.submit(
                sample_task, 
                key=f"task_{i % 10}",  # Nur 10 Keys → alte werden gecancelt
                task_id=i
            )
        time.sleep(2)
        stats = dispatcher.get_stats()
        print(f"\n📊 Stats: {stats}")
    
    # Test 2: Stress-Test
    print("\n📝 Test 2: Stress-Test (1000 Tasks)")
    dispatcher = ImprovedDispatcher(
        max_workers=4,
        enable_memory_tracking=True,
        memory_check_interval=250
    )
    
    for i in range(1000):
        dispatcher.submit(
            sample_task,
            key=f"stress_{i % 50}",
            task_id=i,
            sleep_time=random.uniform(0.001, 0.01)
        )
    time.sleep(5)
    
    # Final stats
    final_stats = dispatcher.get_stats()
    print(f"\n📊 Final Stats: {final_stats}")
    dispatcher.shutdown(wait=True)
    print("\n✅ Tests abgeschlossen")
