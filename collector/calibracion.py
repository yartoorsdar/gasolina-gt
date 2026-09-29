"""Medición de la fase de prueba del sistema de votos ciudadanos.

Objetivo: durante los 7 días de prueba, medir con precisión qué tan buenos son
los votos de la comunidad frente al MEM, para calibrar los umbrales ANTES de que
los votos muevan el precio mostrado. Este módulo NO escribe en `precios` ni
cambia lo que ve el público: solo mide y guarda.

Flujo (todo determinista y reproducible):
  1. `importar_votos()` lee los exports del backend de votos desde
     data/inbox/votos/*.json|csv (contrato abajo) → tabla `votos` (idempotente
     por id_voto). Un voto inválido se cuenta por motivo, nunca se "arregla".
  2. `medir()` calcula, por día × producto × modalidad, la calidad de los votos
     y la compara con el precio MEM de ese día → tabla `calibracion`.
  3. `reporte()` resume la ventana de prueba: error frente al MEM, sesgo,
     efecto ancla, matriz de alertas, curva "error vs nº de votos" y un
     veredicto explícito de si se cumplen los criterios de salida.

Contrato de un voto (JSON: lista o {"votos": [...]}, o CSV con estas columnas):
  id_voto          str, único (idempotencia)
  ts               ISO 8601 con offset (se convierte a hora GT)
  fecha            YYYY-MM-DD (debe coincidir con el día GT de ts)
  producto         superior | regular | diésel (sinónimos aceptados; wti NO)
  modalidad        autoservicio (por defecto) | servicio_completo
  tipo             coincide | otro
  precio           obligatorio si tipo = otro (10..100); vacío si coincide
  precio_mostrado  opcional: el oficial que la persona veía al votar
  vio_oficial      1 (por defecto) | 0 = voto ciego; un ciego no puede "coincidir"
  dispositivo      hash HEXADECIMAL anónimo (12–64 caracteres). Cualquier otra
                   cosa (IP, correo, uuid con guiones) se rechaza: privacidad.
  zona             opcional, texto corto

Los umbrales de `UMBRALES` son valores de ARRANQUE a calibrar con esta misma
medición; el prototipo web (prototipos/dashboard-comunidad.html) replica
`clasificar()` y debe mantenerse en sincronía.
"""

import csv
import json
import math
import random
import re
import statistics
from datetime import datetime
from pathlib import Path

from collector.db import (
    COMBUSTIBLES, _GT, canon_modalidad, canon_producto, conectar, hoy_gt,
)

INBOX_VOTOS = Path(__file__).resolve().parent.parent / "data" / "inbox" / "votos"

# Umbrales de arranque (a calibrar). Precios en quetzales por galón.
UMBRALES = {
    "min_votos": 10,         # votos válidos para hablar de señal comunitaria
    "min_debil": 3,          # por debajo de esto: "sin datos suficientes"
    "min_escritos": 5,       # precios escritos necesarios para hablar de cambio
    "tol_acuerdo": 0.50,     # ±Q de la mediana para contar un voto como "de acuerdo"
    "min_acuerdo": 0.60,     # fracción mínima de acuerdo
    "tol_cambio": 1.00,      # diferencia vs oficial que dispara "posible cambio"
    "precio_min": 10.0,      # rango absoluto aceptado al importar
    "precio_max": 100.0,
    "max_desvio_ref": 15.0,  # un escrito a más de Q15 del MEM se descarta al medir
}

# Criterios para dar por terminada la fase de prueba (también de arranque).
CRITERIOS_SALIDA = {
    "dias": 7,               # días CONSECUTIVOS con votos
    "min_votos_dia": 10,     # votos válidos por producto y día (mediana de la ventana)
    "mae_max": 0.30,         # error absoluto máximo aceptable frente al MEM (Q)
    "pct_dias_ok": 0.80,     # fracción de días que debe cumplir el error
    "objetivo_curva": 0.30,  # p90 del error de la mediana para recomendar un mínimo de votos
}

_TIPOS = ("coincide", "otro")
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_DISPOSITIVO = re.compile(r"^[0-9a-f]{12,64}$")
_COLUMNAS_CSV = ("id_voto", "ts", "fecha", "producto", "modalidad", "tipo", "precio",
                 "precio_mostrado", "vio_oficial", "dispositivo", "zona")


# ──────────────────────────────────────────────
# Validación e importación de votos
# ──────────────────────────────────────────────

def _numero(valor):
    """float finito o None (acepta '' y None como ausente)."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return "invalido"
    return x if math.isfinite(x) else "invalido"


def validar_voto(v: dict, u: dict = None) -> tuple[dict | None, str | None]:
    """Valida un voto crudo. Devuelve (fila_normalizada, None) o (None, motivo)."""
    u = u or UMBRALES
    id_voto = str(v.get("id_voto") or "").strip()
    if not id_voto:
        return None, "sin_id"

    producto = canon_producto(str(v.get("producto") or ""))
    if producto not in COMBUSTIBLES:
        return None, "producto_invalido"
    modalidad = canon_modalidad(v.get("modalidad") or None, producto)
    if modalidad is None:
        return None, "modalidad_invalida"

    tipo = str(v.get("tipo") or "").strip().lower()
    if tipo not in _TIPOS:
        return None, "tipo_invalido"

    dispositivo = str(v.get("dispositivo") or "").strip().lower()
    if not _RE_DISPOSITIVO.match(dispositivo):
        return None, "dispositivo_no_anonimo"

    try:
        ts = datetime.fromisoformat(str(v.get("ts") or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None, "ts_invalido"
    if ts.tzinfo is None:
        return None, "ts_sin_zona_horaria"
    ts_gt = ts.astimezone(_GT)
    fecha = str(v.get("fecha") or "").strip()
    if not _RE_FECHA.match(fecha) or fecha != ts_gt.strftime("%Y-%m-%d"):
        return None, "fecha_incoherente"

    precio = _numero(v.get("precio"))
    mostrado = _numero(v.get("precio_mostrado"))
    if precio == "invalido" or mostrado == "invalido":
        return None, "precio_no_numerico"
    if tipo == "otro":
        if precio is None or not (u["precio_min"] <= precio <= u["precio_max"]):
            return None, "precio_fuera_de_rango"
    elif precio is not None:
        return None, "coincide_con_precio"

    vio = v.get("vio_oficial")
    vio_oficial = 1 if vio in (None, "", 1, "1", True, "true", "True") else 0
    if vio_oficial == 0 and tipo == "coincide":
        return None, "coincide_ciego"
    if vio_oficial == 0:
        mostrado = None

    zona = str(v.get("zona") or "").strip()[:40] or None
    return {
        "id_voto": id_voto, "fecha": fecha, "producto": producto, "modalidad": modalidad,
        "tipo": tipo, "precio": precio, "precio_mostrado": mostrado, "vio_oficial": vio_oficial,
        "dispositivo": dispositivo, "zona": zona, "ts": ts_gt.strftime("%Y-%m-%dT%H:%M:%S-06:00"),
    }, None


def _leer_archivo(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        datos = json.loads(path.read_text(encoding="utf-8"))
        return datos.get("votos", []) if isinstance(datos, dict) else list(datos)
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def importar_votos(conn, carpeta: Path = None, u: dict = None) -> dict:
    """Importa los exports del backend de votos (idempotente por id_voto).

    Returns:
        {"archivos", "leidos", "insertados", "invalidos": {motivo: n}}
    """
    carpeta = Path(carpeta) if carpeta else INBOX_VOTOS
    res = {"archivos": 0, "leidos": 0, "insertados": 0, "invalidos": {}}
    if not carpeta.exists():
        return res
    for path in sorted(list(carpeta.glob("*.json")) + list(carpeta.glob("*.csv"))):
        res["archivos"] += 1
        for crudo in _leer_archivo(path):
            res["leidos"] += 1
            fila, motivo = validar_voto(crudo, u)
            if motivo:
                res["invalidos"][motivo] = res["invalidos"].get(motivo, 0) + 1
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO votos (id_voto, fecha, producto, modalidad, tipo, precio, "
                "precio_mostrado, vio_oficial, dispositivo, zona, ts) "
                "VALUES (:id_voto, :fecha, :producto, :modalidad, :tipo, :precio, :precio_mostrado, "
                ":vio_oficial, :dispositivo, :zona, :ts)", fila)
            res["insertados"] += cur.rowcount
    conn.commit()
    return res


# ──────────────────────────────────────────────
# Clasificación (misma lógica que el prototipo web)
# ──────────────────────────────────────────────

def clasificar(n_votos: int, n_coincide: int, escritos: list[float], ref: float | None,
               externo: bool = False, u: dict = None) -> tuple[str, float | None]:
    """Estado de un producto en un día y mediana de los precios escritos.

    Estados: sin | debil | coincide | cambio | respaldado.
    `respaldado` (cambio + fuente independiente) solo si `externo=True`; en la
    fase de prueba no se usa (la medición registra hasta `cambio`).
    """
    u = u or UMBRALES
    mediana = statistics.median(escritos) if escritos else None
    if n_votos >= u["min_votos"]:
        if len(escritos) >= u["min_escritos"]:
            cerca = sum(1 for x in escritos if abs(x - mediana) <= u["tol_acuerdo"]) / len(escritos)
            if cerca < u["min_acuerdo"]:
                return "debil", mediana
            if ref is not None and abs(mediana - ref) >= u["tol_cambio"]:
                return ("respaldado" if externo else "cambio"), mediana
            return "coincide", mediana
        return ("coincide" if n_coincide / n_votos >= u["min_acuerdo"] else "debil"), mediana
    if n_votos >= u["min_debil"]:
        return "debil", mediana
    return "sin", mediana


# ──────────────────────────────────────────────
# Medición diaria contra el MEM
# ──────────────────────────────────────────────

def _referencia(conn, producto: str, modalidad: str, fecha: str) -> tuple:
    """(precio MEM del día, precio MEM previo). None donde no haya dato oficial."""
    hoy = conn.execute(
        "SELECT precio FROM precios WHERE producto=? AND modalidad=? AND fecha=? AND fuente='MEM'",
        (producto, modalidad, fecha)).fetchone()
    previo = conn.execute(
        "SELECT precio FROM precios WHERE producto=? AND modalidad=? AND fecha<? AND fuente='MEM' "
        "ORDER BY fecha DESC LIMIT 1", (producto, modalidad, fecha)).fetchone()
    return (hoy["precio"] if hoy else None), (previo["precio"] if previo else None)


def _votos_validos(conn, fecha, producto, modalidad, ref, u):
    """Votos del día sin duplicados por dispositivo (queda el primero) ni escritos absurdos.

    Returns: (votos, n_duplicados, n_fuera_rango)
    """
    filas = conn.execute(
        "SELECT * FROM votos WHERE fecha=? AND producto=? AND modalidad=? ORDER BY ts, id_voto",
        (fecha, producto, modalidad)).fetchall()
    vistos, unicos = set(), []
    for r in filas:
        if r["dispositivo"] in vistos:
            continue
        vistos.add(r["dispositivo"])
        unicos.append(r)
    n_dup = len(filas) - len(unicos)
    validos, n_fuera = [], 0
    for r in unicos:
        if r["tipo"] == "otro" and ref is not None and abs(r["precio"] - ref) > u["max_desvio_ref"]:
            n_fuera += 1
            continue
        validos.append(r)
    return validos, n_dup, n_fuera


def medir_dia(conn, fecha: str, producto: str, modalidad: str = None, u: dict = None) -> dict:
    """Mide la calidad de los votos de un día frente al MEM de ese día."""
    u = u or UMBRALES
    producto = canon_producto(producto)
    modalidad = canon_modalidad(modalidad, producto)
    ref, previo = _referencia(conn, producto, modalidad, fecha)
    votos, n_dup, n_fuera = _votos_validos(conn, fecha, producto, modalidad, ref, u)

    escritos = [r["precio"] for r in votos if r["tipo"] == "otro"]
    ciegos = [r["precio"] for r in votos if r["tipo"] == "otro" and not r["vio_oficial"]]
    n = len(votos)
    n_coincide = sum(1 for r in votos if r["tipo"] == "coincide")
    estado, mediana = clasificar(n, n_coincide, escritos, ref, u=u)

    mad = acuerdo = error = None
    if escritos:
        mad = statistics.median([abs(x - mediana) for x in escritos])
        acuerdo = sum(1 for x in escritos if abs(x - mediana) <= u["tol_acuerdo"]) / len(escritos)
        if ref is not None:
            error = mediana - ref

    hora_umbral = votos[u["min_votos"] - 1]["ts"][11:16] if n >= u["min_votos"] else None
    r4 = lambda x: None if x is None else round(x, 4)  # noqa: E731
    return {
        "fecha": fecha, "producto": producto, "modalidad": modalidad,
        "ref_precio": ref, "ref_fuente": "MEM" if ref is not None else None, "ref_previo": previo,
        "n_votos": n, "n_coincide": n_coincide, "n_otro": len(escritos), "n_ciegos": len(ciegos),
        "n_zonas": len({r["zona"] for r in votos if r["zona"]}),
        "n_duplicados": n_dup, "n_fuera_rango": n_fuera,
        "mediana": r4(mediana), "mad": r4(mad), "acuerdo": r4(acuerdo),
        "mediana_ciegos": r4(statistics.median(ciegos)) if ciegos else None,
        "pct_coincide": r4(n_coincide / n) if n else None,
        "error_mediana": r4(error), "error_abs": r4(abs(error)) if error is not None else None,
        "estado": estado, "alerta": 1 if estado in ("cambio", "respaldado") else 0,
        "cambio_real": (1 if abs(ref - previo) >= u["tol_cambio"] else 0) if ref is not None and previo is not None else None,
        "hora_primer_voto": votos[0]["ts"][11:16] if votos else None,
        "hora_umbral": hora_umbral,
    }


def guardar_medicion(conn, m: dict) -> None:
    cols = list(m)
    conn.execute(
        f"INSERT OR REPLACE INTO calibracion ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", m)


def medir(conn, desde: str = None, hasta: str = None, u: dict = None) -> list[dict]:
    """Mide y guarda todos los (día, producto, modalidad) que tengan votos."""
    cond, params = "", []
    if desde:
        cond += " AND fecha >= ?"
        params.append(desde)
    if hasta:
        cond += " AND fecha <= ?"
        params.append(hasta)
    claves = conn.execute(
        f"SELECT DISTINCT fecha, producto, modalidad FROM votos WHERE 1=1{cond} "
        "ORDER BY fecha, producto, modalidad", params).fetchall()
    medidas = []
    for c in claves:
        m = medir_dia(conn, c["fecha"], c["producto"], c["modalidad"], u)
        guardar_medicion(conn, m)
        medidas.append(m)
    conn.commit()
    return medidas


# ──────────────────────────────────────────────
# Curva "error vs número de votos" (¿cuántos votos hacen falta?)
# ──────────────────────────────────────────────

def residuos(conn, desde: str = None, hasta: str = None, solo_ciegos: bool = False,
             u: dict = None) -> list[float]:
    """Errores (precio escrito - MEM del día) de todos los votos válidos con referencia."""
    u = u or UMBRALES
    claves = conn.execute(
        "SELECT DISTINCT fecha, producto, modalidad FROM votos "
        "WHERE (? IS NULL OR fecha >= ?) AND (? IS NULL OR fecha <= ?)", (desde, desde, hasta, hasta)).fetchall()
    res = []
    for c in claves:
        ref, _ = _referencia(conn, c["producto"], c["modalidad"], c["fecha"])
        if ref is None:
            continue
        votos, _, _ = _votos_validos(conn, c["fecha"], c["producto"], c["modalidad"], ref, u)
        res += [r["precio"] - ref for r in votos
                if r["tipo"] == "otro" and (not solo_ciegos or not r["vio_oficial"])]
    return res


def curva_error(res: list[float], ks=(3, 5, 8, 10, 15, 20, 30, 50), iteraciones: int = 300,
                semilla: int = 7) -> dict:
    """Error de la mediana de k votos al azar (sin reposición). Determinista por semilla.

    Returns: {k: {"mae": ..., "p90": ...}} solo para k <= len(res).
    """
    rng = random.Random(semilla)
    curva = {}
    for k in ks:
        if k > len(res):
            continue
        errs = sorted(abs(statistics.median(rng.sample(res, k))) for _ in range(iteraciones))
        curva[k] = {"mae": round(statistics.fmean(errs), 4), "p90": round(errs[int(0.9 * (len(errs) - 1))], 4)}
    return curva


def votos_recomendados(curva: dict, objetivo: float) -> int | None:
    """Menor k cuyo p90 del error queda dentro del objetivo (None si ninguno)."""
    for k in sorted(curva):
        if curva[k]["p90"] <= objetivo:
            return k
    return None


# ──────────────────────────────────────────────
# Reporte de la fase de prueba
# ──────────────────────────────────────────────

def reporte(conn, hasta: str = None, criterios: dict = None, u: dict = None) -> dict:
    """Resumen de la ventana de prueba y veredicto sobre los criterios de salida."""
    criterios = criterios or CRITERIOS_SALIDA
    filas = [dict(r) for r in conn.execute("SELECT * FROM calibracion ORDER BY fecha, producto, modalidad")]
    if hasta:
        filas = [f for f in filas if f["fecha"] <= hasta]
    if not filas:
        return {"dias_con_votos": 0, "listo": False, "motivos": ["Aún no hay votos medidos."], "productos": {}}

    fechas = sorted({f["fecha"] for f in filas})
    consecutivos = 1
    for a, b in zip(reversed(fechas[:-1]), reversed(fechas[1:])):
        if (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days == 1:
            consecutivos += 1
        else:
            break
    ventana = [f for f in filas if f["fecha"] in fechas[-consecutivos:]]

    productos = {}
    for prod in COMBUSTIBLES:
        fp = [f for f in ventana if f["producto"] == prod]
        if not fp:
            continue
        con_error = [f for f in fp if f["error_abs"] is not None]
        ok = [f for f in con_error if f["error_abs"] <= criterios["mae_max"]]
        productos[prod] = {
            "dias": len(fp),
            "votos_por_dia": statistics.median([f["n_votos"] for f in fp]),
            "mae": round(statistics.fmean(f["error_abs"] for f in con_error), 4) if con_error else None,
            "sesgo": round(statistics.fmean(f["error_mediana"] for f in con_error), 4) if con_error else None,
            "pct_dias_ok": round(len(ok) / len(con_error), 4) if con_error else None,
            "pct_coincide": round(statistics.fmean(f["pct_coincide"] for f in fp if f["pct_coincide"] is not None), 4),
            "dias_sin_referencia": sum(1 for f in fp if f["ref_precio"] is None),
            "estados": {e: sum(1 for f in fp if f["estado"] == e) for e in ("sin", "debil", "coincide", "cambio")},
        }

    conf = {"vp": 0, "fp": 0, "fn": 0, "vn": 0}
    for f in ventana:
        if f["cambio_real"] is None:
            continue
        clave = ("v" if f["alerta"] == f["cambio_real"] else "f") + ("p" if f["alerta"] else "n")
        conf[clave] += 1

    # Efecto ancla: ¿los que vieron el oficial escriben más cerca de él que los ciegos?
    ancla = [f["mediana"] - f["mediana_ciegos"] for f in ventana
             if f["mediana"] is not None and f["mediana_ciegos"] is not None]

    res = residuos(conn, fechas[-consecutivos], hasta, u=u)
    curva = curva_error(res)
    k_rec = votos_recomendados(curva, criterios["objetivo_curva"])

    motivos = []
    if consecutivos < criterios["dias"]:
        motivos.append(f"Faltan días: {consecutivos} consecutivos con votos de {criterios['dias']} requeridos.")
    for prod, p in productos.items():
        if p["votos_por_dia"] < criterios["min_votos_dia"]:
            motivos.append(f"{prod}: mediana de {p['votos_por_dia']:.0f} votos/día (mínimo {criterios['min_votos_dia']}).")
        if p["pct_dias_ok"] is None:
            motivos.append(f"{prod}: sin días comparables con el MEM.")
        elif p["pct_dias_ok"] < criterios["pct_dias_ok"]:
            motivos.append(f"{prod}: solo {p['pct_dias_ok']:.0%} de los días con error <= Q{criterios['mae_max']:.2f}.")

    return {
        "dias_con_votos": len(fechas), "dias_consecutivos": consecutivos, "desde": fechas[-consecutivos],
        "hasta": fechas[-1], "productos": productos, "alertas": conf, "sesgo_ancla": (
            round(statistics.fmean(ancla), 4) if ancla else None),
        "n_residuos": len(res), "curva_error": curva, "votos_recomendados": k_rec,
        "listo": not motivos, "motivos": motivos,
    }


def imprimir_reporte(rep: dict) -> None:
    """Resumen legible en español (solo caracteres cp1252: el runner de Windows)."""
    print("=" * 60)
    print("[calibracion] Fase de prueba del sistema de votos")
    if not rep.get("productos"):
        print("  " + rep["motivos"][0])
        return
    print(f"  Ventana: {rep['desde']} a {rep['hasta']} ({rep['dias_consecutivos']} dias consecutivos con votos)")
    for prod, p in rep["productos"].items():
        mae = "sin dato" if p["mae"] is None else f"Q{p['mae']:.2f}"
        sesgo = "sin dato" if p["sesgo"] is None else f"Q{p['sesgo']:+.2f}"
        ok = "sin dato" if p["pct_dias_ok"] is None else f"{p['pct_dias_ok']:.0%}"
        print(f"  {prod:9s} votos/dia={p['votos_por_dia']:.0f}  error medio={mae}  sesgo={sesgo}  "
              f"dias dentro de tolerancia={ok}  confirman={p['pct_coincide']:.0%}")
    c = rep["alertas"]
    print(f"  Alertas de cambio vs MEM: aciertos={c['vp'] + c['vn']}  falsas alarmas={c['fp']}  "
          f"cambios no detectados={c['fn']}  (una discrepancia puede ser que el MEM va atrasado: revisar)")
    if rep["sesgo_ancla"] is not None:
        print(f"  Efecto ancla (vieron el oficial - ciegos): Q{rep['sesgo_ancla']:+.2f}")
    if rep["curva_error"]:
        print("  Error de la mediana segun numero de votos (p90):",
              ", ".join(f"{k}=Q{v['p90']:.2f}" for k, v in rep["curva_error"].items()))
        rec = rep["votos_recomendados"]
        print("  Votos minimos recomendados:", rec if rec else "ninguno alcanza el objetivo todavia")
    print("  VEREDICTO:", "listo para salir de la fase de prueba" if rep["listo"] else "todavia en fase de prueba")
    for m in rep["motivos"]:
        print("   - " + m)


# ──────────────────────────────────────────────
# Punto de entrada (main.py --calibracion)
# ──────────────────────────────────────────────

def ejecutar(cfg: dict = None, carpeta: Path = None) -> dict:
    """Importa votos, mide y guarda la calibración, e imprime el reporte."""
    conn = conectar()
    try:
        imp = importar_votos(conn, carpeta)
        medidas = medir(conn)
        rep = reporte(conn)
        if imp["invalidos"]:
            print("[calibracion] Votos rechazados por motivo:", imp["invalidos"])
        imprimir_reporte(rep)
        return {"fuente": "calibracion", "insertados": imp["insertados"], "medidas": len(medidas),
                "listo": rep["listo"]}
    finally:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    import sys
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    print(ejecutar())
