#!/usr/bin/env python3
"""Cron job: runs all collectors + exports JSONs."""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))

from collector.main import ejecutar_todo

if __name__ == "__main__":
    print("[cron] Ejecutando colectores...")
    results = ejecutar_todo(exportar=True)
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        print(f"  [{status}] {r['nombre']} - {r.get('mensaje', '')}")
    print("[cron] Listo.")
