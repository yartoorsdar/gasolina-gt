"""Inicia un servidor HTTP local para ver el dashboard.

Uso:
    python serve.py              # abre http://localhost:8080
    python serve.py 9000         # puerto personalizado
"""

import http.server
import os
import socketserver
import webbrowser
import sys
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
ROOT_DIR = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def log_message(self, format, *args):
        print(f"[serve] {args[0]}")


print(f"\n{'='*50}")
print(f"  Dashboard: http://localhost:{PORT}/web/index.html")
print(f"  Directorio raiz: {ROOT_DIR}")
print(f"  Salir con Ctrl+C")
print(f"{'='*50}\n")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    webbrowser.open(f"http://localhost:{PORT}/web/index.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
