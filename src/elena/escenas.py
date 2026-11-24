"""Escenas: el volumen y el silencio de cada bus, guardados con nombre (local/escenas.toml).

Solo tocan los buses, no el ruteo. El volumen va en % como pavucontrol, y al aplicarlo se
conserva el balance por canal de cada bus (`escalar`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from elena.acciones import cmd_silencio, cmd_volumen
from elena.modelo import BUSES, ErrorElena, Grafo, escalar
from elena.rutas import LOCAL

RUTA = LOCAL / "escenas.toml"
CLAVES = {b.clave: b for b in BUSES}

ENCABEZADO = """\
# Escenas de ELENA: volumen (% como pavucontrol) y silencio de cada bus.
# Se aplican con `e` en la consola o con `elena escena <nombre>`, y se guardan desde ahí.
# Solo tocan los buses (musica, juegos, sistema, chat, audifonos), no a dónde va cada app.
# Ejemplo:
#   [escenas.juego]
#   volumen = { musica = 40, juegos = 100, sistema = 50, chat = 80 }
#   silencio = { musica = false }

"""


@dataclass
class Escena:
    nombre: str
    volumen: dict[str, float] = field(default_factory=dict)
    silencio: dict[str, bool] = field(default_factory=dict)

    @property
    def resumen(self) -> str:
        partes = []
        for clave, b in CLAVES.items():
            if clave in self.volumen or clave in self.silencio:
                texto = f"{self.volumen[clave]:.0f} %" if clave in self.volumen else "="
                partes.append(f"{b.etiqueta} {texto}" + (" (silencio)" if self.silencio.get(clave) else ""))
        return " · ".join(partes)


def cargar(ruta: Path | None = None) -> dict[str, Escena]:
    ruta = ruta or RUTA
    if not ruta.exists():
        return {}
    try:
        datos = tomllib.loads(ruta.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ErrorElena(f"{ruta}: no es TOML válido ({e}).") from e
    escenas = {}
    for nombre, e in (datos.get("escenas") or {}).items():
        volumen = {k: float(v) for k, v in (e.get("volumen") or {}).items()}
        silencio = {k: bool(v) for k, v in (e.get("silencio") or {}).items()}
        if desconocidos := (set(volumen) | set(silencio)) - set(CLAVES):
            raise ErrorElena(f"{ruta}: la escena «{nombre}» nombra buses que no existen: {', '.join(sorted(desconocidos))}.")
        escenas[nombre] = Escena(nombre, volumen, silencio)
    return escenas


def guardar(escenas: dict[str, Escena], ruta: Path | None = None) -> None:
    ruta = ruta or RUTA
    datos = {"escenas": {n: {"volumen": e.volumen, "silencio": e.silencio} for n, e in escenas.items()}}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(ENCABEZADO + (tomli_w.dumps(datos) if escenas else ""))


def capturar(grafo: Grafo, nombre: str) -> Escena:
    """La escena con el volumen y el silencio actuales de cada bus."""
    escena = Escena(nombre)
    for clave, b in CLAVES.items():
        if (nodo := grafo.nodo(b.nodo)) and nodo.volumen_pct is not None:
            escena.volumen[clave] = round(nodo.volumen_pct)
            escena.silencio[clave] = nodo.silencio
    if not escena.volumen:
        raise ErrorElena("No hay buses de ELENA: no hay nada que guardar.")
    return escena


def comandos(grafo: Grafo, escena: Escena, topados: list[str] | None = None) -> list[list[str]]:
    """Los pactl de la escena. El volumen de Música y Juegos no pasa del tope; lo que se acotó se
    agrega a `topados`."""
    from elena import tope
    cmds = []
    for clave, pct in escena.volumen.items():
        if (nodo := grafo.nodo(CLAVES[clave].nodo)) and nodo.volumenes:
            a = tope.acotar(grafo, nodo, min(max(pct, 0.0), 150.0))
            if a.aviso and topados is not None:
                topados.append(a.aviso)
            cmds.append(cmd_volumen(nodo, escalar(nodo.volumenes, a.pct)))
    for clave, mudo in escena.silencio.items():
        if nodo := grafo.nodo(CLAVES[clave].nodo):
            cmds.append(cmd_silencio(nodo, mudo))
    return cmds
