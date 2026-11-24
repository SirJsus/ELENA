"""Textos para mostrar nodos (los usan la CLI y la TUI)."""

from __future__ import annotations

import math

from elena.modelo import CALIBRACION, SINK_FISICO, Nodo, lineal_a_db

ALIAS = {SINK_FISICO: "Bocinas 5.1", CALIBRACION: "ELENA · Calibración"}


def texto_db(v: float) -> str:
    db = lineal_a_db(v)
    if db == -math.inf:
        return "-∞"
    return "0.0" if abs(db) < 0.05 else f"{db:+.1f}"


def posiciones(n: Nodo) -> list[str]:
    """Etiqueta de cada canal de volumen: las posiciones del nodo, o 1, 2, 3… si no las trae."""
    if len(n.posiciones) == len(n.volumenes):
        return list(n.posiciones)
    return [str(i + 1) for i in range(len(n.volumenes))]


def texto_volumen(n: Nodo) -> str:
    if n.volumen_pct is None:
        return "-"
    partes = [f"{n.volumen_pct:.0f}%"]
    if not n.balanceado:
        partes.append("[" + " ".join(f"{c} {texto_db(v)}" for c, v in zip(posiciones(n), n.volumenes)) + " dB]")
    if n.silencio:
        partes.append("SILENCIADO")
    return " ".join(partes)


def texto_canales(n: Nodo) -> str:
    return f"{n.canales} ch" if n.canales else "? ch"


def nombre_salida(n: Nodo) -> str:
    return ALIAS.get(n.nombre, n.visible)


def nombre_corto(node_name: str) -> str:
    """Para un sink que quizá no existe (el destino que espera un bus): alias o sin prefijo ALSA."""
    return ALIAS.get(node_name) or node_name.removeprefix("alsa_output.")


def texto_app(n: Nodo) -> str:
    partes = [n.visible]
    if n.media and n.media != n.visible:
        partes.append(n.media)
    partes += [texto_canales(n), n.api or "-", texto_volumen(n), f"serial {n.serial}"]
    return " · ".join(partes)
