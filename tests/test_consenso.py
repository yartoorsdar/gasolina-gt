"""Tests del consejo de precios (collector/consenso_precios.py).

Las oraciones de prueba son textuales de notas reales del 15-23 sep 2026.
"""

import json
from unittest.mock import patch

import pytest

from collector.consenso_precios import (
    MEDIO_OFICIAL,
    consejo,
    extraer_llm,
    extraer_regex,
    normalizar_observaciones,
    precision_fuentes,
)
from collector.db import conectar_temporal, guardar_observaciones, guardar_precios

HOY = "2026-09-24"


# ──────────────────────────────────────────────
# Extracción por patrones (respaldo sin LLM)
# ──────────────────────────────────────────────

class TestExtraerRegex:
    def _precios(self, texto):
        return {(o["producto"], o["modalidad"], o["precio"]) for o in extraer_regex(texto, HOY)}

    def test_producto_antes_del_precio(self):
        t = ("De acuerdo con los precios observados, en la modalidad de autoservicio, el galón de "
             "gasolina regular se cotiza en Q43.29, mientras que la gasolina súper alcanza los Q45.29.")
        assert self._precios(t) == {("regular", "autoservicio", 43.29), ("superior", "autoservicio", 45.29)}

    def test_precio_antes_del_producto(self):
        """Antes se emparejaba corrido: la regular recibía el precio del diésel."""
        t = ("En la modalidad de servicio completo, los precios observados alcanzan Q45.69 para la "
             "gasolina superior, Q43.69 para la regular y Q50.49 para el diésel.")
        assert self._precios(t) == {("superior", "servicio_completo", 45.69),
                                    ("regular", "servicio_completo", 43.69),
                                    ("diésel", "servicio_completo", 50.49)}

    def test_descarta_estimados_historicos_y_fechas(self):
        for t in (
            "Gasolina Superior en autoservicio: Precio actual: Q44.00 Nuevo precio estimado: Q34.41.",
            "En autoservicio, el diésel pasó de Q25.12 a Q37.64.",
            "El MEM reportó que, entre el 31 de agosto y el 7 de septiembre, en autoservicio, la superior Q40.58.",
            "En autoservicio el galón de regular cuesta US$5.89 (Q44.98) en El Salvador.",
        ):
            assert self._precios(t) == set(), t

    def test_sin_modalidad_o_ambigua(self):
        assert self._precios("La gasolina regular está en Q43.29.") == set()
        assert self._precios("En autoservicio y servicio completo la regular está en Q43.29.") == set()


class TestNormalizar:
    NOTA = {"url": "https://m/1", "medio": "m", "publicado": "2026-09-23"}

    def test_filtra_tipos_y_modalidad(self):
        crudas = [
            {"producto": "superior", "modalidad": "autoservicio", "precio": 45.29, "tipo": "monitoreado"},
            {"producto": "superior", "modalidad": "autoservicio", "precio": 34.41, "tipo": "estimado"},
            {"producto": "regular", "modalidad": "desconocida", "precio": 43.29, "tipo": "monitoreado"},
            {"producto": "diesel", "modalidad": "autoservicio", "precio": 25.12, "tipo": "historico",
             "fecha": "2026-01-01"},
        ]
        obs = normalizar_observaciones(crudas, self.NOTA, "llm")
        assert [(o["producto"], o["precio"], o["fecha"]) for o in obs] == [("superior", 45.29, "2026-09-23")]

    def test_fecha_fuera_de_rango_se_descarta(self):
        crudas = [{"producto": "regular", "modalidad": "autoservicio", "precio": 43.0,
                   "tipo": "monitoreado", "fecha": "2026-08-01"}]
        assert normalizar_observaciones(crudas, self.NOTA, "llm") == []


class TestExtraerLlm:
    def test_parsea_json_con_cercas(self):
        salida = '```json\n{"observaciones": [{"producto": "superior", "precio": 45.29}]}\n```'
        with patch("collector.noticias._llm_post", return_value=salida):
            obs = extraer_llm("texto", HOY, {"llm": {"base_url": "https://x", "model": "m"}})
        assert obs == [{"producto": "superior", "precio": 45.29}]

    def test_llm_caido_devuelve_none(self):
        with patch("collector.noticias._llm_post", return_value=None):
            assert extraer_llm("texto", HOY, {"llm": {"base_url": "https://x", "model": "m"}}) is None


# ──────────────────────────────────────────────
# Veredicto del consejo
# ──────────────────────────────────────────────

def _obs(medio, precio, fecha=HOY, producto="superior", modalidad="autoservicio"):
    return {"fecha": fecha, "producto": producto, "modalidad": modalidad, "precio": precio,
            "tipo": "monitoreado", "medio": medio, "url": f"https://{medio}/n", "extractor": "llm"}


@pytest.fixture()
def conn():
    return conectar_temporal()


class TestConsejo:
    def test_tres_fuentes_coinciden_alta(self, conn):
        guardar_observaciones(conn, [_obs("a", 45.29), _obs("b", 45.30), _obs("c", 45.25), _obs("d", 47.00)])
        v = consejo(conn, "superior", "autoservicio", HOY, {})
        assert v["confianza"] == "alta" and v["n_coinciden"] == 3 and v["n_fuentes"] == 4
        assert v["precio"] == 45.29
        assert [f["medio"] for f in v["fuentes"] if not f["coincide"]] == ["d"]

    def test_oficial_mas_una_fuente_alta(self, conn):
        guardar_precios(conn, [{"producto": "superior", "fecha": HOY, "precio": 45.29}], "MEM")
        guardar_observaciones(conn, [_obs("a", 45.29)])
        v = consejo(conn, "superior", "autoservicio", HOY, precision_fuentes(conn))
        assert v["confianza"] == "alta"
        assert MEDIO_OFICIAL in {f["medio"] for f in v["fuentes"] if f["coincide"]}

    def test_una_sola_fuente_no_oficial_baja(self, conn):
        guardar_observaciones(conn, [_obs("a", 45.29)])
        assert consejo(conn, "superior", "autoservicio", HOY, {})["confianza"] == "baja"

    def test_desacuerdo_no_es_alta(self, conn):
        guardar_observaciones(conn, [_obs("a", 45.29), _obs("b", 46.50), _obs("c", 47.90)])
        assert consejo(conn, "superior", "autoservicio", HOY, {})["confianza"] == "baja"

    def test_modalidades_no_se_mezclan(self, conn):
        guardar_observaciones(conn, [_obs("a", 45.29), _obs("b", 45.74, modalidad="servicio_completo")])
        assert consejo(conn, "superior", "autoservicio", HOY, {})["n_fuentes"] == 1

    def test_dato_mas_reciente_gana_empate(self, conn):
        """Dos medios dentro de tolerancia: la mediana favorece el más nuevo."""
        guardar_observaciones(conn, [
            _obs("viejo", 50.36, "2026-09-19", "diésel", "servicio_completo"),
            _obs("nuevo", 50.49, "2026-09-21", "diésel", "servicio_completo"),
        ])
        v = consejo(conn, "diésel", "servicio_completo", HOY, {})
        assert v["precio"] == 50.49 and v["fecha"] == "2026-09-21"

    def test_ignora_observaciones_viejas(self, conn):
        guardar_observaciones(conn, [_obs("a", 40.0, "2026-09-01")])
        assert consejo(conn, "superior", "autoservicio", HOY, {}) is None


class TestPrecision:
    def test_error_contra_oficial(self, conn):
        guardar_precios(conn, [{"producto": "superior", "fecha": HOY, "precio": 45.29}], "MEM")
        guardar_observaciones(conn, [_obs("exacto", 45.29), _obs("errado", 46.29)])
        p = precision_fuentes(conn)
        assert p["exacto"]["error_medio"] == 0 and p["exacto"]["peso"] == 1.0
        assert p["errado"]["error_medio"] == 1.0 and p["errado"]["peso"] < p["exacto"]["peso"]
        assert p[MEDIO_OFICIAL]["peso"] == 1.0


class TestGuardarObservaciones:
    def test_validacion(self, conn):
        c = guardar_observaciones(conn, [
            _obs("a", 45.29),
            _obs("a", 45.29),                                    # duplicada
            {**_obs("b", 45.0), "producto": "wti"},              # no es combustible
            {**_obs("b", 45.0), "tipo": "estimado"},             # tipo no comparable
            _obs("b", 5.0),                                      # precio implausible
        ])
        assert c == {"insertadas": 1, "duplicadas": 1, "invalidas": 3}
