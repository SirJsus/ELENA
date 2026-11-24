"""El servidor HTTP del control remoto: aiohttp, solo IPv4, en la LAN.

Rutas (todo lo de /api y /caratula exige la cookie del token; ver acceso.py):

  GET  /?t=<token>            empareja: pone la cookie y redirige a /
  GET  /                      la página (sin cookie, la página pide abrir el enlace del QR)
  GET  /api/estado            el estado completo, una vez (también sirve para saber si hay cookie)
  GET  /api/ws                WebSocket: el estado completo al conectar y en cada cambio
  GET  /caratula              la carátula del reproductor actual
  POST /api/musica            {"orden": …, "reproductor": …?} o {"elegir": id | null}
  POST /api/volumen           {"pct": 62} o {"silencio": true | false | null}
  POST /api/temporizador      {"minutos": 30 | "fin_cancion": true, "nivel": …}
  POST /api/temporizador/mas  {"minutos": 10}
  DELETE /api/temporizador
  POST /api/vm                {"accion": "encender" | "apagar"}
  POST /api/sistema           {"accion": "suspender" | "apagar", "confirmar": true}

Los errores se responden con {"error": "…"} y 4xx. Guía: docs/guias/control-remoto.md
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from aiohttp import WSMsgType, web

from elena.modelo import ErrorElena
from elena.remoto.acceso import COOKIE, PUERTO, Hosts, Token, ip_local
from elena.remoto.centro import Centro, log

ESTATICO = Path(__file__).parent / "estatico"
ARCHIVOS = {   # ruta → (archivo, tipo)
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/app.css": ("app.css", "text/css"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icono.svg": ("icono.svg", "image/svg+xml"),
}
UN_ANO = 365 * 24 * 3600
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
       "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

CENTRO = web.AppKey("centro", Centro)
TOKEN = web.AppKey("token", Token)
HOSTS = web.AppKey("hosts", Hosts)


def _error(estado: int, texto: str) -> web.Response:
    return web.json_response({"error": texto}, status=estado)


@web.middleware
async def seguridad(request: web.Request, handler):
    if not request.app[HOSTS].permitido(request.headers.get("Host")):
        return web.Response(status=421, text="Host no permitido.\n")
    protegido = request.path.startswith(("/api/", "/caratula"))
    if protegido and not request.app[TOKEN].valido(request.cookies.get(COOKIE)):
        return _error(401, "Falta emparejar este teléfono: abre el enlace del QR (elena remoto qr).")
    if request.method not in ("GET", "HEAD") or request.path == "/api/ws":
        if request.headers.get("Origin") != f"http://{request.host}":
            return _error(403, "Origen no permitido.")
    try:
        respuesta = await handler(request)
    except ErrorElena as e:
        respuesta = _error(400, str(e))
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
        respuesta = _error(400, f"Petición inválida: {e}")
    if not isinstance(respuesta, web.WebSocketResponse):
        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
        respuesta.headers.setdefault("Content-Security-Policy", CSP)
    return respuesta


# ---------- página ----------

async def estatico(request: web.Request) -> web.StreamResponse:
    if request.path == "/" and "t" in request.query:
        token = request.app[TOKEN]
        if not token.valido(request.query["t"]):
            return web.Response(status=403, text="Enlace vencido: genera otro con `elena remoto qr`.\n")
        respuesta = web.Response(status=303, headers={"Location": "/"})
        respuesta.set_cookie(COOKIE, request.query["t"], max_age=UN_ANO, httponly=True, samesite="Strict", path="/")
        log(f"Teléfono emparejado: {request.remote}")
        return respuesta
    archivo, tipo = ARCHIVOS[request.path]
    return web.FileResponse(ESTATICO / archivo, headers={"Content-Type": tipo, "Cache-Control": "no-cache"})


async def estado(request: web.Request) -> web.Response:
    return web.json_response(request.app[CENTRO].vista())


async def caratula(request: web.Request) -> web.StreamResponse:
    ruta = request.app[CENTRO].cliente.modelo.ruta_caratula()
    if ruta is None:
        return _error(404, "Sin carátula.")
    return web.FileResponse(ruta, headers={"Cache-Control": "private, max-age=86400"})


# ---------- WebSocket ----------

async def ws(request: web.Request) -> web.WebSocketResponse:
    centro = request.app[CENTRO]
    socket = web.WebSocketResponse(heartbeat=25)
    await socket.prepare(request)
    cola = centro.suscribir()

    async def enviar() -> None:
        await socket.send_json(centro.vista())
        while True:
            await socket.send_json(await cola.get())

    tarea = asyncio.create_task(enviar())
    try:
        async for mensaje in socket:   # el teléfono no manda nada; solo se espera el cierre
            if mensaje.type == WSMsgType.ERROR:
                break
    finally:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await tarea
        centro.desuscribir(cola)
    return socket


# ---------- órdenes ----------

async def _cuerpo(request: web.Request) -> dict:
    datos = await request.json()
    if not isinstance(datos, dict):
        raise ErrorElena("Se esperaba un objeto JSON.")
    return datos


def _ok(**extra) -> web.Response:
    return web.json_response({"ok": True, **extra})


async def musica(request: web.Request) -> web.Response:
    centro, datos = request.app[CENTRO], await _cuerpo(request)
    if "elegir" in datos:
        centro.elegir(datos["elegir"])
    else:
        await centro.musica(str(datos["orden"]), datos.get("reproductor"))
    return _ok()


async def volumen(request: web.Request) -> web.Response:
    centro, datos = request.app[CENTRO], await _cuerpo(request)
    if "silencio" in datos:
        if datos["silencio"] not in (True, False, None):
            raise ErrorElena("«silencio» es true, false o null (alternar).")
        await centro.silencio(datos["silencio"])
    else:
        await centro.volumen(float(datos["pct"]))
    return _ok()


async def temporizador(request: web.Request) -> web.Response:
    centro = request.app[CENTRO]
    if request.method == "DELETE":
        await centro.cancelar()
        return _ok()
    datos = await _cuerpo(request)
    nivel = str(datos.get("nivel", "pausar"))
    if datos.get("fin_cancion"):
        centro.programar(nivel, fin_cancion=True)
    else:
        centro.programar(nivel, minutos=float(datos["minutos"]))
    return _ok(temporizador=centro.temporizador.vista())


async def temporizador_mas(request: web.Request) -> web.Response:
    centro, datos = request.app[CENTRO], await _cuerpo(request)
    centro.mas(float(datos.get("minutos", 10)))
    return _ok(temporizador=centro.temporizador.vista())


async def vm_orden(request: web.Request) -> web.Response:
    centro, datos = request.app[CENTRO], await _cuerpo(request)
    await centro.vm(str(datos["accion"]))
    return _ok()


async def sistema(request: web.Request) -> web.Response:
    centro, datos = request.app[CENTRO], await _cuerpo(request)
    log(f"PC: «{datos.get('accion')}» desde {request.remote}")
    texto = await centro.pc(str(datos["accion"]), datos.get("confirmar"))
    return _ok(texto=texto)


def crear_app(centro: Centro, token: Token | None = None, hosts: Hosts | None = None,
              puerto: int = PUERTO) -> web.Application:
    app = web.Application(middlewares=[seguridad], client_max_size=64 * 1024)
    app[CENTRO] = centro
    app[TOKEN] = token or Token()
    app[HOSTS] = hosts or Hosts(puerto)
    for ruta in ARCHIVOS:
        app.router.add_get(ruta, estatico)
    app.router.add_get("/caratula", caratula)
    app.router.add_get("/api/estado", estado)
    app.router.add_get("/api/ws", ws)
    app.router.add_post("/api/musica", musica)
    app.router.add_post("/api/volumen", volumen)
    app.router.add_post("/api/temporizador", temporizador)
    app.router.add_delete("/api/temporizador", temporizador)
    app.router.add_post("/api/temporizador/mas", temporizador_mas)
    app.router.add_post("/api/vm", vm_orden)
    app.router.add_post("/api/sistema", sistema)
    return app


async def servir(puerto: int = PUERTO, direccion: str = "0.0.0.0") -> None:
    from elena.remoto.reproductores import ClienteMpris

    cliente = ClienteMpris()
    centro = Centro(cliente)
    token = Token()
    token.valor()   # lo crea si no existe
    runner = web.AppRunner(crear_app(centro, token, puerto=puerto), access_log=None,
                           handle_signals=True)
    await runner.setup()
    sitio = web.TCPSite(runner, direccion, puerto)
    try:
        await sitio.start()
    except OSError as e:
        await runner.cleanup()
        raise ErrorElena(f"No puedo escuchar en {direccion}:{puerto}: {e.strerror or e}.") from e
    try:
        ip = ip_local()
    except ErrorElena:
        ip = direccion
    log(f"Control remoto de ELENA en http://{ip}:{puerto}/ (empareja con: elena remoto qr)")
    try:
        await cliente.conectar()
        await centro.correr()
    finally:
        cliente.cerrar()
        await runner.cleanup()


def correr(puerto: int = PUERTO) -> None:
    try:
        asyncio.run(servir(puerto))
    except KeyboardInterrupt:
        pass
