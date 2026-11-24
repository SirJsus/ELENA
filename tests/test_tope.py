"""Tope de volumen de Música y Juegos."""

import copy

import pytest

from conftest import PROCESOS, nodo_crudo
from elena import escenas, tope
from elena.avisos import calcular
from elena.modelo import Grafo, lineal_a_pct, pct_a_lineal
from elena.vigilante import Vigilante

LIMITE = lineal_a_pct(10 ** (8 / 20))     # +8 dB: 136 %


@pytest.fixture(autouse=True)
def tope_8(monkeypatch):
    monkeypatch.setattr(tope, "tope_db", lambda: 8.0)


def _grafo(dump, calibrados=("elena_musica", "elena_juegos", "elena_sistema"), pct=None):
    """La foto con los buses yendo a elena_salida y, si se pide, Música a `pct`."""
    for bus in calibrados:
        nodo_crudo(dump, **{"node.name": f"{bus}.salida"})["info"]["props"]["target.object"] = "elena_salida"
    if pct is not None:
        _volumen(dump, "elena_musica", pct)
    return Grafo.desde_dump(dump, proceso_de=PROCESOS.get)


def _volumen(dump, bus, pct):
    for p in nodo_crudo(dump, **{"node.name": bus, "media.class": "Audio/Sink"})["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"] = [pct_a_lineal(pct)] * len(p["channelVolumes"])


def test_limite_solo_musica_y_juegos_calibrados(dump):
    g = _grafo(dump)
    assert tope.limite_pct(g, g.nodo("elena_musica")) == pytest.approx(LIMITE) and round(LIMITE) == 136
    assert tope.limite_pct(g, g.nodo("elena_juegos")) == pytest.approx(LIMITE)
    assert tope.limite_pct(g, g.nodo("elena_sistema")) is None          # falta medirlo
    sin = Grafo.desde_dump(copy.deepcopy(dump), proceso_de=PROCESOS.get)
    nodo_crudo(dump, **{"node.name": "elena_juegos.salida"})["info"]["props"]["target.object"] = "otro"
    assert tope.limite_pct(Grafo.desde_dump(dump, proceso_de=PROCESOS.get), sin.nodo("elena_juegos")) is None


def test_sin_calibrar_no_hay_tope(dump):
    g = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)    # la foto: los buses van al físico
    assert tope.acotar(g, g.nodo("elena_musica"), 150.0).pct == 150.0


def test_acotar(dump):
    g = _grafo(dump)
    n = g.nodo("elena_musica")
    assert tope.acotar(g, n, 120.0, actual=100.0) == tope.Acotado(120.0)
    a = tope.acotar(g, n, 150.0, actual=100.0)
    assert a.pct == pytest.approx(LIMITE) and "tu máximo es 136 %" in a.aviso
    # Ya estaba arriba (145 %): no sube más, pero no se baja solo; bajar sí se puede
    a = tope.acotar(g, n, 150.0, actual=145.0)
    assert a.pct == 145.0 and "ya está en 145 %" in a.aviso
    assert tope.acotar(g, n, 140.0, actual=145.0) == tope.Acotado(140.0)


def test_acotar_volumenes_conserva_el_balance(dump):
    g = _grafo(dump)
    n = g.nodo("elena_musica")
    alto = (pct_a_lineal(150), pct_a_lineal(150) * 0.5)
    vols, aviso = tope.acotar_volumenes(g, n, alto, actual=100.0)
    assert aviso and lineal_a_pct(max(vols)) == pytest.approx(LIMITE) and vols[1] / vols[0] == pytest.approx(0.5)
    assert tope.acotar_volumenes(g, n, (0.5, 0.5), actual=100.0) == ((0.5, 0.5), None)


def test_escenas_acotan(dump):
    g = _grafo(dump, pct=100.0)
    escena = escenas.Escena("fiesta", {"musica": 150.0, "sistema": 150.0}, {})
    topados: list[str] = []
    cmds = escenas.comandos(g, escena, topados)
    musica, sistema = cmds[0], cmds[1]
    assert lineal_a_pct(float(musica[3])) == pytest.approx(LIMITE, abs=0.1)
    assert lineal_a_pct(float(sistema[3])) == pytest.approx(150.0, abs=0.1)   # Sistema no tiene tope
    assert len(topados) == 1 and "Música" in topados[0]


def test_vigilante_regresa_lo_que_sube_desde_fuera(dump):
    hechos, registro = [], []
    v = Vigilante(ejecutar=hechos.append, escribir=registro.append)
    v.grafo = _grafo(copy.deepcopy(dump), pct=100.0)
    v._cuidar_topes()                                     # primera vista: solo recuerda
    assert hechos == []
    _volumen(dump, "elena_musica", 150.0)                 # pavucontrol la sube a 150 %
    v.grafo = _grafo(dump)
    v._cuidar_topes()
    assert len(hechos) == 1 and lineal_a_pct(float(hechos[0][3])) == pytest.approx(LIMITE, abs=0.1)
    assert any("desde fuera" in r for r in registro)
    # Bajar desde fuera no se toca
    hechos.clear()
    _volumen(dump, "elena_musica", 80.0)
    v.grafo = _grafo(dump)
    v._cuidar_topes()
    assert hechos == []


def test_vigilante_no_baja_lo_que_ya_estaba_arriba(dump):
    hechos, registro = [], []
    v = Vigilante(ejecutar=hechos.append, escribir=registro.append)
    v.grafo = _grafo(dump, pct=145.0)
    v._cuidar_topes()
    assert hechos == [] and any("no lo bajo" in r for r in registro)


def test_aviso_en_la_consola(dump):
    lista = calcular(_grafo(dump, pct=145.0))
    aviso = next(a for a in lista if a.clave == "tope-musica")
    assert "arriba de tu máximo (136 %)" in aviso.texto
    assert "tope-musica" not in {a.clave for a in calcular(_grafo(copy.deepcopy(dump), pct=100.0))}
