"""El vigilante de reglas: mueve cada app nueva al bus de su regla (`elena vigilar`).

Corre como servicio de usuario de systemd (`elena-vigilante`, lo instala scripts/vigilante.sh).
Las reglas `bus = "visual"` mueven grabaciones (visualizadores) al monitor de elena_visual.
Solo actúa sobre las apps **nuevas** (y una vez sobre las que ya estaban al arrancar): si después
mueves una app a mano, no se la regresa. Relee local/reglas.toml cuando cambia.

Una app aparece en el grafo antes de que WirePlumber la conecte; se mueve en cuanto existe. Si
pactl todavía no la conoce, se reintenta en los siguientes cambios del grafo (hasta 5 veces).

También cuida el tope de volumen de Música y Juegos (`tope.py`): si uno sube desde fuera
(pavucontrol, Noctalia, las teclas de volumen) más allá de tu máximo, lo regresa al tope.
Si al arrancar ya estaba arriba, solo lo avisa: no lo baja de golpe. Y corre la compensación
estéreo / 5.1 (`estereo.py`): baja Música mientras suena estéreo, y Sistema siempre.

Y mantiene «Solo audífonos» (`audifonos.py`): con el modo prendido, si un reinicio de PipeWire regresa
las salidas de los buses a las bocinas, las vuelve a mover; si Chat cambia de destino, elena_audifonos
lo sigue. No repite una orden en 3 s, y el camino (binaural o directo) lo pone una vez por nodo.

Y deja los audífonos (el sink al que sale Chat) siempre al 100 %, como el físico: el volumen se maneja
en los buses Audífonos y Chat. En algunos audífonos inalámbricos el volumen es por hardware y la perilla
solo lo baja (o baja sola de vez en cuando): si baja, lo regresa.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from elena import acciones, audifonos, reglas, tope
from elena.formato import nombre_salida
from elena.modelo import ErrorElena, Grafo, Nodo, Tipo, buscar_sink, buscar_visual, escalar
from elena.monitor import Lote, seguir

REINTENTOS = 5
REPETIR_AUDIFONOS = 3.0   # s antes de repetir la misma orden de «Solo audífonos»


def log(texto: str) -> None:
    print(texto, flush=True)  # en el servicio, va al journal (journalctl --user -u elena-vigilante)


class Vigilante:
    def __init__(self, ruta: Path | None = None, ejecutar: Callable[[list[str]], str] = acciones.ejecutar,
                 escribir: Callable[[str], None] = log) -> None:
        self.ruta = ruta or reglas.RUTA
        self.ejecutar = ejecutar
        self.escribir = escribir
        self.grafo = Grafo()
        self.reglas: list[reglas.Regla] = []
        self._mtime: float | None = None
        self._vistas: set[int] = set()
        self._intentos: dict[int, int] = {}
        self._volumen_bus: dict[str, float] = {}   # el último % visto de cada bus con tope
        self.audifonos = audifonos.Lector()
        self._ordenes_audifonos: dict[tuple[str, ...], float] = {}   # orden → cuándo se mandó

    def _recargar(self) -> None:
        try:
            mtime = self.ruta.stat().st_mtime if self.ruta.exists() else None
        except OSError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self.reglas = reglas.cargar(self.ruta)
            self.escribir(f"Reglas: {len(self.reglas)} ({self.ruta})")
        except ErrorElena as e:
            self.escribir(f"ERROR: {e} Sigo con las reglas anteriores.")

    def _destino(self, app: Nodo, regla: reglas.Regla) -> tuple[Nodo, bool, list[str]]:
        """(sink, ¿ya está ahí?, comando para moverla). Las grabaciones van al monitor de elena_visual."""
        if app.tipo == Tipo.GRABACION:
            sink = buscar_visual(self.grafo)
            return sink, sink in self.grafo.origenes(app), acciones.cmd_mover_grabacion(app, sink)
        sink = buscar_sink(self.grafo, regla.bus)
        return sink, sink in self.grafo.sinks_de(app), acciones.cmd_mover(app, sink)

    def _cuidar_topes(self) -> None:
        """Regresa al tope lo que subió desde fuera. Lo que ELENA pone ya viene acotado."""
        for nombre in tope.BUSES_TOPE:
            n = self.grafo.nodo(nombre)
            if n is None or not n.volumenes or n.volumen_pct is None:
                continue
            actual, previo = n.volumen_pct, self._volumen_bus.get(nombre)
            limite = tope.limite_pct(self.grafo, n)
            if limite is not None and previo is None and actual > limite + tope.TOLERANCIA:
                self.escribir(f"{tope.ETIQUETA[nombre]} ya está en {actual:.0f} %, arriba de tu máximo ({limite:.0f} %): "
                              "no lo bajo; no subirá más.")
            if limite is None or previo is None:
                self._volumen_bus[nombre] = actual
                continue
            a = tope.acotar(self.grafo, n, actual, actual=previo)
            if a.aviso is None:
                self._volumen_bus[nombre] = actual
                continue
            try:
                self.ejecutar(acciones.cmd_volumen(n, escalar(n.volumenes, a.pct)))
                self.escribir(f"{tope.ETIQUETA[nombre]} subió a {actual:.0f} % desde fuera: regresa a {a.pct:.0f} % "
                              f"(tu máximo es {limite:.0f} %).")
                self._volumen_bus[nombre] = a.pct
            except ErrorElena as e:
                self.escribir(f"{tope.ETIQUETA[nombre]}: no pude regresarlo al tope ({e}).")

    def _cuidar_audifonos(self) -> None:
        """«Solo audífonos»: lo que falte para que el grafo quede como dice el estado."""
        ahora = time.monotonic()
        for paso in audifonos.plan(self.grafo, self.audifonos.leer()):
            clave = tuple(paso.cmd)
            cuando = self._ordenes_audifonos.get(clave)
            # El camino (pw-cli, con el id del nodo) va una vez por nodo; mover, cada 3 s como mucho
            if cuando is not None and (paso.cmd[0] == "pw-cli" or ahora - cuando < REPETIR_AUDIFONOS):
                continue
            self._ordenes_audifonos[clave] = ahora
            try:
                self.ejecutar(paso.cmd)
                self.escribir(f"Solo audífonos: {paso.texto}")
            except ErrorElena as e:
                self.escribir(f"Solo audífonos: {paso.texto}: no se pudo ({e})")

    def _cuidar_volumen_audifonos(self) -> None:
        """El sink al que sale Chat (los audífonos), de vuelta al 100 % si algo lo bajó."""
        if (chat := self.grafo.nodo("elena_chat.salida")) is None:
            return
        ahora = time.monotonic()
        for sink in self.grafo.sinks_de(chat):
            if not sink.volumenes or all(abs(v - 1.0) < 1e-4 for v in sink.volumenes):
                continue
            cmd = acciones.cmd_volumen(sink, tuple(1.0 for _ in sink.volumenes))
            clave = (*cmd, *(f"{v:.6f}" for v in sink.volumenes))   # el mismo cambio, una vez por segundo
            if ahora - self._ordenes_audifonos.get(clave, -math.inf) < 1.0:
                continue
            self._ordenes_audifonos[clave] = ahora
            try:
                self.ejecutar(cmd)
                self.escribir(f"{sink.visible} bajó a {sink.volumen_pct:.0f} %: regresa al 100 % "
                              "(el volumen va en los buses Audífonos y Chat).")
            except ErrorElena as e:
                self.escribir(f"{sink.visible}: no pude regresarlo al 100 % ({e}).")

    def aplicar(self, lote: Lote) -> None:
        self.grafo.aplicar(lote.objetos, completo=lote.reinicio)
        self._recargar()
        self._cuidar_topes()
        self._cuidar_audifonos()
        self._cuidar_volumen_audifonos()
        apps = {a.id: a for a in self.grafo.de_tipo(Tipo.APP, Tipo.GRABACION)}
        self._vistas &= set(apps)
        for ident, app in apps.items():
            if ident in self._vistas:
                continue
            regla = reglas.destino(self.reglas, app)
            if regla is None:
                self._vistas.add(ident)
                continue
            try:
                sink, ya_esta, cmd = self._destino(app, regla)
            except ErrorElena as e:
                self.escribir(f"{app.visible}: {e}")
                self._vistas.add(ident)
                continue
            if ya_esta:
                self._vistas.add(ident)
                continue
            try:
                self.ejecutar(cmd)
                self.escribir(f"{app.visible} (serial {app.serial}) → {nombre_salida(sink)}  [{regla.descripcion}]")
                self._vistas.add(ident)
            except ErrorElena as e:
                n = self._intentos[ident] = self._intentos.get(ident, 0) + 1
                if n >= REINTENTOS:
                    self.escribir(f"{app.visible}: no la pude mover a {regla.bus} ({e})")
                    self._vistas.add(ident)

    async def correr(self, fuente: AsyncIterator[Lote] | None = None, una_vez: bool = False,
                     compensar: bool | None = None) -> None:
        """`compensar`: la compensación estéreo; por omisión, solo en el servicio (sin `fuente` ni `una_vez`)."""
        self.escribir("Vigilante de ELENA: aplicando reglas fijas a cada app nueva.")
        if compensar is None:
            compensar = fuente is None and not una_vez
        tarea = None
        try:
            async for lote in fuente if fuente is not None else seguir():
                self.aplicar(lote)
                if una_vez and lote.reinicio:
                    return
                if compensar and tarea is None:
                    from elena.estereo import Compensacion
                    tarea = asyncio.create_task(Compensacion(lambda: self.grafo, self.escribir).correr())
        finally:
            if tarea is not None:
                tarea.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tarea


def corriendo() -> bool:
    """¿Hay un `elena vigilar` vivo? (para el aviso de la consola)"""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if len(argv) >= 2 and any(a.endswith(b"elena") for a in argv[:3]) and b"vigilar" in argv:
            return True
    return False

