"""Tests unitarios para collector/consenso_precios.py."""

import pytest


# ──────────────────────────────────────────────
# 1. Constantes y configuración
# ──────────────────────────────────────────────

class TestConstantes:
    def test_productos_obligatorios(self):
        from collector.consenso_precios import PRODUCTOS_OBLIGATORIOS
        assert PRODUCTOS_OBLIGATORIOS == ["superior", "regular", "diessel"]

    def test_tolerancia_y_min_fuentes(self):
        from collector.consenso_precios import TOLERANCIA_QUETLES, MIN_FUENTES_CONSENSO
        assert TOLERANCIA_QUETLES == 0.20
        assert MIN_FUENTES_CONSENSO == 2


# ──────────────────────────────────────────────
# 2. Función validar_consenso_grupo (sin DB)
# ──────────────────────────────────────────────

class FakeRow(dict):
    """Simula sqlite3.Row con keys() y acceso por nombre."""
    def keys(self):
        return self.__dict__.keys()


def make_row(fuente: str, precio: float):
    """Helper para crear rows simuladas."""
    row = FakeRow({"fuente": fuente, "precio": precio})
    return row


class TestValidarConsensoGrupo:
    def test_consenso_2_fuentes_iguales(self):
        from collector.consenso_precios import validar_consenso_grupo

        grupo = [
            make_row("MEM PDF", 44.61),
            make_row("MEM HTML", 44.61),
        ]
        res = validar_consenso_grupo(grupo, "superior", "2026-09-23")

        assert res["consenso"] is True
        assert res["precio_valido"] == 44.61
        assert res["fuentes_coinciden"] == 2

    def test_consenso_2_fuentes_dentro_tolerancia(self):
        from collector.consenso_precios import validar_consenso_grupo

        grupo = [
            make_row("MEM PDF", 44.61),
            make_row("MEM HTML", 44.75),  # diff Q0.14 < Q0.20
        ]
        res = validar_consenso_grupo(grupo, "superior", "2026-09-23")

        assert res["consenso"] is True
        assert res["fuentes_coinciden"] == 2

    def test_sin_consensо_diferencia_mayor(self):
        from collector.consenso_precios import validar_consenso_grupo

        grupo = [
            make_row("MEM PDF", 44.61),
            make_row("Prensa Libre", 45.00),  # diff Q0.39 > Q0.20
        ]
        res = validar_consenso_grupo(grupo, "superior", "2026-09-23")

        assert res["consenso"] is False
        assert res["precio_valido"] is None

    def test_solo_1_fuente(self):
        from collector.consenso_precios import validar_consenso_grupo

        grupo = [make_row("MEM PDF", 44.61)]
        res = validar_consenso_grupo(grupo, "regular", "2026-09-23")

        assert res["consenso"] is False
        assert res["fuentes_coinciden"] == 1

    def test_3_fuentes_con_2_dentro_tolerancia(self):
        from collector.consenso_precios import validar_consenso_grupo

        # MEM PDF y HTML coinciden, Prensa Libre está fuera
        grupo = [
            make_row("MEM PDF", 44.61),
            make_row("MEM HTML", 44.65),  # diff Q0.04
            make_row("Prensa Libre", 45.20),  # lejos de los otros dos
        ]
        res = validar_consenso_grupo(grupo, "diessel", "2026-09-23")

        assert res["consenso"] is True       # 2 fuentes dentro tolerancia
        assert res["fuentes_coinciden"] == 2
        assert len(res["precios_observados"]) == 3

    def test_3_fuentes_todas_dentro_tolerancia(self):
        from collector.consenso_precios import validar_consenso_grupo

        grupo = [
            make_row("MEM PDF", 49.40),
            make_row("MEM HTML", 49.38),
            make_row("Prensa Libre", 49.45),
        ]
        res = validar_consenso_grupo(grupo, "diessel", "2026-09-23")

        assert res["consenso"] is True
        assert res["fuentes_coinciden"] == 3


# ──────────────────────────────────────────────
# 3. Función agrupar_precios
# ──────────────────────────────────────────────

class TestAgruparPrecios:
    def test_agrupa_por_fecha_y_producto(self):
        from collector.consenso_precios import agrupar_precios

        rows = [
            FakeRow({"fecha_observacion": "2026-09-23", "producto": "superior"}),
            FakeRow({"fecha_observacion": "2026-09-23", "producto": "regular"}),
            FakeRow({"fecha_observacion": "2026-09-22", "producto": "superior"}),
        ]

        grupos = agrupar_precios(rows)
        assert len(grupos) == 3

    def test_misma_fecha_diferente_producto(self):
        from collector.consenso_precios import agrupar_precios

        rows = [
            FakeRow({"fecha_observacion": "2026-09-23", "producto": "superior"}),
            FakeRow({"fecha_observacion": "2026-09-23", "producto": "regular"}),
        ]

        grupos = agrupar_precios(rows)
        assert len(grupos) == 2  # separados por producto


# ──────────────────────────────────────────────
# 4. Ejecución completa (con DB temporal)
# ──────────────────────────────────────────────

class TestEjecutarConsenso:
    def test_ejecutar_con_datos_validados(self, tmp_path):
        """Con datos en la DB que coinciden, valida correctamente."""
        from collector.db import conectar_temporal, crear_tablas
        from datetime import datetime

        # Crear DB temporal con datos válidos
        conn = conectar_temporal()
        crear_tablas(conn)

        ahora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
        hoy = datetime.now().strftime("%Y-%m-%d")

        # Insertar precios de 2 fuentes que coinciden (dentro tolerancia)
        conn.execute(
            """INSERT INTO precios_combustible
               (fecha_observacion, producto, precio, incluye_impuestos, regimen, fuente, fetched_at)
               VALUES (?, 'superior', 44.61, 1, 'normal', 'MEM PDF', ?)""",
            (hoy, ahora),
        )
        conn.execute(
            """INSERT INTO precios_combustible
               (fecha_observacion, producto, precio, incluye_impuestos, regimen, fuente, fetched_at)
               VALUES (?, 'superior', 44.65, 1, 'normal', 'MEM HTML', ?)""",
            (hoy, ahora),
        )

        # Insertar regular sin consenso (diferencia > Q0.20)
        conn.execute(
            """INSERT INTO precios_combustible
               (fecha_observacion, producto, precio, incluye_impuestos, regimen, fuente, fetched_at)
               VALUES (?, 'regular', 42.58, 1, 'normal', 'MEM PDF', ?)""",
            (hoy, ahora),
        )

        conn.commit()

        # Patch collector.db.conectar porque ejecutar() importa desde ahí directamente
        import collector.db as db_mod
        original_conectar = getattr(db_mod, "conectar", None)
        
        # Crear una versión mock que usa nuestra DB temporal
        def mock_conectar(path=None):
            return conn
        
        db_mod.conectar = mock_conectar  # type: ignore[attr-defined]

        try:
            import collector.consenso_precios as mod
            resultado = mod.ejecutar()

            assert resultado["fuente"] == "consenso_validador"
            assert resultado["total_consultados"] > 0
            assert resultado["con_senso_alcanzado"] >= 1  # superior tiene consenso
        finally:
            if original_conectar:
                db_mod.conectar = original_conectar
            else:
                delattr(db_mod, "conectar")
            conn.close()

    def test_ejecutar_sin_datos(self, tmp_path):
        """Si no hay precios en la DB, retorna sin errores."""
        from collector.db import conectar_temporal, crear_tablas

        conn = conectar_temporal()
        crear_tablas(conn)
        conn.close()

        # Patch collector.db.conectar porque ejecutar() importa desde ahí directamente
        import collector.db as db_mod
        original_conectar = getattr(db_mod, "conectar", None)
        
        def mock_conectar(path=None):
            return conn
        
        db_mod.conectar = mock_conectar  # type: ignore[attr-defined]

        try:
            import collector.consenso_precios as mod
            resultado = mod.ejecutar()

            assert resultado["fuente"] == "consenso_validador"
            assert resultado["total_consultados"] == 0
            assert resultado["con_senso_alcanzado"] == 0
        finally:
            if original_conectar:
                db_mod.conectar = original_conectar
            else:
                delattr(db_mod, "conectar")
            conn.close()
