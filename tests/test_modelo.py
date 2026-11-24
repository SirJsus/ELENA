"""El modelo del grafo, sobre la foto real de `pw-dump` (ver conftest.py)."""

import copy
import math

import pytest

from elena.modelo import (
    PERFIL_51, SINK_FISICO, ErrorElena, Grafo, Tipo,
    buscar_apps, buscar_objetivo, buscar_sink, escalar, lineal_a_db, lineal_a_pct, pct_a_lineal,
)
from conftest import PROCESOS, nodo_crudo

VIRTUOSO = "alsa_output.usb-Corsair_CORSAIR_VIRTUOSO_Wireless_Gaming_Headset_0000000000000000-00.analog-stereo"


# ---------- vista general ----------

def test_perfil_y_por_defecto(grafo):
    assert grafo.perfil() == PERFIL_51
    assert grafo.sink_por_defecto == "elena_sistema"
    assert grafo.fuente_por_defecto == "elena_mic"


def test_buses(grafo):
    buses = {b.definicion.clave: b for b in grafo.buses()}
    assert list(buses) == ["musica", "juegos", "sistema", "chat", "audifonos"]
    for clave in ("musica", "juegos", "sistema"):
        assert [d.nombre for d in buses[clave].destinos] == [SINK_FISICO]
        assert buses[clave].esperando is None
    assert buses["musica"].nodo.canales == 6 and buses["sistema"].nodo.canales == 2
    assert buses["musica"].apps == []


def test_chat_espera_al_virtuoso_apagado(grafo):
    chat = next(b for b in grafo.buses() if b.definicion.clave == "chat")
    assert chat.destinos == []
    assert chat.esperando == VIRTUOSO


def test_apps_en_sus_buses(grafo):
    buses = {b.definicion.clave: b for b in grafo.buses()}
    (juego,) = buses["juegos"].apps
    assert (juego.visible, juego.api, juego.canales) == ("elena-prueba-pw", "pw", 6)
    assert juego.posiciones == ("FL", "FR", "FC", "LFE", "SL", "SR")
    assert juego.proceso == "pw-cat"  # el PID de un stream nativo sale de su cliente
    (nav,) = buses["sistema"].apps
    assert (nav.visible, nav.media, nav.api, nav.canales) == ("elena-prueba-pulse", "Navegador", "pulse", 2)
    assert nav.proceso == "pacat"


def test_clasificacion(grafo):
    tipo = {n.nombre: n.tipo for n in grafo.nodos.values()}
    assert tipo["elena_juegos"] == Tipo.BUS
    assert tipo["elena_juegos.salida"] == Tipo.INTERNO
    assert tipo["elena_voz"] == Tipo.INTERNO
    assert tipo["elena_mic"] == Tipo.FUENTE
    assert tipo[SINK_FISICO] == Tipo.SALIDA
    assert tipo["noctalia"] == Tipo.GRABACION


def test_grabaciones_y_origenes(grafo):
    (noctalia,) = grafo.de_tipo(Tipo.GRABACION)
    assert noctalia.visible == "Noctalia Spectrum"
    assert [o.nombre for o in grafo.origenes(noctalia)] == ["elena_sistema"]


def test_mic(grafo):
    mic = grafo.mic()
    assert (mic.voz, mic.ruido, mic.puente) == ("off", "on", "off")


def test_mic_ruido_invalido_si_los_dos_caminos_suenan(dump):
    for nombre in ("elena_directo", "elena_limpio_a_voz"):
        for p in nodo_crudo(dump, **{"node.name": nombre})["info"]["params"]["Props"]:
            if "mute" in p:
                p["mute"] = False
    assert Grafo.desde_dump(dump, proceso_de=PROCESOS.get).mic().ruido == "inválido"


def test_sin_elena_no_hay_buses_ni_mic(dump):
    sin_elena = [o for o in dump if not str(((o.get("info") or {}).get("props") or {}).get("node.name", "")).startswith("elena_")]
    grafo = Grafo.desde_dump(sin_elena, proceso_de=PROCESOS.get)
    assert all(b.nodo is None for b in grafo.buses())
    assert grafo.mic() is None


def test_wine_sin_nombres_usa_el_proceso(dump):
    """winepulse deja application.name y node.name en blanco."""
    props = nodo_crudo(dump, **{"application.name": "elena-prueba-pulse"})["info"]["props"]
    props["application.name"] = " "
    props["node.name"] = " "
    props["media.name"] = " "
    grafo = Grafo.desde_dump(dump, proceso_de={50233: "Control_DX12.exe"}.get)
    (app,) = [n for n in grafo.de_tipo(Tipo.APP) if n.pid == 50233]
    assert app.visible == "Control_DX12.exe"


# ---------- cambios en vivo ----------

def test_baja_de_una_app(grafo, dump):
    juego = nodo_crudo(dump, **{"application.name": "elena-prueba-pw"})
    links = [o["id"] for o in dump if o.get("type") == "PipeWire:Interface:Link"
             and juego["id"] in (o["info"]["output-node-id"], o["info"]["input-node-id"])]
    grafo.aplicar([{"id": i, "info": None} for i in [*links, juego["id"]]])
    assert next(b for b in grafo.buses() if b.definicion.clave == "juegos").apps == []
    assert juego["id"] not in grafo.nodos


def test_cambio_de_volumen_reemplaza_el_objeto(grafo, dump):
    # Copia: el grafo guarda referencias a los objetos de `dump`
    bus = copy.deepcopy(nodo_crudo(dump, **{"node.name": "elena_juegos", "media.class": "Audio/Sink"}))
    for p in bus["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"] = [0.125] * 6
            p["mute"] = True
    assert grafo.nodo("elena_juegos").volumen_pct == pytest.approx(100)
    grafo.aplicar([bus])
    assert grafo.nodo("elena_juegos").volumen_pct == pytest.approx(50)
    assert grafo.nodo("elena_juegos").silencio


def test_metadatos_delta(grafo, dump):
    meta = next(o for o in dump if o.get("type") == "PipeWire:Interface:Metadata"
                and o["props"]["metadata.name"] == "default")
    delta = meta | {"metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                                  "value": {"name": "elena_juegos"}}]}
    grafo.aplicar([delta])
    assert grafo.sink_por_defecto == "elena_juegos"
    assert grafo.fuente_por_defecto == "elena_mic"  # las demás claves se conservan
    grafo.aplicar([meta | {"metadata": [{"subject": 0, "key": "default.audio.sink", "value": None}]}])
    assert grafo.sink_por_defecto is None


def test_metadatos_completos_reemplazan(grafo, dump):
    meta = next(o for o in dump if o.get("type") == "PipeWire:Interface:Metadata"
                and o["props"]["metadata.name"] == "default")
    grafo.aplicar([meta | {"metadata": [], "elena.completo": True}])
    assert grafo.sink_por_defecto is None and grafo.fuente_por_defecto is None


def test_estado_completo_borra_lo_anterior(grafo):
    grafo.aplicar([], completo=True)
    assert grafo.nodos == {} and grafo.links == set()


# ---------- volumen ----------

def test_escala_cubica():
    assert lineal_a_pct(0.125) == pytest.approx(50)
    assert pct_a_lineal(70) == pytest.approx(0.343)
    assert lineal_a_db(0.125) == pytest.approx(-18.06, abs=0.01)
    assert lineal_a_db(0) == -math.inf


def test_escalar_conserva_el_balance_en_db():
    antes = (pct_a_lineal(50), pct_a_lineal(50), 1.0, pct_a_lineal(70), 1.0, 1.0)
    despues = escalar(antes, 90)
    assert lineal_a_pct(max(despues)) == pytest.approx(90)
    dif = [lineal_a_db(a) - lineal_a_db(b) for a, b in zip(antes, despues)]
    assert max(dif) == pytest.approx(min(dif))


def test_escalar_desde_cero():
    assert escalar((0.0, 0.0), 50) == pytest.approx((0.125, 0.125))


def test_balanceado(grafo):
    assert grafo.nodo("elena_juegos").balanceado


# ---------- búsqueda (CLI) ----------

def test_buscar_bus_con_o_sin_acentos(grafo):
    for texto in ("musica", "Música", "MUSICA", "elena_musica"):
        assert buscar_sink(grafo, texto).nombre == "elena_musica"


def test_buscar_salida_por_parte_de_su_nombre(grafo):
    assert buscar_sink(grafo, "hdmi").nombre.startswith("alsa_output.pci-0000_2d_00.1.hdmi")


def test_buscar_salida_inexistente(grafo):
    with pytest.raises(ErrorElena, match="No encuentro el destino «virtuoso»"):
        buscar_sink(grafo, "virtuoso")


def test_buscar_apps_exacta_parcial_y_ambigua(grafo):
    assert [a.visible for a in buscar_apps(grafo, "elena-prueba-pw")] == ["elena-prueba-pw"]
    assert [a.visible for a in buscar_apps(grafo, "pacat")] == ["elena-prueba-pulse"]  # por proceso
    assert [a.visible for a in buscar_apps(grafo, "navegador")] == ["elena-prueba-pulse"]  # por media.name
    with pytest.raises(ErrorElena, match="coincide con varias apps"):
        buscar_apps(grafo, "prueba")


def test_buscar_app_por_serial(grafo):
    assert [a.visible for a in buscar_apps(grafo, "1331")] == ["elena-prueba-pw"]
    with pytest.raises(ErrorElena, match="serial o id 99999"):
        buscar_apps(grafo, "99999")


def test_buscar_objetivo(grafo):
    assert [n.nombre for n in buscar_objetivo(grafo, "juegos")] == ["elena_juegos"]
    assert [n.nombre for n in buscar_objetivo(grafo, "elena_mic")] == ["elena_mic"]
    assert [n.visible for n in buscar_objetivo(grafo, "noctalia")] == ["Noctalia Spectrum"]
    with pytest.raises(ErrorElena, match="nodo interno"):
        buscar_objetivo(grafo, "elena_voz")


# ---------- volumen restaurado con otros canales (la VM, 2026-11-06) ----------

def _con_volumen(dump, app: str, valores: list[float], mapa: list[str]) -> None:
    crudo = nodo_crudo(dump, **{"application.name": app})
    for p in crudo["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"], p["channelMap"] = valores, mapa


def test_volumen_estereo_guardado_en_un_stream_51(dump):
    """WirePlumber restauró a la VM (5.1) un volumen guardado en estéreo: [FL, FR]. Se acomoda por
    posición y los canales que faltan toman el más alto, para mandar 6 valores a pactl."""
    from elena.acciones import cmd_volumen
    _con_volumen(dump, "elena-prueba-pw", [0.3, 0.6], ["FL", "FR"])
    grafo = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)
    app = next(n for n in grafo.de_tipo(Tipo.APP) if n.app == "elena-prueba-pw")
    assert app.canales == 6 and app.volumenes == (0.3, 0.6, 0.6, 0.6, 0.6, 0.6)
    cmd = cmd_volumen(app, escalar(app.volumenes, 50))
    assert len(cmd) == 3 + 6


def test_volumen_51_guardado_en_un_stream_estereo(dump):
    _con_volumen(dump, "elena-prueba-pulse", [0.2, 0.4, 0.5, 0.5, 0.1, 0.1], ["FL", "FR", "FC", "LFE", "RL", "RR"])
    grafo = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)
    app = next(n for n in grafo.de_tipo(Tipo.APP) if n.app == "elena-prueba-pulse")
    assert app.canales == 2 and app.volumenes == (0.2, 0.4)


def test_volumen_sin_mapa_se_rellena_en_orden(dump):
    _con_volumen(dump, "elena-prueba-pw", [0.3, 0.6], [])
    grafo = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)
    app = next(n for n in grafo.de_tipo(Tipo.APP) if n.app == "elena-prueba-pw")
    assert app.volumenes == (0.3, 0.6, 0.6, 0.6, 0.6, 0.6)
