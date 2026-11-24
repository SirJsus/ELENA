"""Tu equipo: `local/equipo.toml`, cómo detectarlo y los `.conf` que salen de él.

PipeWire no lee variables de entorno, así que los `.conf` de ELENA son plantillas (`plantillas/`) con
marcadores (`@SINK@`…) que `generar` llena con los valores de `local/equipo.toml` y escribe en
`local/pipewire/pipewire.conf.d/` y `local/wireplumber/wireplumber.conf.d/`, a donde apuntan los
enlaces de `~/.config` (los pone `mise run aplicar`, que también corre `generar`).

- Solo la tarjeta 5.1 es obligatoria. Sin mic no se genera `20-elena-mic.conf` (no hay mic virtual);
  sin audífonos, Chat sale a las bocinas y no se genera `50-elena-audifonos.conf`.
- Los buses de bocinas van a `elena_salida` si la calibración está activa (existe
  `40-elena-calibracion.conf`) y si no, al físico. `calibracion activar|desactivar` pasan el destino.
- Todo se valida con `pw-config` antes de reemplazar nada.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from elena import rutas
from elena.modelo import ErrorElena

OBLIGATORIAS = ("ELENA_TARJETA", "ELENA_PERFIL", "ELENA_SINK_FISICO")
OPCIONALES = ("ELENA_MIC_FISICO", "ELENA_AUDIFONOS")
SALIDA_51 = "output:analog-surround-51"
MIC_CONF = "20-elena-mic.conf"
AUDIFONOS_CONF = "50-elena-audifonos.conf"


@dataclass(frozen=True)
class Equipo:
    tarjeta: str
    perfil: str
    sink: str
    mic: str = ""
    audifonos: str = ""

    @property
    def chat(self) -> str:
        """A donde va Chat: los audífonos o, sin ellos, las bocinas (frontales)."""
        return self.audifonos or self.sink


def desde_datos(datos: dict, origen: str = "equipo.toml") -> Equipo:
    if raros := [k for k, v in datos.items() if not isinstance(v, str)]:
        raise ErrorElena(f"{origen}: todo va en el primer nivel y entre comillas (mise lo lee plano): {', '.join(raros)}.")
    if desconocidas := sorted(set(datos) - set(OBLIGATORIAS) - set(OPCIONALES)):
        raise ErrorElena(f"{origen}: claves desconocidas: {', '.join(desconocidas)}.")
    if faltan := [k for k in OBLIGATORIAS if not datos.get(k, "").strip()]:
        raise ErrorElena(f"{origen}: falta {', '.join(faltan)} (mise run elena -- equipo detectar te lo propone).")
    valores = [datos.get(k, "").strip() for k in (*OBLIGATORIAS, *OPCIONALES)]
    if malos := [v for v in valores if re.search(r'["\\\s]', v)]:
        raise ErrorElena(f"{origen}: un nombre de PipeWire no lleva comillas, \\ ni espacios: {malos[0]!r}.")
    return Equipo(*valores)


def leer(ruta: Path | None = None) -> Equipo:
    ruta = ruta or rutas.EQUIPO
    if not ruta.exists():
        raise ErrorElena(f"No existe {ruta}: corre mise run iniciar.")
    try:
        datos = tomllib.loads(ruta.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ErrorElena(f"{ruta}: no es TOML válido ({e}).") from e
    return desde_datos(datos, str(ruta))


# ---------- Generar los .conf desde plantillas/ ----------

@dataclass(frozen=True)
class Archivo:
    plantilla: Path
    destino: Path


def archivos(eq: Equipo, plantillas: Path | None = None) -> list[Archivo]:
    """Los .conf que lleva este equipo (los opcionales, solo si hay mic o audífonos)."""
    plantillas = plantillas or rutas.PLANTILLAS
    nombres = ["10-elena-buses.conf", "30-elena-visual.conf"]
    if eq.mic:
        nombres.append(MIC_CONF)
    if eq.audifonos:
        nombres.append(AUDIFONOS_CONF)
    return ([Archivo(plantillas / "pipewire" / n, rutas.CONF_PIPEWIRE / n) for n in sorted(nombres)]
            + [Archivo(plantillas / "wireplumber" / "10-elena-perfil.conf",
                       rutas.CONF_WIREPLUMBER / "10-elena-perfil.conf")])


def prioridades(perfil: str) -> str:
    """Los perfiles que WirePlumber prefiere: el del equipo y, por si falta su entrada, solo la salida."""
    lista = list(dict.fromkeys([perfil, perfil.split("+")[0]]))
    return "\n          ".join(f'"{p}"' for p in lista)


def valores(eq: Equipo, destino_bocinas: str) -> dict[str, str]:
    return {
        "DESTINO_BOCINAS": destino_bocinas,
        "CHAT": eq.chat,
        "MIC": eq.mic,
        "AUDIFONOS": eq.audifonos,
        "TARJETA": eq.tarjeta,
        "PRIORIDADES": prioridades(eq.perfil),
    }


MARCADOR = re.compile(r"@([A-Z_]+)@")


def rellenar(texto: str, vals: dict[str, str], nombre: str = "plantilla") -> str:
    def poner(m: re.Match) -> str:
        if m.group(1) not in vals:
            raise ErrorElena(f"{nombre}: marcador desconocido @{m.group(1)}@.")
        if not vals[m.group(1)]:
            raise ErrorElena(f"{nombre}: @{m.group(1)}@ quedaría vacío.")
        return vals[m.group(1)]
    return MARCADOR.sub(poner, texto)


def validar(ruta: Path) -> None:
    """Que PipeWire lea el archivo, sin reiniciar nada (sirve también para los de WirePlumber)."""
    r = subprocess.run(["pw-config", "-n", str(ruta.resolve()), "-r", "list"], capture_output=True, text=True)
    if r.returncode != 0:
        detalle = " ".join(l.split("]", 1)[-1].strip() for l in r.stderr.strip().splitlines()[:3])
        raise ErrorElena(f"PipeWire no lee {ruta.name}: {detalle or r.returncode}")


def _relativa(ruta: Path) -> str:
    try:
        return str(ruta.relative_to(rutas.RAIZ))
    except ValueError:
        return str(ruta)


def generar(eq: Equipo | None = None, destino_bocinas: str | None = None) -> list[str]:
    """Escribe los .conf de local/ (solo los que cambian) y borra los opcionales que ya no van.

    `destino_bocinas` lo pasan `calibracion activar|desactivar`; sin él, sale de si la calibración está
    activa, y entonces también se regenera su .conf (por si cambió el sink)."""
    from elena import calibracion as cb
    eq = eq or leer()
    destino = destino_bocinas or (cb.NODO if cb.activa() else eq.sink)
    vals = valores(eq, destino)
    temporal = Path(os.environ.get("TMPDIR") or rutas.RAIZ / ".cache" / "tmp") / "elena-equipo"
    shutil.rmtree(temporal, ignore_errors=True)
    temporal.mkdir(parents=True)
    try:
        listos = []
        for a in archivos(eq):
            texto = rellenar(a.plantilla.read_text(), vals, a.plantilla.name)
            prueba = temporal / f"{a.destino.parent.parent.name}-{a.destino.name}"
            prueba.write_text(texto)
            validar(prueba)
            listos.append((a, texto))
    finally:
        shutil.rmtree(temporal, ignore_errors=True)
    hechos = []
    for a, texto in listos:
        if not a.destino.exists() or a.destino.read_text() != texto:
            a.destino.parent.mkdir(parents=True, exist_ok=True)
            a.destino.write_text(texto)
            hechos.append(f"Escrito {_relativa(a.destino)}")
    van = {a.destino.name for a in archivos(eq)}
    for nombre, falta in ((MIC_CONF, "ELENA_MIC_FISICO"), (AUDIFONOS_CONF, "ELENA_AUDIFONOS")):
        if nombre not in van and (sobra := rutas.CONF_PIPEWIRE / nombre).exists():
            sobra.unlink()
            hechos.append(f"Borrado {_relativa(sobra)} ({falta} vacío)")
    if destino_bocinas is None and cb.activa():
        antes = cb.CONF.read_text()
        cb.generar(cb.TOML, cb.CONF, destino=eq.sink)
        if cb.CONF.read_text() != antes:
            hechos.append(f"Escrito {_relativa(cb.CONF)}")
    return hechos


# ---------- Detectar el equipo ----------

@dataclass(frozen=True)
class Opcion:
    nombre: str
    descripcion: str


@dataclass
class Deteccion:
    tarjetas: list[tuple[str, str, str]] = field(default_factory=list)   # (tarjeta, perfil, descripción)
    mics: list[Opcion] = field(default_factory=list)
    salidas: list[Opcion] = field(default_factory=list)
    mic: str = ""

    @property
    def equipo(self) -> Equipo | None:
        if not self.tarjetas:
            return None
        tarjeta, perfil, _ = self.tarjetas[0]
        return Equipo(tarjeta, perfil, sink_51(tarjeta), self.mic, "")


def sink_51(tarjeta: str) -> str:
    """El sink 5.1 de una tarjeta ALSA (existe aunque hoy esté en otro perfil: así lo nombra PipeWire)."""
    return f"alsa_output.{tarjeta.removeprefix('alsa_card.')}.analog-surround-51"


def _pactl(tipo: str) -> list[dict]:
    r = subprocess.run(["pactl", "-f", "json", "list", tipo], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorElena(f"pactl list {tipo} falló: {r.stderr.strip() or r.returncode}")
    return json.loads(r.stdout)


def detectar(tarjetas: list[dict] | None = None, sinks: list[dict] | None = None,
             fuentes: list[dict] | None = None) -> Deteccion:
    """Tarjetas con salida 5.1 analógica, mics y salidas que podrían ser audífonos (de `pactl -f json`)."""
    tarjetas = _pactl("cards") if tarjetas is None else tarjetas
    sinks = _pactl("sinks") if sinks is None else sinks
    fuentes = _pactl("sources") if fuentes is None else fuentes
    d = Deteccion()
    for t in tarjetas:
        perfiles = [p for p, info in t.get("profiles", {}).items()
                    if p.split("+")[0] == SALIDA_51 and info.get("available", True)]
        if perfiles:
            # Con la entrada de la tarjeta si existe (así su mic sigue disponible)
            perfil = max(perfiles, key=lambda p: ("+input:" in p, t["profiles"][p].get("priority", 0)))
            d.tarjetas.append((t["name"], perfil, t.get("properties", {}).get("device.description", "")))
    de_tarjeta = tuple(f"alsa_output.{n.removeprefix('alsa_card.')}." for n, _, _ in d.tarjetas)

    def fisico(o: dict) -> bool:
        return o.get("properties", {}).get("device.class") == "sound"

    d.mics = [Opcion(f["name"], f.get("description", "")) for f in fuentes if fisico(f)]
    d.salidas = [Opcion(s["name"], s.get("description", "")) for s in sinks
                 if fisico(s) and not s["name"].startswith(de_tarjeta)]
    usb = [m for m in d.mics if ".usb-" in m.nombre]
    if len(usb) == 1:
        d.mic = usb[0].nombre
    elif len(d.mics) == 1:
        d.mic = d.mics[0].nombre
    return d


def texto_toml(d: Deteccion) -> str:
    """El equipo.toml propuesto, con las alternativas encontradas en comentarios."""
    eq = d.equipo
    if eq is None:
        raise ErrorElena("No encontré ninguna tarjeta con perfil analog-surround-51 (5.1 analógico). ELENA "
                         "necesita una; mira los perfiles con: pactl list cards")

    def otras(opciones: list[Opcion]) -> list[str]:
        return [f'#   "{o.nombre}"  {o.descripcion}' for o in opciones] or ["#   (ninguno)"]

    lineas = [
        "# ELENA — tu equipo. Lo leen mise (como variables de entorno), los scripts y la consola.",
        "# Lo propuso `elena equipo detectar`; después de cambiarlo: mise run aplicar.",
        "# Formato plano (sin [tablas]): mise lo carga con `_.file`.",
        "",
        "# Tarjeta con salida 5.1 analógica y su perfil (pactl list cards)",
    ]
    if len(d.tarjetas) > 1:
        lineas += ["# Otras tarjetas 5.1:"] + [f'#   "{t}"  perfil "{p}"  {desc}' for t, p, desc in d.tarjetas[1:]]
    lineas += [
        f'ELENA_TARJETA = "{eq.tarjeta}"',
        f'ELENA_PERFIL = "{eq.perfil}"',
        "# Su sink 5.1 (aparece con ese perfil)",
        f'ELENA_SINK_FISICO = "{eq.sink}"',
        "",
        "# Mic físico que alimenta el mic virtual elena_mic. Vacío: sin mic virtual. Encontrados:",
        *otras(d.mics),
        f'ELENA_MIC_FISICO = "{eq.mic}"',
        "",
        "# Audífonos: a donde va Chat y «solo audífonos». Vacío: Chat va a las bocinas y no hay «solo",
        "# audífonos». Si son inalámbricos, préndelos antes de detectar. Otras salidas encontradas:",
        *otras(d.salidas),
        'ELENA_AUDIFONOS = ""',
        "",
    ]
    return "\n".join(lineas)


# ---------- Iniciar local/ ----------

def iniciar() -> list[str]:
    """Crea local/: los ejemplos de config/ que falten y, si no hay equipo.toml, el detectado."""
    hechos = []
    rutas.LOCAL.mkdir(parents=True, exist_ok=True)
    for ejemplo in sorted(rutas.EJEMPLOS.glob("*.ejemplo.toml")):
        nombre = ejemplo.name.replace(".ejemplo", "")
        if nombre == "equipo.toml":
            continue
        if not (destino := rutas.LOCAL / nombre).exists():
            shutil.copyfile(ejemplo, destino)
            hechos.append(f"Creado {_relativa(destino)} (desde {_relativa(ejemplo)})")
    if rutas.EQUIPO.exists():
        hechos.append(f"Ya existe {_relativa(rutas.EQUIPO)}: no lo toqué.")
    else:
        rutas.EQUIPO.write_text(texto_toml(detectar()))
        hechos.append(f"Creado {_relativa(rutas.EQUIPO)} con lo detectado: revísalo (mic y audífonos).")
    return hechos


def resumen(eq: Equipo) -> list[str]:
    return [
        f"Tarjeta:   {eq.tarjeta} ({eq.perfil})",
        f"Bocinas:   {eq.sink}",
        f"Mic:       {eq.mic or '(ninguno: sin mic virtual)'}",
        f"Audífonos: {eq.audifonos or '(ninguno: Chat va a las bocinas, sin «solo audífonos»)'}",
    ]
