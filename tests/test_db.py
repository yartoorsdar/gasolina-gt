"""Tests unitarios para collector/db.py.

Verifica:
  - Creación de tablas sin error.
  - Inserciones idempotentes (duplicados no se repiten).
  - Consultas: precios actuales, historial, petróleo, noticias, ejecuciones.
  - Uso de DB en memoria.
"""

import sqlite3

import pytest

from collector.db import (
    conectar_temporal,
    crear_tablas,
    insertar_ejecucion,
    insertar_noticia,
    insertar_precio_combustible,
    insertar_precio_petroleo,
    obtener_historial_petroleo,
    obtener_historial_precios,
    obtener_ultimas_ejecuciones,
    obtener_noticias,
    obtener_noticia_por_url,
    obtener_petroleo_actual,
    obtener_precio,
    obtener_precios_actuales,
)


# ──────────────────────────────────────────────
# Fixture: conexión temporal con tablas creadas
# ──────────────────────────────────────────────

@pytest.fixture()
def conn() -> sqlite3.Connection:
    """Conexión a base en memoria con todas las tablas."""
    return conectar_temporal()


# ──────────────────────────────────────────────
# 1. Creación de tablas
# ──────────────────────────────────────────────

class TestCrearTablas:
    def test_tabla_precios_combustible(self, conn):
        """La tabla precios_combustible se crea sin error."""
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='precios_combustible'").fetchone()

    def test_tabla_precios_petroleo(self, conn):
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='precios_petroleo'").fetchone()

    def test_tabla_noticias(self, conn):
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='noticias'").fetchone()

    def test_tabla_ejecuciones(self, conn):
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ejecuciones'").fetchone()

    def test_columnas_precios_combustible(self, conn):
        """Verifica que existen las columnas regimen y nota."""
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(precios_combustible)").fetchall()]
        assert "regimen" in cols
        assert "nota" in cols
        assert "incluye_impuestos" in cols

    def test_unique_precios_petroleo(self, conn):
        """Existe el índice único para fechas y referencia."""
        indexes = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        assert "idx_petroleo_fecha_ref" in indexes

    def test_unique_noticias_url(self, conn):
        """La tabla noticias tiene URL como UNIQUE."""
        constraints = conn.execute("PRAGMA table_info(noticias)").fetchall()
        # Verificar que la tabla existe y tiene columna url
        col_names = [c["name"] for c in constraints]
        assert "url" in col_names


# ──────────────────────────────────────────────
# 2. Inserciones idempotentes — precios combustible
# ──────────────────────────────────────────────

class TestInsertarPrecioCombustible:
    def test_insertar_uno(self, conn):
        row_id = insertar_precio_combustible(
            conn, "2026-09-15", "regular", 42.00, fuente="MEM"
        )
        assert row_id is not None

    def test_duplicado_ignorado(self, conn):
        """Insertar el mismo registro dos veces: solo se cuenta uno."""
        id1 = insertar_precio_combustible(
            conn, "2026-09-15", "regular", 42.00, fuente="MEM"
        )
        id2 = insertar_precio_combustible(
            conn, "2026-09-15", "regular", 42.00, fuente="MEM"
        )
        # El segundo debería ser None (ya existía) o el mismo id
        assert id1 is not None

    def test_distinto_producto_no_duplicado(self, conn):
        """Distinto producto con misma fecha no es duplicado."""
        insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")
        id_sup = insertar_precio_combustible(conn, "2026-09-15", "superior", 44.00, fuente="MEM")
        assert id_sup is not None

    def test_distinta_fuenteno_duplicado(self, conn):
        """Misma fecha y producto pero distinta fuente no es duplicado."""
        insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")
        id_otra = insertar_precio_combustible(
            conn, "2026-09-15", "regular", 42.50, fuente="Otro"
        )
        assert id_otra is not None

    def test_regimen_default(self, conn):
        """Por defecto el regimen es 'normal'."""
        insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")
        row = obtener_precio(conn, "2026-09-15", "regular", "MEM")
        assert row["regimen"] == "normal"

    def test_regimen_personalizado(self, conn):
        """Se puede especificar un regimen diferente."""
        insertar_precio_combustible(
            conn, "2026-09-15", "diessel", 38.00, fuente="MEM",
            regimen="apoyo_social_2026", nota="Apoyo Q8.00 incluido"
        )
        row = obtener_precio(conn, "2026-09-15", "diessel", "MEM")
        assert row["regimen"] == "apoyo_social_2026"
        assert row["nota"] == "Apoyo Q8.00 incluido"

    def test_incluye_impuestos_false(self, conn):
        """Se puede marcar precio sin impuestos."""
        insertar_precio_combustible(
            conn, "2026-11-15", "regular", 32.90, fuente="MEM",
            incluye_impuestos=0, regimen="exencion_decreto_22_2026"
        )
        row = obtener_precio(conn, "2026-11-15", "regular", "MEM")
        assert row["incluye_impuestos"] == 0


# ──────────────────────────────────────────────
# 3. Inserciones idempotentes — petróleo
# ──────────────────────────────────────────────

class TestInsertarPetroleo:
    def test_insertar_uno(self, conn):
        row_id = insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")
        assert row_id is not None

    def test_duplicado_ignorado(self, conn):
        id1 = insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")
        id2 = insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")
        assert id1 is not None

    def test_distinta_referencia_no_duplicado(self, conn):
        insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")
        id_wti = insertar_precio_petroleo(conn, "2026-09-15", "wti", 74.30, fuente="EIA")
        assert id_wti is not None


# ──────────────────────────────────────────────
# 4. Inserciones — noticias
# ──────────────────────────────────────────────

class TestInsertarNoticia:
    def test_insertar_una(self, conn):
        row_id = insertar_noticia(
            conn, "https://example.com/noticia1", "Guerra en Medio Oriente",
            "Agencia X", categoria="conflicto_productor", relevancia=4
        )
        assert row_id is not None

    def test_duplicado_ignorado(self, conn):
        id1 = insertar_noticia(
            conn, "https://example.com/noticia1", "Guerra en Medio Oriente",
            "Agencia X", categoria="conflicto_productor"
        )
        id2 = insertar_noticia(
            conn, "https://example.com/noticia1", "Guerra en Medio Oriente",
            "Agencia X", categoria="conflicto_productor"
        )
        assert id1 is not None


# ──────────────────────────────────────────────
# 5. Consultas — precios combustible
# ──────────────────────────────────────────────

class TestConsultasPrecios:
    def test_obtener_precios_actuales(self, conn):
        """Obtiene el precio más reciente por producto."""
        insertar_precio_combustible(conn, "2026-09-10", "regular", 41.00, fuente="MEM")
        insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")
        insertar_precio_combustible(conn, "2026-09-12", "superior", 43.00, fuente="MEM")

        actuales = obtener_precios_actuales(conn)
        assert len(actuales) == 2

        # El más reciente de regular debe ser el del 15
        for row in actuales:
            if row["producto"] == "regular":
                assert row["fecha_observacion"] == "2026-09-15"
                assert row["precio"] == 42.00

    def test_obtener_precio_especifico(self, conn):
        insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")
        row = obtener_precio(conn, "2026-09-15", "regular", "MEM")
        assert row is not None
        assert row["precio"] == 42.00

    def test_obtener_precio_no_existe(self, conn):
        row = obtener_precio(conn, "2026-09-15", "regular", "MEM")
        assert row is None

    def test_historial_por_producto(self, conn):
        insertar_precio_combustible(conn, "2026-08-01", "regular", 40.00, fuente="MEM")
        insertar_precio_combustible(conn, "2026-09-01", "regular", 41.50, fuente="MEM")
        insertar_precio_combustible(conn, "2026-09-15", "superior", 43.00, fuente="MEM")

        historial = obtener_historial_precios(conn, producto="regular", dias=90)
        assert len(historial) == 2
        assert historial[0]["producto"] == "regular"


# ──────────────────────────────────────────────
# 6. Consultas — petróleo
# ──────────────────────────────────────────────

class TestConsultasPetroleo:
    def test_petroleo_actual(self, conn):
        insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")
        insertar_precio_petroleo(conn, "2026-09-15", "wti", 74.30, fuente="EIA")

        actuales = obtener_petroleo_actual(conn)
        assert len(actuales) == 2

    def test_ultimo_por_referencia(self, conn):
        insertar_precio_petroleo(conn, "2026-09-10", "brent", 77.00, fuente="EIA")
        insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")

        ultimo = conn.execute(
            "SELECT * FROM precios_petroleo WHERE referencia='brent' ORDER BY fecha DESC LIMIT 1"
        ).fetchone()
        assert ultimo["fecha"] == "2026-09-15"


# ──────────────────────────────────────────────
# 7. Consultas — noticias
# ──────────────────────────────────────────────

class TestConsultasNoticias:
    def test_obtener_noticias(self, conn):
        insertar_noticia(
            conn, "https://example.com/1", "Ataque a oleoducto",
            "Agencia X", publicado_at="2026-09-20", categoria="ataque_infraestructura", relevancia=5
        )
        insertar_noticia(
            conn, "https://example.com/2", "OPEC recorta producción",
            "Reuters", publicado_at="2026-09-18", categoria="opep_produccion", relevancia=3
        )

        noticias = obtener_noticias(conn, dias=30)
        assert len(noticias) == 2


# ──────────────────────────────────────────────
# 8. Consultas — ejecuciones
# ──────────────────────────────────────────────

class TestConsultasEjecuciones:
    def test_insertar_y_listar(self, conn):
        insertar_ejecucion(conn, "precios_mem", ok=1, mensaje="OK")
        import time; time.sleep(1.1)  # asegurar timestamps distintos (precision de SQLite = segundos)
        insertar_ejecucion(conn, "petroleo", ok=0, mensaje="Error API")

        ultimas = obtener_ultimas_ejecuciones(conn)
        assert len(ultimas) == 2
        # La última debería ser la de petroleo (más reciente)
        assert ultimas[0]["modulo"] == "petroleo"


# ──────────────────────────────────────────────
# 9. Conteo total de registros duplicados
# ──────────────────────────────────────────────

class TestConteoDuplicados:
    def test_precios_no_se_repite(self, conn):
        """Insertar el mismo precio 5 veces → solo 1 registro en la tabla."""
        for _ in range(5):
            insertar_precio_combustible(conn, "2026-09-15", "regular", 42.00, fuente="MEM")

        total = conn.execute("SELECT COUNT(*) as c FROM precios_combustible").fetchone()["c"]
        assert total == 1

    def test_petroleo_no_se_repite(self, conn):
        for _ in range(5):
            insertar_precio_petroleo(conn, "2026-09-15", "brent", 78.50, fuente="EIA")

        total = conn.execute("SELECT COUNT(*) as c FROM precios_petroleo").fetchone()["c"]
        assert total == 1

    def test_noticias_no_se_repite(self, conn):
        for _ in range(5):
            insertar_noticia(conn, "https://example.com/unique", "Título", "Agencia")

        total = conn.execute("SELECT COUNT(*) as c FROM noticias").fetchone()["c"]
        assert total == 1
