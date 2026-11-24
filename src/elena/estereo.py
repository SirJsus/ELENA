"""Compensación estéreo / 5.1: baja un bus mientras suena estéreo.

La música estéreo llega mucho más fuerte que la Atmos (otro masterizado: las mezclas Atmos de Apple
Music van a −18 LKFS con picos de −9 dBFS, y una estéreo medida dio picos de −0.3 dBFS): habría que
bajar ~12 dB a mano en cada cambio. Con esto, tu 100 % y tu tope se definen para 5.1 y el estéreo
queda compensado debajo, solo:

- **Detección por la señal**, del monitor del bus (antes de su volumen): **estéreo** si FC, LFE, RL y
  RR están en silencio digital (< −80 dBFS; la VM manda ±1 bit, −90 dBFS) durante 2 s mientras FL o
  FR suenan; **5.1** en cuanto uno de ellos pasa de −70 dBFS. Entre canciones (silencio) se queda el
  último. El stream de la VM siempre es de 6 canales, así que su formato no sirve; pero si todas las
  apps del bus son de 2 canales (Cider), es estéreo aunque el upmix llene las traseras.
- **Un bus estéreo** (Sistema) es estéreo siempre: va compensado siempre.
- **Dónde baja:** el volumen del stream `<bus>.salida` (el loopback que sale del bus), no el del bus.
  Tu volumen, la consola, los medidores y `elena_visual` (que leen el monitor) no cambian.
  En rampa de 0.3 s (8 pasos de `pw-cli set-param`, verificado: −12.00 dB exactos).
- **Cuánto:** `compensacion` para todos los buses de la lista, o uno propio por bus (`sistema = -10.0`).
- **Lo corre el vigilante.** Relee `[estereo]` del TOML cuando cambia. Si alguien mueve el volumen de
  esa salida, lo regresa; al terminar, deja las salidas en 0 dB.

Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from elena.medidor import BLOQUE, Objetivo, niveles
from elena.modelo import BUSES, ErrorElena, Grafo, Nodo, lineal_a_db

ESTEREO, CINCO = "estéreo", "5.1"
EXTRAS = ("FC", "LFE", "RL", "RR")
SILENCIO_DB = -80.0       # los canales extra «callados» (la VM manda ±1 bit: −90 dBFS)
SUENA_DB = -60.0          # el frente «suena»
HAY_51_DB = -70.0         # un canal extra con esto ya es 5.1
ESPERA_ESTEREO = 2.0      # s seguidos de evidencia antes de bajar (no bajar por un silencio de las traseras)
RAMPA = 0.3               # s
PASOS = 8
CADA = 0.25               # s entre revisiones
TOLERANCIA_DB = 0.05

DEF = {b.clave: b for b in BUSES}


# ---------- Detección (sin E/S) ----------

@dataclass
class Detector:
    estado: str = CINCO
    _desde: float | None = None

    def ver(self, picos: dict[str, float], t: float) -> str:
        """`picos`: pico por canal (dBFS) del último bloque del monitor."""
        frente = max((picos.get(c, -math.inf) for c in ("FL", "FR")), default=-math.inf)
        extras = max((picos.get(c, -math.inf) for c in EXTRAS if c in picos), default=-math.inf)
        if extras > HAY_51_DB:
            self.estado, self._desde = CINCO, None
        elif frente > SUENA_DB and extras < SILENCIO_DB:
            self._desde = t if self._desde is None else self._desde
            if t - self._desde >= ESPERA_ESTEREO:
                self.estado = ESTEREO
        else:
            self._desde = None          # silencio o dudoso: se queda como está
        return self.estado


def por_apps(apps: list[Nodo]) -> str | None:
    """Si todas las apps del bus son de 2 canales o menos, es estéreo (aunque el upmix llene las traseras)."""
    if apps and all((a.canales or 0) and (a.canales or 0) <= 2 for a in apps):
        return ESTEREO
    return None


def rampa(desde_db: float, hasta_db: float, pasos: int = PASOS) -> list[float]:
    """Los volúmenes lineales de la rampa (sin el de partida)."""
    return [10 ** ((desde_db + (hasta_db - desde_db) * i / pasos) / 20) for i in range(1, pasos + 1)]


# ---------- La configuración, releída cuando cambia ----------

@dataclass
class Config:
    compensacion: float | None = None
    buses: tuple[str, ...] = ()
    por_bus: dict[str, float] = field(default_factory=dict)    # la propia de un bus (gana sobre `compensacion`)
    _mtime: float | None = field(default=None, repr=False)

    def releer(self, ruta: Path | None = None) -> bool:
        """True si cambió."""
        from elena import calibracion as cb
        ruta = ruta or cb.TOML
        try:
            mtime = ruta.stat().st_mtime
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        antes = (self.compensacion, self.buses, self.por_bus)
        try:
            cal = cb.leer(ruta) if mtime is not None else None
            self.compensacion, self.buses, self.por_bus = (
                (cal.estereo, cal.estereo_buses, dict(cal.estereo_por_bus)) if cal else (None, (), {}))
        except ErrorElena:
            pass                         # TOML roto: se queda con lo de antes
        return (self.compensacion, self.buses, self.por_bus) != antes


# ---------- E/S ----------

def fijar(nodo: Nodo, lineal: float) -> None:
    valores = " ".join(f"{lineal:.6f}" for _ in nodo.volumenes) or f"{lineal:.6f}"
    r = subprocess.run(["pw-cli", "set-param", str(nodo.id), "Props", f"{{ channelVolumes = [ {valores} ] }}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorElena(f"pw-cli no cambió {nodo.nombre}: {(r.stderr or r.stdout).strip()[:200]}")


def db_de(nodo: Nodo) -> float:
    return lineal_a_db(max(nodo.volumenes)) if nodo.volumenes else 0.0


class Lector:
    """`pw-record` del monitor de un bus de 6 canales; alimenta su Detector."""

    def __init__(self, bus: Nodo) -> None:
        self.bus = bus
        self.detector = Detector()
        self.tarea: asyncio.Task | None = None

    async def correr(self) -> None:
        n = len(self.bus.posiciones)
        cmd = Objetivo(self.bus.nombre, n, self.bus.posiciones, monitor=True).comando(f"elena_estereo_{self.bus.nombre}")
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        tam = BLOQUE * n * 4
        try:
            assert proc.stdout
            while True:
                datos = await proc.stdout.readexactly(tam)
                x = np.frombuffer(datos, dtype=np.float32).reshape(-1, n)
                pico, _ = niveles(x)
                self.detector.ver(dict(zip(self.bus.posiciones, pico.tolist())), time.monotonic())
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 2)


class Compensacion:
    """La corre el vigilante junto con las reglas; `grafo` es el suyo (lo actualiza `pw-dump --monitor`)."""

    def __init__(self, grafo: Callable[[], Grafo], escribir: Callable[[str], None],
                 fijar_: Callable[[Nodo, float], None] = fijar) -> None:
        self.grafo = grafo
        self.escribir = escribir
        self.fijar = fijar_
        self.config = Config()
        self.lectores: dict[str, Lector] = {}
        self.estados: dict[str, str] = {}
        self.tocadas: set[str] = set()         # salidas que esta compensación movió alguna vez

    def estado(self, clave: str, grafo: Grafo) -> str | None:
        """estéreo, 5.1 o None (no se sabe todavía / el bus no existe)."""
        d = DEF[clave]
        bus = grafo.nodo(d.nodo)
        if bus is None:
            return None
        if len(bus.posiciones) <= 2:
            return ESTEREO
        apps = next((b.apps for b in grafo.buses() if b.definicion.clave == clave), [])
        if (e := por_apps(apps)) is not None:
            return e
        lector = self.lectores.get(clave)
        if lector is None or lector.tarea is None or lector.tarea.done():
            lector = self.lectores[clave] = Lector(bus)
            lector.tarea = asyncio.create_task(lector.correr())
        return lector.detector.estado

    def objetivo(self, clave: str, estado: str | None) -> float:
        if self.config.compensacion is None or clave not in self.config.buses or estado != ESTEREO:
            return 0.0
        return self.config.por_bus.get(clave, self.config.compensacion)

    async def _ir(self, salida: Nodo, desde: float, hasta: float) -> None:
        for v in rampa(desde, hasta):
            await asyncio.to_thread(self.fijar, salida, v)
            await asyncio.sleep(RAMPA / PASOS)

    async def paso(self) -> None:
        self.config.releer()
        grafo = self.grafo()
        claves = set(self.config.buses) | {c for c in self.tocadas}
        for clave in sorted(claves):
            salida = grafo.nodo(DEF[clave].salida)
            if salida is None:
                continue
            estado = self.estado(clave, grafo) if clave in self.config.buses else None
            objetivo = self.objetivo(clave, estado)
            actual = db_de(salida)
            if estado is not None and self.estados.get(clave) != estado:
                if clave in self.estados or estado == ESTEREO:
                    self.escribir(f"{DEF[clave].etiqueta}: suena {estado} → {objetivo:+.0f} dB")
                self.estados[clave] = estado
            if abs(actual - objetivo) > TOLERANCIA_DB:
                try:
                    await self._ir(salida, actual if math.isfinite(actual) else objetivo, objetivo)
                    self.tocadas.add(clave)
                except ErrorElena as e:
                    self.escribir(f"{DEF[clave].etiqueta}: no pude compensar ({e})")
        for clave in set(self.lectores) - set(self.config.buses):
            if (t := self.lectores.pop(clave).tarea) is not None:
                t.cancel()

    async def correr(self) -> None:
        try:
            while True:
                await self.paso()
                await asyncio.sleep(CADA)
        finally:
            for lector in self.lectores.values():
                if lector.tarea is not None:
                    lector.tarea.cancel()
            self.restaurar()

    def restaurar(self) -> None:
        """Al terminar: las salidas que se movieron, de vuelta a 0 dB."""
        grafo = self.grafo()
        for clave in self.tocadas:
            if (salida := grafo.nodo(DEF[clave].salida)) is not None and abs(db_de(salida)) > TOLERANCIA_DB:
                with contextlib.suppress(ErrorElena):
                    self.fijar(salida, 1.0)
