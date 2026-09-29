"""Tests de scripts/descargar_votos.py: nunca debe tumbar el run diario."""

import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import descargar_votos as dv  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _abrir_ok(req, timeout=0):
    assert req.get_header("Authorization") == "Bearer secreto-de-prueba"
    fecha = req.full_url.split("fecha=")[1]
    return _Resp(json.dumps({"votos": [{"id_voto": f"{fecha}|x"}]}).encode())


def test_sin_token_no_hace_nada(tmp_path, capsys):
    res = dv.descargar(["2026-09-29"], "", destino=tmp_path, abrir=lambda *a, **k: 1 / 0)
    assert res == {"guardados": [], "errores": {}}
    assert list(tmp_path.iterdir()) == []
    assert "Sin VOTOS_EXPORT_TOKEN" in capsys.readouterr().out


def test_descarga_ayer_y_hoy(tmp_path):
    res = dv.descargar(["2026-09-28", "2026-09-29"], "secreto-de-prueba", destino=tmp_path, abrir=_abrir_ok)
    assert res["guardados"] == ["2026-09-28", "2026-09-29"] and res["errores"] == {}
    assert json.loads((tmp_path / "votos_2026-09-29.json").read_text(encoding="utf-8")) == {
        "votos": [{"id_voto": "2026-09-29|x"}]}


def test_errores_del_servidor_o_de_red_no_rompen(tmp_path, capsys):
    def abrir(req, timeout=0):
        if "2026-09-28" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 503, "sin configurar", {}, None)
        raise TimeoutError("sin red")

    res = dv.descargar(["2026-09-28", "2026-09-29"], "secreto-de-prueba", destino=tmp_path, abrir=abrir)
    assert res["guardados"] == [] and res["errores"] == {"2026-09-28": "HTTP 503", "2026-09-29": "TimeoutError"}
    salida = capsys.readouterr().out
    salida.encode("cp1252")                      # el runner de Windows escribe en cp1252
    assert "secreto-de-prueba" not in salida     # el token nunca se imprime


def test_json_roto_no_rompe(tmp_path):
    res = dv.descargar(["2026-09-29"], "secreto-de-prueba", destino=tmp_path,
                       abrir=lambda req, timeout=0: _Resp(b"no es json"))
    assert res["guardados"] == [] and "2026-09-29" in res["errores"]
