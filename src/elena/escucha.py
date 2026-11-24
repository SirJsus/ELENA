"""Prueba con música real: ¿el sub aguanta al volumen ideal?

La prueba de volumen da el volumen ideal de las satélites, pero el sub puede distorsionar antes con
ruido rosa. El ruido es continuo y el LFE de una canción no, así que aquí se prueba con música real:
pones una canción de muchos graves en el bus de Música y subes el bus con una tecla, de 2 en 2 dB, conservando el balance entre canales; aparte puedes subir o bajar solo
el sub. La terminal muestra en vivo el pico que le llega al sub en el DAC, junto a su máximo limpio de
la prueba de volumen. Espacio anota «aquí el sub distorsiona» y Enter, «así suena bien».

**Cambia el volumen del bus de Música** mientras dura, y al salir (q, Ctrl+C o un error) lo regresa
exactamente a como estaba. El volumen original se guarda en `.cache/calibracion/musica-original.json`
mientras tanto: si el proceso muere, la siguiente corrida (o `--restaurar`) lo regresa primero.

Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from elena.acciones import cmd_volumen, ejecutar
from elena.medidor import Objetivo
from elena.modelo import SINK_FISICO, ErrorElena, Grafo, lineal_a_db
from elena.satelites import CARPETA, teclado

BUS = "elena_musica"
PASO_DB = 2.0
BUS_MAX = 24.0          # 2 dB arriba del volumen ideal de las satélites (+22)
SUB_MIN, SUB_MAX = -30.0, 4.0
ESPERA = 1.0            # segundos que hay que oír un nivel antes de poder subir el bus
VENTANA = 3.0           # segundos del pico «reciente» que se muestra
ORIGINAL = CARPETA / "musica-original.json"


# ---------- Estado (sin E/S, para probarlo) ----------

@dataclass
class Marca:
    nota: str                 # distorsiona | bien
    bus: float                # dB sobre el volumen de hoy
    sub: float                # dB extra del sub sobre su balance de hoy
    lfe_dac: float | None     # pico del LFE en el DAC en los últimos segundos (dBFS)


@dataclass
class Escucha:
    """Teclas: + / − el bus, ] / [ el sub, espacio «distorsiona», Enter «bien», q termina."""

    ahora: float = 0.0        # reloj del último cambio del bus
    bus: float = 0.0
    sub: float = 0.0
    fin: bool = False
    aviso: str = ""
    marcas: list[Marca] = field(default_factory=list)
    # Lo más alto de la sesión, aunque no se anote: el bus, y el pico del sub en el DAC con su bus y sub
    bus_max: float = 0.0
    lfe_max: Marca | None = None

    def registrar(self, lfe_dac: float | None) -> None:
        self.bus_max = max(self.bus_max, self.bus)
        if lfe_dac is not None and (self.lfe_max is None or lfe_dac > (self.lfe_max.lfe_dac or -999)):
            self.lfe_max = Marca("máximo", self.bus, self.sub, round(lfe_dac, 1))

    def tecla(self, k: str, reloj: float, lfe_dac: float | None = None) -> bool:
        """Aplica una tecla; True si cambió el volumen."""
        self.aviso = ""
        if k in ("+", "="):
            if reloj - self.ahora < ESPERA:
                self.aviso = "espera a oír este nivel"
                return False
            if self.bus + PASO_DB > BUS_MAX:
                self.aviso = f"es el tope de la prueba (+{BUS_MAX:.0f} dB)"
                return False
            self.bus += PASO_DB
            self.ahora = reloj
            return True
        if k == "-":
            self.bus = max(self.bus - PASO_DB, -20.0)
            self.ahora = reloj
            return True
        if k == "]":
            if self.sub + PASO_DB > SUB_MAX:
                self.aviso = f"el sub no sube más de {SUB_MAX:+.0f} dB en esta prueba"
                return False
            self.sub += PASO_DB
            return True
        if k == "[":
            self.sub = max(self.sub - PASO_DB, SUB_MIN)
            return True
        if k == " ":
            self.marcas.append(Marca("distorsiona", self.bus, self.sub, lfe_dac))
            self.aviso = "anotado: distorsiona"
        elif k in ("\n", "\r"):
            self.marcas.append(Marca("bien", self.bus, self.sub, lfe_dac))
            self.aviso = "anotado: suena bien"
        elif k in ("q", "\x03"):
            self.fin = True
        return False


def volumenes(original: tuple[float, ...], posiciones: tuple[str, ...], bus: float, sub: float) -> tuple[float, ...]:
    """El volumen de hoy subido `bus` dB en todos los canales y `sub` dB más en el LFE (lineal)."""
    return tuple(v * 10 ** ((bus + (sub if p == "LFE" else 0.0)) / 20) for v, p in zip(original, posiciones))


def sin_calibracion(grafo: Grafo, prueba: str) -> None:
    """Estas pruebas ponen el balance en el volumen del bus: con la calibración activa (el bus va a
    elena_salida) se aplicaría dos veces."""
    from elena.modelo import CALIBRACION
    salida = grafo.nodo(f"{BUS}.salida")
    if salida and salida.objetivo == CALIBRACION:
        raise ErrorElena(f"{prueba} es de antes de la calibración: pone el balance en el volumen del bus, y con la "
                         "calibración activa se aplicaría dos veces. Desactívala para repetirla "
                         "(mise run calibracion -- desactivar y mise run aplicar).")


# ---------- Volumen original: guardar y restaurar ----------

def guardar_original(nodo_nombre: str, vols: tuple[float, ...], ruta: Path = ORIGINAL) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"nodo": nodo_nombre, "volumenes": list(vols)}) + "\n")


def restaurar(grafo: Grafo, ruta: Path = ORIGINAL) -> str | None:
    """Regresa el volumen guardado (si hay) y borra el archivo. Devuelve qué hizo."""
    if not ruta.exists():
        return None
    datos = json.loads(ruta.read_text())
    nodo = grafo.nodo(datos["nodo"])
    if nodo is None:
        raise ErrorElena(f"No existe {datos['nodo']} para regresarle su volumen ({ruta}).")
    ejecutar(cmd_volumen(nodo, tuple(datos["volumenes"])))
    ruta.unlink()
    return f"{nodo.visible}: volumen regresado a como estaba antes de la prueba."


# ---------- Medidor del monitor del bus ----------

class Picos:
    """Pico por canal del monitor del bus (antes de su volumen), por bloques de 50 ms."""

    def __init__(self, posiciones: tuple[str, ...]) -> None:
        self.posiciones = posiciones
        self._bloques: deque[tuple[float, np.ndarray]] = deque()
        self._lock = threading.Lock()
        cmd = Objetivo(BUS, len(posiciones), posiciones, monitor=True).comando("elena_prueba_musica")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self._hilo = threading.Thread(target=self._leer, daemon=True)
        self._hilo.start()

    def _leer(self) -> None:
        assert self._proc.stdout
        tam = 2400 * len(self.posiciones) * 4
        while datos := self._proc.stdout.read(tam):
            x = np.frombuffer(datos[: len(datos) // (4 * len(self.posiciones)) * 4 * len(self.posiciones)],
                              dtype=np.float32).reshape(-1, len(self.posiciones))
            ahora = time.monotonic()
            with self._lock:
                self._bloques.append((ahora, np.abs(x).max(axis=0)))
                while self._bloques and self._bloques[0][0] < ahora - 30:
                    self._bloques.popleft()

    def pico_db(self, canal: str, segundos: float = VENTANA) -> float | None:
        """El pico más alto de ese canal en los últimos `segundos`, en dBFS (antes del volumen del bus)."""
        i = self.posiciones.index(canal)
        limite = time.monotonic() - segundos
        with self._lock:
            valores = [p[i] for t, p in self._bloques if t >= limite]
        if not valores or max(valores) <= 0:
            return None
        return lineal_a_db(float(max(valores)))

    def cerrar(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()


# ---------- La prueba ----------

def _maximo_sub() -> float | None:
    from elena.maximo import ultimos_maximos
    return ultimos_maximos().get("LFE", (None, None))[0]


def correr(grafo: Grafo, solo_restaurar: bool = False) -> int:
    if hecho := restaurar(grafo):
        print(hecho)
        grafo = _recargar()
    if solo_restaurar:
        if not hecho:
            print("No había un volumen que regresar.")
        return 0
    if not sys.stdin.isatty():
        raise ErrorElena("La prueba con música es interactiva: córrela en una terminal.")
    nodo = grafo.nodo(BUS)
    if nodo is None or len(nodo.volumenes) != len(nodo.posiciones) or "LFE" not in nodo.posiciones:
        raise ErrorElena(f"Necesito {BUS} en 5.1 (mise run estado).")
    sin_calibracion(grafo, "La prueba con música")
    fisico = grafo.nodo(SINK_FISICO)
    fis_lfe = 0.0
    if fisico and "LFE" in fisico.posiciones and len(fisico.volumenes) == len(fisico.posiciones):
        fis_lfe = lineal_a_db(fisico.volumenes[fisico.posiciones.index("LFE")])
    original, posiciones = nodo.volumenes, nodo.posiciones
    # El monitor de los buses de ELENA mide antes del volumen (monitor.channel-volumes = false, verificado);
    # un sink nulo de `pactl load-module` lo trae en true y ahí el volumen ya viene incluido.
    props = ((grafo.crudo(nodo.id) or {}).get("info") or {}).get("props") or {}
    con_volumen = str(props.get("monitor.channel-volumes", "false")).lower() == "true"
    lfe_hoy = lineal_a_db(original[posiciones.index("LFE")])
    fl_hoy = lineal_a_db(original[posiciones.index("FL")]) if "FL" in posiciones else None
    maximo_sub = _maximo_sub()
    apps = next((b.apps for b in grafo.buses() if b.nodo and b.nodo.nombre == BUS), [])

    print("Prueba con música real: ¿el sub aguanta a tu volumen ideal?")
    print("Pon una canción con muchos graves en el bus de Música (Apple Music en la VM, por ejemplo).")
    print("  + / −   sube o baja el bus 2 dB (conserva el balance; tu volumen ideal es +22 dB)")
    print("  ] / [   sube o baja solo el sub 2 dB")
    print("  espacio anota «aquí el sub distorsiona» · Enter anota «así suena bien» · q termina")
    print("Al salir, el bus regresa exactamente a como estaba. Sube con calma: +22 dB es mucho más fuerte que hoy.")
    vol = " ".join(f"{p} {lineal_a_db(v):.1f}" for p, v in zip(nodo.posiciones, nodo.volumenes))
    print(f"El bus empieza en: {vol} dB (los +dB de la prueba son sobre esto).")
    if not apps:
        print("⚠ Ahora no suena nada en el bus de Música.")
    if maximo_sub is not None:
        print(f"Máximo limpio del sub con ruido rosa: {maximo_sub:.1f} dBFS de pico en el DAC.")

    guardar_original(nodo.nombre, original)
    picos = Picos(posiciones)
    e = Escucha(ahora=time.monotonic())
    try:
        with teclado() as leer:
            aplicado = (None, None)
            while not e.fin:
                if (e.bus, e.sub) != aplicado:
                    ejecutar(cmd_volumen(nodo, volumenes(original, posiciones, e.bus, e.sub)))
                    aplicado = (e.bus, e.sub)
                lfe = picos.pico_db("LFE")
                ganancia_lfe = 0.0 if con_volumen else lfe_hoy + e.bus + e.sub
                lfe_dac = None if lfe is None else lfe + ganancia_lfe + fis_lfe
                fl = picos.pico_db("FL") if fl_hoy is not None else None
                linea = f"\r\033[K  bus {e.bus:+5.1f} dB · sub {e.sub:+5.1f} dB · sub en el DAC: "
                linea += "—" if lfe_dac is None else f"{lfe_dac:6.1f} dBFS"
                if maximo_sub is not None and lfe_dac is not None:
                    linea += f" ({lfe_dac - maximo_sub:+5.1f} vs. su máximo)"
                if fl is not None:
                    linea += f" · FL {fl + (0.0 if con_volumen else fl_hoy + e.bus):6.1f} dBFS"
                if e.aviso:
                    linea += f"   ⚠ {e.aviso}"
                print(linea, end="", flush=True)
                e.registrar(lfe_dac)
                if (k := leer(0.25)) is not None:
                    e.tecla(k, time.monotonic(), None if lfe_dac is None else round(lfe_dac, 1))
    except KeyboardInterrupt:
        pass
    finally:
        picos.cerrar()
        print()
        print(restaurar(_recargar()) or "")

    print(f"Lo más alto: bus {e.bus_max:+.0f} dB sobre el volumen de al empezar"
          + (f"; el sub llegó a {e.lfe_max.lfe_dac:.1f} dBFS en el DAC (bus {e.lfe_max.bus:+.0f}, sub {e.lfe_max.sub:+.0f})"
             if e.lfe_max else ""))
    if e.marcas or e.lfe_max:
        print("Anotado:")
        for m in e.marcas:
            dac = "—" if m.lfe_dac is None else f"{m.lfe_dac:.1f} dBFS"
            print(f"  {m.nota:<11} bus {m.bus:+.0f} dB · sub {m.sub:+.0f} dB · sub en el DAC {dac}")
        ruta = CARPETA / f"musica-{datetime.now():%Y%m%d-%H%M%S}.json"
        ruta.write_text(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                                    "maximo_sub": maximo_sub, "lfe_hoy_db": round(lfe_hoy, 2),
                                    "volumen_al_empezar_db": [round(lineal_a_db(v), 2) for v in original],
                                    "bus_max": e.bus_max, "lfe_max": asdict(e.lfe_max) if e.lfe_max else None,
                                    "marcas": [asdict(m) for m in e.marcas]}, ensure_ascii=False, indent=2) + "\n")
        print(f"Guardado en {ruta}")
    return 0


def _recargar() -> Grafo:
    from elena.cli import cargar_grafo
    return cargar_grafo()
