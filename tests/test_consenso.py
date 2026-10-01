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

    def test_lista_bajo_encabezado_de_modalidad(self):
        """Formato de Publinews 1-oct-2026 (antes el corte de 20 caracteres borraba las líneas)."""
        t = ("Tras exoneración de impuestos, gasolineras aplican cambios en precios de combustibles\n"
             "Los precios reportados este jueves 1 de octubre son los siguientes:\n"
             "Autoservicio\nSúper: Q36.19\nRegular: Q34.89\nDiésel: Q41.69\n"
             "Servicio completo\nSúper: Q37.29\nRegular: Q35.99\nDiésel: Q42.79\n"
             "Los precios pueden variar entre estaciones de servicio.")
        assert self._precios(t) == {
            ("superior", "autoservicio", 36.19), ("regular", "autoservicio", 34.89), ("diésel", "autoservicio", 41.69),
            ("superior", "servicio_completo", 37.29), ("regular", "servicio_completo", 35.99),
            ("diésel", "servicio_completo", 42.79)}

    def test_lista_con_contexto_de_futuro_se_descarta(self):
        t = ("Así quedan los costos del combustible\nA partir del jueves los precios serán:\n"
             "Autoservicio\nSúper: Q36.24\nRegular: Q34.96")
        assert self._precios(t) == set()

    def test_oracion_sin_modalidad_va_como_desconocida(self):
        """Soy502 1-oct-2026: varios productos, presente, sin modalidad → respaldo."""
        t = ("Así amanecieron los precios de los combustibles este 1 de octubre\n"
             "Los precios amanecieron así: gasolina superior Q36.19 , gasolina regular Q34.89 y diésel Q41.69.")
        assert self._precios(t) == {("superior", "desconocida", 36.19), ("regular", "desconocida", 34.89),
                                    ("diésel", "desconocida", 41.69)}

    def test_sin_modalidad_descarta_estimados_y_precios_pasados(self):
        """Textuales de La Hora 23-sep, DCA 22-sep y Soy502 30-sep."""
        for t in (
            "De Q40.66 a Q32.90: así quedarían los combustibles sin IDP e IVA\n"
            "• Diésel : Q40.66 por galón • Gasolina regular : Q32.90 • Gasolina superior : Q34.59",
            "Titular\nCon esos parámetros, los valores de referencia son Q34.59 para la superior, "
            "Q32.90 para la regular y Q40.66 para el diésel, una vez aplicada la medida.",
            "Titular\nMonitoreos realizados por las autoridades hasta el pasado lunes 28 de septiembre muestran que, "
            "los precios se encuentran de la siguiente manera: gasolina superior Q45.29, gasolina regular Q43.29.",
            "Titular\nLa gasolina superior pasaría de Q45.29 a Q36.24 y la regular de Q43.29 a Q34.96.",
        ):
            assert self._precios(t) == set(), t


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
        # La modalidad desconocida se GUARDA así (respaldo en el consejo), nunca se adivina
        assert [(o["producto"], o["modalidad"], o["precio"], o["fecha"]) for o in obs] == [
            ("superior", "autoservicio", 45.29, "2026-09-23"), ("regular", "desconocida", 43.29, "2026-09-23")]

    def test_precio_que_no_aparece_en_la_nota_se_descarta(self):
        """Defensa contra cifras inventadas o calculadas por el LLM."""
        texto = "Autoservicio\nSúper: Q36.19\nRegular: Q 34,89"
        crudas = [
            {"producto": "superior", "modalidad": "autoservicio", "precio": 36.19, "tipo": "monitoreado"},
            {"producto": "regular", "modalidad": "autoservicio", "precio": 34.89, "tipo": "monitoreado"},
            {"producto": "diesel", "modalidad": "autoservicio", "precio": 41.69, "tipo": "monitoreado"},
        ]
        obs = normalizar_observaciones(crudas, self.NOTA, "llm", texto)
        assert [o["precio"] for o in obs] == [36.19, 34.89]

    def test_precio_anunciado_para_manana_entra_con_su_fecha(self):
        """Nota del 30-sep: "a partir de este jueves 1 de octubre…" → fecha 1-oct."""
        nota = {"url": "https://m/2", "medio": "m", "publicado": "2026-09-30"}
        crudas = [{"producto": "superior", "modalidad": "autoservicio", "precio": 36.24,
                   "tipo": "referencia", "fecha": "2026-10-01"},
                  {"producto": "superior", "modalidad": "autoservicio", "precio": 36.24,
                   "tipo": "referencia", "fecha": "2026-10-03"}]   # más de 1 día después: fuera
        assert [o["fecha"] for o in normalizar_observaciones(crudas, nota, "llm")] == ["2026-10-01"]

    def test_fecha_fuera_de_rango_se_descarta(self):
        crudas = [{"producto": "regular", "modalidad": "autoservicio", "precio": 43.0,
                   "tipo": "monitoreado", "fecha": "2026-08-01"}]
        assert normalizar_observaciones(crudas, self.NOTA, "llm") == []


class TestCifraEnTexto:
    def test_variantes(self):
        from collector.consenso_precios import _cifra_en_texto
        assert _cifra_en_texto(36.24, "Q 36,24 por galón")
        assert _cifra_en_texto(36.2, "cuesta Q36.20")
        assert _cifra_en_texto(36.2, "cuesta Q36.2.")
        assert not _cifra_en_texto(36.24, "Q136.24")
        assert not _cifra_en_texto(36.24, "Q36.241")
        assert not _cifra_en_texto(36.24, "Q36.42")


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


DECRETO = {"decreto_22_2026": {"fecha_vigencia_inicio": "2026-10-01", "fecha_vigencia_fin": "2026-12-31"}}
HOY_DECRETO = "2026-10-01"


def _desc(medio, precio, fecha=HOY_DECRETO, producto="superior", tipo="monitoreado"):
    return {**_obs(medio, precio, fecha, producto, "desconocida"), "tipo": tipo}


class TestConsejoDecreto:
    """Caso real del 1-oct-2026: el galón baja ~Q9 al quitar IVA+IDP."""

    def _escenario(self, conn):
        guardar_observaciones(conn, [
            # 28-sep, con impuestos (3 medios coinciden)
            _obs("prensalibre.com", 45.29, "2026-09-28"), _obs("emisoras.com", 45.29, "2026-09-28"),
            _obs("publinews.gt", 45.29, "2026-09-28"),
            # 1-oct, sin impuestos: Publinews con modalidad; Soy502 y Emisoras sin modalidad
            _obs("publinews.gt", 36.19, HOY_DECRETO),
            _obs("publinews.gt", 37.29, HOY_DECRETO, modalidad="servicio_completo"),
            _desc("soy502.com", 36.19), _desc("emisoras.com", 36.19),
            # Anuncio del MEM (cálculo) sin modalidad: referencia
            _desc("dca.gob.gt", 36.24, tipo="referencia"),
        ])

    def test_no_mezcla_precios_con_y_sin_impuestos(self, conn):
        self._escenario(conn)
        v = consejo(conn, "superior", "autoservicio", HOY_DECRETO, {}, DECRETO)
        assert v["fecha"] == HOY_DECRETO and v["precio"] == 36.19
        assert all(f["fecha"] == HOY_DECRETO for f in v["fuentes"])

    def test_sin_modalidad_respalda_y_sube_la_confianza(self, conn):
        self._escenario(conn)
        v = consejo(conn, "superior", "autoservicio", HOY_DECRETO, {}, DECRETO)
        assert v["confianza"] == "alta" and v["n_coinciden"] == 3
        inferidas = {f["medio"] for f in v["fuentes"] if f["modalidad_inferida"]}
        assert inferidas == {"soy502.com", "emisoras.com"}
        # Un precio ANUNCIADO no respalda uno monitoreado
        assert "dca.gob.gt" not in {f["medio"] for f in v["fuentes"]}

    def test_sin_modalidad_nunca_crea_grupo_sola(self, conn):
        guardar_observaciones(conn, [_desc("soy502.com", 36.19), _desc("emisoras.com", 36.19)])
        assert consejo(conn, "superior", "autoservicio", HOY_DECRETO, {}, DECRETO) is None

    def test_anunciado_de_autoservicio_no_respalda_servicio_completo(self, conn):
        """Diésel 1-oct: anunciado Q42.94 (autoservicio) a Q0.15 del SC monitoreado Q42.79."""
        guardar_observaciones(conn, [
            _obs("publinews.gt", 41.69, HOY_DECRETO, "diésel"),
            _obs("publinews.gt", 42.79, HOY_DECRETO, "diésel", "servicio_completo"),
            _desc("dca.gob.gt", 42.94, producto="diésel", tipo="referencia"),
            _desc("soy502.com", 41.69, producto="diésel"),
        ])
        sc = consejo(conn, "diésel", "servicio_completo", HOY_DECRETO, {}, DECRETO)
        assert [f["medio"] for f in sc["fuentes"]] == ["publinews.gt"]
        aut = consejo(conn, "diésel", "autoservicio", HOY_DECRETO, {}, DECRETO)
        assert aut["n_coinciden"] == 2 and aut["precio"] == 41.69

    def test_sin_modalidad_cerca_de_la_otra_modalidad_no_respalda(self, conn):
        guardar_observaciones(conn, [
            _obs("a.gt", 36.19, HOY_DECRETO), _obs("b.gt", 36.30, HOY_DECRETO, modalidad="servicio_completo"),
            _desc("c.gt", 36.25),   # a 0.06 de AS y 0.05 de SC: ambiguo
        ])
        v = consejo(conn, "superior", "autoservicio", HOY_DECRETO, {}, DECRETO)
        assert "c.gt" not in {f["medio"] for f in v["fuentes"]}

    def test_monitoreado_gana_a_anunciado_si_difieren(self, conn):
        guardar_observaciones(conn, [
            _obs("publinews.gt", 41.69, HOY_DECRETO, "diésel"),
            {**_obs("prensalibre.com", 42.94, HOY_DECRETO, "diésel"), "tipo": "referencia"},
        ])
        assert consejo(conn, "diésel", "autoservicio", HOY_DECRETO, {}, DECRETO)["precio"] == 41.69

    def test_medio_que_se_contradice_no_vota(self, conn, capsys):
        """Publinews 1-oct: diésel servicio completo Q42.79 en una nota y Q43.79 en otra."""
        guardar_observaciones(conn, [
            {**_obs("publinews.gt", 42.79, HOY_DECRETO, "diésel", "servicio_completo"), "url": "https://p/1"},
            {**_obs("publinews.gt", 43.79, HOY_DECRETO, "diésel", "servicio_completo"), "url": "https://p/2"},
            _obs("otro.gt", 42.79, HOY_DECRETO, "diésel", "servicio_completo"),
        ])
        v = consejo(conn, "diésel", "servicio_completo", HOY_DECRETO, {}, DECRETO)
        assert [f["medio"] for f in v["fuentes"]] == ["otro.gt"]
        capsys.readouterr().out.encode("cp1252")  # el aviso es imprimible en la consola del runner

    def test_contradiccion_con_mayoria_se_resuelve(self, conn):
        guardar_observaciones(conn, [
            {**_obs("p.gt", 42.79, HOY_DECRETO), "url": "https://p/1"},
            {**_obs("p.gt", 42.79, HOY_DECRETO), "url": "https://p/2"},
            {**_obs("p.gt", 43.79, HOY_DECRETO), "url": "https://p/3"},
        ])
        assert consejo(conn, "superior", "autoservicio", HOY_DECRETO, {}, DECRETO)["precio"] == 42.79


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
