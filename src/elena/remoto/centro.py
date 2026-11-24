"""El centro del control remoto: junta todo lo que ve y hace el teléfono.

- **Música:** `ClienteMpris` (reproductores.py).
- **Volumen:** el bus `elena_musica` en el `Grafo`, que sigue `pw-dump --monitor`; los cambios van
  por pactl (`acciones.cmd_volumen`, un valor por canal con `modelo.escalar`).
- **Temporizador:** el modelo puro (temporizador.py), con un paso cada 0.5 s mientras hay uno.
- **VM:** `systemctl --user start|stop elena-vm` (el apagado de Windows por ACPI) y `vm.pid()`.
- **PC:** `systemctl suspend|poweroff` (logind, sin sudo).

Cada cambio se avisa (`avisar`) y, agrupado en 50 ms, llega a los teléfonos conectados como el
estado completo. `Sistema` es lo que toca el equipo de verdad; las pruebas usan uno falso.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from elena import acciones, tope, vm
from elena.modelo import ErrorElena, Grafo, Nodo, escalar
from elena.monitor import Lote, seguir
from elena.remoto.acceso import CARPETA
from elena.remoto.reproductores import ClienteMpris
from elena.remoto.temporizador import Accion, Entorno, Temporizador

BUS = "elena_musica"
CADA_TEMPORIZADOR = 0.5
CADA_VM = 2.0
ENCENDIENDO = 120.0     # s que se muestra «encendiendo» si QEMU no aparece
AGRUPAR = 0.05          # s para juntar varios cambios en un solo mensaje
RETRASO_APAGADO = 1.0   # s entre responder al teléfono y suspender o apagar la PC


def log(texto: str) -> None:
    print(texto, flush=True)   # en el servicio, va al journal (journalctl --user -u elena-remoto)


class Sistema:
    """Lo que toca el equipo. Bloqueante: el centro lo llama con `asyncio.to_thread`."""

    def volumen(self, nodo: Nodo, volumenes: tuple[float, ...]) -> None:
        acciones.ejecutar(acciones.cmd_volumen(nodo, volumenes))

    def silencio(self, nodo: Nodo, silenciar: bool | None) -> None:
        acciones.ejecutar(acciones.cmd_silencio(nodo, silenciar))

    def vm_corre(self) -> bool:
        return vm.pid() is not None

    def vm_servicio(self) -> bool:
        return acciones.servicio_vm_instalado()

    def encender_vm(self) -> None:
        # Solo con el servicio: sin él, QEMU quedaría dentro del servicio elena-remoto
        if not self.vm_servicio():
            raise ErrorElena("Falta el servicio de la VM: mise run vm:lanzador instalar")
        acciones.ejecutar(["systemctl", "--user", "start", acciones.SERVICIO_VM])

    def apagar_vm(self) -> None:
        """Apaga Windows en orden y espera a que QEMU termine."""
        activo = subprocess_ok(["systemctl", "--user", "is-active", "--quiet", acciones.SERVICIO_VM])
        if activo:
            acciones.ejecutar(["systemctl", "--user", "stop", acciones.SERVICIO_VM])
        elif not vm.apagar():
            raise ErrorElena("Windows no terminó de apagarse.")

    def suspender(self) -> None:
        acciones.ejecutar(["systemctl", "suspend"])

    def apagar_pc(self) -> None:
        acciones.ejecutar(["systemctl", "poweroff"])


def subprocess_ok(cmd: list[str]) -> bool:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True).returncode == 0


class Centro:
    def __init__(self, cliente: ClienteMpris, sistema: Sistema | None = None,
                 reloj: Callable[[], float] = time.time, carpeta: Path = CARPETA,
                 fuente: Callable[[], AsyncIterator[Lote]] = seguir) -> None:
        self.cliente = cliente
        self.cliente.al_cambiar = self.avisar
        self.sistema = sistema or Sistema()
        self.reloj = reloj
        self.archivo = carpeta / "temporizador.json"
        self.fuente = fuente
        self.grafo = Grafo()
        self.grafo_listo = asyncio.Event()
        self.temporizador = Temporizador(reloj=reloj)
        self.colas: set[asyncio.Queue] = set()
        self.vm_corre = False
        self._vm_accion: str | None = None     # encendiendo | apagando
        self._vm_desde = 0.0
        self._vm_tarea: asyncio.Task | None = None
        self._guardado: dict | None = None
        self._ultimo_volumen: dict | None = None
        self._agrupando = False
        self._pendientes: list[Accion] = []
        self._tareas: set[asyncio.Task] = set()

    # ---------- lo que ve el teléfono ----------

    def bus(self) -> Nodo | None:
        return self.grafo.nodo(BUS)

    def vista_volumen(self) -> dict | None:
        n = self.bus()
        if n is None or not n.volumenes:
            return None
        return {"bus": BUS, "pct": round(n.volumen_pct, 1), "mudo": n.silencio}

    def vista_vm(self) -> str:
        if self._vm_accion == "apagando":
            return "apagando"
        if self.vm_corre:
            return "corriendo"
        if self._vm_accion == "encendiendo" and self.reloj() - self._vm_desde < ENCENDIENDO:
            return "encendiendo"
        return "apagada"

    def vista(self) -> dict:
        # `ahora`: la hora de la PC (epoch, ms). El teléfono mide el avance y la cuenta regresiva
        # desde que recibe el mensaje, sin comparar su reloj con el de la PC.
        return {"t": "estado", "ahora": int(self.reloj() * 1000), **self.cliente.modelo.vista(),
                "volumen": self.vista_volumen(),
                "temporizador": self.temporizador.vista(), "vm": self.vista_vm(),
                "vm_servicio": self.sistema.vm_servicio()}

    def suscribir(self) -> asyncio.Queue:
        cola: asyncio.Queue = asyncio.Queue(maxsize=50)
        self.colas.add(cola)
        return cola

    def desuscribir(self, cola: asyncio.Queue) -> None:
        self.colas.discard(cola)

    def _mandar(self, mensaje: dict) -> None:
        for cola in self.colas:
            with contextlib.suppress(asyncio.QueueFull):
                cola.put_nowait(mensaje)

    def avisar(self) -> None:
        """Algo cambió: en AGRUPAR s se manda el estado completo (una vez, aunque lleguen varios)."""
        if self._agrupando:
            return
        self._agrupando = True
        asyncio.get_running_loop().call_later(AGRUPAR, self._difundir)

    def _difundir(self) -> None:
        self._agrupando = False
        if self.colas:
            self._mandar(self.vista())

    def aviso(self, texto: str) -> None:
        log(texto)
        self._mandar({"t": "aviso", "texto": texto})

    # ---------- arranque y ciclos ----------

    async def correr(self) -> None:
        self._pendientes = self.temporizador.cargar(self._leer_guardado())
        await asyncio.gather(self._seguir_grafo(), self.cliente.correr(), self._vigilar_vm(),
                             self._ciclo_temporizador())

    def _leer_guardado(self) -> dict | None:
        try:
            return json.loads(self.archivo.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"ilegible": True}

    def _guardar(self) -> None:
        datos = self.temporizador.a_dict()
        if datos == self._guardado:
            return
        self._guardado = datos
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        if datos is None:
            self.archivo.unlink(missing_ok=True)
            return
        temporal = self.archivo.with_suffix(".nuevo")
        temporal.write_text(json.dumps(datos, ensure_ascii=False))
        temporal.replace(self.archivo)

    async def _seguir_grafo(self) -> None:
        async for lote in self.fuente():
            self.grafo.aplicar(lote.objetos, completo=lote.reinicio)
            if not self.grafo_listo.is_set():
                self.grafo_listo.set()
                pendientes, self._pendientes = self._pendientes, []
                await self._ejecutar(pendientes)   # p. ej., regresar un volumen que se quedó bajando
                self._guardar()
            volumen = self.vista_volumen()
            if volumen != self._ultimo_volumen:
                self._ultimo_volumen = volumen
                self.avisar()

    async def _vigilar_vm(self) -> None:
        while True:
            corre = await asyncio.to_thread(self.sistema.vm_corre)
            antes = self.vista_vm()
            self.vm_corre = corre
            if corre and self._vm_accion == "encendiendo":
                self._vm_accion = None
            if self.vista_vm() != antes:
                self.avisar()
            await asyncio.sleep(CADA_VM)

    async def _ciclo_temporizador(self) -> None:
        while True:
            await asyncio.sleep(CADA_TEMPORIZADOR)
            if self.temporizador.programa is None or not self.grafo_listo.is_set():
                continue
            await self.paso_temporizador()

    async def paso_temporizador(self) -> None:
        antes = self.temporizador.vista()
        await self._ejecutar(self.temporizador.paso(self.entorno()))
        self._guardar()
        if self.temporizador.vista() != antes:
            self.avisar()

    def entorno(self) -> Entorno:
        n = self.bus()
        r = self.cliente.modelo.actual
        pista = restante = None
        if r is not None:
            md = r.metadata
            pista = str(md.get("mpris:trackid") or (md.get("xesam:title"), md.get("xesam:artist")))
            duracion = md.get("mpris:length")
            if isinstance(duracion, int) and duracion > 0:
                restante = max(duracion - self.cliente.modelo.posicion_ahora(r), 0) // 1000
        return Entorno(volumen=n.volumenes if n else None, musica=r.estado if r else None,
                       pista=pista, restante_ms=restante, vm=self.vm_corre)

    async def _ejecutar(self, lista: list[Accion]) -> None:
        for a in lista:
            try:
                await self._accion(a)
            except ErrorElena as e:
                self.aviso(f"Temporizador: {e}")

    async def _accion(self, a: Accion) -> None:
        if a.tipo == "aviso":
            self.aviso(a.texto)
        elif a.tipo in ("volumen", "silencio"):
            n = self.bus()
            if n is None:
                raise ErrorElena(f"no existe el bus {BUS}.")
            if a.tipo == "volumen":
                await asyncio.to_thread(self.sistema.volumen, n, a.volumen)
            else:
                await asyncio.to_thread(self.sistema.silencio, n, True)
        elif a.tipo == "pausa":
            await self.cliente.orden("pausa")
        elif a.tipo == "apagar_vm":
            self._lanzar_apagado_vm()
        elif a.tipo == "suspender":
            self.aviso("Temporizador: suspendiendo la PC.")
            await asyncio.to_thread(self.sistema.suspender)
        elif a.tipo == "apagar_pc":
            self.aviso("Temporizador: apagando la PC.")
            await asyncio.to_thread(self.sistema.apagar_pc)

    def _tarea(self, corrutina) -> asyncio.Task:
        t = asyncio.ensure_future(corrutina)
        self._tareas.add(t)
        t.add_done_callback(self._tareas.discard)
        return t

    # ---------- órdenes del teléfono ----------

    async def musica(self, orden: str, reproductor: str | None = None) -> None:
        await self.cliente.orden(orden, reproductor)

    def elegir(self, reproductor: str | None) -> None:
        self.cliente.elegir(reproductor)

    def _bus_o_error(self) -> Nodo:
        n = self.bus()
        if n is None:
            raise ErrorElena(f"No existe el bus «ELENA · Música» ({BUS}). ¿Falta `mise run aplicar`?")
        return n

    async def volumen(self, pct: float) -> None:
        if not 0 <= pct <= 150:
            raise ErrorElena("El volumen va de 0 a 150 %.")
        n = self._bus_o_error()
        a = tope.acotar(self.grafo, n, pct)   # no pasa de tu máximo (la página llega a 100 %)
        if a.aviso:
            log(a.aviso)
        await asyncio.to_thread(self.sistema.volumen, n, escalar(n.volumenes, a.pct))

    async def silencio(self, silenciar: bool | None) -> None:
        await asyncio.to_thread(self.sistema.silencio, self._bus_o_error(), silenciar)

    def programar(self, nivel: str, minutos: float | None = None, fin_cancion: bool = False) -> None:
        self.temporizador.programar(nivel, minutos=minutos, fin_cancion=fin_cancion, entorno=self.entorno())
        v = self.temporizador.vista()
        log(f"Temporizador: {v['modo']}, nivel {v['nivel']}, vence a las "
            f"{time.strftime('%H:%M:%S', time.localtime(v['vence'] / 1000))}.")
        self._guardar()
        self.avisar()

    def mas(self, minutos: float) -> None:
        self.temporizador.mas(minutos)
        self._guardar()
        self.avisar()

    async def cancelar(self) -> None:
        if self.temporizador.programa is not None:
            log("Temporizador cancelado desde el teléfono.")
        await self._ejecutar(self.temporizador.cancelar())
        self._guardar()
        self.avisar()

    async def vm(self, accion: str) -> None:
        if accion == "encender":
            if self.vm_corre or self._vm_accion:
                return
            await asyncio.to_thread(self.sistema.encender_vm)
            self._vm_accion, self._vm_desde = "encendiendo", self.reloj()
            log("VM: encendiendo desde el teléfono.")
        elif accion == "apagar":
            if not self.vm_corre:
                return
            self._lanzar_apagado_vm()
        else:
            raise ErrorElena(f"No conozco «{accion}»: encender o apagar.")
        self.avisar()

    def _lanzar_apagado_vm(self) -> None:
        if self._vm_accion == "apagando":
            return
        self._vm_accion = "apagando"
        log("VM: apagando Windows.")
        self._vm_tarea = self._tarea(self._apagar_vm())
        self.avisar()

    async def _apagar_vm(self) -> None:
        try:
            await asyncio.to_thread(self.sistema.apagar_vm)
            self.vm_corre = await asyncio.to_thread(self.sistema.vm_corre)
        except ErrorElena as e:
            self.aviso(f"VM: {e}")
        finally:
            self._vm_accion = None
            self.avisar()

    async def pc(self, accion: str, confirmar: bool) -> str:
        if accion not in ("suspender", "apagar"):
            raise ErrorElena(f"No conozco «{accion}»: suspender o apagar.")
        if confirmar is not True:
            raise ErrorElena("Falta confirmar.")
        r = self.cliente.modelo.actual
        if r is not None and r.estado == "Playing":
            with contextlib.suppress(ErrorElena):
                await self.cliente.orden("pausa")
        hacer = self.sistema.suspender if accion == "suspender" else self.sistema.apagar_pc
        texto = "Suspendiendo la PC." if accion == "suspender" else "Apagando la PC."
        self.aviso(texto)

        async def despues() -> None:
            await asyncio.sleep(RETRASO_APAGADO)   # que la respuesta llegue al teléfono antes
            try:
                await asyncio.to_thread(hacer)
            except ErrorElena as e:
                self.aviso(str(e))
        self._tarea(despues())
        return texto
