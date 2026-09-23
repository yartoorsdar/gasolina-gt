"""Tests unitarios para collector/impuestos.py.

Verifica:
  1. Valores de referencia (quitar_impuestos).
  2. Round-trip (quitar + agregar = original, y viceversa).
  3. Estado del decreto en fechas antes, durante y después del rango de vigencia.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from collector.impuestos import (
    agregar_impuestos,
    estado_decreto,
    precio_incluye_impuestos,
    quitar_impuestos,
)


# ──────────────────────────────────────────────
# Fixture: configuración de pruebas
# ──────────────────────────────────────────────

@pytest.fixture()
def cfg():
    """Carga la configuración real desde config.json."""
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def cfg_vigente():
    """Config con decreto vigente (inicio y fin definidos)."""
    cfg_data = {
        "impuestos": {
            "iva": 0.12,
            "idp_por_galon": {
                "superior": 4.70,
                "regular": 4.60,
                "diessel": 1.30,
            },
        },
        "decreto_22_2026": {
            "estado": "vigente",
            "fecha_aprobacion_congreso": "2026-09-22",
            "fecha_vigencia_inicio": "2026-10-01",
            "fecha_vigencia_fin": "2026-12-31",
        },
    }
    return cfg_data


@pytest.fixture()
def cfg_sin_vigencia():
    """Config con decreto pendiente (sin fecha de vigencia)."""
    cfg_data = {
        "impuestos": {
            "iva": 0.12,
            "idp_por_galon": {
                "superior": 4.70,
                "regular": 4.60,
                "diessel": 1.30,
            },
        },
        "decreto_22_2026": {
            "estado": "pendiente_sancion",
            "fecha_aprobacion_congreso": "2026-09-22",
            "fecha_vigencia_inicio": None,
            "fecha_vigencia_fin": "2026-12-31",
        },
    }
    return cfg_data


# ──────────────────────────────────────────────
# 1. Valores de verificación (quitar_impuestos)
# ──────────────────────────────────────────────

class TestValoresVerificacion:
    """Precios con impuestos → sin impuestos."""

    def test_regular(self, cfg):
        resultado = quitar_impuestos(42.00, "regular", cfg)
        assert round(resultado, 2) == pytest.approx(32.90, abs=0.01)

    def test_superior(self, cfg):
        resultado = quitar_impuestos(44.00, "superior", cfg)
        assert round(resultado, 2) == pytest.approx(34.59, abs=0.01)

    def test_diessel(self, cfg):
        resultado = quitar_impuestos(47.00, "diessel", cfg)
        assert round(resultado, 2) == pytest.approx(40.66, abs=0.01)


# ──────────────────────────────────────────────
# 2. Round-trip tests
# ──────────────────────────────────────────────

class TestRoundTrip:
    """quitar → agregar debe retornar al precio original y viceversa."""

    @pytest.mark.parametrize(
        "producto,precio_con_imp", [("regular", 42.00), ("superior", 44.00), ("diessel", 47.00)]
    )
    def test_quitar_agregar(self, cfg, producto, precio_con_imp):
        sin_imp = quitar_impuestos(precio_con_imp, producto, cfg)
        reconstruido = agregar_impuestos(sin_imp, producto, cfg)
        assert reconstruido == pytest.approx(precio_con_imp, abs=0.01)

    @pytest.mark.parametrize(
        "producto,sin_imp", [("regular", 32.90), ("superior", 34.59), ("diessel", 40.66)]
    )
    def test_agregar_quitar(self, cfg, producto, sin_imp):
        con_imp = agregar_impuestos(sin_imp, producto, cfg)
        reconstruido = quitar_impuestos(con_imp, producto, cfg)
        assert reconstruido == pytest.approx(sin_imp, abs=0.01)


# ──────────────────────────────────────────────
# 3. Estado del decreto
# ──────────────────────────────────────────────

class TestEstadoDecreto:
    """Pruebas con decreto vigente (fecha de vigencia definida)."""

    def test_fecha_anterior_al_rango(self, cfg_vigente):
        estado = estado_decreto(date(2026, 9, 1), cfg_vigente)
        assert estado == "pendiente_sancion"

    def test_dentro_del_rango(self, cfg_vigente):
        estado = estado_decreto(date(2026, 11, 15), cfg_vigente)
        assert estado == "vigente"

    def test_en_el_limite_inicio(self, cfg_vigente):
        estado = estado_decreto(date(2026, 10, 1), cfg_vigente)
        assert estado == "vigente"

    def test_en_el_limite_fin(self, cfg_vigente):
        estado = estado_decreto(date(2026, 12, 31), cfg_vigente)
        assert estado == "vigente"

    def test_fecha_posterior_al_rango(self, cfg_vigente):
        estado = estado_decreto(date(2027, 1, 15), cfg_vigente)
        assert estado == "vencido"


class TestEstadoDecretoSinVigencia:
    """Pruebas con decreto pendiente (fecha_vigencia_inicio es null)."""

    def test_pendiente_sin_vigencia(self, cfg_sin_vigencia):
        estado = estado_decreto(date(2026, 9, 1), cfg_sin_vigencia)
        assert estado == "pendiente_sancion"

        estado = estado_decreto(date(2026, 11, 15), cfg_sin_vigencia)
        assert estado == "pendiente_sancion"


# ──────────────────────────────────────────────
# 4. precio_incluye_impuestos
# ──────────────────────────────────────────────

class TestPrecioIncluyeImpuestos:
    """Determina si un precio observado incluye impuestos según la fecha."""

    def test_fuera_del_rango_sí_incluye(self, cfg_vigente):
        # Antes del decreto vigente → precio observado SÍ incluye impuestos
        assert precio_incluye_impuestos("2026-09-15", cfg_vigente) is True

    def test_dentro_del_rango_no_incluye(self, cfg_vigente):
        # Durante el decreto → precio observado NO incluye impuestos
        assert precio_incluye_impuestos("2026-11-15", cfg_vigente) is False

    def test_posterior_al_rango_sí_incluye(self, cfg_vigente):
        # Después del decreto vencido → precio observado SÍ incluye impuestos
        assert precio_incluye_impuestos("2027-03-01", cfg_vigente) is True

    def test_sin_vigencia_sí_incluye(self, cfg_sin_vigencia):
        # Sin vigencia definida → asumimos que siempre incluye impuestos
        assert precio_incluye_impuestos("2026-11-15", cfg_sin_vigencia) is True


# ──────────────────────────────────────────────
# 5. Errores y casos borde
# ──────────────────────────────────────────────

class TestErrores:
    def test_producto_invalido(self, cfg):
        with pytest.raises(ValueError, match="no válido"):
            quitar_impuestos(40.00, "gasohol", cfg)

    @pytest.mark.parametrize(
        "producto", ["superior", "regular", "diessel"]
    )
    def test_precios_cero(self, cfg, producto):
        # Precio 0 → sin impuestos = -idp (matemáticamente correcto)
        resultado = quitar_impuestos(0.0, producto, cfg)
        assert resultado == pytest.approx(-cfg["impuestos"]["idp_por_galon"][producto], abs=0.01)

    def test_agregar_retorna_mismo(self, cfg):
        precio_base = 30.00
        reconstruido = agregar_impuestos(precio_base, "regular", cfg)
        assert reconstruido == pytest.approx((precio_base + 4.60) * 1.12, abs=0.01)
