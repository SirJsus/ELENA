"""Tu 100 %, tu sub y tu máximo con música, desde neutro y a ciegas.

Nada de tu configuración actual cuenta (el volumen de los buses puede traer un balance viejo, y cambia):
aquí todo es absoluto:

- **El nivel** es la ganancia de FL hasta el DAC (bus + volumen del físico), en dB. Las demás satélites
  van con el balance de la nivelación (`elena calibracion nivelar`) y el LFE con +10 dB sobre FL (lo
  que un receptor le suma al canal LFE) más el ajuste del sub. Lo que tenga hoy el bus no cuenta: la
  prueba pone los 6 canales y descuenta el volumen del físico.
- **Tres fases por ronda**, con música 5.1 en el bus de Música:
  1. **Tu 100 %**, tu volumen de todos los días: empieza en un nivel al azar; + y − hasta que sea ese.
  2. **El sub**, a tu 100 %: empieza con un desfase al azar; + y − hasta que los graves estén bien.
  3. **Tu máximo**, lo más fuerte con lo que estás cómodo («a todo volumen»): empieza cerca de tu 100 %.
- **A ciegas:** sin números hasta el final. Dos rondas y una tercera si alguna medida difiere más de
  3 dB; vale la mediana.
- **No cambia tu configuración:** al salir (q, Ctrl+C o un error) el bus regresa exactamente a como
  estaba, con el mismo respaldo que la prueba con música (`.cache/calibracion/musica-original.json`,
  `--restaurar`).

Avisa si la canción es estéreo (FC, LFE y las traseras en silencio digital): tu 100 % se define con 5.1
(el estéreo llega ~12 dB más fuerte). El resultado se guarda en `.cache/calibracion/cien-<fecha>.json`.
Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from elena import escucha as es
from elena.acciones import cmd_volumen, ejecutar
from elena.maximo import PICO_MUSICA, db_por_canal
from elena.modelo import SINK_FISICO, ErrorElena, Grafo, lineal_a_db, lineal_a_pct
from elena.nivelar import ultimos_balances
from elena.satelites import CARPETA, teclado

PASO_DB = 1.0
ESPERA = 0.5                    # segundos entre subidas (la tecla sostenida no sube de golpe)
LFE_BASE = 10.0                 # el LFE va +10 dB sobre FL, como en un receptor
NIVEL_MIN, NIVEL_MAX = -50.0, -6.0     # ganancia de FL hasta el DAC
SUB_MIN, SUB_MAX = -15.0, 10.0         # el ajuste del sub sobre LFE_BASE
INICIO_CIEN = (-30, -18)        # el 100 % empieza al azar aquí (dB enteros, incluidos)
INICIO_SUB = (-6, 6)
INICIO_MAXIMO = (-4, 0)         # el máximo empieza en tu 100 % más esto
TERCERA_SI = 3.0
SILENCIO_DB = -80.0             # «estéreo»: FC, LFE y traseras debajo de esto mientras FL suena
SATELITES = ("FL", "FR", "FC", "RL", "RR")


# ---------- Ganancias (sin E/S) ----------

def ganancias_db(posiciones: tuple[str, ...], nivel: float, balance: dict[str, float], sub: float,
                 fisico: dict[str, float]) -> tuple[float, ...]:
    """La ganancia del bus por canal (dB) para que el DAC reciba: FL `nivel`, cada satélite `nivel` + su
    balance y el LFE `nivel` + 10 + `sub`. El volumen del físico se descuenta."""
    def destino(c: str) -> float:
        return nivel + (LFE_BASE + sub if c == "LFE" else balance.get(c, 0.0))
    return tuple(destino(c) - fisico.get(c, 0.0) for c in posiciones)


def a_lineal(dbs: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(10 ** (d / 20) for d in dbs)


def es_estereo(picos: dict[str, float | None]) -> bool | None:
    """True si FL o FR suenan y FC, LFE, RL y RR están en silencio digital; None si no suena nada."""
    frente = [v for c in ("FL", "FR") if (v := picos.get(c)) is not None]
    if not frente or max(frente) < -60:
        return None
    resto = [picos.get(c) for c in ("FC", "LFE", "RL", "RR") if c in picos]
    return all(v is None or v < SILENCIO_DB for v in resto)


# ---------- Una fase (sin E/S, para probarlo) ----------

@dataclass
class Fase:
    """Teclas: + sube 1 dB (no más de una vez cada 0.5 s), − baja, Enter o espacio «es este», s salta
    (solo el máximo), q termina. El valor no se muestra."""

    nombre: str                     # cien, sub o maximo
    valor: float
    minimo: float
    maximo: float
    ahora: float = -math.inf        # reloj de la última subida
    fin: str | None = None          # marcada, saltada o salir
    aviso: str = ""

    def tecla(self, k: str, reloj: float) -> bool:
        """Aplica una tecla; True si cambió el volumen."""
        self.aviso = ""
        if k in ("+", "="):
            if reloj - self.ahora < ESPERA:
                self.aviso = "espera a oír este nivel"
                return False
            if self.valor + PASO_DB > self.maximo + 1e-9:
                self.aviso = "es lo más alto de la prueba"
                return False
            self.valor += PASO_DB
            self.ahora = reloj
            return True
        if k == "-":
            if self.valor - PASO_DB < self.minimo - 1e-9:
                self.aviso = "es lo más bajo de la prueba"
                return False
            self.valor -= PASO_DB
            return True
        if k in ("\n", "\r", " "):
            self.fin = "marcada"
        elif k == "s" and self.nombre == "maximo":
            self.fin = "saltada"
        elif k in ("q", "\x03"):
            self.fin = "salir"
        return False


def al_azar(rng: np.random.Generator, rango: tuple[int, int]) -> float:
    return float(rng.integers(rango[0], rango[1] + 1))


@dataclass
class Ronda:
    ronda: int
    cien: float | None = None       # ganancia de FL hasta el DAC
    sub: float | None = None        # ajuste del sub sobre LFE_BASE
    maximo: float | None = None
    inicios: dict[str, float] = field(default_factory=dict)


def mediana(valores: list[float]) -> float | None:
    return round(float(statistics.median(valores)), 1) if valores else None


def difieren(rondas: list[Ronda]) -> list[str]:
    """Las medidas cuyas dos primeras rondas difieren más de 3 dB."""
    if len(rondas) != 2:
        return []
    a, b = rondas
    return [m for m in ("cien", "sub", "maximo")
            if getattr(a, m) is not None and getattr(b, m) is not None
            and abs(getattr(a, m) - getattr(b, m)) > TERCERA_SI]


@dataclass
class Resultado:
    cien: float | None
    sub: float | None
    maximo: float | None

    @property
    def lfe(self) -> float | None:
        """El LFE sobre FL, en total."""
        return None if self.sub is None else LFE_BASE + self.sub

    @property
    def tope(self) -> float | None:
        """Cuánto puede subir el bus sobre tu 100 % hasta tu máximo (dB)."""
        return None if self.cien is None or self.maximo is None else self.maximo - self.cien


def resultado(rondas: list[Ronda]) -> Resultado:
    return Resultado(*(mediana([getattr(r, m) for r in rondas if getattr(r, m) is not None])
                       for m in ("cien", "sub", "maximo")))


def texto_resultado(r: Resultado, rondas: list[Ronda], balance: dict[str, float]) -> list[str]:
    def valores(m: str) -> str:
        return ", ".join(f"{getattr(x, m):+.0f}" for x in rondas if getattr(x, m) is not None)
    lineas = []
    if r.cien is not None:
        lineas.append(f"  Tu 100 %:    FL {r.cien:+.1f} dB hasta el DAC (rondas {valores('cien')}): la música 5.1 "
                      f"llega con picos de ~{r.cien + PICO_MUSICA:.0f} dBFS en FL.")
    if r.sub is not None:
        lineas.append(f"  El sub:      LFE {r.lfe:+.1f} dB sobre FL (+{LFE_BASE:.0f} del receptor {r.sub:+.1f}; "
                      f"rondas {valores('sub')}).")
    if r.maximo is not None:
        texto = f"  Tu máximo:   FL {r.maximo:+.1f} dB (rondas {valores('maximo')})"
        if r.tope is not None:
            texto += (f": {r.tope:+.1f} dB sobre tu 100 %; si el bus al 100 % es tu 100 %, "
                      f"su tope es {lineal_a_pct(10 ** (r.tope / 20)):.0f} %.")
        lineas.append(texto)
        if r.tope is not None and r.tope < 0:
            lineas.append("  ⚠ Tu máximo quedó debajo de tu 100 %: repite la prueba (quizá cambió la canción o su parte).")
    if r.cien is not None:
        canales = {"fl": 0.0, **{c.lower(): balance.get(c, 0.0) for c in SATELITES[1:]}}
        partes = [f"{c} {r.cien + g:+.1f}" for c, g in canales.items()]
        if r.lfe is not None:
            partes.append(f"lfe {r.cien + r.lfe:+.1f}")
        lineas.append("  Ganancia de cada canal hasta el DAC a tu 100 % (para el filter-chain, con el bus al 100 %): "
                      + " · ".join(partes) + " dB")
    return lineas


# ---------- La prueba ----------

def _linea(ronda: int, f: Fase, estereo: bool | None) -> str:
    textos = {
        "cien": "tu 100 % (tu volumen de todos los días) · + sube · − baja · Enter: es este",
        "sub": "el sub a tu 100 % · + sube · − baja · Enter: así están bien los graves",
        "maximo": "tu máximo (lo más fuerte cómodo) · + sube · − baja · Enter: es este · s salta",
    }
    musica = {None: "  ♪ sin música", True: "  ⚠ suena estéreo: pon una canción en Atmos", False: "  ♪ 5.1"}[estereo]
    return (f"\r\033[K  ronda {ronda} · {textos[f.nombre]} · q termina{musica}"
            + (f"   ⚠ {f.aviso}" if f.aviso else ""))


def correr(grafo: Grafo, solo_restaurar: bool = False, rng: np.random.Generator | None = None,
           fisico_nombre: str = SINK_FISICO) -> int:
    """`fisico_nombre` solo cambia para probar la cadena con un sink nulo."""
    if hecho := es.restaurar(grafo):
        print(hecho)
        grafo = es._recargar()
    if solo_restaurar:
        if not hecho:
            print("No había un volumen que regresar.")
        return 0
    if not sys.stdin.isatty():
        raise ErrorElena("La comparación es interactiva: córrela en una terminal.")
    nodo = grafo.nodo(es.BUS)
    if nodo is None or len(nodo.volumenes) != len(nodo.posiciones) or "LFE" not in nodo.posiciones:
        raise ErrorElena(f"Necesito {es.BUS} en 5.1 (mise run estado).")
    es.sin_calibracion(grafo, "La comparación")
    balance = ultimos_balances()
    if faltan := [c for c in SATELITES[1:] if c not in balance]:
        raise ErrorElena(f"Falta el balance de {', '.join(faltan)}: corre primero "
                         "mise run calibracion -- nivelar --mic")
    fisico = db_por_canal(grafo, fisico_nombre)
    rng = rng or np.random.default_rng()
    original, posiciones = nodo.volumenes, nodo.posiciones
    apps = next((b.apps for b in grafo.buses() if b.nodo and b.nodo.nombre == es.BUS), [])

    print("Tu 100 %, tu sub y tu máximo, con música y desde neutro.")
    print("Lo que tenga hoy el bus de Música no cuenta: la prueba pone cada canal con el balance de tu")
    print(f"nivelación ({' · '.join(f'{c} {balance[c]:+.1f}' for c in SATELITES[1:])} dB sobre FL) y el LFE +{LFE_BASE:.0f}.")
    print("Pon música en Atmos (5.1) en el bus de Música, una o varias canciones que conozcas bien.")
    print("Tres fases por ronda: tu 100 % (tu volumen de todos los días), el sub y tu máximo (lo más fuerte")
    print("con lo que estás cómodo). Cada una empieza en un nivel al azar y no verás números hasta el final.")
    print("Al salir, el bus regresa exactamente a como estaba.")
    if not apps:
        print("⚠ Ahora no suena nada en el bus de Música.")
    if otras := [a.visible for a in apps if a.volumenes and abs(max(a.volumenes) - 1.0) > 0.01]:
        print(f"⚠ {', '.join(otras)} no está al 100 %: súbela a 100 % para que su volumen no cuente.")

    es.guardar_original(nodo.nombre, original)
    picos = es.Picos(posiciones)
    rondas: list[Ronda] = []
    salir = False
    estado = {"nivel": None, "sub": 0.0}
    aplicado: list = [None]

    def aplicar(nivel: float, sub: float) -> None:
        if aplicado[0] != (nivel, sub):
            ejecutar(cmd_volumen(nodo, a_lineal(ganancias_db(posiciones, nivel, balance, sub, fisico))))
            aplicado[0] = (nivel, sub)

    def fase(leer: Callable[[float], str | None], ronda: int, f: Fase) -> Fase:
        while not f.fin:
            nivel = f.valor if f.nombre != "sub" else estado["nivel"]
            sub = f.valor if f.nombre == "sub" else estado["sub"]
            aplicar(nivel, sub)
            estereo = es_estereo({c: picos.pico_db(c, 1.0) for c in posiciones})
            print(_linea(ronda, f, estereo), end="", flush=True)
            if (k := leer(0.2)) is not None:
                f.tecla(k, time.monotonic())
        print("\r\033[K  " + {"marcada": "anotado.", "saltada": "saltado."}.get(f.fin, ""), flush=True)
        return f

    def esperar(leer: Callable[[float], str | None]) -> bool:
        k = None
        while k not in ("\n", "\r", " ", "q", "\x03"):
            k = leer(0.5)
        return k not in ("q", "\x03")

    try:
        with teclado() as leer:
            for n in (1, 2, 3):
                pendientes = ("cien", "sub", "maximo") if n < 3 else tuple(difieren(rondas))
                if not pendientes:
                    break
                extra = f" (solo {', '.join(pendientes)}: no coincidieron)" if n == 3 else ""
                print(f"\nRonda {n}{extra}. Enter para empezar, q para terminar.")
                if not esperar(leer):
                    break
                r = Ronda(n)
                previa = resultado(rondas)
                estado["sub"] = previa.sub if previa.sub is not None else 0.0
                if "cien" in pendientes:
                    r.inicios["cien"] = al_azar(rng, INICIO_CIEN)
                    f = fase(leer, n, Fase("cien", r.inicios["cien"], NIVEL_MIN, NIVEL_MAX))
                    if f.fin == "salir":
                        salir = True
                    else:
                        r.cien = f.valor
                estado["nivel"] = r.cien if r.cien is not None else previa.cien
                if not salir and "sub" in pendientes and estado["nivel"] is not None:
                    r.inicios["sub"] = al_azar(rng, INICIO_SUB)
                    f = fase(leer, n, Fase("sub", r.inicios["sub"], SUB_MIN, SUB_MAX))
                    if f.fin == "salir":
                        salir = True
                    else:
                        r.sub = f.valor
                        estado["sub"] = f.valor
                if not salir and "maximo" in pendientes and estado["nivel"] is not None:
                    r.inicios["maximo"] = min(estado["nivel"] + al_azar(rng, INICIO_MAXIMO), NIVEL_MAX)
                    f = fase(leer, n, Fase("maximo", r.inicios["maximo"], NIVEL_MIN, NIVEL_MAX))
                    if f.fin == "salir":
                        salir = True
                    elif f.fin == "marcada":
                        r.maximo = f.valor
                if any(v is not None for v in (r.cien, r.sub, r.maximo)):
                    rondas.append(r)
                if salir:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        picos.cerrar()
        print()
        print(es.restaurar(es._recargar()) or "")

    if not rondas:
        return 0
    res = resultado(rondas)
    print("Resultado:")
    print("\n".join(texto_resultado(res, rondas, balance)))
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"cien-{datetime.now():%Y%m%d-%H%M%S}.json"
    ruta.write_text(json.dumps({
        "fecha": datetime.now().isoformat(timespec="seconds"), "modo": "neutro",
        "explicacion": "cien y maximo: ganancia de FL hasta el DAC (dB); sub: ajuste sobre LFE +10 dB",
        "balance_db": balance, "lfe_base_db": LFE_BASE, "fisico_db": fisico,
        "rondas": [asdict(r) for r in rondas], "resultado": asdict(res),
        "lfe_sobre_fl_db": res.lfe, "tope_db": res.tope,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"Guardado en {ruta}")
    return 0
