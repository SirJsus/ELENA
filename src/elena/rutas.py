"""Dónde vive cada cosa: el repo (`ELENA`) y lo de tu equipo (`ELENA_LOCAL`, por omisión `local/`).

`local/` no va en el repositorio: trae tu equipo (`equipo.toml`), tu calibración, tus reglas y escenas,
y los `.conf` de PipeWire y WirePlumber generados desde `plantillas/`. Este módulo no importa nada de
ELENA, para que cualquiera lo pueda usar (también `modelo`, al cargarse).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[2])
LOCAL = Path(os.environ.get("ELENA_LOCAL") or RAIZ / "local")
EQUIPO = LOCAL / "equipo.toml"
CONF_PIPEWIRE = LOCAL / "pipewire" / "pipewire.conf.d"
CONF_WIREPLUMBER = LOCAL / "wireplumber" / "wireplumber.conf.d"
PLANTILLAS = RAIZ / "plantillas"
EJEMPLOS = RAIZ / "config"


def equipo_archivo(ruta: Path | None = None) -> dict[str, str]:
    """Las claves de `equipo.toml` tal cual ({} si no existe o está roto: lo dice `elena equipo`)."""
    try:
        datos = tomllib.loads((ruta or EQUIPO).read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v.strip() for k, v in datos.items() if isinstance(v, str)}


def del_equipo(clave: str) -> str:
    """Una variable del equipo: la del entorno (mise la carga de `equipo.toml`) o, sin mise, la del archivo."""
    return os.environ.get(clave) or equipo_archivo().get(clave, "")
