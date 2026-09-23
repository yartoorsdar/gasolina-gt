"""Funciones puras para cálculo de impuestos sobre combustibles en Guatemala.

No realiza I/O excepto recibir la configuración `cfg` como parámetro.
Reglas:
  - IVA: 12% aplicado sobre (precio_base + IDP).
  - IDP por galón (Decreto 38-92): superior Q4.70, regular Q4.60, diésel Q1.30.

Fórmulas:
  quitar_impuestos(precio, producto, cfg) → precio_base = precio / 1.12 - idp
  agregar_impuestos(precio_base, producto, cfg) → precio_con_impuestos = (precio_base + idp) * 1.12
"""

from datetime import date


# Productos válidos con su IDP correspondiente
PRODUCTOS_VALIDOS = ("superior", "regular", "diessel")


def _idp(producto: str, cfg: dict) -> float:
    """Retorna el IDP en quetzales por galón para un producto dado."""
    if producto not in PRODUCTOS_VALIDOS:
        raise ValueError(
            f"Producto '{producto}' no válido. Usa uno de {PRODUCTOS_VALIDOS}"
        )
    return cfg["impuestos"]["idp_por_galon"][producto]


def quitar_impuestos(precio: float, producto: str, cfg: dict) -> float:
    """Calcula el precio SIN impuestos a partir del precio con impuestos.

    Args:
        precio: Precio observado con impuestos (quetzales por galón).
        producto: Tipo de combustible ("superior", "regular", "diessel").
        cfg: Diccionario con la configuración de impuestos (lee config.json).

    Returns:
        Precio sin impuestos (float, alta precisión).
    """
    idp = _idp(producto, cfg)
    precio_base = precio / 1.12 - idp
    return precio_base


def agregar_impuestos(precio_base: float, producto: str, cfg: dict) -> float:
    """Calcula el precio CON impuestos a partir del precio sin impuestos.

    Args:
        precio_base: Precio sin impuestos (quetzales por galón).
        producto: Tipo de combustible ("superior", "regular", "diessel").
        cfg: Diccionario con la configuración de impuestos (lee config.json).

    Returns:
        Precio con impuestos (float, alta precisión).
    """
    idp = _idp(producto, cfg)
    precio_con_impuestos = (precio_base + idp) * 1.12
    return precio_con_impuestos


def estado_decreto(hoy: date | None = None, cfg: dict = None) -> str:
    """Determina el estado actual del Decreto 22-2026.

    Args:
        hoy: Fecha de referencia (por defecto fecha actual).
        cfg: Configuración con decreto_22_2026.

    Returns:
        "pendiente_sancion" si no hay vigencia_inicio o hoy < inicio
        "vigente" si hoy está dentro del rango [inicio, fin]
        "vencido" si hoy > fecha_vigencia_fin
    """
    if cfg is None:
        import json

        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)

    decreto = cfg["decreto_22_2026"]

    # Si no se proporcionó fecha, usar la de hoy
    if hoy is None:
        from datetime import date as _date

        hoy = _date.today()

    vigencia_inicio = decreto.get("fecha_vigencia_inicio")
    vigencia_fin = decreto.get("fecha_vigencia_fin")

    if vigencia_inicio is None:
        return "pendiente_sancion"

    inicio = date.fromisoformat(vigencia_inicio)

    # Si no hay fecha de fin definida, mientras esté sancionado es vigente
    if vigencia_fin is None:
        if hoy >= inicio:
            return "vigente"
        return "pendiente_sancion"

    fin = date.fromisoformat(vigencia_fin)

    if hoy < inicio:
        return "pendiente_sancion"
    elif hoy <= fin:
        return "vigente"
    else:
        return "vencido"


def precio_incluye_impuestos(fecha_obs: str, cfg: dict = None) -> bool:
    """Determina si un precio observado en una fecha dada incluye impuestos.

    Regla: Si la fecha de observación cae dentro del rango de vigencia
    del decreto 22-2026, el precio OBSERVADO es SIN impuestos y la página
    debe calcular el valor con impuestos como referencia. Fuera de ese rango,
    el precio observado ES CON impuestos.

    Args:
        fecha_obs: Fecha de observación en formato ISO (YYYY-MM-DD).
        cfg: Configuración con decreto_22-2026.

    Returns:
        True si el precio observado INCLUYE impuestos, False si no los incluye.
    """
    if cfg is None:
        import json

        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)

    decreto = cfg["decreto_22_2026"]
    vigencia_inicio = decreto.get("fecha_vigencia_inicio")
    vigencia_fin = decreto.get("fecha_vigencia_fin")

    fecha = date.fromisoformat(fecha_obs)

    # Si no hay vigencia, asumimos que los precios observados incluyen impuestos
    if vigencia_inicio is None or vigencia_fin is None:
        return True

    inicio = date.fromisoformat(vigencia_inicio)
    fin = date.fromisoformat(vigencia_fin)

    # Dentro del rango decreto → precio observado SIN impuestos
    if inicio <= fecha <= fin:
        return False

    # Fuera del rango → precio observado CON impuestos
    return True
