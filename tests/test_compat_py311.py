"""El workflow corre en Python 3.11; en local se usa 3.12+ (3.14).

Desde 3.12 (PEP 701) las f-strings aceptan barras invertidas y las mismas
comillas dentro de {…}; en 3.11 eso es SyntaxError y el módulo entero no
carga (pasó el 2026-09-25: noticias.py tumbó el job en 5 s). Este test lo
detecta desde cualquier versión ≥ 3.12.
"""

import io
import sys
import tokenize
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVOS = sorted((RAIZ / "collector").glob("*.py")) + sorted((RAIZ / "scripts").glob("*.py"))


@pytest.mark.skipif(not hasattr(tokenize, "FSTRING_START"), reason="en 3.11 el propio import ya falla")
def test_fstrings_compatibles_con_python_311():
    problemas = []
    for archivo in ARCHIVOS:
        pila = []
        for t in tokenize.generate_tokens(io.StringIO(archivo.read_text(encoding="utf-8-sig")).readline):
            if t.type == tokenize.FSTRING_START:
                pila.append(t.string.lstrip("fFrRbB")[:1])
            elif t.type == tokenize.FSTRING_END:
                pila.pop()
            elif pila and t.type == tokenize.STRING:
                comilla = t.string.lstrip("rRbBuU")[:1]
                if "\\" in t.string or comilla == pila[-1]:
                    problemas.append(f"{archivo.relative_to(RAIZ)}:{t.start[0]}")
    assert not problemas, "f-strings que Python 3.11 (runner de CI) no puede compilar:\n" + "\n".join(problemas)


def test_version_minima_documentada():
    """Recordatorio: si el workflow sube de 3.11, este test se puede relajar."""
    wf = (RAIZ / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
    assert "python-version: '3.11'" in wf
    assert sys.version_info >= (3, 11)
