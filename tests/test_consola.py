"""La consola del runner de GitHub (windows-latest) es cp1252.

Un print()/logger con un carácter fuera de cp1252 (ej. "→", "←", "✓") lanza
UnicodeEncodeError y tumba el job diario (pasó 2 veces el 2026-09-25). El
workflow ya fuerza UTF-8, pero este test lo evita desde el origen.
"""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVOS = sorted((RAIZ / "collector").glob("*.py")) + sorted((RAIZ / "scripts").glob("*.py"))
_LOGGER_METODOS = {"debug", "info", "warning", "error", "critical", "exception"}


def _es_salida_consola(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id == "print"
    return isinstance(f, ast.Attribute) and f.attr in _LOGGER_METODOS


def _textos(nodo):
    for n in ast.walk(nodo):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


def test_mensajes_de_consola_compatibles_con_cp1252():
    problemas = []
    for archivo in ARCHIVOS:
        arbol = ast.parse(archivo.read_text(encoding="utf-8-sig"))
        for call in (n for n in ast.walk(arbol) if isinstance(n, ast.Call) and _es_salida_consola(n)):
            for texto in _textos(call):
                malos = sorted({c for c in texto if not c.isascii() and c.encode("cp1252", "replace") == b"?"})
                if malos:
                    problemas.append(f"{archivo.relative_to(RAIZ)}:{call.lineno} {malos}")
    assert not problemas, "Caracteres que rompen la consola cp1252 del runner:\n" + "\n".join(problemas)
