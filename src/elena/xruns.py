"""Cortes (xruns) por nodo, a partir de `pw-top -b -n 2`.

`pw-top` imprime una tabla por iteración; la primera sale vacía (estado «C», sin medir) y la
segunda trae los datos. La columna ERR es un contador **acumulado** desde que se creó el nodo,
así que lo que importa son los cortes **nuevos**: los que aparecen mientras la consola está
abierta. Ejemplo de línea:

    R  291    360  48000  13.3us   7.7us  0.00  0.00    0    S16LE 6 48000  + elena-windows

El nombre puede llevar «+ » o «= » (nodos que siguen a otro driver) y el formato puede faltar.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

_LINEA = re.compile(
    r"^(?P<estado>\S)\s+(?P<id>\d+)\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<err>\d+)\s+(?P<resto>.*)$"
)
_FORMATO = re.compile(r"^\S+ \d+ \d+\s+")
IGNORADOS = re.compile(r"^(elena_medidor\.|Dummy-Driver$|Freewheel-Driver$)")


def leer(texto: str) -> dict[int, tuple[str, int]]:
    """{id: (nombre, ERR)} de la última tabla de `pw-top -b`."""
    bloques = re.split(r"^S\s+ID\s+QUANT.*$", texto, flags=re.MULTILINE)
    resultado: dict[int, tuple[str, int]] = {}
    for linea in bloques[-1].splitlines():
        if not (m := _LINEA.match(linea.strip())):
            continue
        nombre = _FORMATO.sub("", m["resto"]).removeprefix("+ ").removeprefix("= ").strip()
        if nombre and not IGNORADOS.match(nombre):
            resultado[int(m["id"])] = (nombre, int(m["err"]))
    return resultado


@dataclass
class Cortes:
    """Cortes nuevos por nodo desde que se abrió la consola, con ventana de tiempo."""

    reloj: Callable[[], float] = time.monotonic
    _base: dict[int, int] | None = None                   # ERR de cada nodo la primera vez
    _ultimo: dict[int, int] = field(default_factory=dict)
    _eventos: list[tuple[float, str, int]] = field(default_factory=list)

    def actualizar(self, tabla: dict[int, tuple[str, int]]) -> None:
        ahora = self.reloj()
        if self._base is None:
            # La primera lectura es la referencia: lo acumulado antes no cuenta
            self._base = {i: err for i, (_, err) in tabla.items()}
            self._ultimo = dict(self._base)
            return
        for i, (nombre, err) in tabla.items():
            # Un nodo nuevo (creado con la consola abierta) empieza en 0
            previo = self._ultimo.get(i, 0)
            if err > previo:
                self._eventos.append((ahora, nombre, err - previo))
            self._ultimo[i] = err

    def recientes(self, ventana: float = 30.0) -> dict[str, int]:
        """{nombre: cortes} de los últimos `ventana` segundos."""
        limite = self.reloj() - ventana
        self._eventos = [e for e in self._eventos if e[0] >= limite]
        suma: dict[str, int] = {}
        for _, nombre, n in self._eventos:
            suma[nombre] = suma.get(nombre, 0) + n
        return suma
