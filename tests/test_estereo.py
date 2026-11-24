"""Compensación estéreo / 5.1."""

import asyncio
import math
from pathlib import Path

import pytest

from elena import calibracion as cb
from elena import estereo as es
from elena.modelo import ErrorElena

RAIZ = Path(__file__).resolve().parent.parent
CINCO = {"FL": -12.0, "FR": -13.0, "FC": -30.0, "LFE": -35.0, "RL": -20.0, "RR": -22.0}
ESTEREO = {"FL": -1.0, "FR": -2.0, "FC": -90.0, "LFE": -90.0, "RL": -90.0, "RR": -90.0}   # la VM: ±1 bit
SILENCIO = dict.fromkeys(CINCO, -90.0)


def test_detector_baja_tras_2_s_de_estereo_y_sube_al_instante():
    d = es.Detector()
    assert d.ver(ESTEREO, 0.0) == es.CINCO
    assert d.ver(ESTEREO, 1.9) == es.CINCO
    assert d.ver(ESTEREO, 2.0) == es.ESTEREO
    assert d.ver(SILENCIO, 3.0) == es.ESTEREO           # entre canciones se queda
    assert d.ver(CINCO, 3.1) == es.CINCO                # en cuanto suena un canal extra


def test_detector_no_baja_por_un_silencio_corto_de_las_traseras():
    d = es.Detector()
    d.ver(ESTEREO, 0.0)
    d.ver(ESTEREO, 1.5)
    d.ver({**ESTEREO, "FC": -75.0}, 1.6)                  # dudoso: reinicia la cuenta
    assert d.ver(ESTEREO, 3.0) == es.CINCO              # la cuenta empieza de nuevo aquí
    assert d.ver(ESTEREO, 4.9) == es.CINCO
    assert d.ver(ESTEREO, 5.0) == es.ESTEREO


def test_detector_silencio_no_cuenta_como_estereo():
    d = es.Detector()
    for t in range(10):
        d.ver(SILENCIO, float(t))
    assert d.estado == es.CINCO


class _App:
    def __init__(self, canales):
        self.canales = canales


def test_por_apps():
    assert es.por_apps([_App(2), _App(2)]) == es.ESTEREO       # Cider: estéreo aunque el upmix llene las traseras
    assert es.por_apps([_App(6)]) is None                       # la VM: lo dice la señal
    assert es.por_apps([_App(2), _App(6)]) is None
    assert es.por_apps([]) is None


def test_rampa():
    r = es.rampa(0.0, -12.0)
    assert len(r) == es.PASOS and r[-1] == pytest.approx(10 ** (-12 / 20))
    assert all(a > b for a, b in zip(r, r[1:]))
    assert 20 * math.log10(r[0]) == pytest.approx(-1.5)


def test_config_relee_solo_si_cambia(tmp_path):
    toml = tmp_path / "calibracion.toml"
    toml.write_text((RAIZ / "tests" / "datos" / "calibracion.toml").read_text()
                    + '\n[estereo]\ncompensacion = -10.0\nbuses = ["musica"]\n')
    c = es.Config()
    assert c.releer(toml) and (c.compensacion, c.buses) == (-10.0, ("musica",))
    assert not c.releer(toml)
    toml.write_text("esto no es toml [")
    import os
    os.utime(toml, (1, 1))
    c.releer(toml)
    assert c.compensacion == -10.0                                # roto: se queda con lo de antes


@pytest.mark.parametrize("estereo,mensaje", [
    ({"compensacion": 3.0}, "fuera de rango"),
    ({"buses": ["chat"]}, "lista de"),
    ({"otra": 1}, "no conozco otra"),
    ({"buses": ["musica"], "sistema": -10.0}, "sin sistema en estereo.buses"),
    ({"buses": ["sistema"], "sistema": -30.0}, "fuera de rango"),
])
def test_valida_la_seccion(estereo, mensaje):
    import tomllib
    datos = tomllib.loads((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    with pytest.raises(ErrorElena, match=mensaje):
        cb.desde_datos({**datos, "estereo": estereo})


def test_el_toml_de_ejemplo_trae_la_seccion_sin_compensar():
    cal = cb.leer(RAIZ / "config" / "calibracion.ejemplo.toml")
    assert cal.estereo == 0.0 and set(cal.estereo_buses) == {"musica", "sistema"}


def test_una_propia_por_bus_gana_sobre_compensacion():
    import tomllib
    datos = tomllib.loads((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    cal = cb.desde_datos({**datos, "estereo": {"compensacion": -15.0, "buses": ["musica", "sistema"], "sistema": -10.0}})
    assert (cal.estereo_de("musica"), cal.estereo_de("sistema"), cal.estereo_de("juegos")) == (-15.0, -10.0, None)
    assert cb.texto_estereo(cal) == "Música -15 · Sistema -10 dB"
    assert cb.texto_estereo(cb.desde_datos({**datos, "estereo": {"compensacion": -15.0}})) == "-15 dB"


# ---------- Compensacion.paso, con un grafo falso ----------

class _Nodo:
    def __init__(self, nombre, posiciones, volumenes=None, ident=1):
        self.nombre, self.posiciones, self.id = nombre, posiciones, ident
        self.volumenes = volumenes or tuple(1.0 for _ in posiciones)


class _Bus:
    def __init__(self, clave, apps):
        self.definicion = es.DEF[clave]
        self.apps = apps


class _Grafo:
    def __init__(self, nodos, apps=None):
        self.nodos = {n.nombre: n for n in nodos}
        self._apps = apps or {}

    def nodo(self, nombre):
        return self.nodos.get(nombre)

    def buses(self):
        return [_Bus(c, a) for c, a in self._apps.items()]


POS6 = ("FL", "FR", "FC", "LFE", "RL", "RR")


@pytest.fixture
def sin_esperas(monkeypatch):
    async def nada(_):
        return None
    monkeypatch.setattr(es.asyncio, "sleep", nada)


def _compensacion(grafo, comp=-12.0, buses=("musica", "sistema")):
    fijados, registro = [], []

    def fijar(nodo, v):
        fijados.append((nodo.nombre, round(20 * math.log10(v), 2)))
        nodo.volumenes = tuple(v for _ in nodo.volumenes)
    c = es.Compensacion(lambda: grafo, registro.append, fijar)
    c.config.releer = lambda ruta=None: False
    c.config.compensacion, c.config.buses = comp, buses
    return c, fijados, registro


def test_sistema_siempre_compensado_y_musica_estereo_por_sus_apps(sin_esperas):
    g = _Grafo([_Nodo("elena_sistema", ("FL", "FR")), _Nodo("elena_sistema.salida", ("FL", "FR")),
                _Nodo("elena_musica", POS6), _Nodo("elena_musica.salida", POS6)],
               apps={"musica": [_App(2)]})
    c, fijados, registro = _compensacion(g)
    asyncio.run(c.paso())
    assert ("elena_sistema.salida", -12.0) in fijados and ("elena_musica.salida", -12.0) in fijados
    assert len([f for f in fijados if f[0] == "elena_sistema.salida"]) == es.PASOS      # en rampa
    assert any("Música: suena estéreo" in r for r in registro)
    # Ya compensado: no se toca otra vez
    fijados.clear()
    asyncio.run(c.paso())
    assert fijados == []
    # Alguien movió la salida desde fuera: se regresa
    g.nodo("elena_sistema.salida").volumenes = (1.0, 1.0)
    asyncio.run(c.paso())
    assert fijados[-1] == ("elena_sistema.salida", -12.0)


def test_apagar_la_compensacion_regresa_a_0(sin_esperas):
    g = _Grafo([_Nodo("elena_sistema", ("FL", "FR")), _Nodo("elena_sistema.salida", ("FL", "FR"))])
    c, fijados, _ = _compensacion(g, buses=("sistema",))
    asyncio.run(c.paso())
    c.config.buses = ()
    fijados.clear()
    asyncio.run(c.paso())
    assert fijados[-1] == ("elena_sistema.salida", 0.0)
    # Y al terminar, todo lo que se movió vuelve a 0 dB
    c.config.buses = ("sistema",)
    asyncio.run(c.paso())
    c.restaurar()
    assert fijados[-1] == ("elena_sistema.salida", 0.0)


def test_sin_compensacion_en_el_toml_no_toca_nada(sin_esperas):
    g = _Grafo([_Nodo("elena_sistema", ("FL", "FR")), _Nodo("elena_sistema.salida", ("FL", "FR"))])
    c, fijados, _ = _compensacion(g, comp=None, buses=())
    asyncio.run(c.paso())
    assert fijados == []


def test_cada_bus_baja_lo_suyo(sin_esperas):
    g = _Grafo([_Nodo("elena_sistema", ("FL", "FR")), _Nodo("elena_sistema.salida", ("FL", "FR")),
                _Nodo("elena_musica", POS6), _Nodo("elena_musica.salida", POS6)],
               apps={"musica": [_App(2)]})
    c, fijados, registro = _compensacion(g, comp=-15.0)
    c.config.por_bus = {"sistema": -10.0}
    asyncio.run(c.paso())
    assert fijados[-1] == ("elena_sistema.salida", -10.0)
    assert ("elena_musica.salida", -15.0) in fijados
    assert any("Sistema: suena estéreo → -10 dB" in r for r in registro)
