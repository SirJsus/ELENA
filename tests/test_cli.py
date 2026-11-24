"""El comando `elena` sobre la foto de pw-dump, sin tocar PipeWire."""

import json

import pytest

from elena import cli
from elena.modelo import ErrorElena, pct_a_lineal


@pytest.fixture
def con_foto(monkeypatch, grafo):
    """`elena` lee la foto en lugar de PipeWire y anota los comandos en lugar de ejecutarlos."""
    comandos = []
    monkeypatch.setattr(cli, "cargar_grafo", lambda: grafo)
    monkeypatch.setattr(cli, "ejecutar", lambda cmd: comandos.append(cmd) or "")
    return comandos


def test_estado_texto(con_foto, capsys):
    assert cli.main(["estado"]) == 0
    salida = capsys.readouterr().out
    assert "Física:   Bocinas 5.1" in salida
    assert "Mic:      voz off · ruido on · puente off" in salida
    assert "Juegos · elena_juegos · 6 ch → Bocinas 5.1" in salida
    assert "  ▸ elena-prueba-pw · Prueba 5.1 · 6 ch · pw · 100% · serial 1331" in salida
    assert "Chat · elena_chat · 2 ch → esperando a alsa_output.usb-Corsair" in salida
    assert "◂ Noctalia Spectrum · 2 ch · pw · 100% · serial 721 ← elena_sistema" in salida
    assert "  ELENA · Mic (elena_mic) · 1 ch" in salida
    assert "Internos" not in salida


def test_estado_todo_muestra_internos(con_foto, capsys):
    cli.main(["estado", "--todo"])
    assert "elena_juegos.salida" in capsys.readouterr().out


def test_estado_json(con_foto, capsys):
    cli.main(["estado", "--json"])
    datos = json.loads(capsys.readouterr().out)
    assert datos["perfil_51"] is True
    juegos = datos["buses"][1]
    assert juegos["clave"] == "juegos" and juegos["bus"]["canales"] == 6
    assert juegos["apps"][0]["visible"] == "elena-prueba-pw"
    assert datos["buses"][3]["esperando"].startswith("alsa_output.usb-Corsair")


def test_mover(con_foto, capsys):
    assert cli.main(["mover", "pacat", "juegos"]) == 0
    assert con_foto == [["pactl", "move-sink-input", "1332", "elena_juegos"]]


def test_error_sin_traza(con_foto, capsys):
    assert cli.main(["mover", "prueba", "juegos"]) == 1
    assert capsys.readouterr().err.startswith("ERROR: «prueba» coincide con varias apps")
    assert con_foto == []


def test_volumen_relativo(con_foto):
    cli.main(["volumen", "sistema", "+10"])  # Sistema está al 50 %
    (cmd,) = con_foto
    assert cmd[:3] == ["pactl", "set-sink-volume", "elena_sistema"]
    assert [float(x) for x in cmd[3:]] == pytest.approx([pct_a_lineal(60)] * 2, abs=1e-6)


def test_silenciar_alterna(con_foto, capsys):
    cli.main(["silenciar", "juegos"])
    assert con_foto == [["pactl", "set-sink-mute", "elena_juegos", "toggle"]]
    assert "silenciado" in capsys.readouterr().out


@pytest.mark.parametrize(("valor", "esperado"), [("80", 80), ("80%", 80), ("+5", 105), ("-30", 70), ("200", 150), ("-500", 0)])
def test_nuevo_volumen(grafo, valor, esperado):
    vols = cli.nuevo_volumen(grafo.nodo("elena_juegos"), valor)  # Juegos está al 100 %
    assert max(vols) == pytest.approx(pct_a_lineal(esperado))


def test_nuevo_volumen_invalido(grafo):
    with pytest.raises(ErrorElena, match="Volumen inválido"):
        cli.nuevo_volumen(grafo.nodo("elena_juegos"), "mucho")
