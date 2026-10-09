"""Genera los iconos de la app instalable (PWA): barril pixel art + el nombre de la web.
Uso: python scripts/generar_iconos_app.py   (usa Segoe UI Bold de Windows para las letras)
"""
from PIL import Image, ImageDraw, ImageFont

FONDO = (11, 18, 32, 255)
AZUL = (77, 179, 255, 255)
BLANCO = (238, 243, 250, 255)
FUENTE = "C:/Windows/Fonts/segoeuib.ttf"


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
    barril = Image.open("sprites/barrel-oil.png").convert("RGBA")
    alto = int(lado * alto_barril)
    ancho = int(barril.width * alto / barril.height)
    barril = barril.resize((ancho, alto), Image.NEAREST)
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
