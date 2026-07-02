# utils/gui_update_batcher.py

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading

class GUIUpdateBatcher:
    """Sammelt GUI-Updates und führt sie in Batches aus"""
    
    def __init__(self, root, batch_interval_ms: int = 30):
        self.root = root
        self.batch_interval_ms = batch_interval_ms
        self._pending_updates = []
        self._batch_scheduled = False
        self._lock = threading.Lock()
    
    def schedule_update(self, update_func, *args, **kwargs):
        """Plant ein GUI-Update"""
        with self._lock:
            self._pending_updates.append((update_func, args, kwargs))
            
            if not self._batch_scheduled:
                self._batch_scheduled = True
                self.root.after(self.batch_interval_ms, self._execute_batch)
    
    def _execute_batch(self):
        """Führt alle gesammelten Updates aus"""
        with self._lock:
            updates = self._pending_updates.copy()
            self._pending_updates.clear()
            self._batch_scheduled = False
        
        # Updates ausführen (bereits im GUI-Thread)
        for func, args, kwargs in updates:
            try:
                func(*args, **kwargs)
            except Exception as e:
                print(f"⚠️ GUI-Update Fehler: {e}")

__all__ = ["GUIUpdateBatcher"]
