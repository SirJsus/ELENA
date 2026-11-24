"""Puente de música: el modelo con mensajes como los que manda Windows (vistos el
2026-11-08), el protocolo contra un Windows falso por socket y la capa D-Bus sin bus."""

import asyncio
import base64
import hashlib
import json
import os

import pytest

from elena import cli, mpris, musica
from elena.musica import Cambios, EstadoMusica, Puente, separar

JPEG = b"\xff\xd8\xff\xe0 caratula de prueba \xff\xd9"
ID = hashlib.sha1(JPEG).hexdigest()
PUEDE_SONANDO = {"play": False, "pausa": True, "alternar": True, "detener": True,
                 "siguiente": True, "anterior": True, "posicion": False}
PUEDE_PAUSA = PUEDE_SONANDO | {"play": True, "pausa": False}


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def estado(**cambios) -> dict:
    """Un «estado» como el que mandó Windows con «Amárrame» (2026-11-08)."""
    return {"t": "estado", "estado": "reproduciendo", "titulo": "Amárrame (feat. Juanes)",
            "artista": "Mon Laferte — La Trenza (Deluxe)", "album": "",
            "artista_album": "Mon Laferte — La Trenza (Deluxe)", "pista": 0, "duracion_ms": 207000,
            "posicion_ms": 27000, "caratula": None, "puede": PUEDE_SONANDO} | cambios


def caratula(datos: bytes = JPEG, ident: str = ID) -> dict:
    return {"t": "caratula", "id": ident, "tipo": "image/jpeg", "datos": base64.b64encode(datos).decode()}


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def modelo(tmp_path, reloj) -> EstadoMusica:
    return EstadoMusica(carpeta=tmp_path / "caratulas", reloj=reloj)


# ---------- el modelo ----------

def test_separar():
    assert separar("Mon Laferte — La Trenza (Deluxe)", "") == ("Mon Laferte", "La Trenza (Deluxe)")
    assert separar("Morat — Feo - Single", "") == ("Morat", "Feo - Single")
    assert separar("A — B — C", "") == ("A", "B — C")
    assert separar("Petit Biscuit", "") == ("Petit Biscuit", "")
    assert separar("Mon Laferte — X", "Álbum real") == ("Mon Laferte — X", "Álbum real")   # si Apple lo corrige


def test_cancion_nueva_registra(modelo):
    c = modelo.recibir(estado())
    assert c.registrar and not c.quitar and not c.salto
    assert {"Metadata", "PlaybackStatus", "CanPlay", "CanPause", "CanGoNext", "CanGoPrevious"} <= c.propiedades
    assert modelo.playback_status == "Playing"
    md = modelo.metadata()
    assert md["xesam:title"] == ("s", "Amárrame (feat. Juanes)")
    assert md["xesam:artist"] == ("as", ["Mon Laferte"])
    assert md["xesam:album"] == ("s", "La Trenza (Deluxe)")
    assert md["xesam:albumArtist"] == ("as", ["Mon Laferte"])
    assert md["mpris:length"] == ("x", 207_000_000)
    assert md["mpris:trackid"][0] == "o" and md["mpris:trackid"][1].startswith("/org/elena/applemusic/pista/p")
    assert "xesam:trackNumber" not in md and "mpris:artUrl" not in md   # Apple Music manda pista 0
    assert modelo.controles == {"CanPlay": True, "CanPause": True, "CanGoNext": True, "CanGoPrevious": True,
                                "CanSeek": False, "CanControl": True}


def test_sin_reproductor_al_inicio(modelo):
    assert modelo.playback_status == "Stopped"
    assert modelo.metadata() == {"mpris:trackid": ("o", musica.SIN_PISTA)}
    assert not any(v for k, v in modelo.controles.items() if k != "CanControl")
    assert modelo.orden_para("PlayPause") is None


def test_estado_vacio_no_registra(modelo):
    # Apple Music recién abierta, antes de elegir algo (visto el 2026-11-08)
    vacio = estado(estado="detenido", titulo="", artista="", artista_album="", duracion_ms=0, posicion_ms=0,
                   puede={"play": True, "pausa": False, "alternar": True, "detener": False,
                          "siguiente": False, "anterior": False, "posicion": False})
    assert not modelo.recibir(vacio)
    assert not modelo.activo
    modelo.recibir(estado())
    assert modelo.recibir(vacio).quitar


def test_posicion_avanza_sonando_y_se_congela_en_pausa(modelo, reloj):
    modelo.recibir(estado(posicion_ms=27000))
    reloj.t += 10
    assert modelo.posicion_us() == 37_000_000
    c = modelo.recibir(estado(estado="pausa", posicion_ms=37000, puede=PUEDE_PAUSA))
    assert c.propiedades == {"PlaybackStatus"} and not c.salto
    reloj.t += 60
    assert modelo.posicion_ms() == 37000
    reloj.t += 1000   # nunca pasa de la duración
    modelo.recibir(estado(posicion_ms=200000))
    reloj.t += 60
    assert modelo.posicion_ms() == 207000


def test_salto_y_correccion_chica(modelo, reloj):
    modelo.recibir(estado(posicion_ms=27000))
    reloj.t += 5
    assert not modelo.recibir(estado(posicion_ms=32800)).salto   # Apple Music en segundos enteros
    assert modelo.recibir(estado(posicion_ms=100)).salto          # «anterior» volvió al inicio


def test_otra_cancion_no_es_salto(modelo):
    modelo.recibir(estado(posicion_ms=120000))
    c = modelo.recibir(estado(titulo="Where's My Love", artista="SYML — Syml", artista_album="SYML — Syml",
                              duracion_ms=202000, posicion_ms=73))
    assert "Metadata" in c.propiedades and not c.salto and not c.registrar
    assert modelo.metadata()["xesam:title"] == ("s", "Where's My Love")


def test_caratula_antes_que_su_estado(modelo, tmp_path):
    assert not modelo.recibir(caratula())          # aún no la nombra ningún estado
    archivo = tmp_path / "caratulas" / f"{ID}.jpg"
    assert archivo.read_bytes() == JPEG
    modelo.recibir(estado(caratula=ID))
    assert modelo.metadata()["mpris:artUrl"] == ("s", archivo.as_uri())


def test_caratula_despues_de_su_estado(modelo):
    modelo.recibir(estado(caratula=ID))
    assert "mpris:artUrl" not in modelo.metadata()   # todavía no llega el archivo
    assert modelo.recibir(caratula()).propiedades == {"Metadata"}
    assert "mpris:artUrl" in modelo.metadata()


@pytest.mark.parametrize("mensaje", [
    caratula(ident="0" * 40),                            # el sha1 no corresponde: dañada
    caratula(ident="../../etc/x"),                       # id que no es un sha1
    {"t": "caratula", "id": ID, "datos": "no es base64!"},
])
def test_caratula_mala_se_ignora(modelo, tmp_path, mensaje):
    assert not modelo.recibir(mensaje)
    assert not list(tmp_path.glob("**/*.jpg"))


def test_caratulas_se_conservan_las_ultimas(tmp_path, monkeypatch):
    monkeypatch.setattr(musica, "MAX_CARATULAS", 3)
    carpeta = tmp_path / "c"
    for i in range(5):
        datos = JPEG + bytes([i])
        ruta = musica.guardar_caratula(carpeta, hashlib.sha1(datos).hexdigest(), datos)
        t = 1_000_000 + i
        os.utime(ruta, (t, t))
    quedan = {r.read_bytes()[-1] for r in carpeta.glob("*.jpg")}
    assert quedan == {2, 3, 4}


def test_controles_apagados(modelo):
    modelo.recibir(estado(puede={k: False for k in PUEDE_SONANDO}))
    assert modelo.controles == {"CanPlay": False, "CanPause": False, "CanGoNext": False, "CanGoPrevious": False,
                                "CanSeek": False, "CanControl": True}
    for accion in ("PlayPause", "Play", "Pause", "Stop", "Next", "Previous"):
        assert modelo.orden_para(accion) is None


def test_ordenes(modelo):
    modelo.recibir(estado())
    assert modelo.orden_para("PlayPause") == "alternar"
    assert modelo.orden_para("Pause") == "pausa"
    assert modelo.orden_para("Play") is None          # ya suena y Play no está permitido
    assert modelo.orden_para("Next") == "siguiente"
    assert modelo.orden_para("Previous") == "anterior"
    assert modelo.orden_para("Stop") == "detener"
    modelo.recibir(estado(estado="pausa", puede=PUEDE_PAUSA | {"alternar": False}))
    assert modelo.orden_para("PlayPause") == "play"
    assert modelo.orden_para("Play") == "play"
    assert modelo.orden_para("Pause") is None


def test_sin_sesion_y_vencido(modelo, reloj):
    modelo.recibir(estado())
    assert modelo.recibir({"t": "sin_sesion"}).quitar
    assert not modelo.activo and modelo.playback_status == "Stopped"
    modelo.recibir(estado())
    reloj.t += 10
    modelo.recibir({"t": "latido"})
    assert not modelo.vencido()
    reloj.t += musica.SIN_NOTICIAS + 1
    assert modelo.vencido()


def test_mensajes_raros_no_rompen(modelo):
    assert musica.decodificar(b"\x1b[2J\x1b[001;001HBdsDxe: starting Boot0009") is None   # el firmware
    assert musica.decodificar(b"[1, 2]") is None
    assert musica.decodificar(b'{"sin": "tipo"}') is None
    assert not modelo.recibir({"t": "desconocido"})
    assert not modelo.recibir(estado(duracion_ms="mucho"))
    assert not modelo.activo


# ---------- el protocolo, contra un Windows falso ----------

class WindowsFalso:
    """El otro lado del socket de QEMU: anota lo que manda Linux y puede mandarle líneas."""

    def __init__(self, ruta) -> None:
        self.ruta = ruta
        self.recibido: list[dict] = []
        self.conectado = asyncio.Event()
        self._escritor: asyncio.StreamWriter | None = None

    async def iniciar(self) -> None:
        self._servidor = await asyncio.start_unix_server(self._atender, path=str(self.ruta))

    async def _atender(self, lector, escritor) -> None:
        self._escritor = escritor
        self.conectado.set()
        while linea := await lector.readline():
            self.recibido.append(json.loads(linea))

    async def manda(self, *lineas) -> None:
        for linea in lineas:
            self._escritor.write(linea if isinstance(linea, bytes) else json.dumps(linea).encode() + b"\n")
        await self._escritor.drain()
        await asyncio.sleep(0.05)

    async def cortar(self) -> None:
        self._escritor.close()
        self._servidor.close()
        await asyncio.sleep(0.05)


async def esperar(condicion, segundos: float = 2.0) -> None:
    limite = asyncio.get_running_loop().time() + segundos
    while not condicion():
        assert asyncio.get_running_loop().time() < limite, "no pasó a tiempo"
        await asyncio.sleep(0.01)


@pytest.fixture
async def windows(tmp_path):
    falso = WindowsFalso(tmp_path / "m.sock")
    await falso.iniciar()
    return falso


def puente_de_prueba(tmp_path, ruta, **opciones):
    publicados: list[Cambios] = []
    textos: list[str] = []
    p = Puente(publicar=lambda c, m: publicados.append(c), ruta=ruta,
               modelo=EstadoMusica(carpeta=tmp_path / "caratulas"),
               archivo_estado=tmp_path / "estado.json", escribir=textos.append, reintento=0.05, **opciones)
    return p, publicados, textos


async def test_protocolo_de_ida_y_vuelta(tmp_path, windows):
    puente, publicados, textos = puente_de_prueba(tmp_path, windows.ruta)
    tarea = asyncio.create_task(puente.correr(una_vez=True))
    await asyncio.wait_for(windows.conectado.wait(), 2)
    await esperar(lambda: windows.recibido)
    assert windows.recibido[0] == {"t": "hola", "v": 1}

    await windows.manda(b"\x1b[2J\x1b[001;001HBdsDxe: starting Boot0009\r\n",   # el firmware
                        {"t": "hola", "v": 1, "app": "AppleMusic.exe", "agente": "puente-musica.ps1"},
                        caratula(), estado(caratula=ID), {"t": "latido"})
    await esperar(lambda: puente.modelo.activo)
    assert publicados[0].registrar
    assert puente.modelo.app == "AppleMusic.exe"
    assert "mpris:artUrl" in puente.modelo.metadata()
    assert any("firmware" in t for t in textos)

    guardado = json.loads((tmp_path / "estado.json").read_text())
    assert guardado["conectado"] and guardado["activo"] and guardado["artista"] == "Mon Laferte"

    assert puente.orden("alternar")
    await esperar(lambda: len(windows.recibido) == 2)
    assert windows.recibido[1] == {"t": "orden", "orden": "alternar"}

    await windows.cortar()                 # la VM se apagó
    await asyncio.wait_for(tarea, 2)
    assert publicados[-1].quitar and not puente.modelo.activo
    assert not puente.orden("alternar")
    assert json.loads((tmp_path / "estado.json").read_text())["conectado"] is False


async def test_sin_noticias_quita_y_pide_todo_otra_vez(tmp_path, windows):
    puente, publicados, textos = puente_de_prueba(tmp_path, windows.ruta, sin_noticias=0.2)
    tarea = asyncio.create_task(puente.correr(una_vez=True))
    await asyncio.wait_for(windows.conectado.wait(), 2)
    await windows.manda(estado())
    assert puente.modelo.activo
    await esperar(lambda: not puente.modelo.activo)      # 0.2 s sin mensajes
    assert publicados[-1].quitar
    await windows.manda({"t": "latido"})                 # Windows vuelve: se le pide todo
    await esperar(lambda: windows.recibido.count({"t": "hola", "v": 1}) == 2)
    await windows.cortar()
    await asyncio.wait_for(tarea, 2)


async def test_windows_arrancando_sin_avisos_y_estado_al_cortar(tmp_path, windows):
    # Visto el 2026-11-08: mientras Windows arranca no hay reproductor, y no se avisa que «se quita».
    # Al cortarse, el archivo de estado dice «sin conexión» aunque no hubiera reproductor.
    puente, publicados, textos = puente_de_prueba(tmp_path, windows.ruta, sin_noticias=0.1)
    tarea = asyncio.create_task(puente.correr(una_vez=True))
    await asyncio.wait_for(windows.conectado.wait(), 2)
    await asyncio.sleep(0.3)
    assert json.loads((tmp_path / "estado.json").read_text())["conectado"] is True
    await windows.manda({"t": "hola", "v": 1, "app": None, "agente": "puente-musica.ps1"}, {"t": "sin_sesion"})
    await windows.cortar()
    await asyncio.wait_for(tarea, 2)
    assert not any("se quita" in t for t in textos) and not publicados
    assert json.loads((tmp_path / "estado.json").read_text())["conectado"] is False


async def test_espera_a_que_exista_el_socket(tmp_path):
    ruta = tmp_path / "todavia-no.sock"
    puente, _, textos = puente_de_prueba(tmp_path, ruta)
    tarea = asyncio.create_task(puente.correr(una_vez=True))
    await asyncio.sleep(0.15)
    assert textos == [f"Esperando la VM ({ruta})…"]      # se avisa una sola vez
    falso = WindowsFalso(ruta)
    await falso.iniciar()
    await asyncio.wait_for(falso.conectado.wait(), 2)
    await falso.cortar()
    await asyncio.wait_for(tarea, 2)


# ---------- la capa D-Bus, sin bus ----------

def test_mpris_introspeccion():
    raiz = mpris.Raiz().introspect()
    assert {p.name for p in raiz.properties} >= {"CanQuit", "CanRaise", "Identity", "DesktopEntry"}
    reproductor = mpris.Reproductor(Puente()).introspect()
    firmas = {p.name: p.signature for p in reproductor.properties}
    assert firmas["Metadata"] == "a{sv}" and firmas["Position"] == "x" and firmas["PlaybackStatus"] == "s"
    assert {m.name for m in reproductor.methods} >= {"PlayPause", "Next", "Previous", "Seek", "SetPosition"}
    assert [(s.name, [a.signature for a in s.args]) for s in reproductor.signals] == [("Seeked", ["x"])]


def test_mpris_metodos_mandan_ordenes(tmp_path):
    puente = Puente(modelo=EstadoMusica(carpeta=tmp_path))
    mandadas: list[str] = []
    puente.orden = lambda nombre, **extra: mandadas.append(nombre) or True
    r = mpris.Reproductor(puente)
    r.PlayPause()                     # sin canción: nada
    puente.modelo.recibir(estado())
    r.PlayPause(); r.Next(); r.Previous(); r.Play(); r.Seek(5_000_000)
    assert mandadas == ["alternar", "siguiente", "anterior"]   # Play no se permite sonando; Seek tampoco
    md = mpris.variantes(puente.modelo)
    assert md["xesam:artist"].signature == "as" and md["xesam:artist"].value == ["Mon Laferte"]


def test_cli_estado(tmp_path, monkeypatch, capsys):
    ruta = tmp_path / "estado.json"
    monkeypatch.setattr(musica, "ARCHIVO_ESTADO", ruta)
    assert cli.main(["vm", "musica", "estado"]) == 0
    assert "no ha corrido" in capsys.readouterr().out
    p, _, _ = puente_de_prueba(tmp_path, tmp_path / "x.sock")
    p.modelo.recibir(estado(estado="pausa", posicion_ms=83000, puede=PUEDE_PAUSA))
    p._guardar()
    assert cli.main(["vm", "musica", "estado"]) == 0
    salida = capsys.readouterr().out
    assert "Suena: Amárrame (feat. Juanes) · Mon Laferte · La Trenza (Deluxe)" in salida
    assert "Estado: pausa, 1:23 de 3:27" in salida
    assert cli.main(["vm", "musica", "otra"]) == 1
    assert cli.main(["vm", "apagar", "on"]) == 1


def test_un_solo_puente(tmp_path, monkeypatch, capsys):
    ruta = tmp_path / "musica.lock"
    monkeypatch.setattr(musica, "CANDADO", ruta)
    with musica.candado(ruta):
        with pytest.raises(musica.PuenteOcupado):
            with musica.candado(ruta):
                pass
        # Si el candado no funcionara, correría el puente y se quedaría esperando la VM: que falle ya
        monkeypatch.setattr(musica.asyncio, "run", lambda c: (c.close(), pytest.fail("tomó el candado")))
        assert cli.main(["vm", "musica"]) == musica.OCUPADO   # el servicio no se reinicia con este código
        assert "Ya hay un puente" in capsys.readouterr().err
    with musica.candado(ruta):   # al soltarlo, se puede otra vez
        pass
