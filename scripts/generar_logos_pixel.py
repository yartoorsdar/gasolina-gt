"""Genera el arbol animado de empresas (emblemas pixel art originales, NO son los
logos oficiales; barcos y pipas tambien) y lo inserta en web/index.html e index.html.
Toda animacion es SOLO de opacity con steps (regla anti-parpadeo movil del proyecto).
Uso: python scripts/generar_logos_pixel.py
"""
import math
import re

N = 24
OUT = "#10151c"

# ---- geometria del arbol (viewBox de 340 de ancho; columnas de cuadricula con gap 8) ----
W = 340
X_IMP = [54, 170, 286]                      # centros de 3 columnas
X_EST = [39.5, 126.5, 213.5, 300.5]         # centros de 4 columnas
# (importador, estacion): Chevron->Texaco, UNO->Shell, UNO->UNO, Puma->Puma
RUTAS = [(0, 0), (1, 1), (1, 2), (2, 3)]


def poner(g, x, y, c, w=N, h=N):
    if 0 <= x < w and 0 <= y < h:
        g[(x, y)] = c


def contorno(g, w=N, h=N):
    extra = {}
    for (x, y) in g:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p = (x + dx, y + dy)
            if p not in g and 0 <= p[0] < w and 0 <= p[1] < h:
                extra[p] = OUT
    g.update(extra)


# ---------------------------------------------------------------- emblemas 24x24
def banda_y(k, x):
    return 4 + k * 5 + int(abs(x - 11.5) * 0.6)


def chevron():
    g = {}
    cols = ("#6db6ff", "#1f6fd1", "#0b3d7a")
    for k in range(3):
        for x in range(3, 21):
            for t in range(3):
                poner(g, x, banda_y(k, x) + t, cols[t])
    return g


def concha():
    g = {}
    for y in range(2, 20):
        for x in range(N):
            dx, dy = x - 11.5, 20.5 - y
            r = math.hypot(dx, dy)
            ang = math.atan2(dx, dy)
            if r <= 10.5 and abs(ang) <= 1.1:
                pos = (ang + 1.1) / (2.2 / 7)
                frac = pos - int(pos)
                if r > 9.2:
                    c = "#f08a00"
                elif frac < 0.2:
                    c = "#d8261c"
                else:
                    c = "#fbce07" if dx > -3 else "#ffe36a"
                g[(x, y)] = c
    for y in (20, 21):
        for x in range(9, 15):
            g[(x, y)] = "#c99a00"
    return g


def dentro(poly, x, y):
    ok = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ok = not ok
        j = i
    return ok


def estrella():
    g = {}
    cx, cy, R, r = 11.5, 12.0, 10.8, 4.6
    poly = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        poly.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    for y in range(N):
        for x in range(N):
            if dentro(poly, x + 0.5, y + 0.5):
                c = "#e2231a"
                if x + y < 21:
                    c = "#ff5a4d"
                elif x + y > 25:
                    c = "#a3140d"
                g[(x, y)] = c
    for p in ((11, 11), (12, 11), (11, 12), (12, 12)):
        g[p] = "#ffffff"
    return g


def felino():
    g = {}
    for y in range(N):
        for x in range(N):
            if ((x - 11.5) / 8.5) ** 2 + ((y - 13) / 7.2) ** 2 <= 1:
                g[(x, y)] = "#ff4a3d" if y < 10 else "#e30613"
    for y in range(3, 9):
        for x in range(4, 4 + (y - 3) + 1):
            g[(x, y)] = "#e30613"
            g[(23 - x, y)] = "#e30613"
    for y in range(5, 9):
        g[(5 + (y - 5) // 2, y)] = "#ff9a92"
        g[(18 - (y - 5) // 2, y)] = "#ff9a92"
    for y in range(15, 19):
        for x in range(8, 16):
            if ((x - 11.5) / 4.2) ** 2 + ((y - 16.5) / 2.6) ** 2 <= 1:
                g[(x, y)] = "#ff9a92"
    for ex in (7, 15):
        for dx in (0, 1):
            for dy in (0, 1):
                g[(ex + dx, 11 + dy)] = "#ffffff"
    g[(8, 12)] = OUT
    g[(15, 12)] = OUT
    for x in range(10, 14):
        g[(x, 15)] = "#3a0a0a"
    g[(11, 16)] = "#3a0a0a"
    g[(12, 16)] = "#3a0a0a"
    return g


def uno():
    g = {}
    for y in range(N):
        for x in range(N):
            if math.hypot(x - 11.5, y - 11.5) <= 10.6:
                s = x + y
                g[(x, y)] = "#ffa24d" if s < 17 else ("#f58220" if s < 27 else "#c9600a")
    for y in range(6, 17):
        for x in (11, 12, 13):
            g[(x, y)] = "#ffffff"
    for p in ((10, 6), (10, 7), (9, 8), (10, 8)):
        g[p] = "#ffffff"
    for y in (17, 18):
        for x in range(9, 16):
            g[(x, y)] = "#ffffff"
    return g


LOGOS = {"chevron": chevron, "shell": concha, "texaco": estrella, "puma": felino, "uno": uno}


def rects(g, filas=None):
    out = []
    ys = sorted({y for (_, y) in g})
    for y in ys:
        xs = sorted(x for (x, yy) in g if yy == y)
        i = 0
        while i < len(xs):
            x0 = xs[i]
            x1 = x0
            while i + 1 < len(xs) and xs[i + 1] == x1 + 1 and g[(xs[i + 1], y)] == g[(x0, y)]:
                i += 1
                x1 = xs[i]
            out.append('<rect x="%d" y="%d" width="%d" height="1" fill="%s"/>' % (x0, y, x1 - x0 + 1, g[(x0, y)]))
            i += 1
    return "".join(out)


def simbolo(id_, g, w=N, h=N):
    contorno(g, w, h)
    return '<symbol id="%s" viewBox="0 0 %d %d">%s</symbol>' % (id_, w, h, rects(g))


# ---- destellos por emblema: (clase de animacion, retraso s, lista de pixeles, color)
def overlays():
    ov = {}
    ov["chevron"] = [
        ("ovf", k * 0.5, [(x, banda_y(k, x)) for x in range(3, 21)], "#ffffff") for k in range(3)
    ]
    ov["uno"] = [("ovf", 0.0, [(7, 5), (8, 5), (6, 6), (7, 6), (6, 7)], "#ffffff")]
    ov["puma"] = [("ovb", 0.0, [(x, 11) for x in (7, 8, 15, 16)], "#e30613"),
                  ("ovb", 0.0, [(x, 12) for x in (7, 8, 15, 16)], OUT)]
    ov["texaco"] = [("ovf", 0.0, [(11, 3), (12, 3)], "#ffffff"),
                    ("ovf", 1.1, [(4, 9), (19, 9)], "#ffffff")]
    ov["shell"] = [("ovf", 0.0, [(9, 6), (10, 6)], "#ffffff"),
                   ("ovf", 1.3, [(15, 9), (14, 9)], "#ffffff")]
    return ov


OV = overlays()


def emp(lg, nombre, color, sub):
    cap = ""
    for clase, ret, pix, col in OV[lg]:
        for (x, y) in pix:
            cap += '<rect class="ov %s" style="animation-delay:%.1fs" x="%d" y="%d" width="1" height="1" fill="%s"/>' % (clase, ret, x, y, col)
    return ('<div class="ae-emp" style="--c:%s"><svg class="ae-logo" viewBox="0 0 24 24" role="img" aria-label="Emblema %s" '
            'shape-rendering="crispEdges"><use href="#lg-%s"/>%s</svg>%s<small>%s</small></div>'
            % (color, nombre, lg, cap, nombre, sub))


# ---------------------------------------------------------------- pipa y barco
def pipa():
    g = {}
    for x in range(2, 13):
        for y, c in zip(range(2, 7), ("#e6edf5", "#cfd8e3", "#cfd8e3", "#9aa7b6", "#66758a")):
            g[(x, y)] = c
    for y, c in zip(range(2, 7), ("#d8261c", "#d8261c", "#d8261c", "#a3140d", "#7a0f09")):
        g[(6, y)] = c
        g[(7, y)] = c
    for x in range(13, 17):
        for y, c in zip(range(3, 7), ("#ffcc33", "#ffcc33", "#e0a800", "#e0a800")):
            g[(x, y)] = c
    g[(15, 4)] = "#bfe3ff"
    g[(16, 4)] = "#bfe3ff"
    for x in range(2, 17):
        g[(x, 7)] = "#2a2f38"
    for x0 in (3, 9, 14):
        for dx in (0, 1):
            for dy in (0, 1):
                g[(x0 + dx, 7 + dy)] = "#3b3f48"
    return g


def barco():
    g = {}
    for x in range(1, 22):
        g[(x, 7)] = "#c0392b"
    for x in range(2, 21):
        g[(x, 8)] = "#2b4a6f"
    for x in range(4, 19):
        g[(x, 9)] = "#1e3550"
    for x in range(2, 21):
        g[(x, 6)] = "#8c97a4"
    for x in range(3, 14):
        g[(x, 4)] = "#cfd8e3"
        g[(x, 5)] = "#9aa7b6" if x not in (6, 10) else "#66758a"
    for x in range(16, 20):
        for y in range(2, 6):
            g[(x, y)] = "#e9eef4"
    for x in (17, 18):
        g[(x, 3)] = "#5aa9e6"
    for y in (0, 1):
        g[(15, y + 1)] = "#2a2f38"
    return g


PW, PH = 18, 10
BW, BH = 24, 12


# ---------------------------------------------------------------- recorridos animados
def muestras(pts, n):
    """n posiciones (x, y, direccion) repartidas por la poligonal pts."""
    tramos = []
    total = 0
    for (a, b) in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        tramos.append((a, b, L))
        total += L
    out = []
    for i in range(n):
        d = total * (0.06 + 0.88 * i / (n - 1))
        for (a, b, L) in tramos:
            if d <= L + 1e-9:
                f = d / L if L else 0
                dx, dy = b[0] - a[0], b[1] - a[1]
                dire = ("d" if dy > 0 else "u") if abs(dy) > abs(dx) else ("r" if dx > 0 else "l")
                out.append((a[0] + dx * f, a[1] + dy * f, dire))
                break
            d -= L
    return out


TRANS = {"r": "", "l": " scale(-1,1)", "d": " rotate(90)", "u": " rotate(-90)"}


ESC = 1.6


def camion(pts, n, dur, off, simbolo_id, cx, cy, extra=""):
    """Un vehiculo recorriendo pts; n fotogramas que se encienden uno tras otro."""
    s = ""
    for i, (x, y, dire) in enumerate(muestras(pts, n)):
        s += ('<g transform="translate(%.1f %.1f)%s"><use href="#%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
              'class="pf pf%d" style="--d:%ss;--n:%d;--i:%d;--o:%.2f"/></g>'
              % (x, y, TRANS[dire], simbolo_id, -cx * ESC, -cy * ESC, cx * 2 * ESC, cy * 2 * ESC, n, dur, n, i, off))
    return s


def svg_barcos():
    h = 92
    s = ['<svg class="ae-red" viewBox="0 0 %d %d" aria-hidden="true">' % (W, h)]
    s.append('<rect x="0" y="0" width="%d" height="42" fill="#2f7fd1" opacity=".28"/>' % W)
    for fase, hh in ((0, 0), (1, 1)):
        olas = "".join('<rect x="%d" y="%d" width="6" height="1" fill="#9fd0ff"/>' % (x, 18 + hh * 12) for x in range(4 + hh * 12, W, 24))
        s.append('<g class="ola ola%d">%s</g>' % (fase, olas))
    # tuberias de descarga desde el puerto a cada importador (gotas que bajan)
    for j, xi in enumerate(X_IMP):
        s.append('<rect x="%.1f" y="42" width="3" height="50" fill="#ffffff" opacity=".22"/>' % (xi - 1.5))
        n = 6
        for i in range(n):
            y = 46 + i * (42 / (n - 1))
            s.append('<rect class="pf pf%d" style="--d:1.8s;--n:%d;--i:%d;--o:%.2f" x="%.1f" y="%.1f" width="5" height="5" fill="#ffd24a"/>'
                     % (n, n, i, j * 0.31, xi - 2.5, y - 2.5))
    # dos barcos en sentido opuesto
    n = 16
    s.append(camion([(-20, 13), (W + 20, 13)], n, 18, 0.0, "pix-barco", BW // 2, BH // 2))
    s.append(camion([(W + 20, 31), (-20, 31)], n, 18, 0.5, "pix-barco", BW // 2, BH // 2))
    s.append("</svg>")
    return "".join(s)


def svg_pipas():
    h = 100
    s = ['<svg class="ae-red" viewBox="0 0 %d %d" aria-hidden="true">' % (W, h)]
    carriles = []
    for k, (a, b) in enumerate(RUTAS):
        xi, xs = X_IMP[a], X_EST[b]
        baja = [(xi - 7, 0), (xi - 7, 34), (xs - 7, 34), (xs - 7, h)]
        sube = [(xs + 7, h), (xs + 7, 66), (xi + 7, 66), (xi + 7, 0)]
        carriles.append((k, baja, sube))
    for k, baja, sube in carriles:
        for pts in (baja, sube):
            d = "M" + " L".join("%.1f %.1f" % p for p in pts)
            s.append('<path d="%s" fill="none" stroke="#ffffff" stroke-opacity=".2" stroke-width="1" stroke-dasharray="3 3"/>' % d)
    for k, baja, sube in carriles:
        s.append(camion(baja, 12, 7, 0.12 + k * 0.23, "pix-pipa", PW // 2, PH // 2))
        s.append('<g opacity=".55">' + camion(sube, 12, 7, 0.55 + k * 0.17, "pix-pipa", PW // 2, PH // 2) + "</g>")
    s.append("</svg>")
    return "".join(s)


FUENTES = (
    '<a href="https://lahora.gt/lh-economia/ralvarado/2026/07/02/subsidio-a-combustibles-en-guatemala-finaliza-este-2-de-julio-con-q1-mil-580-millones-ejecutados/" target="_blank" rel="noopener">La Hora</a>, '
    '<a href="https://revistasumma.com/?p=226760" target="_blank" rel="noopener">Revista Summa</a>, '
    '<a href="https://iies.usac.edu.gt/wp-content/uploads/2020/11/Bolet%C3%ADn-No.-03-marzo-2018.pdf" target="_blank" rel="noopener">USAC</a>'
)


def bloque():
    sprite = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
              + "".join(simbolo("lg-" + k, f()) for k, f in LOGOS.items())
              + simbolo("pix-pipa", pipa(), PW, PH) + simbolo("pix-barco", barco(), BW, BH) + "</svg>")
    imp = [("chevron", "Chevron Guatemala", "#0054a4", "Importador"),
           ("uno", "UNO Guatemala", "#f58220", "Importador"),
           ("puma", "Puma Energy", "#e30613", "Importador")]
    ven = [("texaco", "Texaco", "#e2231a", "Estaciones"),
           ("shell", "Shell", "#fbce07", "Licencia de UNO"),
           ("uno", "UNO", "#f58220", "Estaciones"),
           ("puma", "Puma", "#e30613", "Estaciones")]
    return (
        '<!-- Empresas: quien importa y quien vende al publico -->\n'
        '                    <div class="arbol-emp">\n'
        '                        ' + sprite + '\n'
        '                        <h3>Quién trae y quién vende la gasolina en Guatemala</h3>\n'
        '                        <div class="ae-red-wrap">\n'
        '                        <div class="ae-nivel">Llega en barco: ≈80 % por el Pacífico y ≈20 % por el Atlántico (MEM)</div>\n'
        '                        ' + svg_barcos() + '\n'
        '                        <div class="ae-nivel">Importan combustible</div>\n'
        '                        <div class="ae-fila g3">' + "".join(emp(*i) for i in imp) + '</div>\n'
        '                        ' + svg_pipas() + '\n'
        '                        <div class="ae-nivel">Venden al público (gasolineras)</div>\n'
        '                        <div class="ae-fila g4">' + "".join(emp(*v) for v in ven) + '</div>\n'
        '                        </div>\n'
        '                        <div class="ae-fuente">Pipa llena baja a la gasolinera; pipa vacía regresa. '
        'Dibujos ilustrativos en pixel art, no son los logos oficiales. '
        'Empresas citadas por el MEM y la prensa; no es un ranking de volumen. Fuentes: ' + FUENTES + '.</div>\n'
        '                    </div><!-- /Empresas -->')


def css():
    kf = ""
    for n in (6, 12, 16):
        kf += "        @keyframes trf%d{0%%{opacity:1}%.3f%%{opacity:0}100%%{opacity:0}}\n" % (n, 100.0 / n)
    cls = "".join(".pf%d{animation-name:trf%d}" % (n, n) for n in (6, 12, 16))
    return (
        "/* ── Árbol de empresas (importan -> venden al público). Animación SOLO de opacity ── */\n"
        "        .arbol-emp{margin:18px 8px 4px;text-align:center}\n"
        "        .arbol-emp h3{font-size:.95rem;margin:0 0 6px;color:#eef3fa !important}\n"
        "        .ae-red-wrap{max-width:340px;margin:0 auto}\n"
        "        .arbol-emp .ae-nivel{font-size:.7rem;letter-spacing:.05em;text-transform:uppercase;color:var(--text-muted);margin:10px 0 6px}\n"
        "        .ae-fila{display:grid;gap:8px}\n"
        "        .ae-fila.g3{grid-template-columns:repeat(3,1fr)}\n"
        "        .ae-fila.g4{grid-template-columns:repeat(4,1fr)}\n"
        "        .ae-emp{min-width:0;color:#eef3fa !important;text-align:center;padding:8px 4px;border-radius:10px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);border-top:3px solid var(--c,#888);font-weight:700;font-size:.72rem;line-height:1.15;display:flex;flex-direction:column;align-items:center;gap:4px}\n"
        "        .ae-emp small{font-weight:400;font-size:.65rem;color:var(--text-muted)}\n"
        "        .ae-logo{width:48px;height:48px;image-rendering:pixelated}\n"
        "        .ae-red{display:block;width:100%;height:auto}\n"
        "        .ae-fuente{font-size:.7rem;color:var(--text-muted);margin-top:10px}\n"
        "        .ae-fuente a{color:inherit;text-decoration:underline}\n"
        "        .pf{opacity:0;animation-duration:var(--d);animation-iteration-count:infinite;animation-timing-function:steps(1,end);\n"
        "            animation-delay:calc(((var(--i) / var(--n)) + var(--o) - 1) * var(--d))}\n"
        "        " + cls + "\n" + kf +
        "        .ov{opacity:0;animation:ovf 3.6s steps(1,end) infinite}\n"
        "        .ov.ovb{animation-name:ovb;animation-duration:4.2s}\n"
        "        @keyframes ovf{0%{opacity:0}8%{opacity:.95}16%{opacity:0}100%{opacity:0}}\n"
        "        @keyframes ovb{0%{opacity:0}90%{opacity:0}92%{opacity:1}97%{opacity:0}100%{opacity:0}}\n"
        "        .ola{animation:ola 1.6s steps(1,end) infinite}.ola1{animation-delay:.8s}\n"
        "        @keyframes ola{0%{opacity:1}50%{opacity:0}100%{opacity:0}}\n"
        "        @media (prefers-reduced-motion:reduce){.pf,.ov,.ola{animation:none !important}.ola0{opacity:1}}\n"
    )


def main():
    nuevo = bloque()
    nuevo_css = css()
    pat = re.compile(r'<!-- Empresas:.*?<!-- /Empresas -->', re.S)
    pat_css = re.compile(r'/\* ── Árbol de empresas.*?(?=/\* ── Legibilidad)', re.S)
    for ruta in ("web/index.html", "index.html"):
        b = open(ruta, "rb").read().decode("utf-8")
        nl = "\r\n" if "\r\n" in b else "\n"
        assert len(pat.findall(b)) == 1 and len(pat_css.findall(b)) == 1, ruta
        b = pat.sub(lambda m: nuevo.replace("\n", nl), b)
        b = pat_css.sub(lambda m: nuevo_css.replace("\n", nl) + "        ", b)
        open(ruta, "wb").write(b.encode("utf-8"))


if __name__ == "__main__":
    main()
