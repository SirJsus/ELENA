"""Prueba de las satélites: hasta qué tan grave llega cada bocina y si todas llegan igual.

Por cada satélite toca tonos de 3 s de `desde` Hz hacia abajo, cada `paso` Hz, y marcas
con una tecla el paso donde el tono se debilita, vibra, zumba o distorsiona. Con `--mic`, además
graba cada tono con el mic físico (ELENA_MIC_FISICO) y mide su nivel y su distorsión
(armónicos 2f y 3f): el mic no es de medición, pero es el mismo para todas las bocinas, así que la
comparación entre ellas sí vale.

El tono va **al bus de Música** (no al sink físico): suena con el volumen y el balance del bus, como
la música, y nunca más fuerte. Hoy la música llega al DAC cerca de −47 dBFS en las frontales; un tono
a −20 dBFS directo al físico sonaría ~27 dB más fuerte. Por eso el destino nunca puede ser el físico.

Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from elena.modelo import SINK_FISICO, ErrorElena, Grafo, lineal_a_db

TASA = 48000
DURACION = 3.0          # segundos por tono
RAMPA = 0.05            # subida y bajada del tono (sin clics)
PISO_HZ = 40            # nunca más grave: protege a las satélites
TECHO_HZ = 500
NIVEL_DB = -20.0        # pico del tono en el bus (Apple Music llega con picos de −9 dBFS)
NIVEL_MIN, NIVEL_MAX = -40.0, -10.0
CAIDA_DB = 6.0          # «ya no llega»: el nivel cae 6 dB respecto al primer paso
DISTORSION_PCT = 10.0   # «ya distorsiona»: los armónicos pesan más de 10 % del tono
AUDIBLE_DB = 10.0       # «ya no se oye»: el tono queda a menos de 10 dB del ruido de fondo del mic

SATELITES = ("FL", "FR", "FC", "RL", "RR")
NOMBRES = {
    "FL": "frontal izquierda", "FR": "frontal derecha", "FC": "central",
    "RL": "trasera izquierda", "RR": "trasera derecha", "LFE": "sub (referencia del mic)",
    # Por si el bus usa SL/SR (6 canales «5.1» de PipeWire) en lugar de RL/RR
    "SL": "trasera izquierda", "SR": "trasera derecha",
}

MIC = os.environ.get("ELENA_MIC_FISICO", "")
CARPETA = Path(os.environ.get("ELENA", ".")) / ".cache" / "calibracion"


# ---------- Señal ----------

def frecuencias(desde: int = 200, hasta: int = PISO_HZ, paso: int = 10) -> list[int]:
    """De `desde` hacia abajo hasta `hasta` (incluido si cae en un paso)."""
    if paso <= 0:
        raise ErrorElena("El paso debe ser mayor que 0 Hz.")
    if hasta < PISO_HZ:
        raise ErrorElena(f"La prueba no baja de {PISO_HZ} Hz: más grave puede dañar a las satélites.")
    if desde > TECHO_HZ or desde <= hasta:
        raise ErrorElena(f"«desde» va de {hasta + 1} a {TECHO_HZ} Hz y debe ser mayor que «hasta».")
    return list(range(desde, hasta - 1, -paso))


def tono(f: float, canal: int, canales: int, nivel_db: float = NIVEL_DB,
         segundos: float = DURACION, tasa: int = TASA) -> np.ndarray:
    """Senoidal de `f` Hz solo en `canal` (cuadros × canales, float32), con rampas sin clics."""
    n = int(segundos * tasa)
    t = np.arange(n) / tasa
    x = np.sin(2 * np.pi * f * t) * 10 ** (nivel_db / 20)
    r = min(int(RAMPA * tasa), n // 2)
    if r:
        rampa = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r))
        x[:r] *= rampa
        x[-r:] *= rampa[::-1]
    salida = np.zeros((n, canales), dtype=np.float32)
    salida[:, canal] = x
    return salida


# ---------- Medida con el mic ----------

@dataclass(frozen=True)
class Medida:
    frecuencia: int
    nivel_db: float          # nivel del tono en el mic (dBFS)
    distorsion_pct: float    # √(h2² + h3²) / h1, en %; sin sentido si el tono no se oye
    sobre_ruido_db: float = 99.0   # el tono sobre el ruido de fondo del mic

    @property
    def audible(self) -> bool:
        return self.sobre_ruido_db >= AUDIBLE_DB


def _amplitud(espectro: np.ndarray, resolucion: float, f: float) -> float:
    """Amplitud del pico más alto a ±2 Hz de `f` (o ±2 bins, lo que sea mayor)."""
    centro = f / resolucion
    ancho = max(2.0 / resolucion, 2.0)
    i0, i1 = max(int(centro - ancho), 0), min(int(centro + ancho) + 1, len(espectro))
    return float(espectro[i0:i1].max()) if i1 > i0 else 0.0


def analizar(grabacion: np.ndarray, f: int, tasa: int = TASA) -> Medida | None:
    """Nivel y distorsión del tono `f` en una grabación (cuadros × canales o mono).

    Se toma el tramo del medio (salta la entrada del tono y la latencia) y el canal del mic donde
    el tono llega más fuerte. None si la grabación es demasiado corta.
    """
    x = np.asarray(grabacion, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    inicio, largo = int(0.6 * tasa), int(2.0 * tasa)
    if len(x) < inicio + int(0.5 * tasa):
        return None
    tramo = x[inicio:inicio + largo]
    ventana = np.hanning(len(tramo))
    escala = 2 / ventana.sum()
    resolucion = tasa / len(tramo)
    mejor: Medida | None = None
    for c in range(tramo.shape[1]):
        espectro = np.abs(np.fft.rfft(tramo[:, c] * ventana)) * escala
        h1 = _amplitud(espectro, resolucion, f)
        h2 = _amplitud(espectro, resolucion, 2 * f)
        h3 = _amplitud(espectro, resolucion, 3 * f)
        nivel = lineal_a_db(h1) if h1 > 0 else -200.0
        dist = 100 * float(np.hypot(h2, h3)) / h1 if h1 > 0 else 0.0
        ruido = _ruido(espectro, resolucion, f)
        sobre = lineal_a_db(h1 / ruido) if h1 > 0 and ruido > 0 else (99.0 if h1 > 0 else -99.0)
        if mejor is None or nivel > mejor.nivel_db:
            mejor = Medida(f, round(nivel, 2), round(dist, 2), round(min(sobre, 99.0), 2))
    return mejor


def _ruido(espectro: np.ndarray, resolucion: float, f: float) -> float:
    """Ruido de fondo alrededor del tono: la mediana del espectro de f/2 a 4f, sin el tono ni sus
    armónicos (±3 Hz). La mediana ignora los picos que queden (otro ruido de la sala)."""
    freqs = np.arange(len(espectro)) * resolucion
    banda = (freqs >= f / 2) & (freqs <= 4 * f)
    for n in (1, 2, 3, 4):
        banda &= np.abs(freqs - n * f) > 3
    valores = espectro[banda]
    return float(np.median(valores)) if valores.size else 0.0


def caida(medidas: list[Medida], db: float = CAIDA_DB) -> int | None:
    """La primera frecuencia (bajando) donde el nivel cae `db` respecto a los primeros pasos (el más
    alto de los 3 primeros: la sala sube o baja algún paso suelto)."""
    if not medidas:
        return None
    referencia = max(m.nivel_db for m in medidas[:3])
    return next((m.frecuencia for m in medidas if m.nivel_db <= referencia - db), None)


def se_pierde(medidas: list[Medida]) -> int | None:
    """La primera frecuencia (bajando) donde el mic ya no distingue el tono del ruido de fondo."""
    return next((m.frecuencia for m in medidas if not m.audible), None)


def distorsiona(medidas: list[Medida], pct: float = DISTORSION_PCT) -> int | None:
    """La primera frecuencia (bajando) donde la distorsión pasa de `pct` %."""
    return next((m.frecuencia for m in medidas if m.audible and m.distorsion_pct > pct), None)


# ---------- Recorrido de una bocina (sin E/S, para probarlo) ----------

@dataclass
class Recorrido:
    """Qué paso suena y qué marcaste. Teclas: espacio/Enter marca (también si ya no se oye), r repite,
    a vuelve al paso anterior (más agudo), s salta la bocina y q termina."""

    frecuencias: list[int]
    indice: int = 0
    marca: int | None = None
    fin: str | None = None          # marcada, completa, saltada o salir

    @property
    def actual(self) -> int:
        return self.frecuencias[self.indice]

    def tecla(self, k: str) -> str | None:
        """Aplica una tecla. Devuelve «cortar» si hay que detener el tono que suena."""
        if k in (" ", "\n", "\r"):
            self.marca, self.fin = self.actual, "marcada"
            return "cortar"
        if k == "r":
            return "cortar"
        if k == "a":
            self.indice = max(self.indice - 1, 0)
            return "cortar"
        if k == "s":
            self.fin = "saltada"
            return "cortar"
        if k in ("q", "\x03"):
            self.fin = "salir"
            return "cortar"
        return None

    def termino_tono(self, cortado: bool) -> None:
        """El tono acabó solo (avanza al siguiente paso) o lo cortó una tecla (no avanza)."""
        if self.fin or cortado:
            return
        if self.indice + 1 < len(self.frecuencias):
            self.indice += 1
        else:
            self.fin = "completa"

    @property
    def paso(self) -> int:
        f = self.frecuencias
        return f[0] - f[1] if len(f) > 1 else 0

    @property
    def limite(self) -> int | None:
        """La frecuencia más grave que sonó bien: un paso arriba de la marca."""
        if self.fin == "completa":
            return self.frecuencias[-1]
        if self.fin == "marcada" and self.marca is not None and self.marca != self.frecuencias[0]:
            return self.marca + self.paso
        return None


@dataclass
class Resultado:
    canal: str
    fin: str
    marca: int | None
    limite: int | None
    medidas: list[Medida] = field(default_factory=list)

    @property
    def texto_limite(self) -> str:
        if self.fin == "completa":
            return (f"limpia hasta {self.limite} Hz (el final; si dejó de oírse antes, "
                    f"repítela con --canal {self.canal})")
        if self.fin == "marcada" and self.limite is None:
            return f"falla ya en {self.marca} Hz (el primer paso)"
        if self.fin == "marcada":
            return f"{self.limite} Hz (falla en {self.marca})"
        return "sin marcar"


def recomendacion(resultados: list[Resultado], paso: int) -> str:
    resultados = [r for r in resultados if r.canal != "LFE"]   # el sub solo es referencia del mic
    limites = [r.limite for r in resultados if r.limite is not None]
    if not limites:
        return "Sin límites marcados: no hay recomendación."
    alto, bajo = max(limites), min(limites)
    if len(limites) == 1:
        canal = next(r.canal for r in resultados if r.limite is not None)
        return f"Solo se marcó {canal}: el cruce iría desde {alto + paso} Hz. Prueba las demás para comparar."
    if alto - bajo <= paso:
        return (f"Todas llegan casi igual ({bajo} a {alto} Hz): un solo cruce, un paso arriba del más alto "
                f"→ {alto + paso} Hz.")
    peores = ", ".join(r.canal for r in resultados if r.limite == alto)
    return (f"Varían de {bajo} a {alto} Hz ({peores} se queda antes). Revisa esa bocina (conexión, que no esté "
            f"tapada); si es así de fábrica, el cruce puede ser por bocina, o uno solo en {alto + paso} Hz.")


def tabla(resultados: list[Resultado], con_mic: bool) -> str:
    lineas = []
    for r in resultados:
        linea = f"  {r.canal:<3} {NOMBRES.get(r.canal, ''):<18} {r.texto_limite}"
        if con_mic and r.medidas:
            c, d, p = caida(r.medidas), distorsiona(r.medidas), se_pierde(r.medidas)
            hz = lambda v: f"{v} Hz" if v else "—"  # noqa: E731
            linea += (f" · mic: cae {CAIDA_DB:.0f} dB en {hz(c)}, distorsión >{DISTORSION_PCT:.0f} % en {hz(d)}, "
                      f"ya no se oye en {hz(p)}")
        lineas.append(linea)
    return "\n".join(lineas)


# ---------- E/S: tocar, grabar y leer teclas ----------

def _props(nombre: str) -> str:
    # Sin restaurar volumen ni destino de WirePlumber (por application.name): el tono siempre al 100 %
    # y siempre al bus elegido; si el bus desaparece, no se va a otro sink.
    return ("{ " + f'node.name = "{nombre}" application.name = "ELENA prueba de satélites" '
            'media.role = "Music" state.restore-props = false state.restore-target = false '
            "node.dont-reconnect = true node.dont-fallback = true }")


class Grabadora:
    """`pw-record` del mic mientras suena un tono; un hilo vacía el pipe para que no se trabe."""

    def __init__(self, fuente: str, canales: int = 2, monitor: bool = False) -> None:
        self.canales = canales
        # monitor=True graba el monitor de un sink (solo para probar la cadena con un sink nulo)
        extra = " stream.capture.sink = true" if monitor else ""
        self._datos = bytearray()
        self._proc = subprocess.Popen(
            ["pw-record", "--target", fuente, "--raw", "--format", "f32", "--rate", str(TASA),
             "--channels", str(canales), "--latency", "20ms", "-P",
             '{ node.name = "elena_prueba_mic" application.name = "ELENA prueba de satélites" '
             "node.dont-reconnect = true node.dont-fallback = true" + extra + " }", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self._hilo = threading.Thread(target=self._leer, daemon=True)
        self._hilo.start()

    def _leer(self) -> None:
        assert self._proc.stdout
        while bloque := self._proc.stdout.read(65536):
            self._datos += bloque

    def terminar(self) -> np.ndarray:
        with contextlib.suppress(ProcessLookupError):
            self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._hilo.join(timeout=2)
        n = len(self._datos) // (4 * self.canales) * self.canales
        return np.frombuffer(bytes(self._datos[:n * 4]), dtype=np.float32).reshape(-1, self.canales)


def tocar(senal: np.ndarray, destino: str, posiciones: tuple[str, ...], archivo: Path) -> subprocess.Popen:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_bytes(np.ascontiguousarray(senal, dtype=np.float32).tobytes())
    return subprocess.Popen(
        ["pw-play", "--target", destino, "--raw", "--format", "f32", "--rate", str(TASA),
         "--channels", str(len(posiciones)), "--channel-map", ",".join(posiciones),
         "--volume", "1.0", "-P", _props("elena_prueba_satelites"), str(archivo)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def detener(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextlib.contextmanager
def teclado() -> Iterator[Callable[[float], str | None]]:
    """Lee teclas sueltas (sin Enter) mientras dura el bloque; restaura la terminal al salir."""
    fd = sys.stdin.fileno()
    antes = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def leer(espera: float) -> str | None:
        listo, _, _ = select.select([fd], [], [], espera)
        return os.read(fd, 1).decode(errors="ignore").lower() if listo else None

    try:
        yield leer
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, antes)


# ---------- La prueba ----------

@dataclass
class Opciones:
    destino: str = "elena_musica"
    canales: tuple[str, ...] = SATELITES
    desde: int = 200
    hasta: int = PISO_HZ
    paso: int = 10
    nivel_db: float = NIVEL_DB
    mic: bool = False
    en_bus: bool = False            # ignora la prueba de volumen: tonos al bus, a nivel_db
    relativo: float = 0.0           # dB respecto al volumen cómodo (≤ 0, para probar más bajo)
    # Con prueba de volumen: pico del tono en el DAC por bocina, y el volumen del físico por canal
    niveles: dict[str, float] = field(default_factory=dict)
    fisico_db: dict[str, float] = field(default_factory=dict)


TECHO_DBFS = -1.0
RELATIVO_MIN = -20.0


def con_volumen(op: Opciones, maximos: dict[str, tuple[float, float]], fisico_db: dict[str, float],
                destino: str = SINK_FISICO) -> Opciones:
    """Tonos directo a `destino`, cada uno con el RMS del ruido rosa que fue cómodo en esa bocina (una
    senoidal con el mismo pico sonaría ~8 dB más fuerte que el ruido)."""
    if not RELATIVO_MIN <= op.relativo <= 0:
        raise ErrorElena(f"--relativo va de {RELATIVO_MIN:.0f} a 0 dB (solo para probar más bajo).")
    niveles = {c: round(min(rms + 3.01 + op.relativo, TECHO_DBFS), 2) for c, (_, rms) in maximos.items()}
    return replace(op, destino=destino, niveles=niveles, fisico_db=fisico_db)


def preparar(grafo: Grafo, op: Opciones) -> tuple[tuple[str, ...], list[str]]:
    """Valida el destino y los canales; devuelve las posiciones del destino y los avisos."""
    if op.niveles:
        # Directo al físico, al volumen cómodo que elegiste en la prueba de volumen
        if faltan := [c for c in op.canales if c not in op.niveles]:
            raise ErrorElena(f"Falta la prueba de volumen de {', '.join(faltan)}: córrela primero "
                             f"(mise run calibracion -- volumen --canal {faltan[0]}) o usa --en-bus.")
    elif op.destino == SINK_FISICO or op.destino.startswith("alsa_"):
        raise ErrorElena("Sin prueba de volumen, el tono no puede ir directo a una salida física: sonaría mucho "
                         "más fuerte que la música. Usa un bus (elena_musica).")
    elif not NIVEL_MIN <= op.nivel_db <= NIVEL_MAX:
        raise ErrorElena(f"El nivel va de {NIVEL_MIN:.0f} a {NIVEL_MAX:.0f} dBFS.")
    nodo = grafo.nodo(op.destino)
    if nodo is None:
        raise ErrorElena(f"No existe {op.destino}. ¿Está aplicado ELENA? (mise run estado)")
    posiciones = nodo.posiciones
    if not posiciones:
        raise ErrorElena(f"{op.destino} no tiene posiciones de canal.")
    faltan = [c for c in op.canales if c not in posiciones]
    if faltan:
        raise ErrorElena(f"{op.destino} no tiene {', '.join(faltan)}; tiene {' '.join(posiciones)}.")
    if "LFE" in op.canales and not op.mic:
        raise ErrorElena("El LFE no es una satélite: solo entra con --mic, como referencia de qué tan bien "
                         "oye el mic los graves.")
    if nodo.silencio:
        raise ErrorElena(f"{nodo.visible} está silenciado: actívalo (elena silenciar {op.destino} off).")
    avisos = []
    bus = next((b for b in grafo.buses() if b.nodo and b.nodo.nombre == op.destino), None)
    if bus and not bus.destinos:
        avisos.append(f"{nodo.visible} no está conectado a ninguna salida (espera a {bus.esperando or '?'}).")
    if bus and (otras := [a.visible for a in bus.apps if a.nombre != "elena_prueba_satelites"]):
        avisos.append(f"También suena en {nodo.visible}: {', '.join(otras)}. Páusalo para oír solo el tono.")
    if op.niveles and (otras := sorted({a.visible for b in grafo.buses() if b.definicion.clave != "chat"
                                        for a in b.apps})):
        avisos.append(f"También suena: {', '.join(otras)}. Páusalo para oír solo el tono.")
    if op.mic and not (MIC and grafo.nodo(MIC)):
        raise ErrorElena(f"No encuentro el mic físico ({MIC or 'ELENA_MIC_FISICO vacío'}).")
    return posiciones, avisos


def _volumen_canal(grafo: Grafo, destino: str, canal: str) -> str:
    nodo = grafo.nodo(destino)
    if not nodo or canal not in nodo.posiciones or len(nodo.volumenes) != len(nodo.posiciones):
        return "?"
    return f"{lineal_a_db(nodo.volumenes[nodo.posiciones.index(canal)]):.1f} dB"


def probar_bocina(canal: str, posiciones: tuple[str, ...], op: Opciones,
                  leer: Callable[[float], str | None]) -> Resultado:
    rec = Recorrido(frecuencias(op.desde, op.hasta, op.paso))
    medidas: dict[int, Medida] = {}
    archivo = Path(os.environ.get("TMPDIR") or CARPETA) / "elena-satelites.f32"
    while not rec.fin:
        f = rec.actual
        print(f"\r\033[K  {canal} · {f:>3} Hz   (espacio: falla o no se oye · r repite · a anterior · s salta · q termina)",
              end="", flush=True)
        grabadora = Grabadora(MIC) if op.mic else None
        proc = None
        cortado = False
        try:
            if grabadora:
                time.sleep(0.15)   # que el mic ya esté grabando cuando empiece el tono
            nivel = op.niveles[canal] - op.fisico_db.get(canal, 0.0) if op.niveles else op.nivel_db
            proc = tocar(tono(f, posiciones.index(canal), len(posiciones), nivel), op.destino, posiciones, archivo)
            while proc.poll() is None:
                k = leer(0.05)
                if k and rec.tecla(k) == "cortar":
                    cortado = True
                    detener(proc)
        except KeyboardInterrupt:
            # Ctrl+C: pw-play y pw-record van en su propia sesión y no lo reciben; se detienen aquí
            rec.fin, cortado = "salir", True
        finally:
            if proc:
                detener(proc)
            grabacion = grabadora.terminar() if grabadora else None
        if grabacion is not None:
            if not cortado and (m := analizar(grabacion, f)):
                medidas[f] = m
                texto = (f"distorsión {m.distorsion_pct:5.1f} %" if m.audible
                         else f"ya no se oye ({m.sobre_ruido_db:.0f} dB sobre el ruido)")
                print(f"   mic: {m.nivel_db:6.1f} dBFS · {texto}", end="", flush=True)
        if not cortado:
            time.sleep(0.25)   # un respiro entre pasos
        rec.termino_tono(cortado)
    print()
    orden = sorted(medidas.values(), key=lambda m: -m.frecuencia)
    return Resultado(canal, rec.fin, rec.marca, rec.limite, orden)


def guardar(resultados: list[Resultado], op: Opciones) -> Path:
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"satelites-{datetime.now():%Y%m%d-%H%M%S}.json"
    datos = {"fecha": datetime.now().isoformat(timespec="seconds"), "opciones": asdict(op),
             "resultados": [asdict(r) for r in resultados]}
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
    return ruta


def correr(grafo: Grafo, op: Opciones) -> int:
    if not sys.stdin.isatty():
        raise ErrorElena("La prueba de las satélites es interactiva: córrela en una terminal.")
    if not op.en_bus:
        from elena.maximo import db_por_canal, ultimos_maximos
        if maximos := ultimos_maximos():
            destino = SINK_FISICO if op.destino == "elena_musica" else op.destino   # otro: pruebas
            op = con_volumen(op, maximos, db_por_canal(grafo, destino), destino)
    posiciones, avisos = preparar(grafo, op)
    pasos = frecuencias(op.desde, op.hasta, op.paso)
    print(f"Prueba de las satélites: tonos de {op.desde} a {pasos[-1]} Hz cada {op.paso} Hz, {DURACION:.0f} s cada uno,")
    if op.niveles:
        print(f"directo al físico, al volumen cómodo de tu prueba de volumen{f' ({op.relativo:+.0f} dB)' if op.relativo else ''}.")
    else:
        print(f"a {op.nivel_db:.0f} dBFS de pico en {op.destino} (con su volumen; más bajo que la música).")
        print("Sin prueba de volumen: puede sonar muy bajo. Mejor primero: mise run calibracion -- volumen")
    print("En cada bocina, oprime ESPACIO en el primer paso donde el tono:")
    print("  · deja de oírse: el silencio también es falla (la bocina ya no llega ahí);")
    print("  · cae de golpe, vibra, zumba o distorsiona;")
    print("  · suena más agudo de lo que debería: son los armónicos de la bocina, no el tono.")
    print("Bajando, todo tono se oye un poco más bajo aunque la bocina esté bien (el oído es menos sensible")
    print("en graves): lo que cuenta es la caída brusca. r repite el paso, a vuelve al anterior, s salta, q termina.")
    if op.mic:
        print("Grabando con el mic físico: déjalo fijo en el punto de escucha.")
    for a in avisos:
        print(f"⚠ {a}")
    resultados: list[Resultado] = []
    with teclado() as leer:
        for canal in op.canales:
            volumen = (f"tono a {op.niveles[canal]:.1f} dBFS de pico" if op.niveles
                       else f"volumen del bus {_volumen_canal(grafo, op.destino, canal)}")
            print(f"\n{canal} ({NOMBRES.get(canal, canal)}), {volumen}. Enter para empezar, s para saltarla, q para terminar.")
            k = None
            try:
                while k not in ("\n", "\r", " ", "s", "q", "\x03"):
                    k = leer(0.5)
            except KeyboardInterrupt:
                k = "q"
            if k in ("q", "\x03"):
                break
            if k == "s":
                resultados.append(Resultado(canal, "saltada", None, None))
                continue
            r = probar_bocina(canal, posiciones, op, leer)
            resultados.append(r)
            print(f"  → {r.texto_limite}")
            if r.fin == "salir":
                break
    if not resultados:
        return 0
    print("\nResultado:")
    print(tabla(resultados, op.mic))
    print(recomendacion(resultados, op.paso))
    print(f"Guardado en {guardar(resultados, op)}")
    return 0
