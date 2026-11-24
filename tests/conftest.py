"""Datos de prueba: una foto real de `pw-dump` (2026-11-06), recortada y anonimizada.

`pw-dump-con-apps.json` se capturó con ELENA aplicado y dos apps mudas (ceros) sonando:
- `elena-prueba-pw`: pw-cat nativo, 6 canales (FL FR FC LFE SL SR), hacia elena_juegos.
- `elena-prueba-pulse`: pacat por pipewire-pulse, estéreo, en elena_sistema (el sink por defecto).
Los audífonos (un Corsair Virtuoso) estaban apagados (elena_chat esperando) y el mic en voz off,
ruido on y puente off. Solo se guardaron nodos, links, metadatos, dispositivos y clientes, sin los
params que el modelo no usa, con el machine-id y el número de serie en ceros, y usuario y host genéricos.

El equipo de las pruebas es el de la foto, no el de `local/equipo.toml`: se fija aquí, antes de
importar ELENA (los módulos leen las variables al cargarse). `ELENA_LOCAL` apunta a una carpeta que
no existe, así que ninguna prueba lee ni escribe la config real.
"""

import copy
import json
import os
from pathlib import Path

EQUIPO_DE_PRUEBA = {
    "ELENA_TARJETA": "alsa_card.pci-0000_2f_00.4",
    "ELENA_PERFIL": "output:analog-surround-51+input:analog-stereo",
    "ELENA_SINK_FISICO": "alsa_output.pci-0000_2f_00.4.analog-surround-51",
    "ELENA_MIC_FISICO": "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo-input",
    "ELENA_AUDIFONOS": "alsa_output.usb-Corsair_CORSAIR_VIRTUOSO_Wireless_Gaming_Headset_0000000000000000-00.analog-stereo",
}
os.environ.update(EQUIPO_DE_PRUEBA)
os.environ["ELENA_LOCAL"] = str(Path(__file__).resolve().parents[1] / ".cache" / "pytest" / "sin-local")
# pytest crea --basetemp (.cache/pytest/tmp) pero no su carpeta padre: en un clon nuevo no existe
(Path(__file__).resolve().parents[1] / ".cache" / "pytest").mkdir(parents=True, exist_ok=True)

import pytest

from elena.modelo import Grafo

DATOS = Path(__file__).parent / "datos"
PROCESOS = {50232: "pw-cat", 50233: "pacat"}  # PIDs de la foto


@pytest.fixture(autouse=True)
def _estado_audifonos(tmp_path, monkeypatch):
    """Ninguna prueba lee ni escribe el estado real de «solo audífonos» (.cache/audifonos/)."""
    from elena import audifonos
    monkeypatch.setattr(audifonos, "ESTADO", tmp_path / "audifonos" / "estado.json")


@pytest.fixture(scope="session")
def _dump_original() -> list[dict]:
    return con_audifonos(json.loads((DATOS / "pw-dump-con-apps.json").read_text()))


@pytest.fixture
def dump(_dump_original) -> list[dict]:
    """Copia que cada prueba puede modificar."""
    return copy.deepcopy(_dump_original)


@pytest.fixture
def grafo(dump) -> Grafo:
    return Grafo.desde_dump(dump, proceso_de=PROCESOS.get)


def nodo_crudo(dump: list[dict], **props) -> dict:
    """El objeto crudo del nodo cuyas props coinciden con `props`."""
    for o in dump:
        p = (o.get("info") or {}).get("props") or {}
        if o.get("type") == "PipeWire:Interface:Node" and all(p.get(k) == v for k, v in props.items()):
            return o
    raise KeyError(props)


# ---------- La mezcla para visualizadores (la foto es anterior a elena_visual) ----------
# `con_visual` agrega el sink, los tres alimentadores (el de sistema silenciado) y, si se pide,
# a Noctalia escuchando su monitor en lugar del de elena_sistema.

NOCTALIA = 177   # id de la grabación de Noctalia en la foto (serial 721), escuchando elena_sistema
VISUAL = 9000


def _nodo(ident: int, nombre: str, clase: str, mute: bool = False) -> dict:
    return {
        "id": ident, "type": "PipeWire:Interface:Node",
        "info": {
            "state": "running",
            "props": {"node.name": nombre, "media.class": clase, "object.serial": ident},
            "params": {"Props": [{"mute": mute, "channelVolumes": [1.0, 1.0], "channelMap": ["FL", "FR"]}]},
        },
    }


def _link(ident: int, salida: int, entrada: int) -> dict:
    return {"id": ident, "type": "PipeWire:Interface:Link",
            "info": {"output-node-id": salida, "input-node-id": entrada}}


def con_visual(dump: list[dict], noctalia_en_visual: bool = False) -> list[dict]:
    dump = copy.deepcopy(dump)
    dump += [
        _nodo(VISUAL, "elena_visual", "Audio/Sink"),
        _nodo(9001, "elena_visual_musica", "Stream/Output/Audio"),
        _nodo(9002, "elena_visual_juegos", "Stream/Output/Audio"),
        _nodo(9003, "elena_visual_sistema", "Stream/Output/Audio", mute=True),
    ]
    if noctalia_en_visual:
        dump = [o for o in dump if not (o.get("type") == "PipeWire:Interface:Link"
                                        and o["info"].get("input-node-id") == NOCTALIA)]
        dump.append(_link(9100, VISUAL, NOCTALIA))
    return dump


# ---------- «Solo audífonos» (la foto es anterior a elena_audifonos) ----------
# `con_audifonos` agrega el filter-chain como lo crea 50-elena-audifonos.conf: el sink 5.1 con sus
# controles (los del .conf: directo) y su salida estéreo esperando a los Virtuoso, apagados como Chat en la
# foto. Va en todas las pruebas (`_dump_original`): ELENA aplicado completo.

AUDIFONOS, AUDIFONOS_SALIDA = 9200, 9201
VIRTUOSO = "alsa_output.usb-Corsair_CORSAIR_VIRTUOSO_Wireless_Gaming_Headset_0000000000000000-00.analog-stereo"
SEIS = ["FL", "FR", "FC", "LFE", "RL", "RR"]


def params_audifonos(**cambios: float) -> list:
    """Los controles del .conf (los de por omisión de audifonos.py), con `cambios` por nombre."""
    from elena import audifonos
    valores = {"sp_fl:Azimuth": 30.0, **audifonos.controles(audifonos.Estado()), **cambios}
    return [x for par in valores.items() for x in par]


def con_audifonos(dump: list[dict]) -> list[dict]:
    dump = copy.deepcopy(dump)
    dump += [
        {"id": AUDIFONOS, "type": "PipeWire:Interface:Node",
         "info": {"state": "suspended",
                  "props": {"node.name": "elena_audifonos", "node.description": "ELENA · Audífonos",
                            "media.class": "Audio/Sink", "object.serial": AUDIFONOS, "audio.channels": 6},
                  "params": {"EnumFormat": [{"channels": 6, "position": SEIS}],
                             "Props": [{"mute": False, "channelVolumes": [1.0] * 6, "channelMap": SEIS,
                                        "params": ["monitor.channel-volumes", False]},
                                       {"params": params_audifonos()}]}}},
        {"id": AUDIFONOS_SALIDA, "type": "PipeWire:Interface:Node",
         "info": {"state": "suspended",
                  "props": {"node.name": "elena_audifonos.salida", "media.class": "Stream/Output/Audio",
                            "object.serial": AUDIFONOS_SALIDA, "target.object": VIRTUOSO, "audio.channels": 2},
                  "params": {"Props": [{"mute": False, "channelVolumes": [1.0, 1.0], "channelMap": ["FL", "FR"]}]}}},
    ]
    return dump
