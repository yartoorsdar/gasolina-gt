"""Genera los iconos de la app instalable (PWA): el logo de la web (barril pixel art naranja del
encabezado, el mismo del favicon) + el nombre de la web.
Uso: python scripts/generar_iconos_app.py   (usa Segoe UI Bold de Windows para las letras)
"""
from PIL import Image, ImageDraw, ImageFont

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


def ajustar(fuente_ruta, textos, ancho_max):
    """Mayor tamano de letra con el que el texto completo cabe en ancho_max."""
    tam = 8
    while True:
        f = ImageFont.truetype(fuente_ruta, tam + 1)
        ancho = sum(f.getlength(t) for t, _ in textos)
        if ancho > ancho_max:
            return ImageFont.truetype(fuente_ruta, tam)
        tam += 1


def icono(lado, alto_barril, ancho_texto, y_barril):
    base = Image.new("RGBA", (lado, lado), FONDO)
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


icono(192, 0.58, 0.88, 0.11).save("iconos/icon-192.png")
icono(512, 0.58, 0.88, 0.11).save("iconos/icon-512.png")
icono(512, 0.44, 0.64, 0.20).save("iconos/icon-512-maskable.png")  # zona segura (80 %) de iconos adaptables
icono(180, 0.58, 0.88, 0.11).save("iconos/icon-180.png")             # apple-touch-icon
