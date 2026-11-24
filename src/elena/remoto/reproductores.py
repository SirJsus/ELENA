"""Los reproductores MPRIS del bus de sesión, vistos y controlados desde el control remoto.

El servidor no toca el puente de música de la VM: le habla a «Apple Music (ELENA)» por MPRIS, igual que
Noctalia o playerctl, y de paso a cualquier otro reproductor (Cider, el navegador).

- `Reproductores` es el modelo puro: recibe lo que se lee del bus (GetAll, PropertiesChanged,
  Seeked, altas y bajas) y calcula cuál es el reproductor actual y lo que ve el teléfono. Se prueba
  sin bus.
- `ClienteMpris` lo alimenta desde D-Bus con mensajes de bajo nivel (sin introspección): sigue
  `NameOwnerChanged` de `org.mpris.MediaPlayer2.*` y las señales de /org/mpris/MediaPlayer2, y
  manda las órdenes (PlayPause, Next…).
- MPRIS no avisa la posición con PropertiesChanged: se lee al conectar, cuando cambia el estado o
  la canción, con cada `Seeked` y cada 5 s mientras suena el actual. El teléfono la interpola con
  `medido`.
Spec: https://specifications.freedesktop.org/mpris-spec/latest/
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from elena.modelo import ErrorElena

PREFIJO = "org.mpris.MediaPlayer2."
PREFERIDO = PREFIJO + "elena_applemusic"   # el puente de música de la VM
IGNORADOS = {PREFIJO + "playerctld"}        # repite al reproductor activo: saldría dos veces
RUTA = "/org/mpris/MediaPlayer2"
RAIZ_IFAZ = "org.mpris.MediaPlayer2"
REPRODUCTOR_IFAZ = "org.mpris.MediaPlayer2.Player"
PROPIEDADES_IFAZ = "org.freedesktop.DBus.Properties"
SINCRONIZAR = 5.0                           # s entre lecturas de Position mientras suena el actual
ESTADOS = ("Playing", "Paused", "Stopped")

# Orden del control remoto → método de MPRIS y el permiso (Can*) que lo habilita
ORDENES = {
    "alternar": ("PlayPause", "CanPause"),
    "play": ("Play", "CanPlay"),
    "pausa": ("Pause", "CanPause"),
    "siguiente": ("Next", "CanGoNext"),
    "anterior": ("Previous", "CanGoPrevious"),
    "detener": ("Stop", "CanControl"),
}
PERMISOS = {"CanPlay": "play", "CanPause": "pausa", "CanGoNext": "siguiente",
            "CanGoPrevious": "anterior", "CanSeek": "posicion", "CanControl": "control"}
IMAGENES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_CARATULA = 5 << 20


def desenvolver(valor: object) -> object:
    """Quita los Variant de dbus-fast, también dentro de diccionarios y listas (Metadata)."""
    if isinstance(valor, Variant):
        return desenvolver(valor.value)
    if isinstance(valor, dict):
        return {k: desenvolver(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [desenvolver(v) for v in valor]
    return valor


def corto(nombre: str) -> str:
    """`org.mpris.MediaPlayer2.elena_applemusic` → `elena_applemusic` (el id del teléfono)."""
    return nombre.removeprefix(PREFIJO)


def ruta_de_archivo(url: str) -> Path | None:
    """La ruta de un `file://` que apunta a una imagen razonable, o None."""
    partes = urlparse(url)
    if partes.scheme != "file" or partes.netloc not in ("", "localhost"):
        return None
    ruta = Path(unquote(partes.path))
    try:
        if ruta.suffix.lower() in IMAGENES and ruta.is_file() and ruta.stat().st_size <= MAX_CARATULA:
            return ruta
    except OSError:
        pass
    return None


@dataclass
class Reproductor:
    nombre: str                      # nombre en el bus (org.mpris.MediaPlayer2.…)
    identidad: str = ""              # Identity («Apple Music (ELENA)»)
    propiedades: dict = field(default_factory=dict)   # las de org.mpris.MediaPlayer2.Player, sin Variant
    posicion_us: int = 0
    medido: float = 0.0              # cuándo se leyó posicion_us (reloj del modelo, epoch en s)
    sono: float = 0.0                # la última vez que pasó a Playing (para elegir el actual)

    @property
    def estado(self) -> str:
        e = self.propiedades.get("PlaybackStatus")
        return e if e in ESTADOS else "Stopped"

    @property
    def metadata(self) -> dict:
        m = self.propiedades.get("Metadata")
        return m if isinstance(m, dict) else {}

    @property
    def caratula(self) -> str | None:
        url = self.metadata.get("mpris:artUrl")
        return url if isinstance(url, str) and url else None

    def puede(self, permiso: str) -> bool:
        return bool(self.propiedades.get(permiso, False))


def _texto(valor: object) -> str:
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor if v)
    return str(valor) if valor else ""


class Reproductores:
    """Los reproductores conocidos y cuál es el actual. Cada cambio devuelve True si cambió la vista."""

    def __init__(self, reloj: Callable[[], float] = time.time) -> None:
        self.reloj = reloj
        self.todos: dict[str, Reproductor] = {}
        self.elegido: str | None = None       # elegido en el teléfono; dura mientras exista

    # ----- lo que llega del bus -----

    def alta(self, nombre: str, propiedades: dict, identidad: str = "", posicion_us: int = 0) -> bool:
        r = Reproductor(nombre, identidad=identidad, propiedades=dict(propiedades))
        r.posicion_us, r.medido = posicion_us, self.reloj()
        if r.estado == "Playing":
            r.sono = r.medido
        self.todos[nombre] = r
        return True

    def baja(self, nombre: str) -> bool:
        if self.todos.pop(nombre, None) is None:
            return False
        if self.elegido == nombre:
            self.elegido = None
        return True

    def cambio(self, nombre: str, propiedades: dict) -> bool:
        r = self.todos.get(nombre)
        if r is None or not propiedades:
            return False
        antes = r.estado
        if antes == "Playing" and propiedades.get("PlaybackStatus", antes) != "Playing":
            # al pausar, la posición deja de correr: se fija lo que ya avanzó (hasta que llegue la real)
            r.posicion_us, r.medido = self.posicion_ahora(r), self.reloj()
        r.propiedades.update(propiedades)
        if r.estado == "Playing" and antes != "Playing":
            r.sono = r.medido = self.reloj()
        if "Metadata" in propiedades:
            r.posicion_us, r.medido = 0, self.reloj()   # canción nueva; la posición real llega aparte
        return True

    def posicion(self, nombre: str, posicion_us: int) -> bool:
        """Position leída o recibida con Seeked."""
        r = self.todos.get(nombre)
        if r is None:
            return False
        r.posicion_us, r.medido = max(int(posicion_us), 0), self.reloj()
        return True

    def elegir(self, corto_o_nombre: str | None) -> bool:
        if corto_o_nombre is None:
            self.elegido = None
            return True
        nombre = corto_o_nombre if corto_o_nombre.startswith(PREFIJO) else PREFIJO + corto_o_nombre
        if nombre not in self.todos:
            raise ErrorElena(f"No hay un reproductor «{corto(nombre)}».")
        self.elegido = nombre
        return True

    # ----- lo que se calcula -----

    def posicion_ahora(self, r: Reproductor) -> int:
        if r.estado != "Playing":
            return r.posicion_us
        return r.posicion_us + int((self.reloj() - r.medido) * 1_000_000)

    @property
    def actual(self) -> Reproductor | None:
        """El elegido; si no, el que suena (Apple Music primero, luego el que empezó más
        recientemente); si no, Apple Music; si no, el último que sonó."""
        if self.elegido in self.todos:
            return self.todos[self.elegido]
        if not self.todos:
            return None
        sonando = [r for r in self.todos.values() if r.estado == "Playing"]
        if sonando:
            return max(sonando, key=lambda r: (r.nombre == PREFERIDO, r.sono))
        if PREFERIDO in self.todos:
            return self.todos[PREFERIDO]
        return max(self.todos.values(), key=lambda r: (r.sono, r.nombre))

    def ruta_caratula(self) -> Path | None:
        """El archivo de la carátula del actual, si la publicó como `file://`."""
        r = self.actual
        return ruta_de_archivo(r.caratula) if r and r.caratula else None

    def vista(self) -> dict:
        """Lo que ve el teléfono: `musica` (el actual, o None) y la lista de reproductores."""
        r = self.actual
        lista = [{"id": corto(x.nombre), "nombre": x.identidad or corto(x.nombre), "estado": x.estado}
                 for x in sorted(self.todos.values(), key=lambda x: (x.nombre != PREFERIDO, x.nombre))]
        if r is None:
            return {"musica": None, "reproductores": lista}
        md = r.metadata
        duracion = md.get("mpris:length")
        caratula = r.caratula
        if caratula and caratula.startswith("file://"):
            caratula = "/caratula?v=" + hashlib.sha1(caratula.encode()).hexdigest()[:12]
        elif caratula and not caratula.startswith(("https://", "http://")):
            caratula = None
        musica = {
            "reproductor": corto(r.nombre),
            "nombre": r.identidad or corto(r.nombre),
            "estado": r.estado,
            "titulo": _texto(md.get("xesam:title")),
            "artista": _texto(md.get("xesam:artist")),
            "album": _texto(md.get("xesam:album")),
            "duracion_ms": int(duracion) // 1000 if isinstance(duracion, int) and duracion > 0 else None,
            "posicion_ms": self.posicion_ahora(r) // 1000,
            "medido": int(self.reloj() * 1000),
            "caratula": caratula,
            "puede": {corto_p: r.puede(p) for p, corto_p in PERMISOS.items()},
        }
        return {"musica": musica, "reproductores": lista}


# ---------- D-Bus ----------

REGLAS = (
    "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
    f"member='NameOwnerChanged',arg0namespace='{RAIZ_IFAZ}'",
    f"type='signal',interface='{PROPIEDADES_IFAZ}',member='PropertiesChanged',path='{RUTA}'",
    f"type='signal',interface='{REPRODUCTOR_IFAZ}',member='Seeked',path='{RUTA}'",
)


class ClienteMpris:
    """Sigue los reproductores del bus en `modelo` y llama a `al_cambiar()` cuando cambia la vista."""

    def __init__(self, modelo: Reproductores | None = None, al_cambiar: Callable[[], None] = lambda: None,
                 direccion: str | None = None) -> None:
        self.modelo = modelo or Reproductores()
        self.al_cambiar = al_cambiar
        self.direccion = direccion          # None: el bus de sesión; en las pruebas, uno privado
        self.bus: MessageBus | None = None
        self._duenos: dict[str, set[str]] = {}   # nombre único (:1.187) → nombres MPRIS
        self._generacion: dict[str, int] = {}    # nombre MPRIS → cuántas veces cambió de dueño
        self._tareas: set[asyncio.Task] = set()

    # ----- arranque -----

    async def conectar(self) -> None:
        if self.direccion:
            self.bus = await MessageBus(bus_address=self.direccion).connect()
        else:
            self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.bus.add_message_handler(self._mensaje)
        for regla in REGLAS:
            await self._dbus("AddMatch", "s", [regla])
        nombres = (await self._dbus("ListNames", "", []))[0]
        await asyncio.gather(*(self._alta(n) for n in nombres if self._es_reproductor(n)))

    async def correr(self) -> None:
        """Conecta y sincroniza la posición del actual mientras suena (hasta que se cancele)."""
        if self.bus is None:
            await self.conectar()
        while True:
            await asyncio.sleep(SINCRONIZAR)
            r = self.modelo.actual
            if r is not None and r.estado == "Playing":
                await self._leer_posicion(r.nombre)

    def cerrar(self) -> None:
        for t in self._tareas:
            t.cancel()
        if self.bus is not None:
            self.bus.disconnect()
            self.bus = None

    # ----- órdenes -----

    async def orden(self, accion: str, reproductor: str | None = None) -> None:
        if accion not in ORDENES:
            raise ErrorElena(f"No conozco la orden «{accion}».")
        if reproductor:
            nombre = reproductor if reproductor.startswith(PREFIJO) else PREFIJO + reproductor
            r = self.modelo.todos.get(nombre)
        else:
            r = self.modelo.actual
        if r is None:
            raise ErrorElena("No hay ningún reproductor abierto.")
        metodo, _ = ORDENES[accion]
        respuesta = await self._llamar(r.nombre, REPRODUCTOR_IFAZ, metodo, "", [])
        if respuesta.message_type == MessageType.ERROR:
            raise ErrorElena(f"{r.identidad or corto(r.nombre)} no aceptó «{accion}»: {respuesta.body}")

    def elegir(self, reproductor: str | None) -> None:
        if self.modelo.elegir(reproductor):
            self.al_cambiar()

    # ----- internos -----

    @staticmethod
    def _es_reproductor(nombre: str) -> bool:
        return nombre.startswith(PREFIJO) and nombre not in IGNORADOS

    def _avisar(self, cambio: bool) -> None:
        if cambio:
            self.al_cambiar()

    def _tarea(self, corrutina) -> None:
        t = asyncio.ensure_future(corrutina)
        self._tareas.add(t)
        t.add_done_callback(self._tareas.discard)

    async def _llamar(self, destino: str, interfaz: str, miembro: str, firma: str, cuerpo: list) -> Message:
        assert self.bus is not None
        return await self.bus.call(Message(destination=destino, path=RUTA, interface=interfaz,
                                           member=miembro, signature=firma, body=cuerpo))

    async def _dbus(self, miembro: str, firma: str, cuerpo: list) -> list:
        assert self.bus is not None
        r = await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                        interface="org.freedesktop.DBus", member=miembro,
                                        signature=firma, body=cuerpo))
        if r.message_type == MessageType.ERROR:
            raise ErrorElena(f"D-Bus: {miembro} falló: {r.body}")
        return r.body

    async def _obtener(self, destino: str, interfaz: str, propiedad: str) -> object:
        r = await self._llamar(destino, PROPIEDADES_IFAZ, "Get", "ss", [interfaz, propiedad])
        return None if r.message_type == MessageType.ERROR else desenvolver(r.body[0])

    async def _alta(self, nombre: str) -> None:
        # Si el nombre cambia de dueño mientras se lee (el puente de la 4d lo suelta y lo pide según
        # haya canción), esta lectura ya no vale: lo decide la generación.
        generacion = self._generacion.get(nombre, 0)
        vigente = lambda: self._generacion.get(nombre, 0) == generacion   # noqa: E731
        try:
            dueno = (await self._dbus("GetNameOwner", "s", [nombre]))[0]
            r = await self._llamar(nombre, PROPIEDADES_IFAZ, "GetAll", "s", [REPRODUCTOR_IFAZ])
        except ErrorElena:
            return   # se fue antes de leerlo
        if r.message_type == MessageType.ERROR or not vigente():
            return
        # Alta en cuanto llega GetAll: una señal posterior ya encuentra al reproductor
        self._duenos.setdefault(dueno, set()).add(nombre)
        self._avisar(self.modelo.alta(nombre, desenvolver(r.body[0])))
        identidad = await self._obtener(nombre, RAIZ_IFAZ, "Identity")
        if vigente() and nombre in self.modelo.todos and identidad:
            self.modelo.todos[nombre].identidad = str(identidad)
            self._avisar(True)
        if vigente():
            await self._leer_posicion(nombre)

    async def _leer_posicion(self, nombre: str) -> None:
        posicion = await self._obtener(nombre, REPRODUCTOR_IFAZ, "Position")
        if isinstance(posicion, int):
            self._avisar(self.modelo.posicion(nombre, posicion))

    def _nombres_de(self, remitente: str | None) -> set[str]:
        return set(self._duenos.get(remitente or "", ()))

    def _mensaje(self, m: Message) -> None:
        if m.message_type != MessageType.SIGNAL:
            return
        if m.member == "NameOwnerChanged" and m.interface == "org.freedesktop.DBus":
            nombre, viejo, nuevo = m.body
            if not self._es_reproductor(nombre):
                return
            self._generacion[nombre] = self._generacion.get(nombre, 0) + 1
            if viejo:
                self._duenos.get(viejo, set()).discard(nombre)
                self._avisar(self.modelo.baja(nombre))
            if nuevo:
                self._tarea(self._alta(nombre))
        elif m.member == "PropertiesChanged" and m.path == RUTA:
            interfaz, cambiadas, invalidadas = m.body
            if interfaz != REPRODUCTOR_IFAZ:
                return
            cambiadas = desenvolver(cambiadas)
            for nombre in self._nombres_de(m.sender):
                self._avisar(self.modelo.cambio(nombre, cambiadas))
                if invalidadas or {"PlaybackStatus", "Metadata"} & cambiadas.keys():
                    self._tarea(self._releer(nombre, invalidadas))
        elif m.member == "Seeked" and m.interface == REPRODUCTOR_IFAZ:
            for nombre in self._nombres_de(m.sender):
                self._avisar(self.modelo.posicion(nombre, m.body[0]))

    async def _releer(self, nombre: str, invalidadas: list[str]) -> None:
        """Tras un cambio de estado o canción: la posición real y las propiedades invalidadas."""
        with contextlib.suppress(ErrorElena):
            for p in invalidadas:
                valor = await self._obtener(nombre, REPRODUCTOR_IFAZ, p)
                if valor is not None:
                    self._avisar(self.modelo.cambio(nombre, {p: valor}))
            await self._leer_posicion(nombre)
