"""Tests de la memoria persistente (collector/memoria.py): un CSV por producto.

Semántica que debe mantenerse:
  - Round-trip exportar→importar reproduce la tabla.
  - Un archivo por producto del catálogo, mismas columnas en todos.
  - Determinismo: mismo contenido = archivo byte-idéntico (git sin diff).
  - Importar siembra: no pisa filas existentes.
"""

from collector.db import PRODUCTOS, conectar_temporal, guardar_precios
from collector.memoria import exportar_memoria, importar_memoria, leer_mensual, leer_semilla_anual


def _db_con_filas():
    conn = conectar_temporal()
    guardar_precios(conn, [
        {"producto": "superior", "fecha": "2026-09-16", "precio": 44.66},
        {"producto": "regular", "fecha": "2026-09-16", "precio": 42.58},
        {"producto": "diésel", "fecha": "2026-09-16", "precio": 49.4},
    ], "MEM")
    guardar_precios(conn, [{"producto": "wti", "fecha": "2026-09-24", "precio": 93.56}], "OilPriceAPI")
    return conn


def _filas(conn):
    return sorted(tuple(r) for r in conn.execute("SELECT fecha, producto, modalidad, precio, fuente FROM precios"))


def test_un_csv_por_producto(tmp_path):
    from collector.memoria import TABLAS_MEMORIA
    exportar_memoria(conn=_db_con_filas(), carpeta=tmp_path)
    esperados = [f"{d['archivo']}.csv" for d in PRODUCTOS.values()] + [f"{t}.csv" for t in TABLAS_MEMORIA]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(esperados)
    assert (tmp_path / "diesel.csv").read_text(encoding="utf-8") == (
        "fecha,modalidad,precio,fuente\n2026-09-16,autoservicio,49.4,MEM\n")
    assert (tmp_path / "wti.csv").read_text(encoding="utf-8").splitlines()[1].startswith("2026-09-24,spot,")


def test_csv_formato_anterior_sin_modalidad(tmp_path):
    """Los CSV commitados antes de las modalidades se leen como la principal."""
    (tmp_path / "superior.csv").write_text("fecha,precio,fuente\n2026-09-16,44.66,MEM\n", encoding="utf-8")
    conn = conectar_temporal()
    importar_memoria(conn=conn, carpeta=tmp_path)
    assert tuple(conn.execute("SELECT modalidad, precio FROM precios").fetchone()) == ("autoservicio", 44.66)


def test_modalidades_y_tablas_del_consejo_round_trip(tmp_path):
    from collector.db import guardar_consenso, guardar_observaciones
    conn = _db_con_filas()
    guardar_precios(conn, [{"producto": "superior", "modalidad": "servicio_completo",
                            "fecha": "2026-09-16", "precio": 45.68}], "MEM")
    guardar_observaciones(conn, [{"fecha": "2026-09-16", "producto": "superior", "modalidad": "autoservicio",
                                  "precio": 44.66, "tipo": "monitoreado", "medio": "m", "url": "https://m/1",
                                  "extractor": "llm", "cita": "súper Q44.66"}])
    guardar_consenso(conn, {"fecha": "2026-09-16", "producto": "superior", "modalidad": "autoservicio",
                            "precio": 44.66, "confianza": "alta", "n_coinciden": 2, "n_fuentes": 2,
                            "fuentes": [{"medio": "m", "precio": 44.66}]})
    exportar_memoria(conn=conn, carpeta=tmp_path)
    vacia = conectar_temporal()
    n = importar_memoria(conn=vacia, carpeta=tmp_path)
    assert n["observaciones"] == 1 and n["consenso"] == 1
    assert _filas(vacia) == _filas(conn)
    assert vacia.execute("SELECT confianza FROM consenso").fetchone()[0] == "alta"


def test_round_trip(tmp_path):
    conn = _db_con_filas()
    exportar_memoria(conn=conn, carpeta=tmp_path)
    vacia = conectar_temporal()
    insertadas = importar_memoria(conn=vacia, carpeta=tmp_path)
    assert sum(insertadas.values()) == 4
    assert _filas(vacia) == _filas(conn)


def test_determinista(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    exportar_memoria(conn=_db_con_filas(), carpeta=a)
    exportar_memoria(conn=_db_con_filas(), carpeta=b)
    for p in a.iterdir():
        assert p.read_bytes() == (b / p.name).read_bytes()


def test_sin_archivos_es_primer_ciclo(tmp_path):
    assert sum(importar_memoria(conn=conectar_temporal(), carpeta=tmp_path).values()) == 0


def test_importar_no_pisa_existente(tmp_path):
    exportar_memoria(conn=_db_con_filas(), carpeta=tmp_path)  # superior 16-sep = 44.66
    otra = conectar_temporal()
    guardar_precios(otra, [{"producto": "superior", "fecha": "2026-09-16", "precio": 45.0}], "MEM")
    importar_memoria(conn=otra, carpeta=tmp_path)
    assert otra.execute(
        "SELECT precio FROM precios WHERE fecha='2026-09-16' AND producto='superior'"
    ).fetchone()[0] == 45.0


def test_semilla_anual(tmp_path):
    path = tmp_path / "anual_semilla.csv"
    path.write_text("anio,producto,promedio,fuente\n2020,regular,21.24,consolidado histórico\n", encoding="utf-8")
    assert leer_semilla_anual(path) == [
        {"anio": 2020, "producto": "regular", "promedio": 21.24, "fuente": "consolidado histórico"}
    ]
    assert leer_semilla_anual(tmp_path / "no_existe.csv") == []


def test_mensual(tmp_path):
    path = tmp_path / "mensual.csv"
    path.write_text("anio,mes,producto,promedio,fuente\n2026,7,diésel,37.59,MEM mensual\n", encoding="utf-8")
    assert leer_mensual(path) == [
        {"anio": 2026, "mes": 7, "producto": "diésel", "promedio": 37.59, "fuente": "MEM mensual"}
    ]
    assert leer_mensual(tmp_path / "no_existe.csv") == []


def test_mensual_real_valido():
    """El mensual.csv commitado: productos del catálogo, meses 1-12, sin duplicados."""
    from pathlib import Path
    real =Path(__file__).resolve().parent.parent / "data" / "db" / "mensual.csv"
    filas = leer_mensual(real)
    assert filas, "mensual.csv vacío"
    claves = [(f["anio"], f["mes"], f["producto"]) for f in filas]
    assert len(claves) == len(set(claves))
    assert all(f["producto"] in PRODUCTOS and 1 <= f["mes"] <= 12 and 5 < f["promedio"] < 100 for f in filas)
