"""«Solo audífonos»: Música, Juegos y Sistema a los audífonos, a donde va Chat (`elena audifonos`).

Para cuando alguien más trabaja en el cuarto. `elena_audifonos` (50-elena-audifonos.conf) es un bus
5.1 que convierte a estéreo y sale a donde va Chat. Con el modo prendido, las salidas de los buses de
bocinas (`<bus>.salida`) se mueven ahí con `pactl move-sink-input`; al apagarlo, regresan al
`target.object` de sus props (`elena_salida`, o el físico sin calibración). Lo demás no cambia: el
volumen de los buses, el tope, la compensación estéreo (va en el volumen de la salida, que viaja con
ella), `elena_visual` y Chat.

- **Dos caminos** dentro de `elena_audifonos`, y solo uno suena: **binaural** (cada canal por la HRTF
  KEMAR en su ángulo de bocina) o **directo** (la mezcla estéreo de siempre). Se eligen con las
  ganancias de `salida_l`/`salida_r`, en vivo. Muchos audífonos «7.1» no hacen surround
  por su cuenta: en Linux son una tarjeta USB estéreo.
- **Graves y LFE:** aquí no hay calibración de bocinas. El LFE va +10 dB sobre las frontales (como un
  receptor) y al final hay un estante de graves (+6 dB en 105 Hz, la forma de la curva Harman para
  audífonos). Los dos se cambian en vivo (`elena audifonos graves <dB> [--hz F]`, `lfe <dB>`).
- **El estado** va en `.cache/audifonos/estado.json`. Tras un reinicio de PipeWire, las salidas
  vuelven solas a las bocinas (WirePlumber no restaura un destino movido si el stream trae
  `target.object`): el vigilante relee el estado y vuelve a mover lo que haga falta. Si Chat cambia de
  destino, `elena_audifonos` lo sigue.

Guía: docs/guias/consola.md#solo-audífonos
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from elena.acciones import RAIZ
from elena.formato import nombre_corto, nombre_salida
from elena.modelo import AUDIFONOS, BUSES, ErrorElena, Grafo, Nodo

ESTADO = RAIZ / ".cache" / "audifonos" / "estado.json"
CAMINOS = ("binaural", "directo")
# Las ganancias de salida_l/salida_r (Gain 1 = binaural, Gain 2 = directo), con el nivel incluido.
# Las mismas que 50-elena-audifonos.conf: el binaural suena 4.5 dB más fuerte (medido el 2026-11-14).
NIVEL = {"binaural": 0.3, "directo": 0.5}
ETIQUETA = {b.clave: b.etiqueta for b in BUSES}
# Rangos de lo que se ajusta (el mixer acepta hasta ×10, +20 dB)
RANGO = {"graves": (-6.0, 12.0), "frecuencia": (40.0, 250.0), "lfe": (-10.0, 20.0)}


@dataclass(frozen=True)
class Estado:
    activo: bool = False
    camino: str = "directo"          # el de por omisión (el .conf también)
    graves: float = 6.0              # dB del estante
    frecuencia: float = 105.0        # Hz del estante
    lfe: float = 10.0                # dB del LFE sobre las frontales


def _numero(datos: dict, clave: str, defecto: float) -> float:
    v = datos.get(clave, defecto)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return defecto
    lo, hi = RANGO[clave]
    return float(v) if lo <= v <= hi else defecto


def leer(ruta: Path | None = None) -> Estado:
    ruta = ruta or ESTADO
    try:
        datos = json.loads(ruta.read_text())
    except (OSError, ValueError):
        return Estado()
    if not isinstance(datos, dict):
        return Estado()
    d = Estado()
    camino = datos.get("camino")
    return Estado(activo=bool(datos.get("activo")), camino=camino if camino in CAMINOS else d.camino,
                  graves=_numero(datos, "graves", d.graves), frecuencia=_numero(datos, "frecuencia", d.frecuencia),
                  lfe=_numero(datos, "lfe", d.lfe))


def guardar(estado: Estado, ruta: Path | None = None) -> None:
    ruta = ruta or ESTADO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".nuevo")
    datos = {"activo": estado.activo, "camino": estado.camino, "graves": estado.graves,
             "frecuencia": estado.frecuencia, "lfe": estado.lfe}
    temporal.write_text(json.dumps(datos, indent=2) + "\n")
    temporal.replace(ruta)


def controles(estado: Estado) -> dict[str, float]:
    """Los controles de elena_audifonos como los quiere `estado` (los de por omisión = el .conf)."""
    b, d = (NIVEL["binaural"], 0.0) if estado.camino == "binaural" else (0.0, NIVEL["directo"])
    lfe = round(10 ** (estado.lfe / 20), 6)
    return {
        "salida_l:Gain 1": b, "salida_r:Gain 1": b, "salida_l:Gain 2": d, "salida_r:Gain 2": d,
        "dir_l:Gain 4": lfe, "dir_r:Gain 4": lfe, "bin_l:Gain 6": lfe, "bin_r:Gain 6": lfe,
        "graves_l:Gain": estado.graves, "graves_r:Gain": estado.graves,
        "graves_l:Freq": estado.frecuencia, "graves_r:Freq": estado.frecuencia,
    }


def texto_ajustes(estado: Estado) -> str:
    return f"graves {estado.graves:+.1f} dB en {estado.frecuencia:.0f} Hz · LFE {estado.lfe:+.1f} dB"


class Lector:
    """Relee el estado solo si el archivo cambió (el vigilante lo consulta en cada cambio del grafo)."""

    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = ruta or ESTADO
        self._mtime: float | None = None
        self._estado = Estado()

    def leer(self) -> Estado:
        try:
            mtime = self.ruta.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._mtime = mtime
            self._estado = leer(self.ruta) if mtime is not None else Estado()
        return self._estado


# ---------- Las órdenes (sin E/S) ----------

def cmd_mover_salida(salida: Nodo, destino: str) -> list[str]:
    """La salida de un bus (un loopback, Stream/Output/Audio) a otro sink."""
    if salida.serial is None:
        raise ErrorElena(f"{salida.nombre} no tiene object.serial.")
    return ["pactl", "move-sink-input", str(salida.serial), destino]


def cmd_controles(nodo: Nodo, valores: dict[str, float]) -> list[str]:
    params = " ".join(f'"{k}" {v:.6g}' for k, v in valores.items())
    return ["pw-cli", "set-param", str(nodo.id), "Props", f"{{ params = [ {params} ] }}"]


def diferentes(vivos: dict[str, float], deseados: dict[str, float]) -> dict[str, float]:
    """Los controles que no están como se quieren. Los que el nodo no tiene (un .conf viejo, sin
    graves) no cuentan: mandarlos no haría nada, y `avisos` pide aplicar."""
    return {k: v for k, v in deseados.items() if k in vivos and abs(vivos[k] - v) > 1e-3 * max(1.0, abs(v))}


@dataclass(frozen=True)
class Paso:
    cmd: list[str]
    texto: str           # para el registro del vigilante y los avisos


def plan(grafo: Grafo, estado: Estado) -> list[Paso]:
    """Lo que falta para que el grafo quede como dice `estado` (vacío si ya está)."""
    au = grafo.audifonos()
    if au is None:
        return []
    nodo = grafo.nodo(AUDIFONOS)
    assert nodo is not None
    pasos: list[Paso] = []
    if estado.activo:
        for clave, salida in au.salidas.items():
            if clave not in au.enviados:
                pasos.append(Paso(cmd_mover_salida(salida, AUDIFONOS), f"{ETIQUETA[clave]} → Audífonos"))
        # Seguir a Chat: si sale a otro lado que elena_audifonos (y Chat sí tiene a dónde)
        salida_au = grafo.nodo(f"{AUDIFONOS}.salida")
        if au.chat and salida_au is not None and {n.id for n in au.chat} != {n.id for n in au.destinos}:
            destino = au.chat[0]
            pasos.append(Paso(cmd_mover_salida(salida_au, destino.nombre),
                              f"Audífonos → {nombre_salida(destino)} (a donde va Chat)"))
    else:
        for clave in au.enviados:
            salida = au.salidas[clave]
            if salida.objetivo and salida.objetivo != AUDIFONOS and grafo.nodo(salida.objetivo) is not None:
                destino = grafo.nodo(salida.objetivo)
                assert destino is not None
                pasos.append(Paso(cmd_mover_salida(salida, salida.objetivo),
                                  f"{ETIQUETA[clave]} → {nombre_salida(destino)}"))
    if cambios := diferentes(grafo.params(nodo), controles(estado)):
        pasos.append(Paso(cmd_controles(nodo, cambios), f"Audífonos: {estado.camino} · {texto_ajustes(estado)}"))
    return pasos


def avisos(grafo: Grafo, estado: Estado) -> list[str]:
    """Lo que hay que saber al cambiar algo."""
    au = grafo.audifonos()
    if au is None:
        return ["No existe elena_audifonos: corre `mise run aplicar` (con la VM apagada)."] if estado.activo else []
    lista = []
    nodo = grafo.nodo(AUDIFONOS)
    if nodo is not None and (faltan := set(controles(estado)) - set(grafo.params(nodo))):
        if any(k.startswith("graves_") for k in faltan):
            lista.append("elena_audifonos todavía no tiene el estante de graves: corre `mise run aplicar` "
                         "(con la VM apagada) para cargar el .conf nuevo.")
    if estado.activo and not au.chat and not au.destinos:
        lista.append("Los audífonos no están conectados (¿apagados?): todo queda en silencio hasta que los prendas.")
    return lista


def texto(grafo: Grafo, estado: Estado) -> str:
    """Una línea: el modo, el camino y a dónde sale."""
    au = grafo.audifonos()
    if au is None:
        return "sin elena_audifonos (¿falta mise run aplicar?)"
    modo = "on" if estado.activo else "off"
    partes = [f"solo audífonos {modo}", au.camino or f"{estado.camino}?", texto_ajustes(estado)]
    if au.destinos:
        partes.append("→ " + ", ".join(nombre_salida(d) for d in au.destinos))
    elif au.esperando:
        partes.append(f"→ esperando a {nombre_corto(au.esperando)}")
    if estado.activo and len(au.enviados) < len(au.salidas):
        faltan = [ETIQUETA[c] for c in au.salidas if c not in au.enviados]
        partes.append("faltan: " + ", ".join(faltan))
    elif not estado.activo and au.enviados:
        partes.append("todavía en audífonos: " + ", ".join(ETIQUETA[c] for c in au.enviados))
    return " · ".join(partes)


# ---------- Cambiar el modo (CLI y consola) ----------

ORDENES = ("on", "off", "alternar", "binaural", "directo")


def siguiente(actual: Estado, orden: str) -> Estado:
    """El estado después de una orden: on, off, alternar (el modo) o binaural, directo (el camino)."""
    if orden not in ORDENES:
        raise ErrorElena(f"Orden desconocida: {orden}. Usa: {', '.join(ORDENES)}.")
    if orden in CAMINOS:
        return replace(actual, camino=orden)
    activo = {"on": True, "off": False}.get(orden, not actual.activo)
    return replace(actual, activo=activo)


def ajustar(actual: Estado, **valores: float | None) -> Estado:
    """graves, frecuencia o lfe, dentro de su rango."""
    cambios = {}
    for clave, v in valores.items():
        if v is None:
            continue
        lo, hi = RANGO[clave]
        if not lo <= v <= hi:
            unidad = "Hz" if clave == "frecuencia" else "dB"
            raise ErrorElena(f"{clave} va de {lo:g} a {hi:g} {unidad} (pediste {v:g}).")
        cambios[clave] = float(v)
    return replace(actual, **cambios)


def cambiar(grafo: Grafo, nuevo: Estado, ejecutar: Callable[[list[str]], object],
            ruta: Path | None = None) -> list[str]:
    """Guarda el estado y manda lo que falta. Devuelve qué se hizo y los avisos (para mostrarlos)."""
    if grafo.audifonos() is None:
        raise ErrorElena("No existe elena_audifonos (50-elena-audifonos.conf): corre `mise run aplicar` "
                         "con la VM apagada.")
    guardar(nuevo, ruta)
    hechos = []
    for paso in plan(grafo, nuevo):
        ejecutar(paso.cmd)
        hechos.append(paso.texto)
    return hechos + [f"⚠ {a}" for a in avisos(grafo, nuevo)]
