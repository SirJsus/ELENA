"""«Solo audífonos» (audifonos.py) sobre la foto de pw-dump, con elena_audifonos del conftest."""

import copy
import re
from pathlib import Path

import pytest

from conftest import AUDIFONOS, AUDIFONOS_SALIDA, PROCESOS, VIRTUOSO, params_audifonos
from elena import audifonos, cli
from elena.audifonos import Estado
from elena.modelo import SINK_FISICO, ErrorElena, Grafo
from elena.monitor import Lote
from elena.tui.app import ElenaApp
from elena.tui.vista import filas
from elena.vigilante import Vigilante

SALIDA = {"musica": 52, "juegos": 55, "sistema": 58}   # ids de <bus>.salida en la foto (y sus seriales)
CHAT_SALIDA = 62
CONF = Path(__file__).resolve().parents[1] / "plantillas" / "pipewire" / "50-elena-audifonos.conf"


def _nodo_sink(ident: int, nombre: str, descripcion: str) -> dict:
    return {"id": ident, "type": "PipeWire:Interface:Node",
            "info": {"state": "running",
                     "props": {"node.name": nombre, "node.description": descripcion, "media.class": "Audio/Sink",
                               "object.serial": ident},
                     "params": {"Props": [{"mute": False, "channelVolumes": [1.0, 1.0], "channelMap": ["FL", "FR"]}]}}}


def _link(ident: int, salida: int, entrada: int) -> dict:
    return {"id": ident, "type": "PipeWire:Interface:Link", "info": {"output-node-id": salida, "input-node-id": entrada}}


def enviar(dump: list[dict], *claves: str) -> list[dict]:
    """Las salidas de esos buses, ya en elena_audifonos (como tras `pactl move-sink-input`)."""
    dump = copy.deepcopy(dump)
    for o in dump:
        if o.get("type") == "PipeWire:Interface:Link" and o["info"].get("output-node-id") in {SALIDA[c] for c in claves}:
            o["info"]["input-node-id"] = AUDIFONOS
    return dump


def con_virtuoso(dump: list[dict], audifonos_a: str = VIRTUOSO) -> list[dict]:
    """Los Virtuoso prendidos: Chat sale a ellos y elena_audifonos a `audifonos_a`."""
    dump = copy.deepcopy(dump)
    dump += [_nodo_sink(9300, VIRTUOSO, "CORSAIR VIRTUOSO"), _nodo_sink(9301, "alsa_output.usb-Otro", "Otro"),
             _link(9310, CHAT_SALIDA, 9300), _link(9311, AUDIFONOS_SALIDA, 9300 if audifonos_a == VIRTUOSO else 9301)]
    return dump


def con_params(dump: list[dict], **cambios: float) -> list[dict]:
    dump = copy.deepcopy(dump)
    for o in dump:
        if o["id"] == AUDIFONOS:
            o["info"]["params"]["Props"][1]["params"] = params_audifonos(**cambios)
    return dump


def con_camino(dump: list[dict], binaural: float, directo: float) -> list[dict]:
    return con_params(dump, **{"salida_l:Gain 1": binaural, "salida_r:Gain 1": binaural,
                               "salida_l:Gain 2": directo, "salida_r:Gain 2": directo})


def g(dump: list[dict]) -> Grafo:
    return Grafo.desde_dump(dump, proceso_de=PROCESOS.get)


def cmds(pasos) -> list[list[str]]:
    return [p.cmd for p in pasos]


# ---------- el modelo ----------

def test_estado_en_la_foto(grafo):
    au = grafo.audifonos()
    assert au.enviados == [] and au.destinos == [] and au.chat == []
    assert au.esperando == VIRTUOSO
    assert au.camino == "directo"
    assert sorted(au.salidas) == ["juegos", "musica", "sistema"]


def test_camino_binaural_y_mezclado(dump):
    assert g(con_camino(dump, 0.3, 0.0)).audifonos().camino == "binaural"
    assert g(con_camino(dump, 0.3, 0.5)).audifonos().camino is None     # los dos: no se sabe


def test_sin_elena_audifonos(dump):
    sin = [o for o in dump if o["id"] not in (AUDIFONOS, AUDIFONOS_SALIDA)]
    assert g(sin).audifonos() is None
    assert audifonos.plan(g(sin), Estado(activo=True)) == []
    with pytest.raises(ErrorElena, match="mise run aplicar"):
        audifonos.cambiar(g(sin), Estado(activo=True), lambda c: None)


# ---------- el plan ----------

def test_prender_mueve_los_tres_buses(grafo):
    assert cmds(audifonos.plan(grafo, Estado(activo=True))) == [
        ["pactl", "move-sink-input", "52", "elena_audifonos"],
        ["pactl", "move-sink-input", "55", "elena_audifonos"],
        ["pactl", "move-sink-input", "58", "elena_audifonos"],
    ]


def test_apagado_y_en_su_lugar_no_hace_nada(grafo):
    assert audifonos.plan(grafo, Estado(activo=False)) == []


def test_prendido_y_completo_no_hace_nada(dump):
    assert audifonos.plan(g(enviar(dump, "musica", "juegos", "sistema")), Estado(activo=True)) == []


def test_solo_mueve_lo_que_falta(dump):
    assert cmds(audifonos.plan(g(enviar(dump, "musica", "sistema")), Estado(activo=True))) == [
        ["pactl", "move-sink-input", "55", "elena_audifonos"]]


def test_apagar_regresa_a_su_destino(dump):
    pasos = audifonos.plan(g(enviar(dump, "musica", "juegos")), Estado(activo=False))
    assert cmds(pasos) == [["pactl", "move-sink-input", "52", SINK_FISICO],
                           ["pactl", "move-sink-input", "55", SINK_FISICO]]
    assert pasos[0].texto == "Música → Bocinas 5.1"


def test_sigue_a_chat(dump):
    pasos = audifonos.plan(g(enviar(con_virtuoso(dump, "alsa_output.usb-Otro"), "musica", "juegos", "sistema")),
                           Estado(activo=True))
    assert cmds(pasos) == [["pactl", "move-sink-input", str(AUDIFONOS_SALIDA), VIRTUOSO]]
    assert "a donde va Chat" in pasos[0].texto
    # Si ya van al mismo lado, nada
    assert audifonos.plan(g(enviar(con_virtuoso(dump), "musica", "juegos", "sistema")), Estado(activo=True)) == []


def test_camino(grafo):
    (paso,) = audifonos.plan(grafo, Estado(activo=False, camino="binaural"))
    assert paso.cmd[:4] == ["pw-cli", "set-param", str(AUDIFONOS), "Props"]
    assert paso.cmd[4] == ('{ params = [ "salida_l:Gain 1" 0.3 "salida_r:Gain 1" 0.3 '
                           '"salida_l:Gain 2" 0 "salida_r:Gain 2" 0 ] }')


def test_graves_y_lfe(grafo):
    (paso,) = audifonos.plan(grafo, Estado(graves=9.0, frecuencia=80.0, lfe=4.0))
    assert paso.cmd[4] == ('{ params = [ "dir_l:Gain 4" 1.58489 "dir_r:Gain 4" 1.58489 "bin_l:Gain 6" 1.58489 '
                           '"bin_r:Gain 6" 1.58489 "graves_l:Gain" 9 "graves_r:Gain" 9 '
                           '"graves_l:Freq" 80 "graves_r:Freq" 80 ] }')
    assert "graves +9.0 dB en 80 Hz · LFE +4.0 dB" in paso.texto


def test_conf_viejo_sin_graves(dump):
    """Un elena_audifonos sin el estante (antes de aplicar): no manda lo que no existe y pide aplicar."""
    sin = copy.deepcopy(dump)
    for o in sin:
        if o["id"] == AUDIFONOS:
            p = o["info"]["params"]["Props"][1]["params"]
            o["info"]["params"]["Props"][1]["params"] = [x for k, v in zip(p[::2], p[1::2])
                                                         if not k.startswith("graves_") for x in (k, v)]
    assert audifonos.plan(g(sin), Estado(graves=9.0)) == []
    assert "estante de graves" in audifonos.avisos(g(sin), Estado())[0]


def test_ajustar():
    assert audifonos.ajustar(Estado(), graves=8.0, frecuencia=None) == Estado(graves=8.0)
    with pytest.raises(ErrorElena, match="graves va de -6 a 12 dB"):
        audifonos.ajustar(Estado(), graves=20.0)


def test_avisa_si_los_audifonos_estan_apagados(grafo, dump):
    assert "no están conectados" in audifonos.avisos(grafo, Estado(activo=True))[0]
    assert audifonos.avisos(g(con_virtuoso(dump)), Estado(activo=True)) == []
    assert audifonos.avisos(grafo, Estado(activo=False)) == []


# ---------- el estado ----------

def test_siguiente():
    e = Estado(graves=3.0)
    assert audifonos.siguiente(e, "on") == Estado(True, "directo", graves=3.0)     # no pierde los graves
    assert audifonos.siguiente(Estado(True), "alternar") == Estado(False, "directo")
    assert audifonos.siguiente(Estado(True), "binaural") == Estado(True, "binaural")
    with pytest.raises(ErrorElena):
        audifonos.siguiente(e, "quizá")


def test_guardar_y_leer(tmp_path):
    ruta = tmp_path / "x" / "estado.json"
    assert audifonos.leer(ruta) == Estado()
    audifonos.guardar(Estado(True, "binaural", graves=8.5, frecuencia=90.0, lfe=12.0), ruta)
    assert audifonos.leer(ruta) == Estado(True, "binaural", graves=8.5, frecuencia=90.0, lfe=12.0)
    ruta.write_text("{ roto")
    assert audifonos.leer(ruta) == Estado()
    ruta.write_text('{"activo": true, "camino": "mono", "graves": 99, "lfe": "x"}')   # lo inválido, por omisión
    assert audifonos.leer(ruta) == Estado(True)
    ruta.write_text('{"activo": true, "camino": "directo"}')                       # el estado de antes de los graves
    assert audifonos.leer(ruta) == Estado(True)


def test_cambiar_guarda_y_ejecuta(grafo):
    mandados = []
    hechos = audifonos.cambiar(grafo, Estado(activo=True), mandados.append)
    assert len(mandados) == 3 and audifonos.leer().activo
    assert hechos[:3] == ["Música → Audífonos", "Juegos → Audífonos", "Sistema → Audífonos"]
    assert hechos[-1].startswith("⚠ Los audífonos no están conectados")


# ---------- el vigilante ----------

async def _flujo(*lotes):
    for lote in lotes:
        yield lote


async def test_vigilante_mantiene_el_modo(tmp_path, dump):
    audifonos.guardar(Estado(activo=True, camino="binaural"))
    comandos, lineas = [], []
    v = Vigilante(tmp_path / "reglas.toml", ejecutar=lambda c: comandos.append(c) or "", escribir=lineas.append)
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    # El grafo no cambia (la foto): la segunda vez no repite nada (3 s para mover, una vez el camino)
    await v.correr(_flujo(Lote(dump, reinicio=True), Lote([], reinicio=False)))
    assert [c[:2] for c in comandos] == [["pactl", "move-sink-input"]] * 3 + [["pw-cli", "set-param"]]
    assert "Solo audífonos: Música → Audífonos" in lineas


async def test_vigilante_apagado_no_toca_nada(tmp_path, dump):
    comandos = []
    v = Vigilante(tmp_path / "reglas.toml", ejecutar=lambda c: comandos.append(c) or "", escribir=lambda t: None)
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    await v.correr(_flujo(Lote(dump, reinicio=True)))
    assert comandos == []


# ---------- CLI ----------

@pytest.fixture
def con_foto(monkeypatch, grafo):
    comandos = []
    monkeypatch.setattr(cli, "cargar_grafo", lambda: grafo)
    monkeypatch.setattr(cli, "ejecutar", lambda cmd: comandos.append(cmd) or "")
    monkeypatch.setattr("elena.vigilante.corriendo", lambda: True)
    return comandos


def test_cli_estado(con_foto, capsys):
    assert cli.main(["audifonos"]) == 0
    assert capsys.readouterr().out.strip() == (
        "Audífonos: solo audífonos off · directo · graves +6.0 dB en 105 Hz · LFE +10.0 dB · → esperando a "
        "usb-Corsair_CORSAIR_VIRTUOSO_Wireless_Gaming_Headset_0000000000000000-00.analog-stereo")
    assert con_foto == []


def test_cli_on_y_directo(con_foto, capsys):
    assert cli.main(["audifonos", "on"]) == 0
    salida = capsys.readouterr().out
    assert "Juegos → Audífonos" in salida and "⚠ Los audífonos no están conectados" in salida
    assert len(con_foto) == 3 and audifonos.leer() == Estado(True, "directo")
    cli.main(["audifonos", "binaural"])
    assert con_foto[-1][:2] == ["pw-cli", "set-param"] and audifonos.leer() == Estado(True, "binaural")


def test_cli_graves_y_lfe(con_foto, capsys):
    assert cli.main(["audifonos", "graves", "8", "--hz", "90"]) == 0
    assert audifonos.leer() == Estado(graves=8.0, frecuencia=90.0)
    assert '"graves_l:Gain" 8' in con_foto[-1][4] and '"graves_l:Freq" 90' in con_foto[-1][4]
    assert cli.main(["audifonos", "lfe", "-3"]) == 0
    assert audifonos.leer().lfe == -3.0 and '"dir_l:Gain 4" 0.707946' in con_foto[-1][4]
    assert cli.main(["audifonos", "lfe"]) == 1
    assert cli.main(["audifonos", "graves", "30"]) == 1
    assert "graves +8.0 dB en 90 Hz · LFE -3.0 dB" in capsys.readouterr().out


def test_estado_general_y_json(con_foto, capsys):
    cli.main(["estado"])
    assert "Audífonos: solo audífonos off · directo · graves +6.0 dB" in capsys.readouterr().out
    import json
    cli.main(["estado", "--json"])
    datos = json.loads(capsys.readouterr().out)
    assert datos["audifonos"]["activo"] is False and datos["audifonos"]["camino"] == "directo"
    assert datos["audifonos"]["graves"] == 6.0 and datos["audifonos"]["lfe"] == 10.0


# ---------- consola ----------

async def test_consola_h_y_H(grafo):
    comandos: list[list[str]] = []
    app = ElenaApp(grafo=grafo, ejecutar=lambda cmd: comandos.append(cmd) or "")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("h")
        await pilot.pause(0.1)
        assert audifonos.leer().activo
        await pilot.press("H")
        await pilot.pause(0.1)
        assert "Audífonos" in app._cabecera().plain and "solo on" in app._cabecera().plain
    assert [c[3] for c in comandos[:3]] == ["elena_audifonos"] * 3
    # La foto no cambia: H vuelve a mandar las tres (mover al mismo sink no hace nada) y el camino
    assert comandos[-1][:2] == ["pw-cli", "set-param"] and audifonos.leer().camino == "binaural"


def test_vista_muestra_lo_enviado(dump):
    textos = [f.texto.plain for f in filas(g(enviar(con_virtuoso(dump), "musica", "juegos")))]
    assert any(t.startswith("Música") and "→ ELENA · Audífonos → CORSAIR VIRTUOSO" in t for t in textos)
    assert any("solo audífonos: Música, Juegos" in t for t in textos)


# ---------- la config ----------

def test_conf_y_niveles_coinciden():
    """Los controles del .conf son los de por omisión de audifonos.py (Estado())."""
    texto = CONF.read_text()
    conf = {}
    for nodo, cuerpo in re.findall(r'name = (\w+)\s+control = \{([^}]*)\}', texto):
        for clave, valor in re.findall(r'"([^"]+)" = ([-\d.]+)', cuerpo):
            conf[f"{nodo}:{clave}"] = float(valor)
    for clave, valor in audifonos.controles(Estado()).items():
        assert conf[clave] == pytest.approx(valor, abs=1e-3), clave
    assert "flags = [ nofail ]" in texto
    assert 'node.name          = "elena_audifonos.salida"' in texto


def test_aviso_si_los_audifonos_no_estan_al_100(dump):
    from elena.avisos import calcular
    assert "audifonos-volumen" not in {a.clave for a in calcular(g(con_virtuoso(dump)))}
    bajo = con_virtuoso(dump)
    bajo[-4]["info"]["params"]["Props"][0]["channelVolumes"] = [0.064, 0.064]   # 40 %
    (aviso,) = [a for a in calcular(g(bajo)) if a.clave == "audifonos-volumen"]
    assert "CORSAIR VIRTUOSO está en 40%" in aviso.texto


async def test_vigilante_regresa_los_audifonos_al_100(tmp_path, dump):
    bajo = con_virtuoso(dump)
    bajo[-4]["info"]["params"]["Props"][0]["channelVolumes"] = [0.064, 0.064]   # 40 %
    comandos, lineas = [], []
    v = Vigilante(tmp_path / "reglas.toml", ejecutar=lambda c: comandos.append(c) or "", escribir=lineas.append)
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    await v.correr(_flujo(Lote(bajo, reinicio=True), Lote([], reinicio=False)))
    assert comandos == [["pactl", "set-sink-volume", VIRTUOSO, "1.000000", "1.000000"]]   # una vez
    assert "CORSAIR VIRTUOSO bajó a 40 %: regresa al 100 %" in lineas[-1]
    comandos.clear()
    v2 = Vigilante(tmp_path / "reglas.toml", ejecutar=lambda c: comandos.append(c) or "", escribir=lambda t: None)
    v2.grafo = Grafo(proceso_de=PROCESOS.get)
    await v2.correr(_flujo(Lote(con_virtuoso(dump), reinicio=True)))
    assert comandos == []
