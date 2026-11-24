"""El tope de volumen de los buses de bocinas.

Tu máximo con música (lo mide `elena calibracion comparar`) va en `[maximo] tope` de
`local/calibracion.toml`, en dB sobre tu 100 % (por ejemplo, +8 dB = 136 %). Con la calibración activa,
el 100 % del bus es tu 100 %, así que el tope es simplemente ese porcentaje del bus.

- **Qué buses:** Música y Juegos. Sistema todavía no: su contenido (navegador, sonidos) es otra cosa
  y falta medirlo con contenido real antes de ponerle tope.
- **Solo con la calibración activa** en ese bus (su salida va a `elena_salida`): sin ella, el
  porcentaje del bus no significa lo mismo.
- **No baja nada de golpe:** si un bus ya está arriba del tope (por ejemplo, de antes), no se le
  deja subir más, pero tampoco se baja solo. Bajar siempre se puede.
- Lo usan la CLI (`elena volumen`), las escenas, la consola, el control remoto y el vigilante (que
  regresa al tope lo que suba desde fuera: pavucontrol, Noctalia, las teclas de volumen).

Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elena.modelo import BUSES, CALIBRACION, ErrorElena, Grafo, Nodo, escalar, lineal_a_pct

BUSES_TOPE = ("elena_musica", "elena_juegos")
TOLERANCIA = 0.5        # % (pactl redondea los volúmenes)
ETIQUETA = {b.nodo: b.etiqueta for b in BUSES}


class _Lector:
    """Lee `[maximo] tope` del TOML y lo vuelve a leer solo si el archivo cambió."""

    def __init__(self) -> None:
        self._mtime: float | None = None
        self._tope: float | None = None

    def tope_db(self, ruta: Path | None = None) -> float | None:
        from elena import calibracion as cb
        ruta = ruta or cb.TOML
        try:
            mtime = ruta.stat().st_mtime
        except OSError:
            return None
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._tope = cb.leer(ruta).tope
            except ErrorElena:
                self._tope = None   # un TOML roto no bloquea el volumen (lo avisa `calibracion estado`)
        return self._tope


_lector = _Lector()


def tope_db() -> float | None:
    return _lector.tope_db()


def limite_pct(grafo: Grafo, nodo: Nodo, tope: float | None = None) -> float | None:
    """El % máximo de este bus, o None si no tiene tope."""
    if nodo.nombre not in BUSES_TOPE:
        return None
    salida = grafo.nodo(f"{nodo.nombre}.salida")
    if salida is None or salida.objetivo != CALIBRACION:
        return None
    tope = tope_db() if tope is None else tope
    return None if tope is None else lineal_a_pct(10 ** (tope / 20))


@dataclass(frozen=True)
class Acotado:
    pct: float
    aviso: str | None = None


def acotar(grafo: Grafo, nodo: Nodo, objetivo: float, actual: float | None = None,
           tope: float | None = None) -> Acotado:
    """El % que se puede poner: nunca arriba del tope, salvo que ya estuviera arriba (entonces no sube
    más, pero tampoco se baja solo)."""
    limite = limite_pct(grafo, nodo, tope)
    if limite is None:
        return Acotado(objetivo)
    actual = (nodo.volumen_pct or 0.0) if actual is None else actual
    techo = max(limite, actual)
    if objetivo <= techo + TOLERANCIA:
        return Acotado(objetivo)
    etiqueta = ETIQUETA.get(nodo.nombre, nodo.visible)
    if actual > limite + TOLERANCIA:
        aviso = (f"{etiqueta} ya está en {actual:.0f} %, arriba de tu máximo ({limite:.0f} %): no sube más. "
                 "Bájalo cuando quieras.")
    else:
        aviso = f"{etiqueta}: tu máximo es {limite:.0f} % (el tope de la calibración); no sube más."
    return Acotado(techo, aviso)


def acotar_volumenes(grafo: Grafo, nodo: Nodo, volumenes: tuple[float, ...],
                     actual: float | None = None) -> tuple[tuple[float, ...], str | None]:
    """Lo mismo para volúmenes por canal (lineales): si el canal más alto pasa el tope, se escalan
    conservando las diferencias entre canales."""
    objetivo = lineal_a_pct(max(volumenes, default=0.0))
    a = acotar(grafo, nodo, objetivo, actual)
    if a.aviso is None:
        return volumenes, None
    return escalar(volumenes, a.pct), a.aviso
