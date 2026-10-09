"""Genera los iconos de la app instalable (PWA): el logo de la web (barril pixel art naranja del
encabezado, el mismo del favicon) + el nombre de la web,
sobre un panel de vidrio liquido (borde translucido con reflejos, estilo iOS).
Uso: python scripts/generar_iconos_app.py   (usa Segoe UI Bold de Windows para las letras)
"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONDO = (20, 32, 56, 255)
AZUL = (77, 179, 255, 255)
BLANCO = (238, 243, 250, 255)
FUENTE = "C:/Windows/Fonts/segoeuib.ttf"

# Logo de la web: primer fotograma de "barril" en scripts/generar_iconos_pixel.py (16x16)
LOGO = (
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
)
COLORES = {"K": (13, 20, 34, 255), "O": (255, 138, 31, 255), "o": (196, 95, 12, 255)}


def logo(alto):
    """Logo escalado sin suavizar (pixel art nitido) a `alto` px de alto."""
    ancho_celdas = len(LOGO[0])
    im = Image.new("RGBA", (ancho_celdas, len(LOGO)), (0, 0, 0, 0))
    for y, fila in enumerate(LOGO):
        for x, c in enumerate(fila):
            if c in COLORES:
                im.putpixel((x, y), COLORES[c])
    caja = im.getbbox()
    im = im.crop(caja)
    k = max(1, alto // im.height)
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def degradado(lado, c1, c2):
    """Degradado diagonal c1 (arriba-izquierda) -> c2 (abajo-derecha)."""
    g = Image.linear_gradient("L").resize((lado, lado)).rotate(45, expand=False, fillcolor=0)
    g = Image.linear_gradient("L").resize((lado, lado))
    diag = Image.new("L", (lado, lado))
    px = diag.load()
    for y in range(lado):
        for x in range(lado):
            px[x, y] = int(255 * (x + y) / (2 * lado - 2))
    return Image.composite(Image.new("RGBA", (lado, lado), c2), Image.new("RGBA", (lado, lado), c1), diag)


def fondo_vidrio(lado, margen):
    """Fondo azul profundo + panel de vidrio: relleno translucido, borde con brillo
    especular arriba-izquierda y sombra suave abajo-derecha (como Liquid Glass)."""
    S = 3                                  # supermuestreo para bordes suaves
    L = lado * S
    base = degradado(L, (44, 74, 136, 255), (8, 14, 30, 255))
    # resplandor naranja suave detras del logo
    halo = Image.new("L", (L, L), 0)
    ImageDraw.Draw(halo).ellipse((L * 0.25, L * 0.18, L * 0.75, L * 0.68), fill=70)
    halo = halo.filter(ImageFilter.GaussianBlur(L * 0.07))
    base.alpha_composite(Image.composite(Image.new("RGBA", (L, L), (255, 138, 31, 255)), Image.new("RGBA", (L, L), (0, 0, 0, 0)), halo))

    m = int(L * margen)
    caja = (m, m, L - m, L - m)
    r = int(L * 0.22)
    panel = Image.new("L", (L, L), 0)
    ImageDraw.Draw(panel).rounded_rectangle(caja, r, fill=255)
    # relleno de vidrio: velo claro, mas fuerte arriba
    velo = Image.new("L", (L, L), 0)
    vp = velo.load()
    for y in range(L):
        a = int(46 - 30 * y / L)
        for x in range(L):
            vp[x, y] = a
    velo = ImageChops.multiply(velo, panel)
    base.alpha_composite(Image.composite(Image.new("RGBA", (L, L), (255, 255, 255, 255)), Image.new("RGBA", (L, L), (0, 0, 0, 0)), velo))
    # borde: trazo de ~1.6 % con brillo diagonal (fuerte arriba-izquierda, tenue abajo-derecha)
    grosor = max(2, int(L * 0.016))
    interior = Image.new("L", (L, L), 0)
    ImageDraw.Draw(interior).rounded_rectangle((m + grosor, m + grosor, L - m - grosor, L - m - grosor), r - grosor, fill=255)
    trazo = ImageChops.subtract(panel, interior)
    brillo = Image.new("L", (L, L))
    bp = brillo.load()
    for y in range(L):
        for x in range(L):
            t = (x + y) / (2 * L - 2)
            bp[x, y] = int(255 * max(0.18, 1 - 1.5 * t) if t < 0.55 else 255 * 0.18 + 90 * max(0, t - 0.8))
    borde = ImageChops.multiply(trazo, brillo)
    base.alpha_composite(Image.composite(Image.new("RGBA", (L, L), (255, 255, 255, 255)), Image.new("RGBA", (L, L), (0, 0, 0, 0)), borde))
    # reflejo especular curvo en la parte alta del panel
    esp = Image.new("L", (L, L), 0)
    ImageDraw.Draw(esp).ellipse((m + L * 0.04, m - L * 0.30, L - m - L * 0.04, m + L * 0.30), fill=95)
    esp = ImageChops.multiply(esp.filter(ImageFilter.GaussianBlur(L * 0.012)), panel)
    base.alpha_composite(Image.composite(Image.new("RGBA", (L, L), (255, 255, 255, 255)), Image.new("RGBA", (L, L), (0, 0, 0, 0)), esp))
    return base.resize((lado, lado), Image.LANCZOS)


def ajustar(fuente_ruta, textos, ancho_max):
    """Mayor tamano de letra con el que el texto completo cabe en ancho_max."""
    tam = 8
    while True:
        f = ImageFont.truetype(fuente_ruta, tam + 1)
        ancho = sum(f.getlength(t) for t, _ in textos)
        if ancho > ancho_max:
            return ImageFont.truetype(fuente_ruta, tam)
        tam += 1


def icono(lado, alto_barril, ancho_texto, y_barril, margen):
    base = fondo_vidrio(lado, margen)
    barril = logo(int(lado * alto_barril))
    alto, ancho = barril.height, barril.width
    alto_barril = alto / lado
    base.alpha_composite(barril, ((lado - ancho) // 2, int(lado * y_barril)))
    textos = [("Gasolinasogt", AZUL), (".com", BLANCO)]
    f = ajustar(FUENTE, textos, lado * ancho_texto)
    total = sum(f.getlength(t) for t, _ in textos)
    d = ImageDraw.Draw(base)
    x = (lado - total) / 2
    y = lado * (y_barril + alto_barril) + lado * 0.03
    for t, c in textos:
        d.text((x, y), t, font=f, fill=c)
        x += f.getlength(t)
    return base


icono(192, 0.52, 0.74, 0.14, 0.06).save("iconos/icon-192.png")
icono(512, 0.52, 0.74, 0.14, 0.06).save("iconos/icon-512.png")
icono(512, 0.40, 0.58, 0.22, 0.10).save("iconos/icon-512-maskable.png")  # zona segura (80 %) de iconos adaptables
icono(180, 0.52, 0.74, 0.14, 0.06).save("iconos/icon-180.png")             # apple-touch-icon
