"""Modelo del grafo de audio de ELENA, construido a partir de los objetos de `pw-dump`.

`Grafo` guarda los objetos crudos (y aplica los lotes de `monitor.seguir`); a partir de ellos
calcula los nodos ya interpretados (`Nodo`), los links entre nodos y la vista de ELENA: buses,
apps, otras salidas, grabaciones y el estado del mic virtual.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from pathlib import Path

from elena.rutas import del_equipo

# ---------- Configuración (de local/equipo.toml, que mise carga como variables) ----------

TARJETA = del_equipo("ELENA_TARJETA")
PERFIL_51 = del_equipo("ELENA_PERFIL")
SINK_FISICO = del_equipo("ELENA_SINK_FISICO")


@dataclass(frozen=True)
class DefBus:
    clave: str      # para la CLI: elena mover firefox chat
    etiqueta: str   # para mostrar
    nodo: str       # node.name del sink virtual (10-elena-buses.conf)

    @property
    def salida(self) -> str:
        return f"{self.nodo}.salida"


BUSES = (
    DefBus("musica", "Música", "elena_musica"),
    DefBus("juegos", "Juegos", "elena_juegos"),
    DefBus("sistema", "Sistema", "elena_sistema"),
    DefBus("chat", "Chat", "elena_chat"),
    DefBus("audifonos", "Audífonos", "elena_audifonos"),
)
MICS = ("elena_mic", "elena_mic_web")  # fuentes virtuales que ven las apps (20-elena-mic.conf)

# Calibración de las bocinas (40-elena-calibracion.conf): el sink al que van Música, Juegos y
# Sistema cuando está activa (lo crea un filter-chain que sale al físico)
CALIBRACION = "elena_salida"
BUSES_BOCINAS = ("musica", "juegos", "sistema")

# «Solo audífonos» (50-elena-audifonos.conf, `audifonos.py`): elena_audifonos convierte a estéreo
# (binaural o directo) y sale a donde va Chat; con el modo prendido, los buses de bocinas van ahí
AUDIFONOS = "elena_audifonos"
CHAT = "elena_chat"

# Mezcla para visualizadores (30-elena-visual.conf): los buses que pueden entrar y su alimentador
VISUAL = "elena_visual"
VISUAL_BUSES = ("musica", "juegos", "sistema")


def alimentador_visual(clave: str) -> str:
    """node.name del loopback que lleva un bus a elena_visual (el que se silencia)."""
    return f"{VISUAL}_{clave}"


# ---------- Volumen: lineal (PipeWire), % cúbico (pavucontrol, pactl, wpctl) y dB ----------

def lineal_a_pct(v: float) -> float:
    return max(v, 0.0) ** (1 / 3) * 100


def pct_a_lineal(p: float) -> float:
    return (max(p, 0.0) / 100) ** 3


def lineal_a_db(v: float) -> float:
    return 20 * math.log10(v) if v > 0 else -math.inf


def escalar(volumenes: tuple[float, ...], pct: float) -> tuple[float, ...]:
    """Lleva el canal más alto a `pct` (% cúbico) y conserva las diferencias en dB entre canales.

    `wpctl set-volume` y `pactl set-sink-volume <n>%` igualan los canales;
    la consola siempre manda un valor por canal calculado con esta función.
    """
    objetivo = pct_a_lineal(pct)
    maximo = max(volumenes, default=0.0)
    if maximo <= 0:
        return tuple(objetivo for _ in volumenes)
    return tuple(v * objetivo / maximo for v in volumenes)


# ---------- Nodos ----------

class Tipo(StrEnum):
    BUS = "bus"              # sinks virtuales de ELENA (BUSES)
    SALIDA = "salida"        # sinks físicos y cualquier otro Audio/Sink
    FUENTE = "fuente"        # mics de ELENA (MICS) y fuentes físicas
    APP = "app"              # Stream/Output/Audio de una app
    GRABACION = "grabacion"  # Stream/Input/Audio de una app
    INTERNO = "interno"      # el resto de los nodos elena_* (loopbacks, mezclas, medidores)
    OTRO = "otro"            # drivers, video, MIDI


@dataclass(frozen=True)
class Nodo:
    id: int
    serial: int | None
    nombre: str              # node.name
    descripcion: str         # node.description o media.name
    clase: str               # media.class
    tipo: Tipo
    app: str | None          # application.name (o el proceso si viene en blanco, como en Wine)
    proceso: str | None      # argv[0] sin ruta
    pid: int | None
    api: str | None          # pulse (pipewire-pulse) o pw (nativo); solo apps y grabaciones
    media: str | None        # media.name
    canales: int | None
    posiciones: tuple[str, ...]
    formato: str | None
    tasa: int | None
    estado: str | None       # running, idle, suspended, ...
    volumenes: tuple[float, ...]  # channelVolumes, lineal
    silencio: bool
    objetivo: str | None     # target.object de sus props (los loopbacks lo traen de la config)

    @property
    def visible(self) -> str:
        """El nombre que se le muestra al usuario."""
        if self.tipo in (Tipo.APP, Tipo.GRABACION):
            return self.app or self.media or self.nombre or f"#{self.id}"
        return self.descripcion or self.nombre or f"#{self.id}"

    @property
    def volumen_pct(self) -> float | None:
        """Volumen general como lo muestra pavucontrol: el del canal más alto."""
        return lineal_a_pct(max(self.volumenes)) if self.volumenes else None

    @property
    def balanceado(self) -> bool:
        """True si todos los canales tienen el mismo volumen."""
        return len(set(round(v, 6) for v in self.volumenes)) <= 1


def _lleno(valor: object) -> str | None:
    """Wine (winepulse) deja application.name y node.name en blanco (" ")."""
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


def _default(valor: object) -> object:
    """En EnumFormat, los valores pueden venir como {"default": x, "min": ..., "max": ...}."""
    return valor.get("default") if isinstance(valor, dict) else valor


def leer_proceso(pid: int) -> str | None:
    """argv[0] sin ruta (/ o \\: Wine da rutas de Windows); /proc/<pid>/comm se corta a 15 letras."""
    try:
        argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
        nombre = argv0.replace("\\", "/").rsplit("/", 1)[-1]
        return nombre or Path(f"/proc/{pid}/comm").read_text().strip() or None
    except OSError:
        return None


def _tipo(nombre: str, clase: str) -> Tipo:
    if nombre.startswith("elena_"):
        if clase == "Audio/Sink" and nombre in {b.nodo for b in BUSES}:
            return Tipo.BUS
        if clase == "Audio/Source" and nombre in MICS:
            return Tipo.FUENTE
        return Tipo.INTERNO
    if clase.startswith("Audio/Sink"):
        return Tipo.SALIDA
    if clase.startswith("Audio/Source"):
        return Tipo.FUENTE
    if clase == "Stream/Output/Audio":
        return Tipo.APP
    if clase == "Stream/Input/Audio":
        return Tipo.GRABACION
    return Tipo.OTRO


def _volumenes(vol: dict, canales: int | None, posiciones: list) -> tuple[float, ...]:
    """channelVolumes, siempre uno por canal del nodo.

    WirePlumber restaura el volumen guardado por nombre de app aunque se haya guardado con otros
    canales: la VM (`elena-windows`, 5.1) abría con un volumen estéreo `[FL, FR]` de cuando su
    stream fue de 2 canales. pactl lo muestra con los demás canales en 0 % (la señal sí pasa) y
    solo acepta un valor por canal del stream, así que mover el volumen fallaba. Se acomoda por
    posición; los canales que falten toman el valor más alto.
    """
    valores = [float(v) for v in vol.get("channelVolumes") or []]
    if not valores or not canales or len(valores) == canales:
        return tuple(valores)
    mapa = vol.get("channelMap") or []
    por_posicion = dict(zip(mapa, valores)) if len(mapa) == len(valores) else {}
    relleno = max(valores)
    if por_posicion and len(posiciones) == canales:
        return tuple(por_posicion.get(p, relleno) for p in posiciones)
    return tuple(valores[i] if i < len(valores) else relleno for i in range(canales))


def _nodo(objeto: dict, pid_de_cliente: dict[int, int], proceso_de: Callable[[int], str | None]) -> Nodo:
    info = objeto.get("info") or {}
    props = info.get("props") or {}
    params = info.get("params") or {}
    nombre = _lleno(props.get("node.name")) or ""
    clase = props.get("media.class") or ""
    tipo = _tipo(nombre, clase)

    pid = props.get("application.process.id") or pid_de_cliente.get(props.get("client.id"))
    pid = int(pid) if pid is not None else None
    proceso = proceso_de(pid) if pid is not None and tipo in (Tipo.APP, Tipo.GRABACION) else None
    media = _lleno(props.get("media.name"))

    # Format está vacío mientras el nodo está suspendido; entonces se usa EnumFormat
    formato = (params.get("Format") or params.get("EnumFormat") or [{}])[0]
    canales = _default(formato.get("channels")) or props.get("audio.channels")
    posiciones = _default(formato.get("position")) or []

    vol = next((p for p in params.get("Props") or [] if "channelVolumes" in p), {})
    canales = int(canales) if canales else None
    api = None
    if tipo in (Tipo.APP, Tipo.GRABACION):
        api = "pulse" if props.get("client.api") == "pipewire-pulse" else "pw"

    return Nodo(
        id=objeto["id"],
        serial=props.get("object.serial"),
        nombre=nombre,
        descripcion=_lleno(props.get("node.description")) or media or "",
        clase=clase,
        tipo=tipo,
        app=_lleno(props.get("application.name")) or proceso,
        proceso=proceso,
        pid=pid,
        api=api,
        media=media,
        canales=canales,
        posiciones=tuple(posiciones) if isinstance(posiciones, list) else (),
        formato=_default(formato.get("format")),
        tasa=_default(formato.get("rate")),
        estado=info.get("state"),
        volumenes=_volumenes(vol, canales, posiciones if isinstance(posiciones, list) else []),
        silencio=bool(vol.get("mute", False)),
        objetivo=_lleno(props.get("target.object")),
    )


# ---------- Vista de ELENA ----------

@dataclass
class Bus:
    definicion: DefBus
    nodo: Nodo | None                 # None si el bus no existe (ELENA sin aplicar)
    destinos: list[Nodo]              # a dónde sale (links de <bus>.salida)
    esperando: str | None             # target.object de la salida, si no tiene links
    apps: list[Nodo] = field(default_factory=list)


@dataclass
class EstadoMic:
    voz: str      # on | off
    ruido: str    # on | off | inválido (los dos caminos activos o los dos silenciados)
    puente: str   # on | off | mixto


@dataclass
class EstadoAudifonos:
    enviados: list[str]             # claves de los buses de bocinas que salen a elena_audifonos
    destinos: list[Nodo]            # a dónde sale elena_audifonos
    chat: list[Nodo]                # a dónde sale Chat
    esperando: str | None           # el target.object de elena_audifonos.salida, si no tiene links
    camino: str | None              # binaural, directo o None (no se sabe: mezclado o sin controles)
    salidas: dict[str, Nodo]        # clave del bus de bocinas → su <bus>.salida


@dataclass
class EstadoVisual:
    buses: dict[str, bool | None]   # clave del bus → entra (True), no (False), sin alimentador (None)
    oyentes: list[Nodo]             # las grabaciones conectadas al monitor de elena_visual


class Grafo:
    """Objetos crudos de `pw-dump` y la vista calculada a partir de ellos.

    `proceso_de` resuelve el nombre de un proceso por su PID (se inyecta en las pruebas).
    """

    def __init__(self, proceso_de: Callable[[int], str | None] = leer_proceso) -> None:
        self._objetos: dict[int, dict] = {}
        self._proceso_de = proceso_de

    @classmethod
    def desde_dump(cls, objetos: Iterable[dict], **kw) -> Grafo:
        grafo = cls(**kw)
        grafo.aplicar(objetos, completo=True)
        return grafo

    # ----- cambios -----

    def aplicar(self, objetos: Iterable[dict], completo: bool = False) -> None:
        """Aplica un lote. `completo` = estado inicial: los metadatos traen todas sus claves.

        Fuera del estado inicial, los metadatos son un delta (ver monitor.py) y se fusionan por
        (sujeto, clave); `monitor.seguir` marca con `elena.completo` los que releyó enteros.
        """
        if completo:
            self._objetos.clear()
        for objeto in objetos:
            ident = objeto["id"]
            if "type" not in objeto and objeto.get("info", ...) is None:
                self._objetos.pop(ident, None)
            elif (objeto.get("type") == "PipeWire:Interface:Metadata" and not completo
                  and not objeto.get("elena.completo") and ident in self._objetos):
                previo = {(m["subject"], m["key"]): m for m in self._objetos[ident].get("metadata", [])}
                for m in objeto.get("metadata", []):
                    if m.get("value") is None:
                        previo.pop((m["subject"], m["key"]), None)
                    else:
                        previo[(m["subject"], m["key"])] = m
                self._objetos[ident] = objeto | {"metadata": list(previo.values())}
            else:
                self._objetos[ident] = objeto
        self.__dict__.pop("nodos", None)
        self.__dict__.pop("links", None)

    # ----- consultas básicas -----

    def _de_tipo(self, tipo: str) -> list[dict]:
        return [o for o in self._objetos.values() if o.get("type") == f"PipeWire:Interface:{tipo}"]

    @cached_property
    def nodos(self) -> dict[int, Nodo]:
        pid_de_cliente = {}
        for c in self._de_tipo("Client"):
            pid = ((c.get("info") or {}).get("props") or {}).get("application.process.id")
            if pid is not None:
                pid_de_cliente[c["id"]] = int(pid)
        return {o["id"]: _nodo(o, pid_de_cliente, self._proceso_de) for o in self._de_tipo("Node")}

    @cached_property
    def links(self) -> set[tuple[int, int]]:
        """(nodo de salida, nodo de entrada), uno por par aunque haya un link por canal."""
        pares = set()
        for link in self._de_tipo("Link"):
            info = link.get("info") or {}
            if "output-node-id" in info and "input-node-id" in info:
                pares.add((info["output-node-id"], info["input-node-id"]))
        return pares

    def crudo(self, ident: int) -> dict | None:
        """El objeto tal como lo dio pw-dump (para el detalle de un nodo)."""
        return self._objetos.get(ident)

    def nodo(self, nombre: str) -> Nodo | None:
        return next((n for n in self.nodos.values() if n.nombre == nombre), None)

    def destinos(self, nodo: Nodo) -> list[Nodo]:
        return [self.nodos[e] for s, e in sorted(self.links) if s == nodo.id and e in self.nodos]

    def sinks_de(self, nodo: Nodo) -> list[Nodo]:
        """Los sinks a los que va un nodo, sin las grabaciones (por ejemplo, los medidores de la consola)."""
        return [d for d in self.destinos(nodo) if d.clase.startswith("Audio/Sink")]

    def origenes(self, nodo: Nodo) -> list[Nodo]:
        return [self.nodos[s] for s, e in sorted(self.links) if e == nodo.id and s in self.nodos]

    def metadato(self, nombre: str, clave: str, sujeto: int = 0) -> object:
        for meta in self._de_tipo("Metadata"):
            if (meta.get("props") or {}).get("metadata.name") == nombre:
                for m in meta.get("metadata", []):
                    if m.get("subject") == sujeto and m.get("key") == clave:
                        return m.get("value")
        return None

    def _por_defecto(self, clave: str) -> str | None:
        valor = self.metadato("default", clave)
        return valor.get("name") if isinstance(valor, dict) else None

    @property
    def sink_por_defecto(self) -> str | None:
        return self._por_defecto("default.audio.sink")

    @property
    def fuente_por_defecto(self) -> str | None:
        return self._por_defecto("default.audio.source")

    def perfil(self, tarjeta: str = TARJETA) -> str | None:
        for disp in self._de_tipo("Device"):
            info = disp.get("info") or {}
            if (info.get("props") or {}).get("device.name") == tarjeta:
                perfiles = (info.get("params") or {}).get("Profile") or []
                return perfiles[0].get("name") if perfiles else None
        return None

    # ----- vista de ELENA -----

    def buses(self) -> list[Bus]:
        resultado = []
        for definicion in BUSES:
            nodo = self.nodo(definicion.nodo)
            salida = self.nodo(definicion.salida)
            destinos = self.destinos(salida) if salida else []
            bus = Bus(
                definicion=definicion,
                nodo=nodo,
                destinos=destinos,
                esperando=salida.objetivo if salida and not destinos else None,
            )
            if nodo:
                bus.apps = [n for n in self.origenes(nodo) if n.tipo == Tipo.APP]
            resultado.append(bus)
        return resultado

    def de_tipo(self, *tipos: Tipo) -> list[Nodo]:
        return [n for n in self.nodos.values() if n.tipo in tipos]

    def otras_salidas(self) -> list[tuple[Nodo, list[Nodo]]]:
        """Sinks que no son buses y reciben apps directamente (audífonos, HDMI, la tarjeta 5.1)."""
        return [(s, apps) for s in self.de_tipo(Tipo.SALIDA)
                if (apps := [n for n in self.origenes(s) if n.tipo == Tipo.APP])]

    def apps_sueltas(self) -> list[Nodo]:
        """Apps que no están conectadas a nada (pausadas, corked o esperando destino)."""
        return [a for a in self.de_tipo(Tipo.APP) if not self.sinks_de(a)]

    def mic(self) -> EstadoMic | None:
        """Los interruptores del mic virtual, con la misma lógica que scripts/mic.sh."""
        def mudo(nombre: str) -> bool | None:
            n = self.nodo(nombre)
            return n.silencio if n else None

        voz, directo, limpio = mudo("elena_voz"), mudo("elena_directo"), mudo("elena_limpio_a_voz")
        navegador, chat = mudo("elena_puente_navegador"), mudo("elena_puente_chat")
        if None in (voz, directo, limpio, navegador, chat):
            return None
        ruido = {(True, False): "on", (False, True): "off"}.get((directo, limpio), "inválido")
        puente = {(True, True): "off", (False, False): "on"}.get((navegador, chat), "mixto")
        return EstadoMic(voz="off" if voz else "on", ruido=ruido, puente=puente)

    def params(self, nodo: Nodo) -> dict[str, float]:
        """Los controles de un filter-chain (Props → params), como los cambia `pw-cli set-param`."""
        props = ((self._objetos.get(nodo.id) or {}).get("info") or {}).get("params", {}).get("Props") or []
        valores: dict[str, float] = {}
        for p in props:
            lista = p.get("params") if isinstance(p, dict) else None
            if isinstance(lista, list):
                for clave, valor in zip(lista[::2], lista[1::2]):
                    if isinstance(clave, str) and isinstance(valor, int | float) and not isinstance(valor, bool):
                        valores[clave] = float(valor)
        return valores

    def audifonos(self) -> EstadoAudifonos | None:
        """«Solo audífonos»: qué buses de bocinas salen a elena_audifonos, a dónde sale ella y Chat."""
        if (nodo := self.nodo(AUDIFONOS)) is None:
            return None
        salidas = {c: s for c in BUSES_BOCINAS
                   if (s := self.nodo(next(b.salida for b in BUSES if b.clave == c))) is not None}
        enviados = [c for c, s in salidas.items() if nodo in self.sinks_de(s)]
        salida = self.nodo(f"{AUDIFONOS}.salida")
        destinos = self.sinks_de(salida) if salida else []
        chat_salida = self.nodo(f"{CHAT}.salida")
        p = self.params(nodo)
        binaural = p.get("salida_l:Gain 1", 0.0) > 0 and p.get("salida_r:Gain 1", 0.0) > 0
        directo = p.get("salida_l:Gain 2", 0.0) > 0 and p.get("salida_r:Gain 2", 0.0) > 0
        camino = "binaural" if binaural and not directo else ("directo" if directo and not binaural else None)
        return EstadoAudifonos(
            enviados=enviados,
            destinos=destinos,
            chat=self.sinks_de(chat_salida) if chat_salida else [],
            esperando=salida.objetivo if salida and not destinos else None,
            camino=camino,
            salidas=salidas,
        )

    def visual(self) -> EstadoVisual | None:
        """Qué buses entran a elena_visual (alimentador sin silenciar) y quién la escucha."""
        if (mezcla := self.nodo(VISUAL)) is None:
            return None
        buses = {}
        for clave in VISUAL_BUSES:
            alimentador = self.nodo(alimentador_visual(clave))
            buses[clave] = None if alimentador is None else not alimentador.silencio
        oyentes = [n for n in self.destinos(mezcla) if n.tipo == Tipo.GRABACION]
        return EstadoVisual(buses=buses, oyentes=oyentes)


# ---------- Búsqueda por texto (CLI) ----------

class ErrorElena(Exception):
    """Error para mostrarle al usuario tal cual (sin traza)."""


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos: «Música» == «musica»."""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_acentos.casefold().strip()


def buscar_bus(grafo: Grafo, texto: str) -> Nodo | None:
    t = normalizar(texto)
    for definicion in BUSES:
        if t in (definicion.clave, normalizar(definicion.etiqueta), definicion.nodo):
            nodo = grafo.nodo(definicion.nodo)
            if nodo is None:
                raise ErrorElena(f"El bus {definicion.etiqueta} ({definicion.nodo}) no existe. ¿Falta `mise run aplicar`?")
            return nodo
    return None


def buscar_sink(grafo: Grafo, texto: str) -> Nodo:
    """Un destino para mover apps: bus (clave, etiqueta o node.name) o cualquier otro sink
    (node.name exacto o parte única de su descripción, ej. «virtuoso» o «hdmi»)."""
    if bus := buscar_bus(grafo, texto):
        return bus
    sinks = grafo.de_tipo(Tipo.SALIDA)
    if exacto := [s for s in sinks if s.nombre == texto]:
        return exacto[0]
    t = normalizar(texto)
    parecidos = [s for s in sinks if t in normalizar(s.descripcion) or t in normalizar(s.nombre)]
    if len(parecidos) == 1:
        return parecidos[0]
    if not parecidos:
        raise ErrorElena(f"No encuentro el destino «{texto}». Buses: "
                         + ", ".join(b.clave for b in BUSES) + "; o una salida: "
                         + ", ".join(s.nombre for s in sinks))
    raise ErrorElena(f"«{texto}» coincide con varias salidas: " + ", ".join(s.nombre for s in parecidos))


def buscar_visual(grafo: Grafo) -> Nodo:
    if (nodo := grafo.nodo(VISUAL)) is None:
        raise ErrorElena(f"La mezcla para visualizadores ({VISUAL}) no existe. ¿Falta `mise run aplicar` "
                         "o reiniciar PipeWire después de agregar 30-elena-visual.conf?")
    return nodo


def buscar_apps(grafo: Grafo, texto: str, tipos: tuple[Tipo, ...] = (Tipo.APP,)) -> list[Nodo]:
    """Los streams de una app: por serial o id, o por nombre (app, proceso o media.name).

    Primero se buscan coincidencias exactas y después parciales. Varios streams de la misma app
    (Firefox abre uno por pestaña) se devuelven juntos; si coinciden apps distintas, es ambiguo.
    """
    candidatos = grafo.de_tipo(*tipos)
    if texto.isdigit():
        numero = int(texto)
        if encontrados := [n for n in candidatos if numero in (n.serial, n.id)]:
            return encontrados
        raise ErrorElena(f"No hay ningún stream con serial o id {texto}.")
    t = normalizar(texto)

    def nombres(n: Nodo) -> list[str]:
        return [normalizar(x) for x in (n.app, n.proceso, n.media) if x]

    encontrados = [n for n in candidatos if t in nombres(n)]
    if not encontrados:
        encontrados = [n for n in candidatos if any(t in x for x in nombres(n))]
    if not encontrados:
        disponibles = sorted({n.visible for n in candidatos})
        raise ErrorElena(f"No encuentro la app «{texto}». Suenan: " + (", ".join(disponibles) or "(ninguna)"))
    if len({n.visible for n in encontrados}) > 1:
        raise ErrorElena(f"«{texto}» coincide con varias apps: "
                         + ", ".join(sorted({f"{n.visible} (serial {n.serial})" for n in encontrados}))
                         + ". Usa un nombre más preciso o el serial.")
    return encontrados


def buscar_objetivo(grafo: Grafo, texto: str) -> list[Nodo]:
    """Para volumen y silencio: un bus, un mic de ELENA, una salida o fuente por node.name, o una app."""
    if bus := buscar_bus(grafo, texto):
        return [bus]
    if nodo := grafo.nodo(texto):
        if nodo.tipo == Tipo.INTERNO:
            raise ErrorElena(f"{texto} es un nodo interno de ELENA. Para el mic usa: mise run mic:voz, "
                             "mic:ruido o mic:puente; para la mezcla de visualizadores: elena visual.")
        return [nodo]
    return buscar_apps(grafo, texto, (Tipo.APP, Tipo.GRABACION))
