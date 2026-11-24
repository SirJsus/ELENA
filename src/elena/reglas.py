"""Reglas fijas por app: «esta app siempre a este bus» (local/reglas.toml).

Las aplica el vigilante (`elena vigilar`, servicio `elena-vigilante`) a cada app nueva:
WirePlumber recuerda lo que se mueve a mano, pero no con las apps que eligen el sink por nombre
(FMOD Ex, como Toy Shire), y en WirePlumber 0.5.18 `stream.rules` no rutea.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from elena.modelo import ErrorElena, Nodo, Tipo
from elena.rutas import LOCAL

RUTA = LOCAL / "reglas.toml"

ENCABEZADO = """\
# Reglas fijas de ELENA: cada app nueva va al bus de la primera regla que coincide.
# Las aplica el vigilante (`elena vigilar`, servicio elena-vigilante) y se editan con `r` en la
# consola, con `elena regla <app> <bus>` o a mano aquí.
#   bus      musica, juegos, sistema, chat, o el node.name de una salida.
#            visual: para apps que GRABAN el sonido del sistema (visualizadores); las manda
#            a la mezcla elena_visual (30-elena-visual.conf). Las demás son para apps que suenan.
#   app      application.name      ┐ al menos uno; aceptan comodines (* y ?) y no
#   proceso  argv[0] (Wine: X.exe) │ distinguen mayúsculas. Si hay varios, deben
#   media    media.name            ┘ coincidir todos.
# Ejemplos:
#   [[regla]]
#   app = "Discord"
#   bus = "chat"
#
#   [[regla]]
#   proceso = "ToyShire*"
#   bus = "juegos"
#
#   [[regla]]
#   app = "Waywallen Daemon"
#   bus = "visual"

"""

VISUAL = "visual"  # el «bus» de las reglas de grabaciones (visualizadores → elena_visual)


@dataclass(frozen=True)
class Regla:
    bus: str
    app: str | None = None
    proceso: str | None = None
    media: str | None = None

    def coincide(self, nodo: Nodo) -> bool:
        criterios = [(self.app, nodo.app), (self.proceso, nodo.proceso), (self.media, nodo.media)]
        usados = [(patron, valor) for patron, valor in criterios if patron]
        return bool(usados) and all(
            valor is not None and fnmatch.fnmatchcase(valor.casefold(), patron.casefold()) for patron, valor in usados
        )

    @property
    def es_visual(self) -> bool:
        """Regla de una grabación (visualizador); las demás son de apps que suenan."""
        return self.bus.casefold() == VISUAL

    def aplica_a(self, nodo: Nodo) -> bool:
        """Una regla «visual» solo mueve grabaciones, y las demás solo apps que suenan: así una
        app que suena y graba (Waywallen) puede tener una regla de cada tipo."""
        return self.es_visual == (nodo.tipo == Tipo.GRABACION) and self.coincide(nodo)

    @property
    def descripcion(self) -> str:
        partes = [f"{k} = {v}" for k, v in (("app", self.app), ("proceso", self.proceso), ("media", self.media)) if v]
        return " y ".join(partes) + f" → {self.bus}"


def cargar(ruta: Path | None = None) -> list[Regla]:
    ruta = ruta or RUTA
    if not ruta.exists():
        return []
    try:
        datos = tomllib.loads(ruta.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ErrorElena(f"{ruta}: no es TOML válido ({e}).") from e
    reglas = []
    for i, r in enumerate(datos.get("regla", []), 1):
        if not isinstance(r, dict) or not r.get("bus") or not any(r.get(k) for k in ("app", "proceso", "media")):
            raise ErrorElena(f"{ruta}: la regla {i} necesita «bus» y al menos uno de «app», «proceso» o «media».")
        reglas.append(Regla(bus=str(r["bus"]), app=r.get("app"), proceso=r.get("proceso"), media=r.get("media")))
    return reglas


def guardar(reglas: list[Regla], ruta: Path | None = None) -> None:
    ruta = ruta or RUTA
    datos = {"regla": [{k: v for k, v in vars(r).items() if v} for r in reglas]}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(ENCABEZADO + (tomli_w.dumps(datos) if reglas else ""))


def destino(reglas: list[Regla], nodo: Nodo) -> Regla | None:
    """La primera regla que coincide con la app (de arriba abajo) y es de su tipo."""
    return next((r for r in reglas if r.aplica_a(nodo)), None)


def criterio(nodo: Nodo) -> dict[str, str]:
    """Cómo reconocer a esta app en una regla nueva: por application.name, o por el proceso si
    la app no lo trae (Wine lo deja en blanco y entonces `app` ya es el proceso)."""
    if nodo.app and nodo.app != nodo.proceso:
        return {"app": nodo.app}
    if nodo.proceso:
        return {"proceso": nodo.proceso}
    if nodo.media:
        return {"media": nodo.media}
    raise ErrorElena(f"{nodo.visible} no tiene nombre, proceso ni media.name con qué reconocerla.")


def fijar(reglas: list[Regla], nodo: Nodo, bus: str) -> list[Regla]:
    """Pone primero una regla «esta app → bus» y quita las que ya coincidían con ella (del mismo tipo)."""
    nueva = Regla(bus=bus, **criterio(nodo))
    if nueva.es_visual != (nodo.tipo == Tipo.GRABACION):
        raise ErrorElena(f"«{VISUAL}» es solo para apps que graban (visualizadores) y los buses, para apps "
                         f"que suenan; {nodo.visible} es una {'grabación' if nodo.tipo == Tipo.GRABACION else 'app'}.")
    return [nueva, *(r for r in reglas if not r.aplica_a(nodo))]


def quitar(reglas: list[Regla], nodo: Nodo) -> list[Regla]:
    return [r for r in reglas if not r.aplica_a(nodo)]
