"""La mezcla para visualizadores (elena_visual): modelo, reglas «visual», vigilante y CLI.

La foto de pw-dump (2026-11-06) no tiene elena_visual: la agrega `conftest.con_visual`.
"""

import pytest

from conftest import NOCTALIA, PROCESOS, con_visual
from elena import cli, reglas
from elena.modelo import ErrorElena, Grafo, Tipo
from elena.monitor import Lote
from elena.reglas import Regla
from elena.vigilante import Vigilante


@pytest.fixture
def grafo_visual(dump) -> Grafo:
    return Grafo.desde_dump(con_visual(dump), proceso_de=PROCESOS.get)


# ---------- modelo ----------

def test_estado_visual(dump):
    grafo = Grafo.desde_dump(con_visual(dump, noctalia_en_visual=True), proceso_de=PROCESOS.get)
    visual = grafo.visual()
    assert visual.buses == {"musica": True, "juegos": True, "sistema": False}
    assert [o.app for o in visual.oyentes] == ["Noctalia Spectrum"]
    assert grafo.nodo("elena_visual").tipo == Tipo.INTERNO   # no es un bus ni una salida para apps


def test_sin_visual(grafo):
    assert grafo.visual() is None


# ---------- reglas ----------

def test_regla_visual_solo_para_grabaciones(grafo_visual):
    noctalia = grafo_visual.nodos[NOCTALIA]
    app = next(a for a in grafo_visual.de_tipo(Tipo.APP) if a.app == "elena-prueba-pulse")
    lista = [Regla("visual", app="*"), Regla("musica", app="*")]
    assert reglas.destino(lista, noctalia).bus == "visual"
    assert reglas.destino(lista, app).bus == "musica"      # la visual no se le aplica a la app


def test_fijar_no_mezcla_tipos(grafo_visual):
    noctalia = grafo_visual.nodos[NOCTALIA]
    app = next(a for a in grafo_visual.de_tipo(Tipo.APP) if a.app == "elena-prueba-pulse")
    lista = reglas.fijar([Regla("juegos", app="*")], noctalia, "visual")
    assert lista == [Regla("visual", app="Noctalia Spectrum"), Regla("juegos", app="*")]
    with pytest.raises(ErrorElena, match="solo para apps que graban"):
        reglas.fijar([], app, "visual")
    with pytest.raises(ErrorElena):
        reglas.fijar([], noctalia, "musica")
    assert reglas.quitar(lista, noctalia) == [Regla("juegos", app="*")]


# ---------- vigilante ----------

async def _flujo(*lotes):
    for lote in lotes:
        yield lote


def _vigilante(tmp_path, lista):
    ruta = tmp_path / "reglas.toml"
    reglas.guardar(lista, ruta)
    comandos, lineas = [], []
    v = Vigilante(ruta, ejecutar=lambda c: comandos.append(c) or "", escribir=lineas.append)
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    return v, comandos, lineas


async def test_vigilante_mueve_visualizadores(tmp_path, dump):
    v, comandos, lineas = _vigilante(tmp_path, [Regla("visual", app="Noctalia Spectrum")])
    await v.correr(_flujo(Lote(con_visual(dump), reinicio=True)))
    assert comandos == [["pactl", "move-source-output", "721", "elena_visual.monitor"]]
    assert any("Noctalia Spectrum (serial 721) →" in l for l in lineas)


async def test_vigilante_no_mueve_si_ya_esta(tmp_path, dump):
    v, comandos, _ = _vigilante(tmp_path, [Regla("visual", app="Noctalia Spectrum")])
    await v.correr(_flujo(Lote(con_visual(dump, noctalia_en_visual=True), reinicio=True)))
    assert comandos == []


async def test_vigilante_sin_visual_avisa(tmp_path, dump):
    v, comandos, lineas = _vigilante(tmp_path, [Regla("visual", app="Noctalia Spectrum")])
    await v.correr(_flujo(Lote(dump, reinicio=True)))
    assert comandos == [] and any("elena_visual) no existe" in l for l in lineas)


async def test_vigilante_no_mueve_grabaciones_con_reglas_de_apps(tmp_path, dump):
    v, comandos, _ = _vigilante(tmp_path, [Regla("musica", app="Noctalia*")])
    await v.correr(_flujo(Lote(con_visual(dump), reinicio=True)))
    assert comandos == []


# ---------- CLI ----------

@pytest.fixture
def cli_visual(monkeypatch, tmp_path, grafo_visual):
    comandos = []
    monkeypatch.setattr(reglas, "RUTA", tmp_path / "reglas.toml")
    monkeypatch.setattr(cli, "cargar_grafo", lambda: grafo_visual)
    monkeypatch.setattr(cli, "ejecutar", lambda c: comandos.append(c) or "")
    return comandos


def test_cli_visual_estado(cli_visual, capsys):
    assert cli.main(["visual"]) == 0
    assert "música on · juegos on · sistema off  ← nadie escucha" in capsys.readouterr().out
    cli.main(["estado"])
    assert "Visual:   música on · juegos on · sistema off" in capsys.readouterr().out


def test_cli_visual_interruptores(cli_visual, capsys):
    assert cli.main(["visual", "sistema", "on"]) == 0
    assert cli.main(["visual", "Música", "off"]) == 0
    assert cli.main(["visual", "juegos"]) == 0                # alterna: estaba activo
    assert cli_visual == [
        ["pactl", "set-sink-input-mute", "9003", "0"],
        ["pactl", "set-sink-input-mute", "9001", "1"],
        ["pactl", "set-sink-input-mute", "9002", "toggle"],
    ]
    salida = capsys.readouterr().out
    assert "Sistema en la mezcla de visualizadores: on" in salida
    assert "Juegos en la mezcla de visualizadores: off" in salida
    assert cli.main(["visual", "chat", "on"]) == 1


def test_cli_regla_y_mover_visual(cli_visual, capsys):
    assert cli.main(["regla", "noctalia", "visual"]) == 0
    assert reglas.cargar() == [Regla("visual", app="Noctalia Spectrum")]
    assert cli_visual == [["pactl", "move-source-output", "721", "elena_visual.monitor"]]
    assert cli.main(["mover", "noctalia", "visual"]) == 0
    assert cli_visual[-1] == ["pactl", "move-source-output", "721", "elena_visual.monitor"]
    assert cli.main(["regla", "noctalia", "--quitar"]) == 0
    assert reglas.cargar() == []
    # Una app que suena no va a la mezcla
    assert cli.main(["regla", "pacat", "visual"]) == 1


def test_cli_sin_visual(con_foto_sin_visual, capsys):
    assert cli.main(["visual", "musica", "on"]) == 1
    assert "no existe" in capsys.readouterr().err


@pytest.fixture
def con_foto_sin_visual(monkeypatch, grafo):
    monkeypatch.setattr(cli, "cargar_grafo", lambda: grafo)
    monkeypatch.setattr(cli, "ejecutar", lambda c: "")
