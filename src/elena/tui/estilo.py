"""Estilos de la consola con los 16 colores ANSI de la terminal, sin colores fijos.

Así la consola toma la paleta de la terminal (en kitty, la que genera Noctalia a partir del
wallpaper: «green» es el lavanda principal, «red» un salmón) y deja ver su fondo translúcido.
"""

CURSOR = "on black"        # la fila elegida; en kitty, black = color0 = el fondo de la selección
VACIO = "bright_black"     # celdas vacías de las barras
APAGADO = "bright_black"   # silenciado o inactivo
ACENTO = "green"           # buses, destinos activos y lo que está «on»
ALERTA = "red"             # saturación, más de 100 %, estados inválidos
AVISO = "yellow"
