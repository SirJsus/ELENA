"""«Ver este video»: abre en la PC un enlace de YouTube que llega del teléfono.

- **Solo videos de YouTube.** `url_de_youtube` saca el ID del video (y, si vienen, el inicio `t` y la
  lista `list`) y arma la URL de nuevo: nunca se pasa el texto que llegó. Con la cookie robada, lo
  peor que se puede hacer es abrir un video de YouTube.
- **El comando es configurable:** `ELENA_REMOTO_ABRIR` (por omisión `xdg-open {url}`). Se separa con
  `shlex` y `{url}` se reemplaza dentro de un argumento: no pasa por una shell. El contenedor del
  navegador, el espacio de trabajo y la ventana son cosa de cada quien: la variable puede apuntar a
  un script propio (guía del control remoto).
- El servicio lo lanza con `systemd-run --user` (ver `centro.Sistema.abrir`): así el navegador, si
  hay que abrirlo, no queda dentro del servicio elena-remoto.
"""

from __future__ import annotations

import os
import re
import shlex
from urllib.parse import parse_qs, urlencode, urlsplit

from elena.modelo import ErrorElena

COMANDO = "xdg-open {url}"
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
RUTAS_CON_ID = ("shorts", "live", "embed")   # youtube.com/<ruta>/<id>
ID = re.compile(r"[A-Za-z0-9_-]{11}")
LISTA = re.compile(r"[A-Za-z0-9_-]{2,64}")
INICIO = re.compile(r"(?:(\d{1,2})h)?(?:(\d{1,4})m)?(?:(\d{1,6})s?)?")
MAX_TEXTO = 2048


def _inicio(valor: str) -> int | None:
    """`90`, `90s`, `1m30s` o `1h2m3s` → segundos; None si no se entiende."""
    m = INICIO.fullmatch(valor)
    if not valor or not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s or None


def url_de_youtube(texto: str) -> str:
    """La URL limpia del video de YouTube de `texto`, o ErrorElena."""
    texto = str(texto).strip()
    if not texto or len(texto) > MAX_TEXTO:
        raise ErrorElena("Pega el enlace de un video de YouTube.")
    if "://" not in texto:
        texto = "https://" + texto        # «youtu.be/…» copiado sin el https://
    try:
        partes = urlsplit(texto)
        host, puerto = (partes.hostname or "").lower(), partes.port
    except ValueError:
        raise ErrorElena("Ese enlace no se entiende.") from None
    if (partes.scheme not in ("http", "https") or host not in HOSTS or puerto is not None
            or partes.username is not None or partes.password is not None):
        raise ErrorElena("Solo se pueden abrir enlaces de YouTube.")

    consulta = parse_qs(partes.query)
    segmentos = [s for s in partes.path.split("/") if s]
    if host == "youtu.be":
        video = segmentos[0] if len(segmentos) == 1 else ""
    elif segmentos == ["watch"]:
        video = (consulta.get("v") or [""])[0]
    elif len(segmentos) == 2 and segmentos[0] in RUTAS_CON_ID:
        video = segmentos[1]
    else:
        video = ""
    if not ID.fullmatch(video):
        raise ErrorElena("Ese enlace de YouTube no es de un video.")

    limpia = {"v": video}
    lista = (consulta.get("list") or [""])[0]
    if LISTA.fullmatch(lista):
        limpia["list"] = lista
    inicio = _inicio((consulta.get("t") or consulta.get("start") or [""])[0])
    if inicio:
        limpia["t"] = f"{inicio}s"
    base = "https://music.youtube.com/watch" if host == "music.youtube.com" else "https://www.youtube.com/watch"
    return f"{base}?{urlencode(limpia)}"


def comando(url: str, plantilla: str | None = None) -> list[str]:
    """El comando que abre `url`: `ELENA_REMOTO_ABRIR` con `{url}` reemplazado (o la URL al final)."""
    plantilla = plantilla if plantilla is not None else os.environ.get("ELENA_REMOTO_ABRIR") or COMANDO
    try:
        partes = shlex.split(plantilla)
    except ValueError as e:
        raise ErrorElena(f"ELENA_REMOTO_ABRIR no se entiende: {e}.") from None
    if not partes:
        raise ErrorElena("ELENA_REMOTO_ABRIR está vacío.")
    if not any("{url}" in p for p in partes):
        return [*partes, url]
    return [p.replace("{url}", url) for p in partes]
