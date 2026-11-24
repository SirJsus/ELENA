"""El centro (sin HTTP) y el servidor (aiohttp, con un cliente de pruebas).

Nada toca el equipo: el reproductor es un `Reproductores` con un cliente falso, el grafo sale de la
foto de `pw-dump` de tests/datos y `Sistema` es uno falso que anota lo que se le pide.
"""

import asyncio
import json

import pytest
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer, unused_port

from elena.modelo import ErrorElena, escalar
from elena.monitor import Lote
from elena.remoto import centro as centro_mod
from elena.remoto.acceso import COOKIE, Hosts, Token
from elena.remoto.centro import Centro
from elena.remoto.reproductores import PREFERIDO, ORDENES, Reproductores
from elena.remoto.servidor import crear_app


class Reloj:
    def __init__(self) -> None:
        self.t = 1_762_700_000.0

    def __call__(self) -> float:
        return self.t


class ClienteFalso:
    def __init__(self, reloj) -> None:
        self.modelo = Reproductores(reloj=reloj)
        self.al_cambiar = lambda: None
        self.ordenes: list[tuple[str, str | None]] = []

    async def orden(self, accion, reproductor=None):
        if accion not in ORDENES:
            raise ErrorElena(f"No conozco la orden «{accion}».")
        if self.modelo.actual is None:
            raise ErrorElena("No hay ningún reproductor abierto.")
        self.ordenes.append((accion, reproductor))

    def elegir(self, reproductor):
        self.modelo.elegir(reproductor)
        self.al_cambiar()

    async def correr(self):
        await asyncio.Event().wait()


class SistemaFalso:
    def __init__(self) -> None:
        self.llamadas: list[tuple] = []
        self.corre = False
        self.servicio = True

    def volumen(self, nodo, volumenes):
        self.llamadas.append(("volumen", nodo.nombre, tuple(round(v, 6) for v in volumenes)))

    def silencio(self, nodo, silenciar):
        self.llamadas.append(("silencio", nodo.nombre, silenciar))

    def vm_corre(self):
        return self.corre

    def vm_servicio(self):
        return self.servicio

    def encender_vm(self):
        if not self.servicio:
            raise ErrorElena("Falta el servicio de la VM: mise run vm:lanzador instalar")
        self.llamadas.append(("encender_vm",))

    def apagar_vm(self):
        self.llamadas.append(("apagar_vm",))
        self.corre = False

    def suspender(self):
        self.llamadas.append(("suspender",))

    def apagar_pc(self):
        self.llamadas.append(("apagar_pc",))


def metadata(titulo="Instant Crush", largo=337_000_000, trackid="/elena/1"):
    return {"mpris:trackid": trackid, "xesam:title": titulo, "xesam:artist": ["Daft Punk"],
            "xesam:album": "Random Access Memories", "mpris:length": largo}


def props(estado="Playing", **md):
    return {"PlaybackStatus": estado, "Metadata": metadata(**md), "CanPlay": True, "CanPause": True,
            "CanGoNext": True, "CanGoPrevious": True, "CanSeek": False, "CanControl": True}


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def centro(tmp_path, reloj, dump):
    cliente = ClienteFalso(reloj)
    sistema = SistemaFalso()

    async def fuente():
        yield Lote(dump, reinicio=True)

    c = Centro(cliente, sistema=sistema, reloj=reloj, carpeta=tmp_path / "remoto", fuente=fuente)
    c.grafo.aplicar(dump, completo=True)
    c.grafo_listo.set()
    return c


def vols(sistema):
    return [x[2] for x in sistema.llamadas if x[0] == "volumen"]


# ---------- el centro ----------

async def test_vista(centro):
    centro.cliente.modelo.alta(PREFERIDO, props(), identidad="Apple Music (ELENA)", posicion_us=60_000_000)
    v = centro.vista()
    assert v["t"] == "estado" and v["ahora"] == int(centro.reloj() * 1000)
    assert v["musica"]["titulo"] == "Instant Crush" and v["musica"]["posicion_ms"] == 60_000
    assert v["volumen"] == {"bus": "elena_musica", "pct": 100.0, "mudo": False}
    assert v["temporizador"] is None and v["vm"] == "apagada" and v["vm_servicio"] is True


async def test_entorno(centro, reloj):
    centro.cliente.modelo.alta(PREFERIDO, props(), posicion_us=37_000_000)
    reloj.t += 10
    e = centro.entorno()
    assert e.musica == "Playing" and e.pista == "/elena/1"
    assert e.restante_ms == 337_000 - 47_000 and len(e.volumen) == 6 and e.vm is False


async def test_temporizador_completo(centro, reloj, tmp_path):
    centro.cliente.modelo.alta(PREFERIDO, props())
    centro.programar("pausar", minutos=1)
    assert json.loads(centro.archivo.read_text())["fase"] == "contando"
    reloj.t += 30
    while centro.temporizador.programa and centro.temporizador.programa.fase == "desvaneciendo" \
            or reloj.t < 1_762_700_030.5:
        await centro.paso_temporizador()
        reloj.t += 0.5
    puestos = vols(centro.sistema)
    assert puestos[0] == (1.0,) * 6 and puestos[-1] == (0.0,) * 6
    assert centro.cliente.ordenes == [("pausa", None)]
    assert json.loads(centro.archivo.read_text())["fase"] == "pausando"
    centro.cliente.modelo.cambio(PREFERIDO, {"PlaybackStatus": "Paused"})
    await centro.paso_temporizador()
    reloj.t += 1.5
    await centro.paso_temporizador()
    assert vols(centro.sistema)[-1] == (1.0,) * 6            # volumen restaurado
    assert centro.temporizador.programa is None and not centro.archivo.exists()


async def test_temporizador_apaga_la_vm_y_la_pc(centro, reloj):
    centro.sistema.corre = centro.vm_corre = True
    centro.programar("apagar", minutos=1)
    reloj.t += 60
    await centro.paso_temporizador()          # sin música: directo a apagar la VM
    assert centro.vista_vm() == "apagando"
    await asyncio.sleep(0.05)                 # la tarea de apagado (SistemaFalso termina enseguida)
    assert ("apagar_vm",) in centro.sistema.llamadas and centro.vista_vm() == "apagada"
    await centro.paso_temporizador()
    assert centro.sistema.llamadas[-1] == ("apagar_pc",) and centro.temporizador.programa is None


async def test_retoma_y_restaura_al_arrancar(centro, reloj):
    centro.cliente.modelo.alta(PREFERIDO, props())
    centro.programar("pausar", minutos=1)
    reloj.t += 45
    await centro.paso_temporizador()          # a media bajada…
    datos = centro.temporizador.a_dict()
    nuevo = Centro(centro.cliente, sistema=SistemaFalso(), reloj=reloj,
                   carpeta=centro.archivo.parent, fuente=centro.fuente)
    nuevo._pendientes = nuevo.temporizador.cargar(datos)   # …y se reinició el servicio
    await nuevo._seguir_grafo()               # con el primer lote del grafo, regresa el volumen
    assert vols(nuevo.sistema) == [(1.0,) * 6] and nuevo.temporizador.programa is None


async def test_volumen_y_silencio(centro):
    await centro.volumen(50)
    assert vols(centro.sistema) == [tuple(round(v, 6) for v in escalar((1.0,) * 6, 50))]
    await centro.silencio(None)
    assert centro.sistema.llamadas[-1] == ("silencio", "elena_musica", None)
    with pytest.raises(ErrorElena):
        await centro.volumen(200)


async def test_vm(centro, reloj):
    await centro.vm("encender")
    assert centro.vista_vm() == "encendiendo"
    reloj.t += centro_mod.ENCENDIENDO + 1
    assert centro.vista_vm() == "apagada"     # QEMU nunca apareció
    centro.sistema.servicio = False
    centro._vm_accion = None
    with pytest.raises(ErrorElena, match="vm:lanzador"):
        await centro.vm("encender")
    await centro.vm("apagar")                 # ya apagada: nada
    assert ("apagar_vm",) not in centro.sistema.llamadas
    with pytest.raises(ErrorElena):
        await centro.vm("reiniciar")


async def test_pc_pausa_antes(centro, monkeypatch):
    monkeypatch.setattr(centro_mod, "RETRASO_APAGADO", 0)
    centro.cliente.modelo.alta(PREFERIDO, props())
    with pytest.raises(ErrorElena, match="confirmar"):
        await centro.pc("apagar", None)
    assert await centro.pc("suspender", True) == "Suspendiendo la PC."
    await asyncio.sleep(0.05)
    assert centro.cliente.ordenes == [("pausa", None)] and centro.sistema.llamadas[-1] == ("suspender",)


# ---------- el servidor ----------

@pytest.fixture
async def http(centro, tmp_path):
    puerto = unused_port()
    token = Token(tmp_path / "token")
    token.nuevo()
    app = crear_app(centro, token=token, hosts=Hosts(puerto, leer_ips=lambda: {"127.0.0.1"}), puerto=puerto)
    async with TestClient(TestServer(app, host="127.0.0.1", port=puerto)) as cliente:
        cliente.origen = f"http://127.0.0.1:{puerto}"
        cliente.token = token
        yield cliente


async def emparejar(http):
    r = await http.get(f"/?t={http.token.valor()}", allow_redirects=False)
    assert r.status == 303 and r.headers["Location"] == "/"
    assert COOKIE in r.cookies and r.cookies[COOKIE]["httponly"] and r.cookies[COOKIE]["samesite"] == "Strict"


def post(http, ruta, datos, metodo="POST"):
    return http.request(metodo, ruta, json=datos, headers={"Origin": http.origen})


async def test_pagina_y_cabeceras(http):
    r = await http.get("/")
    assert r.status == 200 and "ELENA" in await r.text()
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "no-referrer"
    for ruta in ("/app.js", "/app.css", "/manifest.webmanifest", "/icono.svg"):
        assert (await http.get(ruta)).status == 200
    assert (await http.get("/../pyproject.toml")).status == 404


async def test_host_ajeno(http):
    r = await http.get("/", headers={"Host": "elena.ejemplo.com"})
    assert r.status == 421


async def test_emparejar(http):
    r = await http.get("/api/estado")
    assert r.status == 401 and "QR" in (await r.json())["error"]
    assert (await http.get("/?t=equivocado", allow_redirects=False)).status == 403
    await emparejar(http)
    r = await http.get("/api/estado")
    assert r.status == 200 and (await r.json())["volumen"]["pct"] == 100.0
    http.token.nuevo()                        # elena remoto token nuevo: la cookie vieja ya no sirve
    assert (await http.get("/api/estado")).status == 401


async def test_origen(http, centro):
    await emparejar(http)
    centro.cliente.modelo.alta(PREFERIDO, props())
    r = await http.post("/api/musica", json={"orden": "alternar"})
    assert r.status == 403
    r = await http.post("/api/musica", json={"orden": "alternar"}, headers={"Origin": "http://otro.sitio"})
    assert r.status == 403
    assert centro.cliente.ordenes == []


async def test_ordenes(http, centro):
    await emparejar(http)
    centro.cliente.modelo.alta(PREFERIDO, props())
    assert (await post(http, "/api/musica", {"orden": "alternar"})).status == 200
    assert (await post(http, "/api/musica", {"orden": "siguiente", "reproductor": "elena_applemusic"})).status == 200
    assert centro.cliente.ordenes == [("alternar", None), ("siguiente", "elena_applemusic")]
    r = await post(http, "/api/musica", {"orden": "brincar"})
    assert r.status == 400 and "brincar" in (await r.json())["error"]
    r = await post(http, "/api/musica", {})
    assert r.status == 400
    assert (await post(http, "/api/volumen", {"pct": 50})).status == 200
    assert (await post(http, "/api/volumen", {"silencio": True})).status == 200
    assert centro.sistema.llamadas[-1] == ("silencio", "elena_musica", True)
    assert (await post(http, "/api/volumen", {"silencio": "sí"})).status == 400
    r = await http.post("/api/volumen", data="no es json", headers={"Origin": http.origen})
    assert r.status == 400


async def test_temporizador_por_http(http, centro):
    await emparejar(http)
    r = await post(http, "/api/temporizador", {"minutos": 30, "nivel": "suspender"})
    assert r.status == 200 and (await r.json())["temporizador"]["nivel"] == "suspender"
    r = await post(http, "/api/temporizador/mas", {"minutos": 10})
    assert (await r.json())["temporizador"]["vence"] == int((centro.reloj() + 2400) * 1000)
    assert (await post(http, "/api/temporizador", {"minutos": 30, "nivel": "x"})).status == 400
    r = await post(http, "/api/temporizador", {"fin_cancion": True})
    assert r.status == 400                    # nada sonando
    assert (await post(http, "/api/temporizador", None, metodo="DELETE")).status == 200
    assert centro.temporizador.programa is None


async def test_vm_y_pc_por_http(http, centro, monkeypatch):
    monkeypatch.setattr(centro_mod, "RETRASO_APAGADO", 0)
    await emparejar(http)
    assert (await post(http, "/api/vm", {"accion": "encender"})).status == 200
    assert ("encender_vm",) in centro.sistema.llamadas
    r = await post(http, "/api/sistema", {"accion": "apagar"})
    assert r.status == 400 and "confirmar" in (await r.json())["error"]
    r = await post(http, "/api/sistema", {"accion": "apagar", "confirmar": True})
    assert r.status == 200
    await asyncio.sleep(0.05)
    assert centro.sistema.llamadas[-1] == ("apagar_pc",)


async def test_caratula(http, centro, tmp_path):
    await emparejar(http)
    assert (await http.get("/caratula")).status == 404
    jpg = tmp_path / "c.jpg"
    jpg.write_bytes(b"\xff\xd8\xff falso")
    p = props()
    p["Metadata"]["mpris:artUrl"] = jpg.as_uri()
    centro.cliente.modelo.alta(PREFERIDO, p)
    r = await http.get("/caratula")
    assert r.status == 200 and await r.read() == jpg.read_bytes()


async def test_websocket(http, centro):
    with pytest.raises(WSServerHandshakeError):          # sin cookie, el WebSocket no abre
        async with http.ws_connect("/api/ws", headers={"Origin": http.origen}):
            pass
    await emparejar(http)
    with pytest.raises(WSServerHandshakeError):
        async with http.ws_connect("/api/ws"):   # sin Origin
            pass
    async with http.ws_connect("/api/ws", headers={"Origin": http.origen}) as ws:
        primero = await ws.receive_json(timeout=2)
        assert primero["t"] == "estado" and primero["musica"] is None
        centro.cliente.modelo.alta(PREFERIDO, props())
        centro.avisar()
        segundo = await ws.receive_json(timeout=2)
        assert segundo["musica"]["titulo"] == "Instant Crush"
        centro.aviso("Hola")
        assert (await ws.receive_json(timeout=2)) == {"t": "aviso", "texto": "Hola"}
    await asyncio.sleep(0.05)
    assert centro.colas == set()


# ---------- acceso y CLI ----------

def test_hosts_relee_si_cambia_la_ip():
    ips = {"192.0.2.5"}
    reloj = Reloj()
    h = Hosts(8765, leer_ips=lambda: set(ips), reloj=reloj)
    assert h.permitido("192.0.2.5:8765") and h.permitido("LOCALHOST:8765")
    assert not h.permitido("192.0.2.5:9999") and not h.permitido(None)
    ips.add("192.0.2.77")                 # el DHCP le dio otra IP
    assert not h.permitido("192.0.2.77:8765")   # todavía en caché
    reloj.t += 31
    assert h.permitido("192.0.2.77:8765")


def test_token(tmp_path):
    t = Token(tmp_path / "remoto" / "token")
    assert t.valor(crear=False) is None and not t.valido("x")
    valor = t.valor()
    assert len(valor) >= 40 and t.valido(valor) and not t.valido(valor + "x") and not t.valido(None)
    assert (tmp_path / "remoto" / "token").stat().st_mode & 0o777 == 0o600


def test_cli_qr_y_token(tmp_path, monkeypatch, capsys):
    from elena import cli
    from elena.remoto import acceso
    monkeypatch.setattr(acceso, "CARPETA", tmp_path)
    monkeypatch.setattr(acceso, "ip_local", lambda: "192.0.2.5")
    assert cli.main(["remoto", "qr"]) == 0
    salida = capsys.readouterr().out
    primero = acceso.Token().valor()
    assert f"http://192.0.2.5:{acceso.PUERTO}/?t={primero}" in salida and "█" in salida
    assert cli.main(["remoto", "token", "nuevo"]) == 0
    assert acceso.Token().valor() != primero and "volver a escanear" in capsys.readouterr().out
    assert cli.main(["remoto", "token"]) == 1
    assert cli.main(["remoto", "qr", "nuevo"]) == 1
