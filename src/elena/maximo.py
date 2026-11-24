"""Prueba de volumen máximo: el volumen más alto con el que estás cómodo, por bocina.

Va **antes** de la prueba de las satélites: esa prueba toca sus tonos a este volumen, porque a volumen
bajo un tono grave apenas se oye y su límite se escapa. También da el tope de los buses.

Por cada bocina toca ráfagas de ruido rosa de 2 s: las satélites de 200 Hz para arriba (arriba de lo
que luego se prueba, para que los graves no cambien el nivel cómodo) y el sub de 30 a 120 Hz. Empieza
en **tu nivel de música de hoy** y solo sube cuando lo pides, de 2 en 2 dB. Enter marca
«hasta aquí: lo más fuerte con lo que estoy cómodo» (el máximo es este) y espacio, «ya vibra o
distorsiona» (el máximo es el paso anterior).

Va **directo al sink físico**, porque hay que pasar del volumen del bus; por eso el nivel se calcula
canal por canal en dBFS en el DAC: nivel de hoy = volumen del bus de Música en ese canal + el pico de
Apple Music (−9 dBFS, medido) + el volumen del físico. Topes: nunca más de 30 dB arriba
de hoy ni de −1 dBFS, y cada subida pide oír al menos 1 s el nivel anterior (la tecla sostenida no
sube de golpe).

**El máximo vale para la posición de la perilla del amplificador:** si se mueve, hay que repetirla.
Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from elena.modelo import BUSES, SINK_FISICO, ErrorElena, Grafo, lineal_a_db, lineal_a_pct
from elena.satelites import CARPETA, NOMBRES, RAMPA, TASA, detener, tocar, teclado

RAFAGA = 2.0              # segundos por ráfaga
PASO_DB = 2.0
SOBRE_HOY_MAX = 30.0      # nunca más de 30 dB arriba del nivel de hoy
TECHO_DBFS = -1.0         # ni más de −1 dBFS en el DAC
ESPERA = 1.0              # segundos que hay que oír un nivel antes de poder subir
PICO_MUSICA = -9.0        # picos de Apple Music en el bus (sin Sound Check)
# El tope es tu **volumen ideal**: el nivel que eliges es tu «a todo volumen»; los picos de una canción
# pueden pasarlo y bajas de ahí cuando quieres oír lo de afuera. Por eso se
# compara contenido típico (los picos de −9 dBFS de Apple Music, con un factor de cresta parecido al
# del ruido rosa) y no el peor caso a 0 dBFS, y sin margen.
PICO_CONTENIDO = PICO_MUSICA
MARGEN_DB = 0.0
BANDA_SATELITE = (200, 20000)
BANDA_SUB = (30, 120)

BOCINAS = ("FL", "FR", "FC", "RL", "RR", "LFE")
NOMBRES_MAX = {**NOMBRES, "LFE": "sub"}


# ---------- Señal ----------

def ruido_rosa(banda: tuple[float, float], segundos: float = RAFAGA, tasa: int = TASA,
               semilla: int | None = None) -> np.ndarray:
    """Ruido rosa (−3 dB por octava) solo dentro de `banda`, con pico 1.0 y rampas sin clics."""
    n = int(segundos * tasa)
    rng = np.random.default_rng(semilla)
    espectro = np.fft.rfft(rng.normal(size=n))
    f = np.fft.rfftfreq(n, 1 / tasa)
    peso = np.zeros_like(f)
    dentro = (f >= banda[0]) & (f <= banda[1])
    peso[dentro] = 1 / np.sqrt(f[dentro])
    x = np.fft.irfft(espectro * peso, n)
    x /= np.abs(x).max()
    r = min(int(RAMPA * tasa), n // 2)
    rampa = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r))
    x[:r] *= rampa
    x[-r:] *= rampa[::-1]
    return x


def banda_de(canal: str) -> tuple[float, float]:
    return BANDA_SUB if canal == "LFE" else BANDA_SATELITE


def rms_relativo(x: np.ndarray) -> float:
    """RMS respecto al pico, en dB (el ruido rosa queda ~11 dB abajo de su pico; una senoidal, 3 dB)."""
    return lineal_a_db(float(np.sqrt(np.mean(np.square(x)))) / float(np.abs(x).max()))


def ultimos_maximos(carpeta: Path = CARPETA) -> dict[str, tuple[float, float]]:
    """El máximo más reciente de cada bocina, juntando corridas (una se puede repetir sola con --canal):
    canal → (pico, RMS) del ruido en el DAC, en dBFS."""
    maximos: dict[str, tuple[float, float]] = {}
    for corrida in sorted(carpeta.glob("volumen-*.json")):
        for r in json.loads(corrida.read_text()).get("resultados", []):
            if r.get("maximo") is not None:
                maximos[r["canal"]] = (r["maximo"], r["maximo"] + r.get("rms_rel", 0.0))
    return maximos


# ---------- Niveles ----------

def db_por_canal(grafo: Grafo, nombre: str) -> dict[str, float]:
    nodo = grafo.nodo(nombre)
    if not nodo or len(nodo.volumenes) != len(nodo.posiciones):
        return {}
    return {p: lineal_a_db(v) for p, v in zip(nodo.posiciones, nodo.volumenes)}


def nivel_de_hoy(musica: dict[str, float], fisico: dict[str, float], canal: str) -> float:
    """Pico de tu música de hoy en el DAC, en ese canal (dBFS)."""
    return musica[canal] + PICO_MUSICA + fisico.get(canal, 0.0)


@dataclass
class Subida:
    """Estado de una bocina (sin E/S, para probarlo). Teclas: + sube, − baja, espacio «ya vibra o
    distorsiona», Enter «hasta aquí», s salta, q termina."""

    hoy: float
    ahora: float = 0.0              # reloj del último cambio de nivel
    paso: float = PASO_DB
    nivel: float = field(init=False)
    maximo: float | None = None
    fin: str | None = None          # distorsiona, tope, saltada o salir
    aviso: str = ""

    def __post_init__(self) -> None:
        self.nivel = self.hoy

    @property
    def techo(self) -> float:
        return min(self.hoy + SOBRE_HOY_MAX, TECHO_DBFS)

    @property
    def sobre_hoy(self) -> float:
        return self.nivel - self.hoy

    def tecla(self, k: str, reloj: float) -> str | None:
        """Devuelve «cambio» (otro nivel: cortar y volver a tocar), «cortar» (terminó) o None."""
        self.aviso = ""
        if k in ("+", "="):
            if reloj - self.ahora < ESPERA:
                self.aviso = "espera a oír este nivel"
                return None
            if self.nivel + self.paso > self.techo + 1e-9:
                self.aviso = f"es el tope de la prueba ({self.techo:.0f} dBFS)"
                return None
            self.nivel += self.paso
            self.ahora = reloj
            return "cambio"
        if k == "-":
            self.nivel = max(self.nivel - self.paso, self.hoy - 12)
            self.ahora = reloj
            return "cambio"
        if k == " ":
            self.maximo, self.fin = self.nivel - self.paso, "distorsiona"
            return "cortar"
        if k in ("\n", "\r"):
            self.maximo, self.fin = self.nivel, "tope"
            return "cortar"
        if k == "s":
            self.fin = "saltada"
            return "cortar"
        if k in ("q", "\x03"):
            self.fin = "salir"
            return "cortar"
        return None


@dataclass
class Maximo:
    canal: str
    fin: str
    hoy: float                       # pico de la música de hoy en el DAC (dBFS)
    maximo: float | None             # pico del ruido al volumen más alto cómodo, en el DAC (dBFS)
    rms_rel: float = 0.0             # RMS del ruido respecto a su pico (dB): la prueba de las satélites
                                     # iguala sus tonos a ese RMS, no al pico

    @property
    def margen(self) -> float | None:
        return None if self.maximo is None else self.maximo - self.hoy


# El sub no limita el tope (2026-11-11): con ruido rosa continuo distorsionó a +6 dB, pero con música
# real (`elena calibracion musica`) llegó a ~20 dB arriba del volumen ideal sin distorsionar.
SIN_TOPE = frozenset({"LFE"})


def holguras(maximos: dict[str, float], bus: dict[str, float], fisico: dict[str, float],
             margen: float = MARGEN_DB) -> dict[str, float]:
    """Por canal: cuántos dB puede subir el bus desde su volumen actual hasta que el contenido típico
    llegue al máximo de esa bocina. Negativo: hoy ya está por encima."""
    return {c: maximos[c] - margen - (db + PICO_CONTENIDO + fisico.get(c, 0.0))
            for c, db in bus.items() if c in maximos and c not in SIN_TOPE and math.isfinite(db)}


def tope_bus(maximos: dict[str, float], bus: dict[str, float], fisico: dict[str, float],
             margen: float = MARGEN_DB) -> float | None:
    """La holgura del canal más justo: el bus no sube más que eso."""
    h = holguras(maximos, bus, fisico, margen)
    return min(h.values()) if h else None


def texto_topes(grafo: Grafo, maximos: dict[str, float]) -> list[str]:
    fisico = db_por_canal(grafo, SINK_FISICO)
    lineas = []
    for definicion in BUSES:
        if definicion.clave in ("chat", "audifonos"):
            continue
        nodo = grafo.nodo(definicion.nodo)
        bus = db_por_canal(grafo, definicion.nodo)
        if not nodo or not bus:
            continue
        h = holguras(maximos, bus, fisico)
        if not h:
            continue
        canal = min(h, key=h.get)
        holgura = h[canal]
        hoy = max(nodo.volumenes)
        tope = lineal_a_pct(hoy * 10 ** (holgura / 20))
        estado = "ya lo pasa" if holgura < 0 else f"{holgura:+.1f} dB sobre hoy"
        linea = f"  {definicion.etiqueta:<8} hoy {lineal_a_pct(hoy):3.0f} % → tope {tope:3.0f} % ({estado}; lo limita {canal})"
        if otros := [c for c in h if c != canal]:
            sin = min(h[c] for c in otros)
            if sin - holgura >= 3:
                linea += f"; sin {canal}: {sin:+.1f} dB"
        lineas.append(linea)
    return lineas


# ---------- La prueba ----------

def _linea(canal: str, s: Subida) -> str:
    return (f"\r\033[K  {canal} · {s.sobre_hoy:+5.1f} dB sobre tu música ({s.nivel:6.1f} dBFS)"
            f"   + sube · − baja · espacio: vibra/distorsiona · Enter: hasta aquí · s · q"
            + (f"   ⚠ {s.aviso}" if s.aviso else ""))


def probar_bocina(canal: str, hoy: float, fisico_db: float, banda: tuple[float, float], leer,
                  destino: str = SINK_FISICO) -> Maximo:
    s = Subida(hoy, ahora=time.monotonic())
    base = ruido_rosa(banda)
    posiciones = ("FL", "FR", "FC", "LFE", "RL", "RR")
    archivo = Path(os.environ.get("TMPDIR") or CARPETA) / "elena-maximo.f32"
    proc = None
    try:
        while not s.fin:
            print(_linea(canal, s), end="", flush=True)
            senal = np.zeros((len(base), 6), dtype=np.float32)
            # el volumen del físico también cuenta: la señal es lo que falta para llegar al nivel
            senal[:, posiciones.index(canal)] = base * 10 ** ((s.nivel - fisico_db) / 20)
            proc = tocar(senal, destino, posiciones, archivo)
            while proc.poll() is None:
                k = leer(0.05)
                if k is None:
                    continue
                accion = s.tecla(k, time.monotonic())
                print(_linea(canal, s), end="", flush=True)
                if accion:
                    detener(proc)
            time.sleep(0.1)
    except KeyboardInterrupt:
        s.fin = "salir"
    finally:
        if proc:
            detener(proc)
    print()
    return Maximo(canal, s.fin or "salir", round(hoy, 2), None if s.maximo is None else round(s.maximo, 2),
                  round(rms_relativo(base), 2))


def correr(grafo: Grafo, canales: tuple[str, ...], destino: str = SINK_FISICO) -> int:
    """`destino` solo cambia para probar la cadena con un sink nulo de 6 canales."""
    if not sys.stdin.isatty():
        raise ErrorElena("La prueba de volumen es interactiva: córrela en una terminal.")
    if faltan := [c for c in canales if c not in BOCINAS]:
        raise ErrorElena(f"Bocina desconocida: {', '.join(faltan)}. Son {' '.join(BOCINAS)}.")
    musica, fisico = db_por_canal(grafo, "elena_musica"), db_por_canal(grafo, destino)
    if not musica or not fisico:
        raise ErrorElena("Necesito elena_musica y el sink físico 5.1 (mise run estado).")
    if grafo.nodo(destino).silencio:
        raise ErrorElena("El sink físico está silenciado.")
    print("Prueba de volumen máximo: ruido rosa por bocina, directo al físico, empezando en el nivel")
    print("de tu música de hoy. + sube 2 dB (después de oír 1 s el nivel), − baja 2 dB.")
    print("  · ENTER: es lo más fuerte con lo que estás cómodo → el máximo es este.")
    print("  · ESPACIO: ya vibra, zumba, raspa o distorsiona → el máximo es el paso anterior.")
    print("La prueba de las satélites tocará sus tonos a este volumen. No muevas la perilla del amplificador:")
    print("el máximo vale para su posición de hoy (márcala). Pausa la música; si suena muy fuerte, protégete.")
    resultados: list[Maximo] = []
    with teclado() as leer:
        for canal in canales:
            banda = banda_de(canal)
            hoy = nivel_de_hoy(musica, fisico, canal)
            print(f"\n{canal} ({NOMBRES_MAX.get(canal, canal)}), ruido de {banda[0]:.0f} a {banda[1]:.0f} Hz, "
                  f"hoy {hoy:.1f} dBFS. Enter para empezar, s para saltarla, q para terminar.")
            k = None
            try:
                while k not in ("\n", "\r", " ", "s", "q", "\x03"):
                    k = leer(0.5)
            except KeyboardInterrupt:
                k = "q"
            if k in ("q", "\x03"):
                break
            if k == "s":
                resultados.append(Maximo(canal, "saltada", round(hoy, 2), None))
                continue
            r = probar_bocina(canal, hoy, fisico.get(canal, 0.0), banda, leer, destino)
            resultados.append(r)
            if r.maximo is not None:
                print(f"  → máximo {r.maximo:.1f} dBFS ({r.margen:+.1f} dB sobre tu música)")
            if r.fin == "salir":
                break
    if not resultados:
        return 0
    print("\nResultado:")
    for r in resultados:
        texto = (f"{r.maximo:6.1f} dBFS ({r.margen:+.1f} dB sobre tu música; "
                 f"{'vibra o distorsiona arriba' if r.fin == 'distorsiona' else 'lo más fuerte cómodo'})"
                 if r.maximo is not None else "sin marcar")
        print(f"  {r.canal:<3} {NOMBRES_MAX.get(r.canal, ''):<18} {texto}")
    maximos = {r.canal: r.maximo for r in resultados if r.maximo is not None}
    if topes := texto_topes(grafo, maximos):
        print("Tope de los buses (tu volumen ideal: la música típica, con picos de −9 dBFS, llega a tu nivel):")
        print("\n".join(topes))
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"volumen-{datetime.now():%Y%m%d-%H%M%S}.json"
    ruta.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                                "resultados": [asdict(r) for r in resultados]},
                               ensure_ascii=False, indent=2) + "\n")
    print(f"Guardado en {ruta}")
    print("Siguiente: la prueba de las satélites ya toca a este volumen: mise run calibracion -- satelites --mic")
    return 0
