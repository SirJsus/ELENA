"""Medidores por canal: un `pw-record` por nodo que manda muestras crudas, y su nivel en dBFS.

Verificado:
- El monitor de un sink (`stream.capture.sink = true`) mide **antes** del volumen del sink.
  La salida de un bus se calcula sumando su volumen por canal (lo hace la TUI).
- Capturar una app (`--target <serial>`) mide **después** del volumen de la app.
- Con `--channel-map` igual a las posiciones del nodo, cada canal llega a su lugar
  (probado con un nivel distinto por canal: FL FR FC LFE RL RR = -20…-45 dB).
- `node.passive` no deja suspender el bus, así que el medidor se abre solo mientras hace falta.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import math
import os
import re
import subprocess
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

TASA = 48000
BLOQUE = TASA // 20   # 50 ms entre lecturas, al ritmo de la TUI (20 fps); el pico es exacto igual
F_SETPIPE_SZ = getattr(fcntl, "F_SETPIPE_SZ", 1031)
PISO_DB = -90.0
RETENCION = 1.5       # segundos que se sostiene el pico
SATURA_DB = -0.1


@dataclass(frozen=True)
class Objetivo:
    """Qué medir: `target` es un node.name o un serial (pw-record --target acepta los dos)."""

    target: str
    canales: int
    posiciones: tuple[str, ...] = ()
    monitor: bool = False  # el monitor de un sink, no el sink en sí

    def comando(self, nombre: str) -> list[str]:
        props = [f'node.name = "{nombre}"', 'application.name = "ELENA medidor"',
                 # Si el nodo desaparece, el medidor no se pasa a otro (por ejemplo, al mic)
                 "node.dont-reconnect = true", "node.dont-fallback = true"]
        if self.monitor:
            props.append("stream.capture.sink = true")
        cmd = ["pw-record", "--target", self.target, "--raw", "--format", "f32", "--rate", str(TASA),
               "--channels", str(self.canales), "--latency", "20ms", "-P", "{ " + " ".join(props) + " }"]
        if len(self.posiciones) == self.canales:
            cmd[cmd.index("--latency"):cmd.index("--latency")] = ["--channel-map", ",".join(self.posiciones)]
        return [*cmd, "-"]


def niveles(bloque: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pico y RMS por canal, en dBFS, de un bloque (cuadros × canales)."""
    x = bloque.astype(np.float64)
    pico = np.abs(x).max(axis=0)
    rms = np.sqrt((x * x).mean(axis=0))
    with np.errstate(divide="ignore"):
        return (np.maximum(20 * np.log10(pico), PISO_DB), np.maximum(20 * np.log10(rms), PISO_DB))


@dataclass
class Nivel:
    pico: np.ndarray
    rms: np.ndarray
    retenido: np.ndarray            # el pico más alto de los últimos RETENCION segundos
    satura: np.ndarray              # bool: llegó a SATURA_DB en los últimos RETENCION segundos
    _desde: np.ndarray = field(repr=False)  # cuándo se fijó cada retenido
    cuando: float = 0.0

    @classmethod
    def nuevo(cls, canales: int) -> Nivel:
        piso = np.full(canales, PISO_DB)
        return cls(piso.copy(), piso.copy(), piso.copy(), np.zeros(canales, bool), np.zeros(canales))

    def actualizar(self, pico: np.ndarray, rms: np.ndarray, ahora: float) -> None:
        vencido = ahora - self._desde >= RETENCION
        sube = pico >= self.retenido
        renovar = vencido | sube
        self.retenido = np.where(renovar, pico, self.retenido)
        self.satura = np.where(renovar, pico >= SATURA_DB, self.satura | (pico >= SATURA_DB))
        self._desde = np.where(renovar, ahora, self._desde)
        self.pico, self.rms, self.cuando = pico, rms, ahora


class Proceso(Protocol):
    """Lo que se usa de `subprocess.Popen`."""

    stdout: Any  # con fileno()

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


Lanzar = Callable[..., Proceso]
TUBERIA = 512 * 1024  # bytes; 50 ms de 6 canales f32 son ~58 KB


def _lanzar(*cmd: str) -> Proceso:
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


class Medidor:
    """Un pw-record leído **por intervalos**, no por evento.

    pw-record escribe en cada ciclo del grafo (con la VM, cada 7.5 ms): leerlo por evento
    despertaba el ciclo de asyncio unas 130 veces por segundo por medidor. Aquí se lee cada
    50 ms todo lo acumulado en la tubería, sin bloquear (la tubería se agranda para que nunca
    se llene entre lecturas), y el pico sale de todas esas muestras.
    """

    def __init__(self, objetivo: Objetivo, nombre: str, lanzar: Lanzar = _lanzar,
                 reloj: Callable[[], float] = time.monotonic, intervalo: float = BLOQUE / TASA) -> None:
        self.objetivo = objetivo
        self.nombre = nombre
        self.nivel: Nivel | None = None
        self.fin: float | None = None  # cuándo terminó pw-record (el nodo desapareció o PipeWire se reinició)
        self._lanzar = lanzar
        self._reloj = reloj
        self._intervalo = intervalo

    async def correr(self) -> None:
        n = self.objetivo.canales
        cuadro = n * 4
        proceso = self._lanzar(*self.objetivo.comando(self.nombre))
        fd = proceso.stdout.fileno()
        os.set_blocking(fd, False)
        with contextlib.suppress(OSError):
            fcntl.fcntl(fd, F_SETPIPE_SZ, TUBERIA)
        bufer = bytearray()
        nivel = Nivel.nuevo(n)
        try:
            while True:
                await asyncio.sleep(self._intervalo)
                fin = False
                while True:
                    try:
                        datos = os.read(fd, TUBERIA)
                    except BlockingIOError:
                        break
                    if not datos:
                        fin = True  # pw-record terminó (el nodo desapareció o PipeWire se reinició)
                        break
                    bufer += datos
                usable = len(bufer) // cuadro * cuadro
                if usable:
                    bloque = np.frombuffer(bytes(bufer[:usable]), dtype="<f4").reshape(-1, n)
                    del bufer[:usable]
                    nivel.actualizar(*niveles(bloque), self._reloj())
                    self.nivel = nivel
                if fin:
                    break
        finally:
            self.nivel = None
            self.fin = self._reloj()
            if proceso.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    proceso.kill()
            with contextlib.suppress(Exception):
                proceso.wait(timeout=1)
            with contextlib.suppress(Exception):
                proceso.stdout.close()


class Tarea(Protocol):
    def cancel(self) -> Any: ...


def _nombre_nodo(clave: tuple[str, str]) -> str:
    return "elena_medidor." + re.sub(r"[^A-Za-z0-9_.-]", "_", ".".join(clave))


class Medidores:
    """Abre y cierra medidores según lo que haga falta medir en cada momento.

    `iniciar` arranca una corrutina y devuelve algo cancelable. En la TUI es `App.run_worker`,
    así Textual cancela los medidores al salir y cada uno mata su pw-record.
    """

    REINTENTO = 2.0  # segundos antes de reabrir un medidor cuyo pw-record terminó

    def __init__(self, iniciar: Callable[[Coroutine], Tarea] = asyncio.ensure_future, lanzar: Lanzar = _lanzar,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self._iniciar = iniciar
        self._lanzar = lanzar
        self._reloj = reloj
        self._activos: dict[Any, tuple[Medidor, Tarea]] = {}

    def sincronizar(self, deseados: dict[Any, Objetivo]) -> None:
        for clave in list(self._activos):
            medidor, tarea = self._activos[clave]
            # Si PipeWire se reinicia, los pw-record mueren: se reabren pasado un momento
            murio = medidor.fin is not None and self._reloj() - medidor.fin >= self.REINTENTO
            if deseados.get(clave) != medidor.objetivo or murio:
                tarea.cancel()
                del self._activos[clave]
        for clave, objetivo in deseados.items():
            if clave not in self._activos and objetivo.canales > 0:
                medidor = Medidor(objetivo, _nombre_nodo(clave), self._lanzar, self._reloj)
                self._activos[clave] = (medidor, self._iniciar(medidor.correr()))

    def nivel(self, clave: Any) -> Nivel | None:
        activo = self._activos.get(clave)
        return activo[0].nivel if activo else None

    @property
    def activos(self) -> int:
        return len(self._activos)

    def cerrar(self) -> None:
        self.sincronizar({})


def db_con_volumen(db: np.ndarray, volumenes: tuple[float, ...], silencio: bool) -> np.ndarray:
    """Lo que sale de un bus: lo que entra (monitor) más su volumen por canal."""
    if silencio:
        return np.full_like(db, PISO_DB)
    if len(volumenes) != len(db):
        return db
    ganancia = np.array([20 * math.log10(v) if v > 0 else -math.inf for v in volumenes])
    return np.maximum(db + ganancia, PISO_DB)
