"""Lectura del estado de PipeWire con `pw-dump`: una sola vez o en vivo con `--monitor`.

Formato verificado con PipeWire 1.6.10:
- `pw-dump --monitor` manda un arreglo JSON con el estado completo y después un arreglo por cada
  cambio. Cada arreglo termina con una línea que solo tiene `]`.
- Nodos, links, dispositivos y clientes llegan **completos** en cada cambio; una baja llega como
  `{"id": N, "info": null}`, sin `type`.
- Los metadatos llegan como **delta** (solo las claves que cambiaron). Un borrado llega como una
  lista vacía, sin decir qué se borró: entonces hay que volver a leer el objeto con `pw-dump <id>`.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass


class Decodificador:
    """Separa el flujo de texto de `pw-dump --monitor` en lotes (arreglos JSON).

    El texto llega en pedazos arbitrarios, incluso cortando un carácter UTF-8 a la mitad
    (las descripciones llevan «·» y acentos), por eso se decodifica de forma incremental.
    """

    def __init__(self) -> None:
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._json = json.JSONDecoder()
        self._bufer = ""

    def alimentar(self, datos: bytes) -> list[list[dict]]:
        self._bufer += self._utf8.decode(datos)
        lotes: list[list[dict]] = []
        # Un lote está completo cuando aparece su `]` final en una línea propia; antes de eso
        # no vale la pena intentar decodificar (el estado inicial pesa ~900 KB).
        while "\n]" in self._bufer:
            inicio = len(self._bufer) - len(self._bufer.lstrip())
            try:
                lote, fin = self._json.raw_decode(self._bufer, inicio)
            except json.JSONDecodeError:
                break  # el `]` era de algo interno; falta texto
            lotes.append(lote)
            self._bufer = self._bufer[fin:]
        return lotes


def es_baja(objeto: dict) -> bool:
    """`{"id": N, "info": null}`: el objeto N ya no existe."""
    return "type" not in objeto and objeto.get("info", ...) is None


def leer_estado(*ids: int) -> list[dict]:
    """Estado completo (o solo los objetos `ids`) con un `pw-dump` de una sola vez."""
    salida = subprocess.run(
        ["pw-dump", "--no-colors", *map(str, ids)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(salida) if salida.strip() else []


@dataclass
class Lote:
    """Un cambio del grafo. `reinicio` indica que es el estado completo y hay que empezar de cero."""

    objetos: list[dict]
    reinicio: bool = False


async def seguir(reintento: float = 1.0) -> AsyncIterator[Lote]:
    """Sigue el grafo en vivo. Si `pw-dump` muere (por ejemplo, al reiniciar PipeWire), lo relanza
    y el siguiente lote llega con `reinicio=True`.

    Los deltas de metadatos vacíos (borrados) se completan aquí releyendo el objeto, así que quien
    consume los lotes no tiene que saber de esa rareza de `pw-dump`.
    """
    while True:
        proceso = await asyncio.create_subprocess_exec(
            "pw-dump", "--monitor", "--no-colors",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        decodificador = Decodificador()
        primero = True
        try:
            assert proceso.stdout is not None
            while datos := await proceso.stdout.read(65536):
                for objetos in decodificador.alimentar(datos):
                    yield Lote(await _completar_metadatos(objetos), reinicio=primero)
                    primero = False
        finally:
            if proceso.returncode is None:
                proceso.kill()
            await proceso.wait()
        await asyncio.sleep(reintento)


async def _completar_metadatos(objetos: list[dict]) -> list[dict]:
    resultado = []
    for objeto in objetos:
        if objeto.get("type") == "PipeWire:Interface:Metadata" and not objeto.get("metadata"):
            completo = await asyncio.to_thread(leer_estado, objeto["id"])
            if completo:
                objeto = completo[0] | {"elena.completo": True}
        resultado.append(objeto)
    return resultado
