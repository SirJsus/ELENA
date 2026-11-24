"""Los reproductores MPRIS del control remoto.

El modelo (`Reproductores`) se prueba sin bus. El cliente (`ClienteMpris`) se prueba contra un
dbus-daemon privado (socket en .cache/pytest/, nunca el bus de sesión) con un reproductor falso.
"""

import asyncio
import shutil
import subprocess
import time

import pytest
from dbus_fast import Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_method, dbus_property, dbus_signal

from elena.modelo import ErrorElena
from elena.remoto import reproductores as rep
from elena.remoto.reproductores import PREFERIDO, PREFIJO, ClienteMpris, Reproductores, desenvolver


class Reloj:
    def __init__(self, t: float = 1_762_700_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def props(estado="Playing", titulo="Feo", artista=("Morat",), album="Feo - Single", largo=215_000_000,
          arte=None, **can) -> dict:
    md = {"mpris:trackid": "/x/1", "xesam:title": titulo, "xesam:artist": list(artista),
          "xesam:album": album, "mpris:length": largo}
    if arte:
        md["mpris:artUrl"] = arte
    base = {"CanPlay": True, "CanPause": True, "CanGoNext": True, "CanGoPrevious": True,
            "CanSeek": False, "CanControl": True}
    return {"PlaybackStatus": estado, "Metadata": md, **base, **can}


# ---------- el modelo ----------

def test_desenvolver():
    v = {"Metadata": Variant("a{sv}", {"xesam:title": Variant("s", "Feo"),
                                       "xesam:artist": Variant("as", ["Morat"])}),
         "CanPlay": Variant("b", True)}
    assert desenvolver(v) == {"Metadata": {"xesam:title": "Feo", "xesam:artist": ["Morat"]}, "CanPlay": True}


def test_vista_vacia():
    assert Reproductores().vista() == {"musica": None, "reproductores": []}


def test_vista_de_apple_music():
    reloj = Reloj()
    m = Reproductores(reloj=reloj)
    m.alta(PREFERIDO, props(artista=["Mon Laferte", "Juanes"]), identidad="Apple Music (ELENA)", posicion_us=41_200_000)
    v = m.vista()
    assert v["reproductores"] == [{"id": "elena_applemusic", "nombre": "Apple Music (ELENA)", "estado": "Playing"}]
    musica = v["musica"]
    assert musica["reproductor"] == "elena_applemusic" and musica["estado"] == "Playing"
    assert musica["artista"] == "Mon Laferte, Juanes" and musica["album"] == "Feo - Single"
    assert musica["duracion_ms"] == 215_000 and musica["posicion_ms"] == 41_200
    assert musica["medido"] == int(reloj.t * 1000)
    assert musica["puede"] == {"play": True, "pausa": True, "siguiente": True, "anterior": True,
                               "posicion": False, "control": True}


def test_posicion_corre_sonando_y_se_fija_en_pausa():
    reloj = Reloj()
    m = Reproductores(reloj=reloj)
    m.alta(PREFERIDO, props(), posicion_us=10_000_000)
    reloj.t += 3.5
    assert m.vista()["musica"]["posicion_ms"] == 13_500
    m.cambio(PREFERIDO, {"PlaybackStatus": "Paused"})
    reloj.t += 60
    assert m.vista()["musica"]["posicion_ms"] == 13_500        # en pausa no avanza
    m.posicion(PREFERIDO, 13_000_000)                          # llega la real
    assert m.vista()["musica"]["posicion_ms"] == 13_000
    m.cambio(PREFERIDO, {"PlaybackStatus": "Playing"})
    reloj.t += 2
    assert m.vista()["musica"]["posicion_ms"] == 15_000


def test_cancion_nueva_reinicia_la_posicion():
    reloj = Reloj()
    m = Reproductores(reloj=reloj)
    m.alta(PREFERIDO, props(), posicion_us=200_000_000)
    m.cambio(PREFERIDO, {"Metadata": props(titulo="Otra")["Metadata"]})
    assert m.vista()["musica"]["titulo"] == "Otra" and m.vista()["musica"]["posicion_ms"] == 0


def test_cual_es_el_actual():
    reloj = Reloj()
    m = Reproductores(reloj=reloj)
    cider, firefox = PREFIJO + "cider", PREFIJO + "firefox.instance_1_23"
    m.alta(cider, props(estado="Paused"))
    assert m.actual.nombre == cider                       # el único
    m.alta(PREFERIDO, props(estado="Paused"))
    assert m.actual.nombre == PREFERIDO                   # nadie suena: Apple Music
    reloj.t += 1
    m.cambio(cider, {"PlaybackStatus": "Playing"})
    assert m.actual.nombre == cider                       # el que suena gana a Apple Music en pausa
    reloj.t += 1
    m.alta(firefox, props(estado="Playing"))
    assert m.actual.nombre == firefox                     # entre los que suenan, el más reciente
    m.cambio(PREFERIDO, {"PlaybackStatus": "Playing"})
    assert m.actual.nombre == PREFERIDO                   # sonando, Apple Music primero
    m.elegir("cider")
    assert m.actual.nombre == cider                       # lo elegido en el teléfono manda
    m.baja(cider)
    assert m.elegido is None and m.actual.nombre == PREFERIDO
    m.cambio(PREFERIDO, {"PlaybackStatus": "Stopped"})
    m.cambio(firefox, {"PlaybackStatus": "Paused"})
    m.baja(PREFERIDO)
    assert m.actual.nombre == firefox                     # el último que sonó
    with pytest.raises(ErrorElena):
        m.elegir("no-existe")
    assert [r["id"] for r in m.vista()["reproductores"]] == ["firefox.instance_1_23"]


def test_apple_music_va_primero_en_la_lista():
    m = Reproductores()
    m.alta(PREFIJO + "aaa", props())
    m.alta(PREFERIDO, props())
    assert [r["id"] for r in m.vista()["reproductores"]] == ["elena_applemusic", "aaa"]


def test_caratulas(tmp_path):
    jpg = tmp_path / "carátula 1.jpg"
    jpg.write_bytes(b"\xff\xd8\xff fake")
    m = Reproductores()
    m.alta(PREFERIDO, props(arte=jpg.as_uri()))
    assert m.vista()["musica"]["caratula"].startswith("/caratula?v=")
    assert m.ruta_caratula() == jpg
    m.cambio(PREFERIDO, {"Metadata": props(arte="https://is1-ssl.mzstatic.com/x.jpg")["Metadata"]})
    assert m.vista()["musica"]["caratula"] == "https://is1-ssl.mzstatic.com/x.jpg"
    assert m.ruta_caratula() is None
    m.cambio(PREFERIDO, {"Metadata": props(arte="data:image/png;base64,xx")["Metadata"]})
    assert m.vista()["musica"]["caratula"] is None


def test_ruta_de_archivo_solo_imagenes(tmp_path):
    texto = tmp_path / "secreto.txt"
    texto.write_text("x")
    assert rep.ruta_de_archivo(texto.as_uri()) is None
    assert rep.ruta_de_archivo((tmp_path / "no.jpg").as_uri()) is None
    assert rep.ruta_de_archivo("file://otra-maquina/x.jpg") is None
    assert rep.ruta_de_archivo("https://x/y.jpg") is None


def test_cambio_de_desconocido_no_hace_nada():
    m = Reproductores()
    assert not m.cambio(PREFERIDO, {"PlaybackStatus": "Paused"})
    assert not m.posicion(PREFERIDO, 5)
    assert not m.baja(PREFERIDO)


# ---------- el cliente, contra un dbus-daemon privado ----------

CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={ruta}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@pytest.fixture(scope="session")
def bus_privado(tmp_path_factory):
    """Dirección de un dbus-daemon propio (su socket queda en .cache/pytest/tmp/)."""
    if shutil.which("dbus-daemon") is None:
        pytest.skip("no hay dbus-daemon")
    carpeta = tmp_path_factory.mktemp("dbus")
    socket = carpeta / "bus"
    config = carpeta / "bus.conf"
    config.write_text(CONFIG.format(ruta=socket))
    proceso = subprocess.Popen(["dbus-daemon", f"--config-file={config}", "--nofork"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    limite = time.monotonic() + 5
    while not socket.exists():
        if proceso.poll() is not None or time.monotonic() > limite:
            proceso.kill()
            pytest.fail(f"dbus-daemon no arrancó: {proceso.stderr.read().decode()}")
        time.sleep(0.02)
    yield f"unix:path={socket}"
    proceso.terminate()
    proceso.wait(5)


class FalsoRaiz(ServiceInterface):
    def __init__(self, identidad: str) -> None:
        super().__init__("org.mpris.MediaPlayer2")
        self.identidad = identidad

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return self.identidad


class FalsoReproductor(ServiceInterface):
    def __init__(self) -> None:
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.estado = "Paused"
        self.titulo = "Feo"
        self.posicion = 41_000_000
        self.llamadas: list[str] = []

    def _md(self) -> dict:
        return {"mpris:trackid": Variant("o", "/x/1"), "xesam:title": Variant("s", self.titulo),
                "xesam:artist": Variant("as", ["Morat"]), "mpris:length": Variant("x", 215_000_000)}

    def poner(self, estado: str) -> None:
        self.estado = estado
        self.emit_properties_changed({"PlaybackStatus": estado})

    @dbus_method()
    def PlayPause(self):
        self.llamadas.append("PlayPause")
        self.poner("Paused" if self.estado == "Playing" else "Playing")

    @dbus_method()
    def Next(self):
        self.llamadas.append("Next")
        self.titulo = "Siguiente"
        self.posicion = 0
        self.emit_properties_changed({"Metadata": self._md()})

    @dbus_signal()
    def Seeked(self, posicion: int) -> "x":
        return posicion

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self.estado

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return self._md()

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return self.posicion

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return True


async def hasta(condicion, limite: float = 3.0) -> None:
    fin = time.monotonic() + limite
    while not condicion():
        if time.monotonic() > fin:
            raise AssertionError("no se cumplió a tiempo")
        await asyncio.sleep(0.01)


@pytest.fixture
async def falso(bus_privado):
    """Un reproductor falso exportado (sin nombre todavía) en el bus privado."""
    bus = await MessageBus(bus_address=bus_privado).connect()
    reproductor = FalsoReproductor()
    bus.export(rep.RUTA, FalsoRaiz("Falso"))
    bus.export(rep.RUTA, reproductor)
    yield bus, reproductor
    bus.disconnect()


@pytest.fixture
async def cliente(bus_privado):
    avisos: list[int] = []
    c = ClienteMpris(al_cambiar=lambda: avisos.append(1), direccion=bus_privado)
    c.avisos = avisos
    yield c
    c.cerrar()


async def test_ve_el_reproductor_que_ya_estaba(falso, cliente):
    bus, _ = falso
    await bus.request_name(PREFERIDO)
    await cliente.conectar()
    await hasta(lambda: (cliente.modelo.vista()["musica"] or {}).get("posicion_ms"))
    musica = cliente.modelo.vista()["musica"]
    assert musica["nombre"] == "Falso" and musica["titulo"] == "Feo" and musica["estado"] == "Paused"
    assert musica["posicion_ms"] == 41_000 and musica["puede"]["siguiente"] is True
    assert cliente.avisos
    await bus.release_name(PREFERIDO)
    await hasta(lambda: cliente.modelo.vista()["musica"] is None)


async def test_alta_tardia_cambios_y_ordenes(falso, cliente):
    bus, falso_r = falso
    await cliente.conectar()
    assert cliente.modelo.vista()["musica"] is None
    await bus.request_name(PREFERIDO)
    await hasta(lambda: cliente.modelo.actual is not None)

    await cliente.orden("alternar")
    await hasta(lambda: cliente.modelo.vista()["musica"]["estado"] == "Playing")
    assert falso_r.llamadas == ["PlayPause"]

    await cliente.orden("siguiente", reproductor="elena_applemusic")
    await hasta(lambda: cliente.modelo.vista()["musica"]["titulo"] == "Siguiente")
    assert falso_r.llamadas == ["PlayPause", "Next"]

    await asyncio.sleep(0.2)                  # que termine la relectura de Position tras Next (da 0)
    falso_r.Seeked(90_000_000)                # Position sigue en 0: el 90 solo puede venir de Seeked
    await hasta(lambda: cliente.modelo.todos[PREFERIDO].posicion_us == 90_000_000)

    with pytest.raises(ErrorElena):
        await cliente.orden("brincar")
    with pytest.raises(ErrorElena, match="no aceptó"):
        await cliente.orden("anterior")       # el falso no tiene Previous: D-Bus responde con error


async def test_orden_sin_reproductor(cliente):
    await cliente.conectar()
    with pytest.raises(ErrorElena, match="ningún reproductor"):
        await cliente.orden("alternar")


async def test_nombre_que_va_y_viene_no_deja_fantasma(falso, cliente):
    bus, _ = falso
    await cliente.conectar()
    for _ in range(5):
        await bus.request_name(PREFERIDO)
        await bus.release_name(PREFERIDO)
    await asyncio.sleep(0.3)
    assert cliente.modelo.todos == {}
    await bus.request_name(PREFERIDO)
    await hasta(lambda: PREFERIDO in cliente.modelo.todos)


async def test_ignora_lo_que_no_es_mpris(bus_privado, cliente):
    otro = await MessageBus(bus_address=bus_privado).connect()
    try:
        await otro.request_name("dev.noctalia.Mpris")
        await otro.request_name(rep.PREFIJO + "playerctld")
        await cliente.conectar()
        await asyncio.sleep(0.2)
        assert cliente.modelo.todos == {}
    finally:
        otro.disconnect()
