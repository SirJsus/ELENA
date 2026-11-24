"""Paso 5: cortes (pw-top), avisos y los comandos de las acciones."""

from pathlib import Path

import pytest

from conftest import DATOS, PROCESOS, nodo_crudo
from elena import acciones
from elena.avisos import calcular
from elena.modelo import ErrorElena, Grafo, Tipo, buscar_apps
from elena.xruns import Cortes, leer

# ---------- pw-top ----------


def test_leer_pw_top_real():
    tabla = leer((DATOS / "pw-top.txt").read_text())
    por_nombre = {n: e for n, e in tabla.values()}
    assert por_nombre["alsa_output.pci-0000_2f_00.4.analog-surround-51"] == 17  # «+ » quitado
    assert por_nombre["noctalia"] == 48                                           # «= » quitado
    assert por_nombre["elena-windows"] == 0
    assert por_nombre["Zen"] == 0                                                 # estado I, formato 44100
    assert "alsa_output.pci-0000_2d_00.1.hdmi-stereo-extra3" in por_nombre        # sin formato
    assert not any(n.startswith("elena_medidor.") for n in por_nombre)            # los medidores no cuentan
    assert "Dummy-Driver" not in por_nombre


def test_cortes_solo_cuenta_los_nuevos():
    t = [0.0]
    c = Cortes(reloj=lambda: t[0])
    c.actualizar({1: ("alsa", 17), 2: ("elena-windows", 0)})   # referencia: lo acumulado no cuenta
    assert c.recientes() == {}
    t[0] = 2
    c.actualizar({1: ("alsa", 17), 2: ("elena-windows", 3), 9: ("juego.exe", 2)})  # 9 es nuevo: desde 0
    assert c.recientes() == {"elena-windows": 3, "juego.exe": 2}
    t[0] = 4
    c.actualizar({1: ("alsa", 18), 2: ("elena-windows", 3), 9: ("juego.exe", 2)})
    assert c.recientes() == {"elena-windows": 3, "juego.exe": 2, "alsa": 1}
    t[0] = 33
    assert c.recientes() == {"alsa": 1}   # los de t=2 salieron de la ventana de 30 s


# ---------- avisos ----------


def _claves(lista):
    return {a.clave for a in lista}


def test_sin_avisos_en_la_foto(grafo):
    assert calcular(grafo) == []


def test_sin_elena(dump):
    sin = [o for o in dump if not str(((o.get("info") or {}).get("props") or {}).get("node.name", "")).startswith("elena_")]
    assert "sin-elena" in _claves(calcular(Grafo.desde_dump(sin, proceso_de=PROCESOS.get)))


def test_perfil_estereo(dump):
    for o in dump:
        if o.get("type") == "PipeWire:Interface:Device":
            for perfil in (o["info"].get("params") or {}).get("Profile") or []:
                perfil["name"] = "output:analog-stereo+input:analog-stereo"
    lista = calcular(Grafo.desde_dump(dump, proceso_de=PROCESOS.get))
    assert "perfil" in _claves(lista)
    assert next(a for a in lista if a.clave == "perfil").nivel == "alerta"


def test_sink_fisico_fuera_del_100(dump):
    fisico = nodo_crudo(dump, **{"node.name": "alsa_output.pci-0000_2f_00.4.analog-surround-51"})
    for p in fisico["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"] = [0.512] * 6  # 80 %
    lista = calcular(Grafo.desde_dump(dump, proceso_de=PROCESOS.get))
    assert next(a for a in lista if a.clave == "fisico").texto.startswith("El sink físico está en 80%")


@pytest.mark.parametrize(("entornos", "clave"), [
    ([{"HOME": "/x"}], "steam"),                                             # sin ELENA
    ([{"PULSE_SINK": "elena_juegos"}], "steam-pw"),                          # sin winepulse
    ([{"PULSE_SINK": "elena_juegos", "PROTON_USE_PIPEWIRE": "0"}], None),    # como steam.sh
    ([{"HOME": "/x"}, {"PULSE_SINK": "elena_juegos", "PROTON_USE_PIPEWIRE": "0"}], None),  # algún proceso con ELENA
    ([], None),                                                              # Steam cerrado
])
def test_steam(grafo, entornos, clave):
    claves = _claves(calcular(grafo, entornos))
    assert (clave in claves) if clave else not (claves & {"steam", "steam-pw"})


def _como_wine(dump, app, api_pulse: bool, bus: str, canales: int | None = None):
    """Convierte una app de la foto en un juego de Wine conectado a `bus`."""
    crudo = nodo_crudo(dump, **{"application.name": app})
    if not api_pulse:
        crudo["info"]["props"].pop("client.api", None)
    else:
        crudo["info"]["props"]["client.api"] = "pipewire-pulse"
    if canales:
        for f in crudo["info"]["params"].get("Format") or []:
            f["channels"] = canales
    destino = nodo_crudo(dump, **{"node.name": bus, "media.class": "Audio/Sink"})["id"]
    for o in dump:
        if o.get("type") == "PipeWire:Interface:Link" and o["info"]["output-node-id"] == crudo["id"]:
            o["info"]["input-node-id"] = destino
    pid = crudo["info"]["props"].get("application.process.id") or 50232
    return Grafo.desde_dump(dump, proceso_de={pid: "Control_DX12.exe", 50232: "Control_DX12.exe"}.get)


def test_juego_con_winepipewire(dump):
    g = _como_wine(dump, "elena-prueba-pw", api_pulse=False, bus="elena_juegos")
    assert any(a.clave.startswith("winepipewire-") for a in calcular(g))


def test_juego_fuera_de_juegos(dump):
    g = _como_wine(dump, "elena-prueba-pulse", api_pulse=True, bus="elena_sistema")
    lista = calcular(g)
    assert any(a.clave.startswith("fuera-") and "Sistema" in a.texto for a in lista)


def test_juego_estereo_en_juegos_es_info(dump):
    g = _como_wine(dump, "elena-prueba-pulse", api_pulse=True, bus="elena_juegos")
    (aviso,) = [a for a in calcular(g) if a.clave.startswith("estereo-")]
    assert aviso.nivel == "info" and "2 canales" in aviso.texto


def test_app_en_chat_sin_audifonos(dump):
    g = _como_wine(dump, "elena-prueba-pulse", api_pulse=True, bus="elena_chat")
    assert any(a.clave == "chat" and "no se oye" in a.texto for a in calcular(g))


def test_cortes_en_avisos(grafo):
    lista = calcular(grafo, cortes={"elena-windows": 2, "alsa": 7})
    niveles = {a.clave: a.nivel for a in lista}
    assert niveles == {"cortes-alsa": "alerta", "cortes-elena-windows": "aviso"}


# ---------- comandos de las acciones ----------


def test_cmd_probar(grafo, monkeypatch):
    monkeypatch.setattr(acciones, "RAIZ", Path("/repo"))
    juegos = grafo.nodo("elena_juegos")
    assert acciones.cmd_probar(juegos, "RL") == ["mise", "exec", "-C", "/repo", "--", "/repo/scripts/probar-canales.sh", "elena_juegos", "3"]
    assert acciones.cmd_probar(juegos, "LFE")[-1] == "6"
    assert acciones.cmd_probar(juegos, None)[-1] == "elena_juegos"   # todas, con voz
    (app,) = buscar_apps(grafo, "elena-prueba-pw")
    with pytest.raises(ErrorElena, match="no es una salida"):
        acciones.cmd_probar(app, "FL")


def test_cmd_scripts(monkeypatch):
    monkeypatch.setattr(acciones, "RAIZ", Path("/repo"))
    monkeypatch.setattr(acciones, "servicio_vm_instalado", lambda: False)
    assert acciones.cmd_aplicar()[-2:] == ["/repo/scripts/aplicar.sh", "todo"]
    assert acciones.cmd_quitar()[-1] == "/repo/scripts/quitar.sh"
    assert acciones.cmd_vm()[-1] == "/repo/vm/arrancar.sh"
    assert acciones.cmd_steam()[-1] == "/repo/scripts/steam.sh"


def test_lanzar_y_detener_un_grupo():
    p = acciones.lanzar(["sh", "-c", "sleep 30 & sleep 30"])
    assert p.poll() is None
    acciones.detener(p)
    assert p.wait(timeout=5) != 0


def test_detener_remata_a_quien_ignora_sigterm():
    """Como speaker-test: un hijo que ignora SIGTERM no debe quedar huérfano."""
    import os
    import time
    p = acciones.lanzar(["sh", "-c", "trap '' TERM; sleep 30 & wait"])
    time.sleep(0.2)
    acciones.detener(p, gracia=0.3)
    p.wait(timeout=2)   # recoger al líder (si no, queda como zombi y el grupo «existe»)
    time.sleep(0.3)     # el hijo huérfano lo recoge el sistema
    with pytest.raises(ProcessLookupError):
        os.killpg(p.pid, 0)  # el grupo ya no tiene ningún proceso


def test_ejecutar_con_salida():
    assert acciones.ejecutar_con_salida(["sh", "-c", "echo hola; echo error >&2; exit 3"]) == (3, "hola\nerror")



def test_medidores_no_cuentan_como_destino(dump):
    """Con la consola abierta, cada app también va a su medidor (elena_medidor.app.N)."""
    g = _como_wine(dump, "elena-prueba-pulse", api_pulse=True, bus="elena_sistema")
    (app,) = buscar_apps(g, "elena-prueba-pulse")
    medidor = {"id": 9999, "type": "PipeWire:Interface:Node",
               "info": {"props": {"node.name": "elena_medidor.app.1", "media.class": "Stream/Input/Audio"}, "params": {}}}
    link = {"id": 9998, "type": "PipeWire:Interface:Link", "info": {"output-node-id": app.id, "input-node-id": 9999}}
    g.aplicar([medidor, link])
    (aviso,) = [a for a in calcular(g) if a.clave.startswith("fuera-")]
    assert aviso.texto.endswith("está en ELENA · Sistema, no en Juegos.")
    assert [s.nombre for s in g.sinks_de(app)] == ["elena_sistema"]


# ---------- Calibración ----------

def _calibrado(dump, con_salida: bool, volumen: float = 1.0, musica_balance: bool = False):
    """La foto con Música yendo a elena_salida (y, si se pide, el sink de la calibración)."""
    nodo_crudo(dump, **{"node.name": "elena_musica.salida"})["info"]["props"]["target.object"] = "elena_salida"
    if con_salida:
        import copy
        fisico = nodo_crudo(dump, **{"node.name": "alsa_output.pci-0000_2f_00.4.analog-surround-51"})
        salida = copy.deepcopy(fisico)
        salida["id"] = 99999
        salida["info"]["props"].update({"node.name": "elena_salida", "node.description": "ELENA · Calibración"})
        for p in salida["info"]["params"]["Props"]:
            if "channelVolumes" in p:
                p["channelVolumes"] = [volumen] * 6
        dump.append(salida)
    if musica_balance:
        for p in nodo_crudo(dump, **{"node.name": "elena_musica", "media.class": "Audio/Sink"})["info"]["params"]["Props"]:
            if "channelVolumes" in p:
                p["channelVolumes"] = [0.1, 0.1, 0.1, 1.0, 0.2, 0.2]
    return Grafo.desde_dump(dump, proceso_de=PROCESOS.get)


def test_calibracion_sin_elena_salida(dump):
    lista = calcular(_calibrado(dump, con_salida=False))
    aviso = next(a for a in lista if a.clave == "sin-calibracion")
    assert aviso.nivel == "alerta" and "no suenan" in aviso.texto


def test_calibracion_bien(dump):
    assert not _claves(calcular(_calibrado(dump, con_salida=True))) & {"sin-calibracion", "calibracion-volumen"}


def test_calibracion_fuera_del_100_y_balance_doble(dump):
    claves = _claves(calcular(_calibrado(dump, con_salida=True, volumen=0.512, musica_balance=True)))
    assert {"calibracion-volumen", "doble-musica"} <= claves
