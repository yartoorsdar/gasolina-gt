"""Tests de la medición de la fase de prueba de votos (collector/calibracion.py).

Todo con votos SIMULADOS dentro de tmp/DB en memoria (conftest.py aísla los datos
reales). Aquí se fija el contrato: qué se rechaza, cómo se clasifica, cómo se mide
contra el MEM y cuándo se da por cumplida la fase de prueba.
"""

import csv
import json
import random
from pathlib import Path
from datetime import date, timedelta

import pytest

from collector import calibracion as cal
from collector.db import conectar, conectar_temporal, guardar_precios
from collector.memoria import exportar_memoria, importar_memoria


def _voto(i, producto="regular", tipo="otro", precio=43.29, fecha="2026-09-29", vio=1, zona=None, disp=None):
    """Voto crudo válido; `i` fija el dispositivo y el minuto (orden temporal)."""
    return {
        "id_voto": f"{fecha}-{producto}-{i}-{tipo}-{disp or ''}",
        "ts": f"{fecha}T{9 + (i // 60):02d}:{i % 60:02d}:00-06:00",
        "fecha": fecha, "producto": producto, "modalidad": "autoservicio", "tipo": tipo,
        "precio": precio if tipo == "otro" else None, "precio_mostrado": 43.29 if vio else None,
        "vio_oficial": vio, "dispositivo": disp or f"{i:012x}", "zona": zona,
    }


def _cargar(conn, votos):
    for v in votos:
        fila, motivo = cal.validar_voto(v)
        assert motivo is None, motivo
        conn.execute(
            "INSERT INTO votos (id_voto, fecha, producto, modalidad, tipo, precio, precio_mostrado, "
            "vio_oficial, dispositivo, zona, ts) VALUES (:id_voto, :fecha, :producto, :modalidad, :tipo, "
            ":precio, :precio_mostrado, :vio_oficial, :dispositivo, :zona, :ts)", fila)
    conn.commit()


def _mem(conn, producto, fecha, precio):
    guardar_precios(conn, [{"producto": producto, "fecha": fecha, "precio": precio}], "MEM")


def _ruido(n, centro, sigma=0.1, semilla=1):
    rng = random.Random(semilla)
    return [round(centro + rng.gauss(0, sigma), 2) for _ in range(n)]


# ── Validación ─────────────────────────────────────────────

def test_voto_valido_se_normaliza():
    fila, motivo = cal.validar_voto(dict(_voto(1, producto="diesel"), dispositivo="ABCDEF012345"))
    assert motivo is None
    assert fila["producto"] == "diésel" and fila["dispositivo"] == "abcdef012345"
    assert fila["ts"] == "2026-09-29T09:01:00-06:00" and fila["vio_oficial"] == 1


@pytest.mark.parametrize("cambio,motivo", [
    ({"id_voto": ""}, "sin_id"),
    ({"producto": "wti"}, "producto_invalido"),
    ({"producto": "bunker"}, "producto_invalido"),
    ({"tipo": "quizas"}, "tipo_invalido"),
    ({"dispositivo": "190.86.10.4"}, "dispositivo_no_anonimo"),
    ({"dispositivo": "persona@correo.com"}, "dispositivo_no_anonimo"),
    ({"dispositivo": "3f2b8c1e-aaaa-bbbb-cccc-1234567890ab"}, "dispositivo_no_anonimo"),
    ({"dispositivo": "abc"}, "dispositivo_no_anonimo"),
    ({"precio": 9.99}, "precio_fuera_de_rango"),
    ({"precio": 100.5}, "precio_fuera_de_rango"),
    ({"precio": "abc"}, "precio_no_numerico"),
    ({"precio": "nan"}, "precio_no_numerico"),
    ({"tipo": "coincide", "precio": 43.29}, "coincide_con_precio"),
    ({"tipo": "coincide", "precio": None, "vio_oficial": 0}, "coincide_ciego"),
    ({"fecha": "2026-09-28"}, "fecha_incoherente"),
    ({"ts": "2026-09-29T09:01:00"}, "ts_sin_zona_horaria"),
    ({"ts": "ayer"}, "ts_invalido"),
    ({"modalidad": "spot"}, "modalidad_invalida"),
])
def test_votos_invalidos_se_rechazan_con_motivo(cambio, motivo):
    fila, m = cal.validar_voto(dict(_voto(1), **cambio))
    assert fila is None and m == motivo


def test_ts_utc_se_convierte_a_hora_guatemala():
    # 02:00 UTC del 30 = 20:00 del 29 en Guatemala (UTC-6): la fecha debe ser la del 29.
    fila, motivo = cal.validar_voto(dict(_voto(1), ts="2026-09-30T02:00:00Z", fecha="2026-09-29"))
    assert motivo is None and fila["ts"] == "2026-09-29T20:00:00-06:00"
    _, motivo = cal.validar_voto(dict(_voto(1), ts="2026-09-30T02:00:00Z", fecha="2026-09-30"))
    assert motivo == "fecha_incoherente"


# ── Importación ────────────────────────────────────────────

def test_importar_votos_json_y_csv_es_idempotente(tmp_path):
    conn = conectar_temporal()
    buenos = [_voto(i) for i in range(4)]
    (tmp_path / "a.json").write_text(json.dumps({"votos": buenos + [dict(_voto(9), producto="wti")]}), encoding="utf-8")
    with open(tmp_path / "b.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cal._COLUMNAS_CSV, extrasaction="ignore")
        w.writeheader()
        w.writerow(_voto(20, tipo="coincide"))          # precio vacío en CSV
        w.writerow(dict(_voto(21), dispositivo="10.0.0.1"))
    r1 = cal.importar_votos(conn, tmp_path)
    assert r1["archivos"] == 2 and r1["leidos"] == 7 and r1["insertados"] == 5
    assert r1["invalidos"] == {"producto_invalido": 1, "dispositivo_no_anonimo": 1}
    r2 = cal.importar_votos(conn, tmp_path)               # reimportar = no duplica
    assert r2["insertados"] == 0
    assert conn.execute("SELECT COUNT(*) FROM votos").fetchone()[0] == 5


def test_importar_sin_carpeta_no_falla(tmp_path):
    assert cal.importar_votos(conectar_temporal(), tmp_path / "no_existe")["leidos"] == 0


# ── Clasificación ──────────────────────────────────────────

def test_clasificar_todos_los_estados():
    ref = 43.29
    assert cal.clasificar(2, 2, [], ref)[0] == "sin"
    assert cal.clasificar(5, 5, [], ref)[0] == "debil"
    # 12 votos: 8 confirman, 4 escritos (< min_escritos) → decide la proporción que confirma
    assert cal.clasificar(12, 8, _ruido(4, ref), ref)[0] == "coincide"
    assert cal.clasificar(12, 2, _ruido(10, ref), ref)[0] == "coincide"
    # 12 escritos que bajan Q4.60 → posible cambio; con fuente externa → respaldado
    bajan = _ruido(12, ref - 4.6)
    assert cal.clasificar(12, 0, bajan, ref) == ("cambio", pytest.approx(ref - 4.6, abs=0.1))
    assert cal.clasificar(12, 0, bajan, ref, externo=True)[0] == "respaldado"
    # dispersión alta (no hay acuerdo) → débil aunque el promedio se aleje
    assert cal.clasificar(12, 0, [38, 40, 42, 44, 46, 48, 39, 41, 43, 45, 47, 49], ref)[0] == "debil"


def test_clasificar_paridad_con_el_worker_de_votos():
    """Mismo fixture que verifica votos-api (node --test): si cambia una lógica, falla la otra."""
    ruta = Path(__file__).resolve().parent / "fixtures" / "clasificar_casos.json"
    for c in json.loads(ruta.read_text(encoding="utf-8")):
        estado, med = cal.clasificar(c["n_votos"], c["n_coincide"], c["escritos"], c["ref"], externo=c["externo"])
        assert estado == c["estado"], c["nombre"]
        if c["mediana"] is None:
            assert med is None
        else:
            assert med == pytest.approx(c["mediana"], abs=1e-3), c["nombre"]


def test_clasificar_diferencia_menor_a_un_quetzal_no_es_cambio():
    assert cal.clasificar(12, 0, _ruido(12, 43.29 - 0.6), 43.29)[0] == "coincide"


# ── Medición contra el MEM ─────────────────────────────────

def test_medir_dia_con_cambio_real_del_mem():
    conn = conectar_temporal()
    _mem(conn, "regular", "2026-09-28", 43.29)
    _mem(conn, "regular", "2026-09-29", 38.69)            # el decreto baja Q4.60
    _cargar(conn, [_voto(i, precio=p) for i, p in enumerate(_ruido(12, 38.69))])
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["ref_precio"] == 38.69 and m["ref_previo"] == 43.29 and m["ref_fuente"] == "MEM"
    assert m["n_votos"] == 12 and m["n_otro"] == 12
    assert abs(m["error_mediana"]) < 0.1 and m["error_abs"] == abs(m["error_mediana"])
    assert m["estado"] == "coincide"                       # la comunidad ya coincide con el NUEVO oficial
    assert m["cambio_real"] == 1 and m["alerta"] == 0      # (el oficial ya cambió: no es alerta)


def test_medir_dia_alerta_cuando_el_mem_aun_no_cambia():
    conn = conectar_temporal()
    _mem(conn, "regular", "2026-09-28", 43.29)
    _mem(conn, "regular", "2026-09-29", 43.29)            # el MEM sigue igual
    _cargar(conn, [_voto(i, precio=p) for i, p in enumerate(_ruido(12, 38.69))])
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["estado"] == "cambio" and m["alerta"] == 1 and m["cambio_real"] == 0
    assert m["error_mediana"] == pytest.approx(-4.6, abs=0.1)


def test_medir_dia_sin_referencia_mem_no_falla():
    conn = conectar_temporal()
    _cargar(conn, [_voto(i, precio=p) for i, p in enumerate(_ruido(12, 43.29))])
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["ref_precio"] is None and m["error_mediana"] is None and m["cambio_real"] is None
    assert m["estado"] == "coincide" and m["mediana"] == pytest.approx(43.29, abs=0.1)


def test_referencia_es_solo_mem_no_otras_fuentes():
    conn = conectar_temporal()
    guardar_precios(conn, [{"producto": "regular", "fecha": "2026-09-29", "precio": 43.29}], "GlobalPetrolPrices")
    assert cal._referencia(conn, "regular", "autoservicio", "2026-09-29") == (None, None)


def test_duplicados_y_fuera_de_rango():
    conn = conectar_temporal()
    _mem(conn, "regular", "2026-09-29", 43.29)
    votos = [_voto(i, precio=p) for i, p in enumerate(_ruido(9, 43.29))]
    votos.append(_voto(50, precio=43.30, disp=f"{3:012x}"))    # mismo dispositivo que el voto 3
    votos.append(_voto(51, precio=70.0))                        # dentro de 10..100 pero a Q27 del MEM
    _cargar(conn, votos)
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["n_duplicados"] == 1 and m["n_fuera_rango"] == 1 and m["n_votos"] == 9


def test_hora_umbral_es_la_del_voto_numero_min_votos():
    conn = conectar_temporal()
    _cargar(conn, [_voto(i, precio=43.29) for i in range(12)])
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["hora_primer_voto"] == "09:00" and m["hora_umbral"] == "09:09"   # el 10.º voto (i = 9)


def test_efecto_ancla_mediana_de_ciegos_y_pct_coincide():
    conn = conectar_temporal()
    _mem(conn, "regular", "2026-09-29", 43.29)
    votos = [_voto(i, tipo="coincide") for i in range(6)]                      # veían el oficial
    votos += [_voto(10 + i, precio=p, vio=0) for i, p in enumerate(_ruido(6, 43.9))]  # ciegos
    _cargar(conn, votos)
    m = cal.medir_dia(conn, "2026-09-29", "regular")
    assert m["n_ciegos"] == 6 and m["pct_coincide"] == 0.5
    assert m["mediana_ciegos"] == pytest.approx(43.9, abs=0.15)


def test_medir_guarda_todas_las_combinaciones_y_es_reejecutable():
    conn = conectar_temporal()
    _cargar(conn, [_voto(i) for i in range(3)] + [_voto(i, producto="superior") for i in range(3)]
                + [_voto(i, fecha="2026-09-30") for i in range(3)])
    assert len(cal.medir(conn)) == 3
    assert len(cal.medir(conn)) == 3
    assert conn.execute("SELECT COUNT(*) FROM calibracion").fetchone()[0] == 3


# ── Curva error vs número de votos ─────────────────────────

def test_curva_es_determinista_y_mejora_con_mas_votos():
    res = _ruido(200, 0.0, sigma=0.5, semilla=3)
    c1, c2 = cal.curva_error(res), cal.curva_error(res)
    assert c1 == c2
    assert c1[30]["p90"] < c1[3]["p90"]
    assert cal.votos_recomendados(c1, 0.30) is not None
    assert cal.votos_recomendados(c1, 0.0001) is None
    assert 50 not in cal.curva_error(res[:20])              # nunca simula más votos de los que hay


# ── Reporte y veredicto ────────────────────────────────────

def _fase(conn, dias, saltar=(), sigma=0.1, votos=12):
    """Simula una fase de prueba con MEM estable y votos honestos (semillas fijas)."""
    ini = date(2026, 9, 29)
    for d in range(dias):
        f = (ini + timedelta(days=d)).isoformat()
        if d in saltar:
            continue
        for k, (prod, ref) in enumerate((("superior", 45.29), ("regular", 43.29), ("diésel", 49.40))):
            _mem(conn, prod, f, ref)
            precios = _ruido(votos, ref, sigma, semilla=d * 10 + k)
            _cargar(conn, [_voto(i, producto=prod, fecha=f, precio=p, disp=f"{d:02d}{i:010x}")
                           for i, p in enumerate(precios)])
    cal.medir(conn)


def test_veredicto_listo_tras_siete_dias_buenos():
    conn = conectar_temporal()
    _fase(conn, 7)
    rep = cal.reporte(conn)
    assert rep["dias_consecutivos"] == 7 and rep["listo"] is True and rep["motivos"] == []
    assert all(p["mae"] < 0.30 and p["pct_dias_ok"] == 1.0 for p in rep["productos"].values())
    assert rep["votos_recomendados"] is not None


def test_veredicto_no_listo_con_pocos_dias():
    conn = conectar_temporal()
    _fase(conn, 5)
    rep = cal.reporte(conn)
    assert rep["listo"] is False and any("Faltan días" in m for m in rep["motivos"])


def test_un_hueco_reinicia_los_dias_consecutivos():
    conn = conectar_temporal()
    _fase(conn, 7, saltar=(3,))
    assert cal.reporte(conn)["dias_consecutivos"] == 3      # solo cuentan los días 4..6 (0-index) hasta hoy


def test_veredicto_no_listo_si_los_votos_son_malos():
    conn = conectar_temporal()
    _fase(conn, 7, sigma=1.5)
    rep = cal.reporte(conn)
    assert rep["listo"] is False and any("error <=" in m or "votos/día" in m for m in rep["motivos"])


def test_reporte_vacio():
    rep = cal.reporte(conectar_temporal())
    assert rep["listo"] is False and rep["dias_con_votos"] == 0


# ── Persistencia (memoria en git) ──────────────────────────

def test_votos_y_calibracion_persisten_y_son_deterministas(tmp_path):
    conn = conectar_temporal()
    _fase(conn, 2)
    exportar_memoria(conn=conn, carpeta=tmp_path / "a")
    exportar_memoria(conn=conn, carpeta=tmp_path / "b")
    for nombre in ("votos.csv", "calibracion.csv"):
        assert (tmp_path / "a" / nombre).read_bytes() == (tmp_path / "b" / nombre).read_bytes()
    nueva = conectar_temporal()
    importar_memoria(conn=nueva, carpeta=tmp_path / "a")
    for tabla in ("votos", "calibracion"):
        assert nueva.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == \
               conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] > 0


def test_el_csv_de_votos_no_contiene_ip_ni_correos(tmp_path):
    conn = conectar_temporal()
    _fase(conn, 1)
    exportar_memoria(conn=conn, carpeta=tmp_path)
    texto = (tmp_path / "votos.csv").read_text(encoding="utf-8")
    assert "@" not in texto
    with open(tmp_path / "votos.csv", encoding="utf-8") as f:
        assert all(cal._RE_DISPOSITIVO.match(r["dispositivo"]) for r in csv.DictReader(f))


# ── Punto de entrada ───────────────────────────────────────

def test_ejecutar_de_punta_a_punta(tmp_path, capsys):
    conn = conectar()                                        # DB aislada por conftest
    _mem(conn, "regular", "2026-09-29", 43.29)
    conn.commit()
    conn.close()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    votos = [_voto(i, precio=p) for i, p in enumerate(_ruido(12, 43.29))] + [dict(_voto(40), dispositivo="1.2.3.4")]
    (inbox / "votos.json").write_text(json.dumps(votos), encoding="utf-8")
    res = cal.ejecutar(carpeta=inbox)
    salida = capsys.readouterr().out
    assert res == {"fuente": "calibracion", "insertados": 12, "medidas": 1, "listo": False}
    assert "dispositivo_no_anonimo" in salida and "VEREDICTO" in salida
    salida.encode("cp1252")                                  # el runner de Windows escribe en cp1252
