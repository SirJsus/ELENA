"""El decodificador del flujo de `pw-dump --monitor`."""

import random

from elena.monitor import Decodificador, es_baja

# Dos lotes con el formato de pw-dump: cada arreglo termina con `]` en su propia línea,
# y hay un `]` interno al inicio de línea dentro del primero (no debe confundir al decodificador).
TEXTO = """[
  {
    "id": 53,
    "type": "PipeWire:Interface:Node",
    "info": { "props": { "node.name": "elena_juegos", "node.description": "ELENA · Juegos" },
      "params": { "Format": [
]
      } }
  }
]
[
  {
    "id": 53,
    "info": null
  }
]
"""


def test_decodifica_lotes_completos():
    lotes = Decodificador().alimentar(TEXTO.encode())
    assert len(lotes) == 2
    assert lotes[0][0]["info"]["props"]["node.description"] == "ELENA · Juegos"
    assert lotes[1] == [{"id": 53, "info": None}]


def test_pedazos_arbitrarios_incluso_a_mitad_de_un_caracter():
    datos = TEXTO.encode()
    esperado = Decodificador().alimentar(datos)
    assert len(esperado) == 2 and "·" in TEXTO
    rng = random.Random(7)
    for _ in range(200):
        dec, lotes, i = Decodificador(), [], 0
        while i < len(datos):
            n = rng.randint(1, 9)
            lotes += dec.alimentar(datos[i:i + n])
            i += n
        assert lotes == esperado


def test_lote_incompleto_espera_mas_texto():
    dec = Decodificador()
    corte = TEXTO.index('"id": 53,\n    "info": null')
    assert len(dec.alimentar(TEXTO[:corte].encode())) == 1
    assert dec.alimentar(TEXTO[corte:].encode()) == [[{"id": 53, "info": None}]]


def test_es_baja():
    assert es_baja({"id": 5, "info": None})
    assert not es_baja({"id": 5, "type": "PipeWire:Interface:Node", "info": {}})
    # Los metadatos no tienen `info`: no son bajas
    assert not es_baja({"id": 94, "type": "PipeWire:Interface:Metadata", "metadata": []})
