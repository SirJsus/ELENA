"""El temporizador para dormir del control remoto: un modelo puro.

`Temporizador.paso(entorno)` recibe lo que pasa ahora (el volumen del bus «ELENA · Música», el
estado de la música y si la VM corre) y devuelve las acciones que el servidor ejecuta. El reloj
se inyecta, así que se prueba sin audio ni esperas.

Al vencer:
1. **Desvanece** el bus: de su volumen a 0 en 30 s (8 s con «al terminar esta canción»), en %
   cúbico y conservando las diferencias entre canales (`modelo.escalar`).
2. **Pausa** por MPRIS y espera a que el reproductor deje de sonar (máx. 3 s; si sigue, lo pide
   otra vez; si aun así sigue, silencia el bus).
3. Espera **1.5 s** más y **restaura** el volumen: el búfer de QEMU (100 ms) y la orden por el
   puente (hasta 350 ms) todavía dejan salir audio después de la pausa.
4. Según el **nivel**: a) `pausar` termina; b) `vm` apaga la VM; c) `suspender`, d) `apagar`:
   lo mismo que b y, cuando la VM ya terminó (o tras `ESPERA_VM`), suspende o apaga la PC.

Durante el desvanecido mandas tú: si el volumen del bus cambia desde otro lado (la consola,
el teléfono, pavucontrol), el temporizador se cancela y se respeta el volumen nuevo; si la música
deja de sonar por otra causa, se cancela y se restaura el volumen. Antes de vencer, pausar no lo
cancela: sigue contando, y al vencer se salta el desvanecido y la pausa.

El estado se guarda (`a_dict`) en `.cache/remoto/temporizador.json`; al arrancar, `cargar` sigue
un temporizador que aún no vence y restaura el volumen si se quedó un desvanecido a medias.
Guía: docs/guias/control-remoto.md
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from elena.modelo import ErrorElena, escalar, lineal_a_pct

NIVELES = ("pausar", "vm", "suspender", "apagar")
DESVANECER = 30.0         # s de desvanecido con minutos
DESVANECER_FIN = 8.0      # s de desvanecido con «al terminar esta canción»
ANTES_DEL_FIN = 0.5       # s antes del final de la canción en que se pausa
ESPERA_PAUSA = 3.0        # s máximos esperando a que el reproductor deje de sonar
MARGEN = 1.5              # s entre la pausa y restaurar el volumen
ESPERA_VM = 150.0         # s máximos esperando a que la VM termine (vm.apagar espera 120)
TOLERANCIA = 1.5          # puntos de % cúbico: más que esto es un cambio de volumen ajeno
CADUCA = 60.0             # s: al cargar, un temporizador vencido hace más que esto se descarta
MAX_MINUTOS = 12 * 60


@dataclass(frozen=True)
class Entorno:
    """Lo que el servidor ve en cada paso."""

    volumen: tuple[float, ...] | None    # el del bus elena_musica (lineal, por canal); None si no existe
    musica: str | None = None            # Playing | Paused | Stopped; None sin reproductor
    pista: str | None = None             # algo que identifique la canción (cambia con ella)
    restante_ms: int | None = None       # duración − posición de la canción actual
    vm: bool = False                     # ¿corre la VM?


@dataclass(frozen=True)
class Accion:
    tipo: str                            # volumen | silencio | pausa | apagar_vm | suspender | apagar_pc | aviso
    volumen: tuple[float, ...] = ()
    texto: str = ""


@dataclass
class Programa:
    nivel: str
    modo: str                            # minutos | fin_cancion
    vence: float                         # epoch (s); con fin_cancion se recalcula en cada paso
    fase: str = "contando"               # contando | desvaneciendo | pausando | restaurando | apagando_vm
    pista: str | None = None             # con fin_cancion: la canción que hay que dejar terminar
    original: list[float] | None = None  # volumen del bus antes de desvanecer
    puestos: list[float] = field(default_factory=list)   # últimos % puestos por el desvanecido
    desde: float = 0.0                   # cuándo empezó la fase actual
    duracion: float = DESVANECER         # duración del desvanecido
    reintento: bool = False              # ya se pidió la pausa dos veces


class Temporizador:
    def __init__(self, reloj: Callable[[], float] = time.time) -> None:
        self.reloj = reloj
        self.programa: Programa | None = None

    # ----- órdenes del teléfono -----

    def programar(self, nivel: str = "pausar", minutos: float | None = None, fin_cancion: bool = False,
                  entorno: Entorno | None = None) -> None:
        if nivel not in NIVELES:
            raise ErrorElena(f"No conozco el nivel «{nivel}». Usa: {', '.join(NIVELES)}.")
        if self.programa is not None and self.programa.fase != "contando":
            raise ErrorElena("El temporizador ya está terminando; cancélalo antes de poner otro.")
        ahora = self.reloj()
        if fin_cancion:
            if entorno is None or entorno.musica != "Playing" or not entorno.restante_ms:
                raise ErrorElena("«Al terminar esta canción» necesita una canción sonando con duración conocida.")
            self.programa = Programa(nivel, "fin_cancion", ahora + entorno.restante_ms / 1000 - ANTES_DEL_FIN,
                                     pista=entorno.pista, duracion=DESVANECER_FIN)
            return
        if minutos is None or not 0 < minutos <= MAX_MINUTOS:
            raise ErrorElena(f"Los minutos van de 1 a {MAX_MINUTOS}.")
        self.programa = Programa(nivel, "minutos", ahora + minutos * 60)

    def mas(self, minutos: float) -> None:
        p = self.programa
        if p is None:
            raise ErrorElena("No hay temporizador.")
        if p.modo != "minutos" or p.fase != "contando":
            raise ErrorElena("Solo se puede alargar un temporizador de minutos que no ha vencido.")
        if not 0 < minutos <= MAX_MINUTOS:
            raise ErrorElena(f"Los minutos van de 1 a {MAX_MINUTOS}.")
        p.vence = min(p.vence + minutos * 60, self.reloj() + MAX_MINUTOS * 60)

    def cancelar(self) -> list[Accion]:
        """Cancela; si ya estaba bajando el volumen, lo regresa."""
        p, self.programa = self.programa, None
        if p is not None and p.original is not None and p.fase in ("desvaneciendo", "pausando", "restaurando"):
            return [Accion("volumen", tuple(p.original))]
        return []

    # ----- el paso -----

    def paso(self, e: Entorno) -> list[Accion]:
        p = self.programa
        if p is None:
            return []
        ahora = self.reloj()
        metodo = getattr(self, f"_{p.fase}")
        return metodo(p, e, ahora)

    def _cambiar(self, p: Programa, fase: str, ahora: float) -> None:
        p.fase, p.desde = fase, ahora

    def _contando(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        if p.modo == "fin_cancion":
            if e.pista != p.pista:
                # la canción ya cambió (la posición de Apple Music es estimada): pausar ya
                return self._empezar(p, e, ahora, desvanecer=False)
            if e.restante_ms is not None:
                # en pausa, lo que falta no baja: el vencimiento se recorre y el temporizador espera
                p.vence = ahora + e.restante_ms / 1000 - ANTES_DEL_FIN
            if e.musica != "Playing":
                return []
        if ahora >= p.vence - p.duracion:
            return self._empezar(p, e, ahora, desvanecer=True)
        return []

    def _empezar(self, p: Programa, e: Entorno, ahora: float, desvanecer: bool) -> list[Accion]:
        """Vence (o está por vencer): desvanecer, o directo a pausar, o saltar al nivel."""
        if e.musica != "Playing" or e.volumen is None:
            # nada que pausar (o no existe el bus): directo a lo que sigue según el nivel
            return [Accion("aviso", texto="Temporizador: no había música sonando.")] + self._despues(p, e, ahora)
        p.original = list(e.volumen)
        if desvanecer:
            # el desvanecido termina en `vence`; si empezó tarde, dura lo que queda
            p.duracion = max(min(p.duracion, p.vence - ahora), 0.0)
            self._cambiar(p, "desvaneciendo", ahora)
            return [Accion("aviso", texto=f"Temporizador: bajando el volumen en {p.duracion:.0f} s.")] \
                + self._desvaneciendo(p, e, ahora)
        return self._pausar(p, ahora, [Accion("volumen", escalar(e.volumen, 0.0))])

    def _desvaneciendo(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        assert p.original is not None
        original = tuple(p.original)
        if e.volumen is None:
            self.programa = None
            return [Accion("aviso", texto="Temporizador cancelado: desapareció el bus de música.")]
        actual = lineal_a_pct(max(e.volumen))
        ajeno = not any(abs(actual - x) <= TOLERANCIA for x in [lineal_a_pct(max(original)), *p.puestos])
        if ajeno:
            self.programa = None
            return [Accion("aviso", texto=f"Temporizador cancelado: el volumen cambió a {actual:.0f} %.")]
        if e.musica != "Playing":
            self.programa = None
            return [Accion("aviso", texto="Temporizador cancelado: la música dejó de sonar."),
                    Accion("volumen", original)]
        if p.modo == "fin_cancion" and e.pista != p.pista:
            return self._pausar(p, ahora, [Accion("volumen", escalar(original, 0.0))])
        avance = 1.0 if p.duracion <= 0 else min((ahora - p.desde) / p.duracion, 1.0)
        pct = lineal_a_pct(max(original)) * (1 - avance)
        p.puestos = (p.puestos + [pct])[-4:]
        acciones = [Accion("volumen", escalar(original, pct))]
        if avance >= 1.0:
            return self._pausar(p, ahora, acciones)
        return acciones

    def _pausar(self, p: Programa, ahora: float, antes: list[Accion]) -> list[Accion]:
        self._cambiar(p, "pausando", ahora)
        return antes + [Accion("pausa")]

    def _pausando(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        if e.musica == "Playing":
            if ahora - p.desde < ESPERA_PAUSA:
                return []
            if not p.reintento:
                p.reintento, p.desde = True, ahora
                return [Accion("aviso", texto="Temporizador: el reproductor no pausó; lo pido otra vez."),
                        Accion("pausa")]
            # no hay forma de pausarlo: se silencia el bus para no despertar a nadie
            self._cambiar(p, "restaurando", ahora - MARGEN)
            return [Accion("aviso", texto="Temporizador: el reproductor no pausó; silencio el bus."),
                    Accion("silencio")] + self._restaurando(p, e, ahora)
        self._cambiar(p, "restaurando", ahora)
        return []

    def _restaurando(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        if ahora - p.desde < MARGEN:
            return []
        acciones = [Accion("volumen", tuple(p.original))] if p.original is not None else []
        return acciones + self._despues(p, e, ahora)

    def _despues(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        """Tras pausar: termina (nivel a) o apaga la VM (b a d)."""
        if p.nivel == "pausar":
            self.programa = None
            return [Accion("aviso", texto="Temporizador: listo, música en pausa.")]
        self._cambiar(p, "apagando_vm", ahora)
        if e.vm:
            return [Accion("apagar_vm")]
        return self._apagando_vm(p, e, ahora)

    def _apagando_vm(self, p: Programa, e: Entorno, ahora: float) -> list[Accion]:
        if e.vm and ahora - p.desde < ESPERA_VM:
            return []
        self.programa = None
        if p.nivel == "vm":
            return [Accion("aviso", texto="Temporizador: listo, VM apagada.")]
        return [Accion("suspender" if p.nivel == "suspender" else "apagar_pc")]

    # ----- lo que ve el teléfono y el disco -----

    def vista(self) -> dict | None:
        p = self.programa
        if p is None:
            return None
        return {"vence": int(p.vence * 1000), "modo": p.modo, "nivel": p.nivel, "fase": p.fase}

    def a_dict(self) -> dict | None:
        return asdict(self.programa) if self.programa else None

    def cargar(self, datos: dict | None) -> list[Accion]:
        """Retoma lo guardado al arrancar el servidor. Devuelve las acciones para dejar el volumen bien."""
        self.programa = None
        if not datos:
            return []
        try:
            p = Programa(**datos)
        except TypeError:
            return [Accion("aviso", texto="Temporizador guardado ilegible: lo descarto.")]
        if p.fase == "contando":
            if p.vence - p.duracion < self.reloj() - CADUCA:
                return [Accion("aviso", texto="Temporizador guardado ya vencido: lo descarto.")]
            self.programa = p
            return [Accion("aviso", texto="Temporizador retomado.")]
        acciones = [Accion("aviso", texto=f"Temporizador interrumpido en «{p.fase}»: lo descarto.")]
        if p.original is not None and p.fase in ("desvaneciendo", "pausando", "restaurando"):
            acciones.append(Accion("volumen", tuple(p.original)))
        return acciones
