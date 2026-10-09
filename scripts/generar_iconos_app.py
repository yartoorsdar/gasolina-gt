"""Genera los iconos de la app instalable (PWA) a partir del barril pixel art.
Uso: python scripts/generar_iconos_app.py
"""
from PIL import Image

FONDO = (11, 18, 32, 255)


def icono(lado, alto_rel):
    base = Image.new("RGBA", (lado, lado), FONDO)
    barril = Image.open("sprites/barrel-oil.png").convert("RGBA")
    alto = int(lado * alto_rel)
    ancho = int(barril.width * alto / barril.height)
    barril = barril.resize((ancho, alto), Image.NEAREST)
    base.alpha_composite(barril, ((lado - ancho) // 2, (lado - alto) // 2))
    return base


icono(192, 0.78).save("iconos/icon-192.png")
icono(512, 0.78).save("iconos/icon-512.png")
icono(512, 0.56).save("iconos/icon-512-maskable.png")  # zona segura para iconos adaptables
icono(180, 0.78).save("iconos/icon-180.png")             # apple-touch-icon
