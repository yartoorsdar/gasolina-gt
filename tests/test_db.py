"""Tests unitarios para collector/db.py (esquema genérico: catálogo + precios).

Verifica:
  - Catálogo de productos y vistas historial_<producto>.
  - guardar_precios: insertar / actualizar / sin_cambio / descartar por prioridad.
  - borrar_precios y lecturas con los mismos filtros.
  - Normalización de DBs viejas (diessel → diésel, bunker fuera, fuentes canónicas).
  - Noticias y ejecuciones.
"""

import sqlite3

import pytest

from collector.db import (
    PRODUCTOS,
    borrar_precios,
    canon_fuente,
    conectar_temporal,
    crear_tablas,
    guardar_precios,
    insertar_ejecucion,
    insertar_noticia,
    leer_actuales,
    leer_historial,
    leer_ultimo,
    obtener_noticias,
    obtener_ultimas_ejecuciones,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """Conexión a base en memoria con todas las tablas."""
    return conectar_temporal()


def _fila(producto, fecha, precio, fuente=None):
    f = {"producto": producto, "fecha": fecha, "precio": precio}
    if fuente:
        f["fuente"] = fuente
    return f


# ──────────────────────────────────────────────
# 1. Estructura
# ──────────────────────────────────────────────

class TestEstructura:
    def test_catalogo_productos(self, conn):
        codigos = [r["codigo"] for r in conn.execute("SELECT codigo FROM productos ORDER BY orden")]
        assert codigos == ["regular", "superior", "diésel", "wti"]

    def test_vista_por_producto(self, conn):
        vistas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        assert vistas == {f"historial_{d['archivo']}" for d in PRODUCTOS.values()}

    def test_vista_filtra_su_producto(self, conn):
        guardar_precios(conn, [_fila("diésel", "2026-09-15", 49.0), _fila("wti", "2026-09-15", 90.0)], "MEM")
        rows = conn.execute("SELECT precio FROM historial_diesel").fetchall()
        assert [r["precio"] for r in rows] == [49.0]

    def test_crear_tablas_idempotente(self, conn):
        crear_tablas(conn)
        crear_tablas(conn)
        assert conn.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == len(PRODUCTOS)


# ──────────────────────────────────────────────
# 2. Escritura genérica (upsert)
# ──────────────────────────────────────────────

class TestGuardarPrecios:
    def test_insertar(self, conn):
        c = guardar_precios(conn, [_fila("regular", "2026-09-15", 42.0)], "MEM")
        assert c["insertados"] == 1
        assert leer_ultimo(conn, "regular")["precio"] == 42.0

    def test_reejecutar_actualiza_no_duplica(self, conn):
        guardar_precios(conn, [_fila("regular", "2026-09-15", 42.0)], "MEM")
        c = guardar_precios(conn, [_fila("regular", "2026-09-15", 42.5)], "MEM")
        assert c["actualizados"] == 1
        assert conn.execute("SELECT COUNT(*) FROM precios").fetchone()[0] == 1
        assert leer_ultimo(conn, "regular")["precio"] == 42.5

    def test_mismo_valor_sin_cambio(self, conn):
        guardar_precios(conn, [_fila("wti", "2026-09-15", 90.0)], "OilPriceAPI")
        c = guardar_precios(conn, [_fila("wti", "2026-09-15", 90.0)], "OilPriceAPI")
        assert c["sin_cambio"] == 1

    def test_fuente_baja_no_pisa_oficial(self, conn):
        guardar_precios(conn, [_fila("superior", "2026-09-15", 44.0)], "MEM")
        c = guardar_precios(conn, [_fila("superior", "2026-09-15", 43.0)], "Chapin TV")
        assert c["descartados"] == 1
        assert leer_ultimo(conn, "superior")["precio"] == 44.0

    def test_oficial_pisa_fuente_baja(self, conn):
        guardar_precios(conn, [_fila("superior", "2026-09-15", 43.0)], "Chapin TV")
        c = guardar_precios(conn, [_fila("superior", "2026-09-15", 44.0)], "Ministerio de Energía y Minas")
        assert c["actualizados"] == 1
        row = leer_ultimo(conn, "superior")
        assert (row["precio"], row["fuente"]) == (44.0, "MEM")

    def test_sin_sobrescribir_solo_siembra(self, conn):
        guardar_precios(conn, [_fila("regular", "2026-09-15", 42.0)], "MEM")
        c = guardar_precios(conn, [_fila("regular", "2026-09-15", 41.0)], "MEM", sobrescribir=False)
        assert c["descartados"] == 1
        assert leer_ultimo(conn, "regular")["precio"] == 42.0

    def test_alias_de_producto(self, conn):
        guardar_precios(conn, [_fila("diessel", "2026-09-15", 49.0), _fila("super", "2026-09-15", 44.0)], "MEM")
        assert set(leer_actuales(conn)) == {"diésel", "superior"}

    def test_filas_invalidas(self, conn):
        c = guardar_precios(conn, [
            _fila("bunker", "2026-09-15", 20.0),       # fuera del catálogo
            _fila("regular", "15/09/2026", 42.0),     # fecha mal formada
            _fila("regular", "2026-09-15", None),     # sin precio
            _fila("regular", "2026-09-15", -1),       # precio no positivo
        ], "MEM")
        assert c["invalidos"] == 4
        assert conn.execute("SELECT COUNT(*) FROM precios").fetchone()[0] == 0

    def test_fuente_por_fila_gana_a_la_default(self, conn):
        guardar_precios(conn, [_fila("regular", "2026-09-15", 42.0, fuente="GlobalPetrolPrices")], "otra")
        assert leer_ultimo(conn, "regular")["fuente"] == "GlobalPetrolPrices"


# ──────────────────────────────────────────────
# 3. Lectura y borrado (mismos filtros)
# ──────────────────────────────────────────────

class TestLecturaBorrado:
    @pytest.fixture()
    def datos(self, conn):
        guardar_precios(conn, [
            _fila("regular", "2026-09-01", 41.0),
            _fila("regular", "2026-09-10", 41.5),
            _fila("regular", "2026-09-15", 42.0),
            _fila("superior", "2026-09-12", 43.0),
        ], "MEM")
        return conn

    def test_historial_por_producto_ordenado(self, datos):
        assert [r["fecha"] for r in leer_historial(datos, "regular")] == ["2026-09-01", "2026-09-10", "2026-09-15"]

    def test_historial_rango(self, datos):
        rows = leer_historial(datos, "regular", desde="2026-09-05", hasta="2026-09-12")
        assert [r["precio"] for r in rows] == [41.5]

    def test_leer_actuales(self, datos):
        actuales = leer_actuales(datos)
        assert actuales["regular"]["fecha"] == "2026-09-15"
        assert actuales["superior"]["precio"] == 43.0
        assert "wti" not in actuales

    def test_borrar_rango(self, datos):
        assert borrar_precios(datos, "regular", desde="2026-09-10") == 2
        assert [r["fecha"] for r in leer_historial(datos, "regular")] == ["2026-09-01"]

    def test_borrar_por_fuente(self, datos):
        guardar_precios(datos, [_fila("wti", "2026-09-15", 90.0)], "OilPriceAPI")
        assert borrar_precios(datos, fuente="OilPriceAPI") == 1
        assert leer_ultimo(datos, "wti") is None


# ──────────────────────────────────────────────
# 4. Normalización de DBs viejas
# ──────────────────────────────────────────────

class TestNormalizacion:
    def _insertar_crudo(self, conn, fecha, producto, precio, fuente):
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            (fecha, producto, precio, fuente, "2026-09-01T00:00:00-06:00"),
        )

    def test_diessel_duplicado_y_bunker(self, conn):
        self._insertar_crudo(conn, "2024-01-01", "diessel", 29.0, "Ministerio de Energía y Minas")
        self._insertar_crudo(conn, "2024-01-01", "diésel", 29.5, "Ministerio de Energía y Minas")
        self._insertar_crudo(conn, "2024-01-02", "diessel", 29.1, "Ministerio de Energía y Minas")
        self._insertar_crudo(conn, "2024-01-01", "bunker", 20.0, "Ministerio de Energía y Minas")
        crear_tablas(conn)

        rows = conn.execute("SELECT fecha, producto, precio, fuente FROM precios ORDER BY fecha").fetchall()
        assert [tuple(r) for r in rows] == [
            ("2024-01-01", "diésel", 29.5, "MEM"),
            ("2024-01-02", "diésel", 29.1, "MEM"),
        ]

    def test_canon_fuente(self):
        assert canon_fuente("Ministerio de Energia y Minas (HTML)") == "MEM"
        assert canon_fuente("MEM HTML") == "MEM"
        assert canon_fuente(None) == "manual"
        assert canon_fuente("Fuente Nueva") == "Fuente Nueva"


# ──────────────────────────────────────────────
# 5. Noticias y ejecuciones
# ──────────────────────────────────────────────

class TestNoticiasEjecuciones:
    def test_obtener_noticias(self, conn):
        from collector.db import hoy_gt
        insertar_noticia(conn, "https://example.com/1", "Ataque a oleoducto", "Agencia X",
                         publicado_at=hoy_gt(), relevancia=5)
        insertar_noticia(conn, "https://example.com/2", "Vieja", "Reuters", publicado_at="2020-01-01")
        assert [n["titulo"] for n in obtener_noticias(conn, dias=30)] == ["Ataque a oleoducto"]

    def test_noticias_no_se_repiten(self, conn):
        for _ in range(5):
            insertar_noticia(conn, "https://example.com/unique", "Título", "Agencia")
        assert conn.execute("SELECT COUNT(*) FROM noticias").fetchone()[0] == 1

    def test_ejecuciones(self, conn):
        insertar_ejecucion(conn, "petroleo", ok=0, mensaje="Error API")
        ultimas = obtener_ultimas_ejecuciones(conn)
        assert ultimas[0]["modulo"] == "petroleo"
        assert ultimas[0]["fin"] is not None
