"""Íconos pixel art animados (16x16) para Gasolinasogt.

Uso: python scripts/generar_iconos_pixel.py salida.json
El JSON resultante es el objeto `PX_ICONOS` embebido en web/index.html e
index.html (mantener ambas copias iguales). El favicon (iconos/) sale del
primer fotograma de "barril". Para editar un ícono: cambiar su cuadrícula,
regenerar y reemplazar `const PX_ICONOS = {...};` en los dos HTML.

Cada ícono = lista de fotogramas (cuadrículas 16x16). Se emite SVG compacto:
- los píxeles iguales en TODOS los fotogramas van una sola vez (grupo fijo);
- cada fotograma solo lleva lo que cambia (grupo .pxf animado por opacity).
Un <path> por color por grupo; runs horizontales "M x y h n v1 h-n z".
"""
import json
import sys

PALETA = {
    "K": "#0d1422", "O": "#ff8a1f", "o": "#c45f0c", "W": "#fff1dc", "w": "#f4f7fb",
    "R": "#ff4d3d", "r": "#b3261b", "C": "#38d9f5", "c": "#1a8fb0", "G": "#35e07a",
    "g": "#1b8f4a", "Y": "#ffc233", "y": "#c98a00", "A": "#9fb3c8", "a": "#5f7189",
    "S": "#d6e2f0", "B": "#9c5b21", "L": "#e0a458", "D": "#2b3550", "E": "#38a8f5",
    "e": "#1f5f99", "X": "currentColor", "x": "currentColor@0.35",
}

N = 16


def g(*filas):
    filas = list(filas)
    assert len(filas) == N, len(filas)
    for f in filas:
        assert len(f) == N, (len(f), f)
    return [list(f) for f in filas]


def copia(grid):
    return [fila[:] for fila in grid]


def pintar(grid, pixeles, c):
    out = copia(grid)
    for r, col in pixeles:
        out[r][col] = c
    return out


def desplazar(grid, dy=0, dx=0):
    out = [["."] * N for _ in range(N)]
    for r in range(N):
        for col in range(N):
            if grid[r][col] != ".":
                rr, cc = r + dy, col + dx
                if 0 <= rr < N and 0 <= cc < N:
                    out[rr][cc] = grid[r][col]
    return out


ICONOS = {}

# 1. Barril (logo): brillo metálico que recorre el cuerpo
barril = g(
    "................",
    "....KKKKKKKK....",
    "...KooooooooK...",
    "...KOOOOOOOOK...",
    "...KKKKKKKKKK...",
    "...KOOOOOOOOK...",
    "...KOOOOOOOOK...",
    "...KOOOKKOOOK...",
    "...KOOKKKKOOK...",
    "...KOOOKKOOOK...",
    "...KKKKKKKKKK...",
    "...KOOOOOOOOK...",
    "...KOOOOOOOOK...",
    "...KooooooooK...",
    "....KKKKKKKK....",
    "................",
)
filas_brillo = [3, 5, 6, 11, 12]
ICONOS["barril"] = {"dur": 2.4, "frames": [
    barril,
    pintar(barril, [(r, 5) for r in filas_brillo], "W"),
    pintar(barril, [(r, 10) for r in filas_brillo], "W"),
    pintar(barril, [(r, 13 - 1) for r in filas_brillo if False], "W"),
]}

# 2. Bomba de gasolina: la pantalla cambia (precio corriendo) y cae una gota
bomba = g(
    "................",
    "..KKKKKKKK......",
    "..KRRRRRRK......",
    "..KRwwwwRK.AA...",
    "..KRwCCwRK..A...",
    "..KRwwwwRK..A...",
    "..KRRRRRRK..AA..",
    "..KRRRRRRK...A..",
    "..KRrrrrRKA..A..",
    "..KRRRRRRK.A.A..",
    "..KRRRRRRK..AA..",
    "..KRRRRRRK......",
    "..KRRRRRRK......",
    ".KKKKKKKKKK.....",
    ".KaaaaaaaaK.....",
    ".KKKKKKKKKK.....",
)
p1 = pintar(pintar(bomba, [(4, 5), (4, 6)], "w"), [(4, 4), (4, 5), (4, 7)], "C")
p1 = pintar(p1, [(11, 13)], "O")
p2 = pintar(pintar(bomba, [(4, 5), (4, 6)], "w"), [(4, 4), (4, 6)], "C")
p2 = pintar(p2, [(12, 13)], "O")
ICONOS["bomba"] = {"dur": 1.5, "frames": [bomba, p1, p2]}

# 3. Gráfica: la línea se dibuja sola
ejes = [["."] * N for _ in range(N)]
for r in range(1, 13):
    ejes[r][1] = "A"
for col in range(1, 15):
    ejes[12][col] = "A"
puntos = [(9, 2), (8, 3), (7, 4), (8, 5), (9, 6), (8, 7), (7, 8), (6, 9), (5, 10), (4, 11), (3, 12), (2, 13)]
flecha = [(2, 12), (3, 13), (2, 14)]
ICONOS["grafica"] = {"dur": 2.4, "frames": [
    pintar(ejes, puntos[:3], "G"),
    pintar(ejes, puntos[:6], "G"),
    pintar(ejes, puntos[:9], "G"),
    pintar(pintar(ejes, puntos, "G"), flecha, "G"),
]}

# 4. Periódico: las líneas de texto se "escriben"
periodico = g(
    "................",
    "..AAAAAAAAAAA...",
    "..AwwwwwwwwwA...",
    "..AwRRRRRRRwAA..",
    "..AwwwwwwwwwAwA.",
    "..AwCCCwaaawAwA.",
    "..AwCCCwwwwwAwA.",
    "..AwCCCwaaawAwA.",
    "..AwwwwwwwwwAwA.",
    "..Awaa.....wAwA.",
    "..AwwwwwwwwwAwA.",
    "..Awaa.....wAwA.",
    "..AwwwwwwwwwAwA.",
    "..AAAAAAAAAAAAA.",
    "................",
    "................",
)
periodico = [["w" if (c == "." and 3 <= col <= 11 and r in (9, 11)) else c for col, c in enumerate(f)] for r, f in enumerate(periodico)]
ICONOS["periodico"] = {"dur": 1.8, "frames": [
    periodico,
    pintar(periodico, [(9, c) for c in range(4, 11)] + [(11, c) for c in range(4, 7)], "a"),
    pintar(periodico, [(9, c) for c in range(4, 11)] + [(11, c) for c in range(4, 11)], "a"),
]}

# 5. Alerta: el triángulo late de amarillo a naranja
alerta = g(
    ".......YY.......",
    "......YYYY......",
    "......YKKY......",
    ".....YYKKYY.....",
    ".....YYKKYY.....",
    "....YYYKKYYY....",
    "....YYYKKYYY....",
    "...YYYYKKYYYY...",
    "...YYYYKKYYYY...",
    "..YYYYYYYYYYYY..",
    "..YYYYYKKYYYYY..",
    ".YYYYYYKKYYYYYY.",
    ".YYYYYYYYYYYYYY.",
    "YYYYYYYYYYYYYYYY",
    "................",
    "................",
)
ICONOS["alerta"] = {"dur": 1.0, "frames": [
    alerta, [["O" if c == "Y" else c for c in f] for f in alerta],
]}

# 6. Espadas: chispas al chocar
espadas = g(
    "................",
    ".S............S.",
    ".SS..........SS.",
    "..SS........SS..",
    "...SS......SS...",
    "....SS....SS....",
    ".....SS..SS.....",
    "......SSSS......",
    "......SSSS......",
    ".....YYSSYY.....",
    "....YY....YY....",
    "...BYY....YYB...",
    "..BB........BB..",
    ".BB..........BB.",
    "................",
    "................",
)
ICONOS["espadas"] = {"dur": 1.2, "frames": [
    espadas,
    pintar(espadas, [(5, 7), (5, 8), (7, 4), (7, 11)], "Y"),
    pintar(pintar(espadas, [(4, 7), (4, 8), (6, 4), (6, 11), (8, 3), (8, 12)], "W"), [(5, 7), (5, 8)], "Y"),
]}

# 7. Caja: rebota
caja = g(
    "................",
    "................",
    "...BBBBBBBBBB...",
    "..BLLLLLLLLLLB..",
    "..BLBLLLLLLBLB..",
    "..BLLBLLLLBLLB..",
    "..BLLLBLLBLLLB..",
    "..BLLLLBBLLLLB..",
    "..BLLLLBBLLLLB..",
    "..BLLLBLLBLLLB..",
    "..BLLBLLLLBLLB..",
    "..BLBLLLLLLBLB..",
    "..BLLLLLLLLLLB..",
    "...BBBBBBBBBB...",
    "................",
    "................",
)
sombra = [(14, c) for c in range(3, 13)]
ICONOS["caja"] = {"dur": 1.0, "frames": [
    pintar(caja, sombra, "a"),
    pintar(desplazar(caja, dy=-1), [(14, c) for c in range(5, 11)], "a"),
]}

# 8. Globo: gira (los continentes se desplazan)
mascara = g(
    ".....AAAAAA.....",
    "...AAEEEEEEAA...",
    "..AEEEEEEEEEEA..",
    "..AEEEEEEEEEEA..",
    ".AEEEEEEEEEEEEA.",
    ".AEEEEEEEEEEEEA.",
    ".AEEEEEEEEEEEEA.",
    ".AEEEEEEEEEEEEA.",
    ".AEEEEEEEEEEEEA.",
    ".AEEEEEEEEEEEEA.",
    "..AEEEEEEEEEEA..",
    "..AEEEEEEEEEEA..",
    "...AAEEEEEEAA...",
    ".....AAAAAA.....",
    "................",
    "................",
)
tierra = [
    "................",
    "......GG........",
    ".....GGGG.....G.",
    "....GGGG.....GG.",
    ".....GG.....GGG.",
    "......G......GG.",
    "..G..........G..",
    ".GGG.....G......",
    ".GGG....GGG.....",
    "..GG....GGGG....",
    "...G.....GG.....",
    "..........G.....",
    "................",
    "................",
    "................",
    "................",
]
frames_globo = []
for k in range(4):
    fr = copia(mascara)
    for r in range(N):
        for col in range(N):
            if fr[r][col] == "E" and tierra[r][(col + 4 * k) % N] == "G":
                fr[r][col] = "G"
    frames_globo.append(fr)
ICONOS["globo"] = {"dur": 2.0, "frames": frames_globo}

# 9. Rayo: destello blanco
rayo = g(
    ".........YYYY...",
    "........YYYY....",
    ".......YYYY.....",
    "......YYYY......",
    ".....YYYY.......",
    "....YYYYYYYY....",
    "...YYYYYYYY.....",
    ".......YYYY.....",
    "......YYYY......",
    ".....YYYY.......",
    "....YYY.........",
    "...YY...........",
    "..Y.............",
    "................",
    "................",
    "................",
)
ICONOS["rayo"] = {"dur": 1.5, "frames": [
    rayo, [["W" if c == "Y" else c for c in f] for f in rayo], rayo,
]}

# 10. Gota de petróleo: cae y salpica
forma = [
    "....O....",
    "...ODO...",
    "...ODO...",
    "..ODDDO..",
    ".ODDDDDO.",
    ".ODCDDDO.",
    "ODCDDDDDO",
    "ODCDDDDDO",
    "ODDDDDDDO",
    ".ODDDDDO.",
    "..OOOOO..",
]
frames_gota = []
for dy in range(4):
    fr = [["."] * N for _ in range(N)]
    for r, fila in enumerate(forma):
        for col, c in enumerate(fila):
            if c != "." and 0 <= r + dy < N - 1:
                fr[r + dy][col + 3] = c
    for col in range(2, 14):
        fr[15][col] = "o"
    if dy == 3:
        for p in [(14, 1), (13, 2), (14, 13), (13, 12)]:
            fr[p[0]][p[1]] = "O"
    frames_gota.append(fr)
ICONOS["gota"] = {"dur": 1.6, "frames": frames_gota}

# 11. Punto (título del semáforo): late con halo, usa currentColor
punto = g(
    "................",
    "................",
    "................",
    "................",
    "................",
    "......XXXX......",
    ".....XXXXXX.....",
    ".....XXWXXX.....",
    ".....XXXXXX.....",
    ".....XXXXXX.....",
    "......XXXX......",
    "................",
    "................",
    "................",
    "................",
    "................",
)
halo = pintar(punto, [(4, c) for c in range(6, 10)] + [(11, c) for c in range(6, 10)]
              + [(r, 4) for r in range(6, 10)] + [(r, 11) for r in range(6, 10)], "x")
ICONOS["punto"] = {"dur": 1.2, "frames": [punto, halo]}


# ──────────────────────────────────────────────
# SVG compacto
# ──────────────────────────────────────────────

def path_por_color(pixeles):
    """pixeles: set[(r, c, color)] → '<path…>' por color con runs horizontales."""
    por_color = {}
    for r, col, color in pixeles:
        por_color.setdefault(color, set()).add((r, col))
    salida = []
    for color, celdas in sorted(por_color.items()):
        d = []
        for r in range(N):
            cols = sorted(c for rr, c in celdas if rr == r)
            i = 0
            while i < len(cols):
                j = i
                while j + 1 < len(cols) and cols[j + 1] == cols[j] + 1:
                    j += 1
                d.append(f"M{cols[i]} {r}h{j - i + 1}v1h-{j - i + 1}z")
                i = j + 1
        hexa = PALETA[color]
        if "@" in hexa:
            fill, op = hexa.split("@")
            salida.append(f'<path fill="{fill}" fill-opacity="{op}" d="{"".join(d)}"/>')
        else:
            salida.append(f'<path fill="{hexa}" d="{"".join(d)}"/>')
    return "".join(salida)


def svg_icono(nombre, datos):
    frames = datos["frames"]
    n = len(frames)
    conjuntos = [{(r, c, f[r][c]) for r in range(N) for c in range(N) if f[r][c] != "."} for f in frames]
    fijo = set.intersection(*conjuntos) if n > 1 else conjuntos[0]
    partes = [path_por_color(fijo)]
    if n > 1:
        dur = datos["dur"]
        for i, conj in enumerate(conjuntos):
            delay = 0 if i == 0 else -round((n - i) * dur / n, 3)
            partes.append(
                f'<g class="pxf f{i}" style="animation:px{n} {dur}s steps(1,end) infinite;'
                f'animation-delay:{delay}s">{path_por_color(conj - fijo)}</g>'
            )
    return "".join(partes)


if __name__ == "__main__":
    salida = {nombre: svg_icono(nombre, datos) for nombre, datos in ICONOS.items()}
    destino = sys.argv[1]
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False)
    print({k: len(v) for k, v in salida.items()}, "total", sum(len(v) for v in salida.values()))
