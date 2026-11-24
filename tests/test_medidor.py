"""Medidores: nivel por canal, pico sostenido, comando de pw-record y el gestor."""

import asyncio
import os
import threading

import numpy as np
import pytest

from elena import medidor
from elena.medidor import (
    BLOQUE, PISO_DB, RETENCION, Medidor, Medidores, Nivel, Objetivo, db_con_volumen, niveles,
)


def test_niveles_de_un_seno():
    t = np.arange(4800) / 48000
    seno = 0.5 * np.sin(2 * np.pi * 1000 * t)
    bloque = np.stack([seno, seno * 0.1, np.zeros_like(seno)], axis=1)
    pico, rms = niveles(bloque)
    assert pico[0] == pytest.approx(-6.02, abs=0.01)
    assert rms[0] == pytest.approx(-9.03, abs=0.01)   # seno: rms = pico − 3.01 dB
    assert pico[1] == pytest.approx(-26.02, abs=0.01)
    assert pico[2] == PISO_DB and rms[2] == PISO_DB   # silencio: el piso, no -inf


def test_pico_sostenido_y_saturacion():
    n = Nivel.nuevo(2)
    n.actualizar(np.array([-6.0, 0.0]), np.array([-9.0, -3.0]), ahora=0.0)
    assert list(n.retenido) == [-6.0, 0.0] and list(n.satura) == [False, True]
    n.actualizar(np.array([-20.0, -20.0]), np.array([-23.0, -23.0]), ahora=1.0)
    assert list(n.retenido) == [-6.0, 0.0] and list(n.satura) == [False, True]  # se sostiene
    n.actualizar(np.array([-20.0, -20.0]), np.array([-23.0, -23.0]), ahora=RETENCION + 0.1)
    assert list(n.retenido) == [-20.0, -20.0] and list(n.satura) == [False, False]  # venció
    n.actualizar(np.array([-3.0, -30.0]), np.array([-6.0, -33.0]), ahora=RETENCION + 0.2)
    assert list(n.retenido) == [-3.0, -20.0]  # un pico más alto renueva al momento


def test_comando_monitor_con_mapa():
    cmd = Objetivo("elena_juegos", 6, ("FL", "FR", "FC", "LFE", "RL", "RR"), monitor=True).comando("elena_medidor.x")
    assert cmd[:3] == ["pw-record", "--target", "elena_juegos"]
    assert cmd[cmd.index("--channel-map") + 1] == "FL,FR,FC,LFE,RL,RR"
    props = cmd[cmd.index("-P") + 1]
    assert "stream.capture.sink = true" in props and 'node.name = "elena_medidor.x"' in props
    assert "node.dont-reconnect = true" in props
    assert cmd[-1] == "-"


def test_comando_app_por_serial_sin_mapa():
    cmd = Objetivo("1331", 2).comando("m")
    assert "--channel-map" not in cmd
    assert "stream.capture.sink" not in cmd[cmd.index("-P") + 1]


def test_db_con_volumen():
    db = np.array([-20.0, -20.0])
    assert list(db_con_volumen(db, (1.0, 0.125), False)) == pytest.approx([-20.0, -38.06], abs=0.01)
    assert list(db_con_volumen(db, (1.0, 1.0), True)) == [PISO_DB, PISO_DB]
    assert list(db_con_volumen(db, (0.0, 1.0), False)) == [PISO_DB, -20.0]


class ProcesoFalso:
    """Un pw-record que escribe `bloques` bloques de muestras en una tubería real y termina."""

    def __init__(self, canales: int, valor: float, bloques: int) -> None:
        lectura, escritura = os.pipe()
        datos = np.full((BLOQUE * bloques, canales), valor, dtype="<f4").tobytes()

        def escribir() -> None:
            vista = memoryview(datos)
            while vista:
                vista = vista[os.write(escritura, vista):]
            os.close(escritura)

        threading.Thread(target=escribir, daemon=True).start()
        self.stdout = os.fdopen(lectura, "rb", buffering=0)
        self.codigo = None
        self.matado = False

    def poll(self):
        return self.codigo

    def kill(self) -> None:
        self.matado = True
        self.codigo = -9

    def wait(self, timeout=None) -> int:
        return self.codigo or 0


async def test_medidor_lee_por_intervalos_y_cierra_el_proceso():
    procesos, picos = [], []

    def lanzar(*cmd):
        procesos.append((cmd, ProcesoFalso(2, 0.5, bloques=3)))
        return procesos[-1][1]

    m = Medidor(Objetivo("1331", 2), "m", lanzar=lanzar, intervalo=0.01)
    real = Nivel.actualizar

    def espiar(self, pico, rms, ahora):
        real(self, pico, rms, ahora)
        picos.append(float(self.pico[0]))

    Nivel.actualizar = espiar
    try:
        await asyncio.wait_for(m.correr(), timeout=5)
    finally:
        Nivel.actualizar = real
    assert picos and all(abs(x + 6.02) < 0.01 for x in picos)  # todo lo escrito se midió
    assert m.nivel is None                 # terminó: ya no hay lectura
    assert procesos[0][1].matado           # y el proceso se cerró


async def test_gestor_abre_cierra_y_reabre_si_cambia():
    tareas = []

    def iniciar(coro):
        tareas.append(asyncio.ensure_future(coro))
        return tareas[-1]

    g = Medidores(iniciar=iniciar, lanzar=lambda *cmd: ProcesoFalso(1, 0.0, bloques=0))
    g.sincronizar({"a": Objetivo("x", 1), "b": Objetivo("y", 1)})
    assert g.activos == 2
    g.sincronizar({"a": Objetivo("x", 1)})          # b ya no hace falta
    assert g.activos == 1 and len(tareas) == 2
    g.sincronizar({"a": Objetivo("x", 2)})          # cambió el formato: se reabre
    assert g.activos == 1 and len(tareas) == 3
    g.cerrar()
    assert g.activos == 0
    await asyncio.gather(*tareas, return_exceptions=True)
    assert all(t.done() for t in tareas)


def test_nombre_de_nodo_seguro():
    assert medidor._nombre_nodo(("app", "1331")) == "elena_medidor.app.1331"
    assert medidor._nombre_nodo(("nodo", "a b/c")) == "elena_medidor.nodo.a_b_c"
