"""La capa D-Bus del puente de música: el reproductor MPRIS «Apple Music (ELENA)».

Publica `org.mpris.MediaPlayer2` y `org.mpris.MediaPlayer2.Player` en /org/mpris/MediaPlayer2 con
dbus-fast. El nombre `org.mpris.MediaPlayer2.elena_applemusic` se pide cuando hay una canción y
se suelta cuando no (así los widgets no muestran un reproductor fantasma). Lo que publica sale de
`EstadoMusica` (elena.musica); los métodos (PlayPause, Next…) mandan la orden a Windows por el
puente, y si Apple Music no la permite no hacen nada, como pide MPRIS.

- Quit no hace nada (CanQuit = false): cerrar Apple Music apaga Windows.
- Raise enfoca la ventana de la VM con scripts/apple-music.sh, como el lanzador.
- Volume y Rate son de solo lectura en la práctica (1.0): el volumen es el del bus «ELENA · Música».
Spec: https://specifications.freedesktop.org/mpris-spec/latest/
"""

import asyncio
import subprocess
from pathlib import Path

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_method, dbus_property, dbus_signal

from elena.musica import RAIZ, SOCKET, Cambios, EstadoMusica, Puente, log

NOMBRE = "org.mpris.MediaPlayer2.elena_applemusic"
RUTA = "/org/mpris/MediaPlayer2"
IDENTIDAD = "Apple Music (ELENA)"
LANZADOR = "elena-apple-music"   # el .desktop del lanzador «Apple Music (ELENA)»: de ahí toma el widget el ícono


def variantes(modelo: EstadoMusica) -> dict[str, Variant]:
    return {clave: Variant(firma, valor) for clave, (firma, valor) in modelo.metadata().items()}


class Raiz(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.mpris.MediaPlayer2")

    @dbus_method()
    def Raise(self):
        subprocess.Popen([str(RAIZ / "scripts" / "apple-music.sh")], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @dbus_method()
    def Quit(self):
        pass   # CanQuit = false

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return IDENTIDAD

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return LANZADOR

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return []


class Reproductor(ServiceInterface):
    def __init__(self, puente: Puente) -> None:
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.puente = puente

    @property
    def modelo(self) -> EstadoMusica:
        return self.puente.modelo

    def _orden(self, accion: str) -> None:
        if orden := self.modelo.orden_para(accion):
            self.puente.orden(orden)

    @dbus_method()
    def Next(self):
        self._orden("Next")

    @dbus_method()
    def Previous(self):
        self._orden("Previous")

    @dbus_method()
    def Pause(self):
        self._orden("Pause")

    @dbus_method()
    def PlayPause(self):
        self._orden("PlayPause")

    @dbus_method()
    def Stop(self):
        self._orden("Stop")

    @dbus_method()
    def Play(self):
        self._orden("Play")

    @dbus_method()
    def Seek(self, Offset: "x"):
        m = self.modelo
        if not m.controles["CanSeek"] or m.pista is None:
            return
        destino = m.posicion_ms() + Offset / 1000
        if m.pista.duracion_ms and destino > m.pista.duracion_ms:
            self._orden("Next")   # como pide MPRIS
        else:
            self.puente.orden("posicion", ms=int(max(0, destino)))

    @dbus_method()
    def SetPosition(self, TrackId: "o", Position: "x"):
        m = self.modelo
        if (m.controles["CanSeek"] and m.pista is not None and TrackId == m.pista.trackid
                and 0 <= Position <= m.pista.duracion_ms * 1000):
            self.puente.orden("posicion", ms=Position // 1000)

    @dbus_method()
    def OpenUri(self, Uri: "s"):
        pass

    @dbus_signal()
    def Seeked(self, posicion: int) -> "x":
        return posicion

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self.modelo.playback_status

    @dbus_property()
    def Rate(self) -> "d":
        return 1.0

    @Rate.setter
    def Rate(self, valor: "d"):
        pass

    @dbus_property()
    def Volume(self) -> "d":
        return 1.0

    @Volume.setter
    def Volume(self, valor: "d"):
        pass   # el volumen es el del bus «ELENA · Música»

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return variantes(self.modelo)

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return self.modelo.posicion_us()

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return self.modelo.controles["CanGoNext"]

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return self.modelo.controles["CanGoPrevious"]

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return self.modelo.controles["CanPlay"]

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return self.modelo.controles["CanPause"]

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return self.modelo.controles["CanSeek"]

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True

    def valor(self, propiedad: str) -> object:
        """El valor actual de una propiedad, para PropertiesChanged."""
        if propiedad == "Metadata":
            return variantes(self.modelo)
        if propiedad == "PlaybackStatus":
            return self.modelo.playback_status
        return self.modelo.controles[propiedad]


class Publicador:
    """Lleva los cambios del modelo al bus: pide o suelta el nombre, PropertiesChanged y Seeked."""

    def __init__(self, bus: MessageBus, reproductor: Reproductor) -> None:
        self.bus = bus
        self.reproductor = reproductor
        self._cola: asyncio.Task | None = None

    def __call__(self, cambios: Cambios, modelo: EstadoMusica) -> None:
        if cambios.registrar or cambios.quitar:
            # pedir y soltar el nombre es asíncrono; en orden, por si llegan seguidos
            anterior = self._cola
            self._cola = asyncio.ensure_future(self._nombre(cambios.registrar and modelo.activo, anterior))
        if cambios.propiedades:
            self.reproductor.emit_properties_changed({p: self.reproductor.valor(p) for p in cambios.propiedades})
        if cambios.salto:
            self.reproductor.Seeked(modelo.posicion_us())

    async def _nombre(self, pedir: bool, anterior: asyncio.Task | None) -> None:
        if anterior is not None:
            await asyncio.gather(anterior, return_exceptions=True)
        try:
            if pedir:
                await self.bus.request_name(NOMBRE)
            else:
                await self.bus.release_name(NOMBRE)
        except Exception as e:
            log(f"D-Bus: no pude {'pedir' if pedir else 'soltar'} {NOMBRE}: {e!r}")


async def servir(ruta: Path = SOCKET) -> None:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    puente = Puente(ruta=ruta)
    reproductor = Reproductor(puente)
    bus.export(RUTA, Raiz())
    bus.export(RUTA, reproductor)
    puente.publicar = Publicador(bus, reproductor)
    log(f"D-Bus: {RUTA} listo; {NOMBRE} se pide cuando suene algo")
    await puente.correr()
