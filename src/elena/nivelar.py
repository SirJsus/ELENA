"""Prueba de nivelación: el balance entre las satélites, desde neutro y a ciegas.

Un balance puesto de memoria o heredado de otro sistema (los volúmenes por canal de los buses, por
ejemplo) casi nunca está medido, y la prueba de volumen lo hereda. Esta prueba lo mide sin herencia:

- **Todo en neutro:** directo al físico, con la misma señal digital en todas las bocinas. Ni los buses
  ni el balance de hoy intervienen.
- **A/B contra FL:** ruido rosa de 500 Hz a 2 kHz (la banda de los receptores: sin los modos de la sala
  ni las diferencias en graves), primero FL y luego la otra bocina, en bucle. Con + y − se ajusta la
  otra hasta que las dos suenen igual; Enter la anota.
- **A ciegas:** cada bocina empieza con un desfase al azar de ±6 dB y el número no se ve hasta el final.
  Dos rondas (la segunda en otro orden) y una tercera si difieren más de 2 dB; vale la mediana.
- **Con --mic,** antes de las rondas, el mic físico mide cada bocina con la misma señal, sin
  mostrarlo hasta el final. No es de medición, pero arriba de 200 Hz la comparación entre bocinas sí vale.

El nivel es fijo para todas: 10 dB abajo del volumen ideal de FL (prueba de volumen), o −50 dBFS de RMS
si no hay prueba de volumen. Solo cuenta la diferencia entre bocinas, no el nivel.

El sub no entra: ni el oído ni el mic lo nivelan bien con ruido. Su ganancia se afina con música.
El resultado es la ganancia de cada satélite respecto a FL: lo que va en [bocinas] de calibracion.toml.
Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from elena.maximo import db_por_canal, ruido_rosa, rms_relativo, ultimos_maximos
from elena.modelo import SINK_FISICO, ErrorElena, Grafo
from elena.satelites import CARPETA, MIC, NOMBRES, TASA, Grabadora, detener, tocar, teclado

BANDA = (500, 2000)
REFERENCIA = "FL"
BOCINAS = ("FR", "FC", "RL", "RR")
RAFAGA = 1.5             # segundos de ruido por bocina en cada vuelta
PAUSA = 0.3              # entre FL y la otra
FINAL = 0.7              # después de la otra, antes de volver a FL
PASO_DB = 1.0
DESFASE_MAX = 6          # el inicio al azar, en dB enteros
AJUSTE_MAX = 15.0        # la otra bocina, a no más de ±15 dB de FL
TECHO_DBFS = -1.0        # ni más de −1 dBFS de pico en el DAC
BAJO_IDEAL = 10.0        # el nivel: 10 dB abajo del volumen ideal de FL
NIVEL_SIN_VOLUMEN = -50.0
TERCERA_SI = 2.0         # una tercera ronda si las dos primeras difieren más de esto
MIC_DURACION = 2.5
AUDIBLE_DB = 10.0        # el mic: la bocina debe quedar 10 dB arriba del silencio
DESACUERDO_DB = 3.0      # avisar si el oído y el mic difieren más de esto


# ---------- Señal y niveles (sin E/S) ----------

def nivel_referencia(maximos: dict[str, tuple[float, float]]) -> float:
    """RMS del ruido en el DAC (dBFS): 10 dB abajo del volumen ideal de FL, o un nivel bajo fijo."""
    if REFERENCIA in maximos:
        return round(maximos[REFERENCIA][1] - BAJO_IDEAL, 2)
    return NIVEL_SIN_VOLUMEN


def a_rms_1(x: np.ndarray) -> np.ndarray:
    return x / float(np.sqrt(np.mean(np.square(x))))


def ciclo(base: np.ndarray, posiciones: tuple[str, ...], canal: str, nivel: float, ajuste: float,
          fisico: dict[str, float], tasa: int = TASA) -> np.ndarray:
    """Una vuelta del A/B: FL al `nivel` (RMS en el DAC), pausa, `canal` a `nivel + ajuste`, pausa.
    `base` tiene RMS 1; el volumen del físico se descuenta para que el DAC reciba el nivel pedido."""
    n, p, f = len(base), int(PAUSA * tasa), int(FINAL * tasa)
    x = np.zeros((2 * n + p + f, len(posiciones)), dtype=np.float32)
    x[:n, posiciones.index(REFERENCIA)] = base * 10 ** ((nivel - fisico.get(REFERENCIA, 0.0)) / 20)
    x[n + p:2 * n + p, posiciones.index(canal)] = base * 10 ** ((nivel + ajuste - fisico.get(canal, 0.0)) / 20)
    return x


def techo_ajuste(nivel: float, cresta: float) -> float:
    """Lo más que puede subir la otra bocina sin que su pico pase de −1 dBFS."""
    return min(AJUSTE_MAX, TECHO_DBFS - (nivel + cresta))


def inicio_al_azar(rng: np.random.Generator) -> float:
    return float(rng.integers(-DESFASE_MAX, DESFASE_MAX + 1))


def combinar(valores: list[float]) -> float | None:
    return round(float(statistics.median(valores)), 1) if valores else None


def necesita_tercera(valores: list[float]) -> bool:
    return len(valores) == 2 and abs(valores[0] - valores[1]) > TERCERA_SI


# ---------- El ajuste de una bocina (sin E/S, para probarlo) ----------

@dataclass
class Ajuste:
    """Teclas: + sube la otra bocina 1 dB, − la baja, Enter o espacio «suenan igual», s la salta,
    q termina. El valor no se muestra: solo se oye."""

    inicio: float
    techo: float = AJUSTE_MAX
    ajuste: float = field(init=False)
    fin: str | None = None          # igual, saltada o salir
    aviso: str = ""

    def __post_init__(self) -> None:
        self.ajuste = min(self.inicio, self.techo)

    def tecla(self, k: str) -> str | None:
        """«cambio» (otro nivel: cortar y volver a tocar desde FL), «cortar» (terminó) o None."""
        self.aviso = ""
        if k in ("+", "="):
            if self.ajuste + PASO_DB > self.techo + 1e-9:
                self.aviso = "es lo más alto de la prueba"
                return None
            self.ajuste += PASO_DB
            return "cambio"
        if k == "-":
            if self.ajuste - PASO_DB < -AJUSTE_MAX - 1e-9:
                self.aviso = "es lo más bajo de la prueba"
                return None
            self.ajuste -= PASO_DB
            return "cambio"
        if k in ("\n", "\r", " "):
            self.fin = "igual"
            return "cortar"
        if k == "s":
            self.fin = "saltada"
            return "cortar"
        if k in ("q", "\x03"):
            self.fin = "salir"
            return "cortar"
        return None


# ---------- El mic ----------

def nivel_banda(grabacion: np.ndarray, banda: tuple[float, float] = BANDA, tasa: int = TASA) -> float | None:
    """Energía de la grabación dentro de `banda`, en dB (solo sirve para comparar), en el canal del mic
    donde llega más fuerte. Toma el tramo del medio. None si es demasiado corta."""
    x = np.asarray(grabacion, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    inicio, largo = int(0.6 * tasa), int(1.5 * tasa)
    if len(x) < inicio + largo:
        return None
    tramo = x[inicio:inicio + largo]
    f = np.fft.rfftfreq(len(tramo), 1 / tasa)
    dentro = (f >= banda[0]) & (f <= banda[1])
    ventana = np.hanning(len(tramo))[:, None]
    potencia = (np.abs(np.fft.rfft(tramo * ventana, axis=0)) ** 2)[dentro].sum(axis=0)
    mejor = float(potencia.max())
    return round(10 * np.log10(mejor), 2) if mejor > 0 else -200.0


def ganancias_mic(niveles: dict[str, float], silencio: float | None) -> dict[str, float | None]:
    """Por bocina, la ganancia que le haría falta para sonar como FL en el mic (None si el mic casi no
    la distingue del silencio)."""
    if REFERENCIA not in niveles:
        return {}
    def oible(c: str) -> bool:
        return silencio is None or niveles[c] - silencio >= AUDIBLE_DB
    if not oible(REFERENCIA):
        return {c: None for c in niveles if c != REFERENCIA}
    return {c: (round(niveles[REFERENCIA] - v, 1) if oible(c) else None)
            for c, v in niveles.items() if c != REFERENCIA}


def medir_mic(canales: tuple[str, ...], posiciones: tuple[str, ...], nivel: float, fisico: dict[str, float],
              destino: str, fuente: str = MIC, monitor: bool = False) -> tuple[dict[str, float], float | None]:
    """Graba el silencio y luego cada bocina sola (FL primero) con la misma señal. `fuente` y `monitor`
    solo cambian para probar la cadena con el monitor de un sink nulo."""
    archivo = Path(os.environ.get("TMPDIR") or CARPETA) / "elena-nivelar-mic.f32"
    base = a_rms_1(ruido_rosa(BANDA, MIC_DURACION))
    canales_mic = 6 if monitor else 2

    def grabar(canal: str | None) -> float | None:
        g = Grabadora(fuente, canales_mic, monitor)
        proc = None
        try:
            time.sleep(0.15)
            if canal is None:
                time.sleep(MIC_DURACION)
            else:
                x = np.zeros((len(base), len(posiciones)), dtype=np.float32)
                x[:, posiciones.index(canal)] = base * 10 ** ((nivel - fisico.get(canal, 0.0)) / 20)
                proc = tocar(x, destino, posiciones, archivo)
                proc.wait()
        finally:
            if proc:
                detener(proc)
            datos = g.terminar()
        return nivel_banda(datos)

    silencio = grabar(None)
    niveles = {}
    for c in (REFERENCIA, *canales):
        print(f"\r\033[K  mic: {c}…", end="", flush=True)
        if (v := grabar(c)) is not None:
            niveles[c] = v
        time.sleep(0.2)
    print("\r\033[K  mic: listo (lo verás al final).")
    return niveles, silencio


# ---------- La prueba ----------

@dataclass
class Ronda:
    canal: str
    ronda: int
    inicio: float
    ajuste: float | None
    fin: str


def _linea(canal: str, ronda: int, a: Ajuste) -> str:
    return (f"\r\033[K  ronda {ronda} · {canal} ({NOMBRES.get(canal, canal)}) contra FL"
            f"   + sube · − baja · Enter: suenan igual · s salta · q termina"
            + (f"   ⚠ {a.aviso}" if a.aviso else ""))


def ajustar(canal: str, ronda: int, inicio: float, techo: float,
            generar: Callable[[str, float], np.ndarray], destino: str, posiciones: tuple[str, ...],
            leer: Callable[[float], str | None]) -> Ronda:
    a = Ajuste(inicio, techo)
    archivo = Path(os.environ.get("TMPDIR") or CARPETA) / "elena-nivelar.f32"
    proc = None
    try:
        while not a.fin:
            print(_linea(canal, ronda, a), end="", flush=True)
            proc = tocar(generar(canal, a.ajuste), destino, posiciones, archivo)
            while proc.poll() is None:
                k = leer(0.05)
                if k is None:
                    continue
                accion = a.tecla(k)
                print(_linea(canal, ronda, a), end="", flush=True)
                if accion:
                    detener(proc)
    except KeyboardInterrupt:
        a.fin = "salir"
    finally:
        if proc:
            detener(proc)
    print("\r\033[K  " + {"igual": f"{canal}: anotada.", "saltada": f"{canal}: saltada."}.get(a.fin, ""),
          flush=True)
    return Ronda(canal, ronda, inicio, a.ajuste if a.fin == "igual" else None, a.fin or "salir")


def balance_de_hoy(grafo: Grafo) -> dict[str, float]:
    """El balance que hoy tiene el bus de Música, respecto a FL (dB)."""
    bus = db_por_canal(grafo, "elena_musica")
    if REFERENCIA not in bus:
        return {}
    return {c: round(v - bus[REFERENCIA], 1) for c, v in bus.items()}


def texto_resultado(resultado: dict[str, float | None], rondas: list[Ronda], mic: dict[str, float | None],
                    hoy: dict[str, float], ideal_fl: float | None) -> list[str]:
    lineas = ["Ganancia de cada bocina respecto a FL para que suenen igual (lo que va en [bocinas]):",
              f"  FL  {NOMBRES['FL']:<18}  +0.0 dB  (la referencia)"]
    for c, g in resultado.items():
        valores = [r.ajuste for r in rondas if r.canal == c and r.ajuste is not None]
        if g is None:
            lineas.append(f"  {c:<3} {NOMBRES.get(c, ''):<18}  sin anotar")
            continue
        linea = (f"  {c:<3} {NOMBRES.get(c, ''):<18} {g:+5.1f} dB  "
                 f"(rondas {', '.join(f'{v:+.0f}' for v in valores)})")
        if c in mic:
            linea += f" · mic {mic[c]:+.1f}" if mic[c] is not None else " · mic: no la distingue"
        if c in hoy:
            linea += f" · hoy {hoy[c]:+.1f}"
            if abs(g - hoy[c]) >= 2:
                linea += f" ({g - hoy[c]:+.1f} dB contra hoy)"
        lineas.append(linea)
    if desacuerdo := [c for c, g in resultado.items()
                      if g is not None and mic.get(c) is not None and abs(g - mic[c]) > DESACUERDO_DB]:
        lineas.append(f"⚠ El oído y el mic difieren más de {DESACUERDO_DB:.0f} dB en {', '.join(desacuerdo)}: "
                      "revisa que el mic esté en el punto de escucha, o repítela con --canal.")
    if ideal_fl is not None:
        ideales = " · ".join(f"{c} {ideal_fl + g:.1f}" for c, g in resultado.items() if g is not None)
        lineas.append(f"Tu volumen ideal por bocina con este balance (pico en el DAC; FL {ideal_fl:.1f} medido): "
                      f"{ideales} dBFS")
    return lineas


def guardar(nivel: float, rondas: list[Ronda], resultado: dict, mic_niveles: dict, silencio: float | None,
            mic: dict, hoy: dict) -> Path:
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"nivelar-{datetime.now():%Y%m%d-%H%M%S}.json"
    ruta.write_text(json.dumps({
        "fecha": datetime.now().isoformat(timespec="seconds"), "referencia": REFERENCIA,
        "banda_hz": list(BANDA), "nivel_rms_dbfs": nivel, "rondas": [asdict(r) for r in rondas],
        "resultado_db": resultado, "mic": {"niveles_db": mic_niveles, "silencio_db": silencio, "ganancias_db": mic},
        "hoy_db": hoy,
    }, ensure_ascii=False, indent=2) + "\n")
    return ruta


def ultimos_balances(carpeta: Path = CARPETA) -> dict[str, float]:
    """La ganancia más reciente de cada satélite (las corridas con --canal se juntan)."""
    balance: dict[str, float] = {}
    for corrida in sorted(carpeta.glob("nivelar-*.json")):
        for c, g in json.loads(corrida.read_text()).get("resultado_db", {}).items():
            if g is not None:
                balance[c] = g
    return balance


def _esperar_enter(leer: Callable[[float], str | None]) -> bool:
    try:
        k = None
        while k not in ("\n", "\r", " ", "q", "\x03"):
            k = leer(0.5)
    except KeyboardInterrupt:
        return False
    return k not in ("q", "\x03")


def correr(grafo: Grafo, canales: tuple[str, ...] = BOCINAS, mic: bool = False, destino: str = SINK_FISICO,
           rng: np.random.Generator | None = None) -> int:
    """`destino` solo cambia para probar la cadena con un sink nulo de 6 canales."""
    if not sys.stdin.isatty():
        raise ErrorElena("La nivelación es interactiva: córrela en una terminal.")
    if faltan := [c for c in canales if c not in BOCINAS]:
        raise ErrorElena(f"Bocina desconocida: {', '.join(faltan)}. Son {' '.join(BOCINAS)} (contra FL).")
    nodo = grafo.nodo(destino)
    fisico = db_por_canal(grafo, destino)
    if nodo is None or not fisico:
        raise ErrorElena("Necesito el sink físico 5.1 (mise run estado).")
    if faltan := [c for c in (REFERENCIA, *canales) if c not in nodo.posiciones]:
        raise ErrorElena(f"{destino} no tiene {', '.join(faltan)}.")
    if nodo.silencio:
        raise ErrorElena("El sink físico está silenciado.")
    if mic and not (MIC and grafo.nodo(MIC)):
        raise ErrorElena(f"No encuentro el mic físico ({MIC or 'ELENA_MIC_FISICO vacío'}).")
    rng = rng or np.random.default_rng()
    maximos = ultimos_maximos()
    nivel = nivel_referencia(maximos)
    crudo = ruido_rosa(BANDA, RAFAGA)
    base = a_rms_1(crudo)
    techo = techo_ajuste(nivel, -rms_relativo(crudo))
    posiciones = nodo.posiciones

    def generar(canal: str, ajuste: float) -> np.ndarray:
        return ciclo(base, posiciones, canal, nivel, ajuste, fisico)

    print("Nivelación: el balance entre las satélites, desde neutro y a ciegas.")
    print(f"Ruido rosa de {BANDA[0]} Hz a {BANDA[1] // 1000} kHz, directo al físico, a {nivel:.1f} dBFS de RMS "
          "(el mismo nivel digital en todas): primero FL y luego otra bocina, en bucle.")
    print("Con + y − cambias la otra bocina hasta que las dos suenen IGUAL de fuerte; Enter la anota.")
    print("Cada bocina empieza en un nivel al azar y no verás números hasta el final: guíate solo por el oído.")
    print("Siéntate en tu punto de escucha, mirando al frente, y no gires la cabeza hacia la bocina.")
    print("Pausa la música. No muevas la perilla del amplificador.")
    if otras := sorted({a.visible for b in grafo.buses() if b.definicion.clave != "chat" for a in b.apps}):
        print(f"⚠ También suena: {', '.join(otras)}. Páusalo.")

    rondas: list[Ronda] = []
    mic_niveles: dict[str, float] = {}
    silencio: float | None = None
    salir = False
    with teclado() as leer:
        if mic:
            print("\nPrimero el mic: deja el mic físico en el punto de escucha y guarda silencio "
                  f"(~{(len(canales) + 2) * (MIC_DURACION + 0.5):.0f} s). Enter para empezar, q para saltarlo.")
            if _esperar_enter(leer):
                mic_niveles, silencio = medir_mic(canales, posiciones, nivel, fisico, destino)
        for n in (1, 2, 3):
            if n == 1:
                orden = list(canales)
            elif n == 2:
                orden = [canales[i] for i in rng.permutation(len(canales))]
            else:
                orden = [c for c in canales
                         if necesita_tercera([r.ajuste for r in rondas if r.canal == c and r.ajuste is not None])]
            if not orden:
                break
            extra = " (solo las que no coincidieron)" if n == 3 else ""
            print(f"\nRonda {n}{extra}: {' '.join(orden)}. Enter para empezar, q para terminar.")
            if not _esperar_enter(leer):
                break
            for c in orden:
                r = ajustar(c, n, inicio_al_azar(rng), techo, generar, destino, posiciones, leer)
                rondas.append(r)
                if r.fin == "salir":
                    salir = True
                    break
                time.sleep(0.4)
            if salir:
                break

    resultado = {c: combinar([r.ajuste for r in rondas if r.canal == c and r.ajuste is not None])
                 for c in canales}
    if not rondas and not mic_niveles:
        return 0
    mic_g = ganancias_mic(mic_niveles, silencio)
    hoy = balance_de_hoy(grafo)
    ideal_fl = maximos.get(REFERENCIA, (None, None))[0]
    print("\nResultado:")
    print("\n".join(texto_resultado(resultado, rondas, mic_g, hoy, ideal_fl)))
    print(f"Guardado en {guardar(nivel, rondas, resultado, mic_niveles, silencio, mic_g, hoy)}")
    return 0
