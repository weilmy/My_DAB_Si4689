# utils/image_manager.py

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from PIL import Image, ImageTk
from typing import Dict, Optional, Any, List
import tracemalloc
import gc

class ImageManager:
    """
    Zentrales Image-Management mit Memory-Leak-Schutz
    
    Features:
    - Caching von geladenen Bildern
    - Automatisches Cleanup
    - Memory-Tracking
    - PIL + PhotoImage Support
    """
    
    def __init__(self, enable_memory_tracking: bool = True, verbose: bool = False):
        self._images: Dict[str, Any] = {}  # Cache: key → PhotoImage
        self._pil_images: List[Image.Image] = []  # PIL Images für Cleanup
        
        # Memory-Tracking
        self._memory_tracking = enable_memory_tracking
        self._load_count = 0
        self._snapshot: Optional[tracemalloc.Snapshot] = None
        self._verbose = verbose
        
        if self._memory_tracking and not tracemalloc.is_tracing():
            tracemalloc.start()
        if self._verbose:
            print("✅ ImageManager initialisiert")
    
    def load_image(
        self, 
        key: str, 
        filepath: str,
        resize: Optional[tuple] = None,
        use_pil: bool = False
    ) -> Optional[Any]:
        """
        Lädt Bild mit automatischem Cleanup
        
        Args:
            key: Eindeutiger Schlüssel für Caching
            filepath: Pfad zur Bilddatei
            resize: Optional (width, height) für Resize
            use_pil: True = PIL Image zurückgeben, False = PhotoImage
        
        Returns:
            PhotoImage oder PIL Image oder None
        """
        # Altes Bild mit gleichem Key freigeben
        if key in self._images:
            self._release_image(key)
        
        try:
            # PIL Image öffnen
            pil_img = Image.open(filepath)
            self._pil_images.append(pil_img)
            if resize:
                pil_img = pil_img.resize(resize, Image.LANCZOS)
            if use_pil:
                result = pil_img
            else:
                result = ImageTk.PhotoImage(pil_img)
            self._images[key] = result
            self._load_count += 1
            if self._load_count % 50 == 0 and self._memory_tracking:
                self._check_memory()
            return result
            
        except Exception as e:
            print(f"❌ Fehler beim Laden von {filepath}: {e}")
            return None
    
    def load_photoimage(
        self,
        key: str,
        filepath: str,
        resize: Optional[tuple] = None
    ) -> Optional[tk.PhotoImage]:
        """Convenience für direktes PhotoImage laden (ohne PIL)"""
        try:
            if key in self._images:
                self._release_image(key)
            img = tk.PhotoImage(file=filepath)
            
            # Resize bei PhotoImage nicht möglich → PIL verwenden
            if resize:
                return self.load_image(key, filepath, resize, use_pil=False)
            
            self._images[key] = img
            self._load_count += 1
            return img
            
        except Exception as e:
            print(f"❌ PhotoImage-Fehler: {e}")
            return None
    
    def get_image(self, key: str) -> Optional[Any]:
        """Gibt gecachtes Bild zurück"""
        return self._images.get(key)
    
    def _release_image(self, key: str):
        """Gibt einzelnes Bild frei"""
        if key in self._images:
            self._images.pop(key)

    
    def _check_memory(self):
        """Prüft Image-Memory"""
        if not self._memory_tracking:
            return
        try:
            gc.collect()
            if self._snapshot is None:
                self._snapshot = tracemalloc.take_snapshot()
                return
            current = tracemalloc.take_snapshot()
            top = current.compare_to(self._snapshot, 'lineno')
            
            # Filter auf PIL/Tkinter
            relevant = [
                s for s in top 
                if ('/PIL/' in str(s) or '/tkinter/' in str(s)) 
                and s.size_diff > 50 * 1024
            ]
            if relevant and self._verbose:
                print(f"\n🖼️ ImageManager Memory ({self._load_count} Loads):")
                for stat in relevant[:3]:
                    print(f"  {stat}")
        except Exception as e:
            print(f"⚠️ Image Memory-Check Fehler: {e}")
    
    def cleanup_all(self):
        """Gibt alle Bilder frei"""
        if self._verbose:
            print(f"🧹 ImageManager Cleanup ({len(self._images)} Bilder)...")
        
        # PhotoImages
        for key in list(self._images.keys()):
            self._release_image(key)
        
        # PIL Images explizit schließen
        for pil_img in self._pil_images:
            try:
                pil_img.close()
            except:
                pass
        self._pil_images.clear()
        self._images.clear()
    
    def get_stats(self) -> dict:
        """Gibt Statistiken zurück"""
        return {
            'cached_images': len(self._images),
            'pil_images': len(self._pil_images),
            'total_loads': self._load_count,
        }
    
    def __del__(self):
        """Destruktor"""
        self.cleanup_all()

__all__ = ["ImageManager"]