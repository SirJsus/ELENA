"""Los comandos que arman las acciones (no se ejecuta nada)."""

import pytest

from elena import acciones
from elena.acciones import cmd_mic, cmd_mover, cmd_silencio, cmd_volumen, ejecutar
from elena.modelo import ErrorElena, buscar_apps


def test_mover_usa_el_serial_y_el_node_name(grafo):
    (app,) = buscar_apps(grafo, "elena-prueba-pulse")
    assert cmd_mover(app, grafo.nodo("elena_juegos")) == ["pactl", "move-sink-input", "1332", "elena_juegos"]


def test_mover_solo_apps_hacia_salidas(grafo):
    (app,) = buscar_apps(grafo, "elena-prueba-pulse")
    with pytest.raises(ErrorElena, match="no es una salida"):
        cmd_mover(app, grafo.nodo("elena_mic"))
    with pytest.raises(ErrorElena, match="no es una app"):
        cmd_mover(grafo.nodo("elena_juegos"), grafo.nodo("elena_musica"))


def test_volumen_un_valor_por_canal(grafo):
    cmd = cmd_volumen(grafo.nodo("elena_juegos"), (0.125, 0.125, 1, 0.343, 1, 1))
    assert cmd == ["pactl", "set-sink-volume", "elena_juegos",
                   "0.125000", "0.125000", "1.000000", "0.343000", "1.000000", "1.000000"]


def test_volumen_de_app_fuente_y_grabacion(grafo):
    (app,) = buscar_apps(grafo, "elena-prueba-pw")
    assert cmd_volumen(app, (1,) * 6)[:3] == ["pactl", "set-sink-input-volume", "1331"]
    assert cmd_volumen(grafo.nodo("elena_mic"), (1,))[:3] == ["pactl", "set-source-volume", "elena_mic"]
    assert cmd_volumen(grafo.nodo("noctalia"), (1, 1))[:2] == ["pactl", "set-source-output-volume"]


def test_volumen_con_canales_de_mas(grafo):
    with pytest.raises(ErrorElena, match="tiene 6 canales y recibí 2"):
        cmd_volumen(grafo.nodo("elena_juegos"), (1, 1))


def test_silencio(grafo):
    bus = grafo.nodo("elena_juegos")
    assert cmd_silencio(bus, True) == ["pactl", "set-sink-mute", "elena_juegos", "1"]
    assert cmd_silencio(bus, False)[-1] == "0"
    assert cmd_silencio(bus, None)[-1] == "toggle"


def test_mic_usa_el_script(monkeypatch):
    monkeypatch.setattr(acciones, "RAIZ", acciones.Path("/repo"))
    assert cmd_mic("ruido", True) == ["/repo/scripts/mic.sh", "ruido", "on"]
    with pytest.raises(ErrorElena, match="Interruptor desconocido"):
        cmd_mic("eco", True)


def test_ejecutar_traduce_errores():
    with pytest.raises(ErrorElena, match="No encuentro"):
        ejecutar(["/no/existe/elena"])
    with pytest.raises(ErrorElena, match="Falló"):
        ejecutar(["false"])
