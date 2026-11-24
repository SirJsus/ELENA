"""Calibración de las bocinas: `local/calibracion.toml` → `40-elena-calibracion.conf` (en `local/`).

El TOML es la fuente de verdad: en dB, ms o metros, y Hz. Este módulo lo lee, lo valida y escribe
el `filter-chain` que crea el sink `elena_salida` (5.1) y lo manda al físico. Por bocina:

  satélite: entrada → EQ común → pasa altos ×2 → ganancia → retardo → su bocina
                                └► pasa bajos ×2 ─┐
  LFE:      nivel LFE ────────────────────────────┴► suma → EQ del sub → ganancia → retardo → sub

- El cruce es un Linkwitz-Riley de 4.º orden (dos biquads de Q 0.707 por lado), antes de las
  ganancias: los graves que se le quitan a una satélite llegan al sub con la ganancia del sub.
- Ganancia final de cada bocina = `nivel` (tu 100 %, FL hasta el DAC) + su ganancia + el preamp
  de `evitar_saturacion`. El LFE además lleva `[lfe] nivel` (lo que un receptor le suma al canal LFE).
- `evitar_saturacion`: si algún camino sube de 0 dB (nivel + ganancia + bandas positivas del
  EQ), todo baja lo mismo (preamp) para que ninguno pase de 0 dB, y se avisa.
- Los nodos se llaman por canal y etapa (`fl_ganancia`, `fr_retardo`, `fl_altos1`, `sub_suma`…), así
  que cada valor es un control que se cambia en vivo: `pw-cli set-param <id> Props '{ params = [
  "fl_ganancia:Mult" 0.5 ] }'`.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from elena import rutas
from elena.modelo import BUSES, CALIBRACION, SINK_FISICO, ErrorElena
from elena.modelo import BUSES_BOCINAS as CLAVES_BOCINAS

ELENA = rutas.RAIZ
TOML = rutas.LOCAL / "calibracion.toml"
CONF = rutas.CONF_PIPEWIRE / "40-elena-calibracion.conf"
NODO = CALIBRACION

SATELITES = ("fl", "fr", "fc", "rl", "rr")
BOCINAS = (*SATELITES, "sub")
# Orden de los canales del sink (como los buses) y la bocina que sale por cada uno
POSICIONES = ("FL", "FR", "FC", "LFE", "RL", "RR")
BOCINA_DE = {"FL": "fl", "FR": "fr", "FC": "fc", "LFE": "sub", "RL": "rl", "RR": "rr"}
# Entrada de cada canal a la suma del sub (`sub_suma:Gain N` apaga su parte en vivo)
ENTRADA_SUMA = {"fl": 1, "fr": 2, "fc": 3, "rl": 4, "rr": 5, "lfe": 6}

VELOCIDAD_SONIDO = 343.0         # m/s
MAX_RETARDO = 0.05               # s: el `max-delay` de cada nodo delay (17 m de diferencia)
Q_CRUCE = 0.707
TIPOS = ("pico", "estante_grave", "estante_agudo")
Q_POR_TIPO = {"pico": 1.0, "estante_grave": 0.707, "estante_agudo": 0.707}
# El EQ va en ranuras fijas `bq_raw` (coeficientes calculados aquí, RBJ): agregar, cambiar o quitar una
# banda es mandar sus coeficientes en vivo, sin reiniciar. Las que sobran quedan en neutro. Verificado
# el 2026-11-13: `bq_raw` acepta b0…a2 en vivo (y divide entre a0); sin `config.coefficients` deja
# pasar silencio, así que cada ranura lleva los suyos en el .conf.
RANURAS_EQ = 8                   # EQ común, en cada satélite
RANURAS_EQ_SUB = 4
TASA = 48000                     # el grafo solo admite 48 kHz (CLAUDE.md)
NEUTRO = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
COEFICIENTES = ("b0", "b1", "b2", "a0", "a1", "a2")

RANGO_GANANCIA = (-24.0, 24.0)
RANGO_NIVEL = (-60.0, 24.0)
RANGO_RETARDO_MS = (0.0, MAX_RETARDO * 1000)
RANGO_DISTANCIA = (0.0, 20.0)
RANGO_FRECUENCIA = (20.0, 20000.0)
RANGO_Q = (0.1, 10.0)
RANGO_CRUCE = (40.0, 200.0)
RANGO_TOPE = (0.0, 10.57)        # el bus no pasa de 150 % (+10.57 dB)
RANGO_ESTEREO = (-24.0, 0.0)


# ---------- El modelo ----------

@dataclass(frozen=True)
class Banda:
    tipo: str                    # pico, estante_grave o estante_agudo
    frecuencia: float
    ganancia: float
    q: float


@dataclass(frozen=True)
class Bocina:
    ganancia: float = 0.0
    retardo_ms: float | None = None
    distancia: float | None = None   # m al punto de escucha
    cruce: float | None = None       # Hz; gana sobre [cruce]. 0 = rango completo (solo satélites)


@dataclass(frozen=True)
class Calibracion:
    nivel: float = 0.0
    evitar_saturacion: bool = True
    bocinas: dict[str, Bocina] = field(default_factory=lambda: {b: Bocina() for b in BOCINAS})
    lfe: float = 0.0
    cruce: float | None = None
    eq: tuple[Banda, ...] = ()
    eq_sub: tuple[Banda, ...] = ()
    tope: float | None = None
    perilla: str = ""
    estereo: float | None = None             # dB que baja un bus cuando suena estéreo (None = apagado)
    estereo_buses: tuple[str, ...] = ()      # claves: musica, juegos o sistema
    estereo_por_bus: dict[str, float] = field(default_factory=dict)   # la propia de un bus (gana sobre `estereo`)

    def estereo_de(self, clave: str) -> float | None:
        """Cuánto baja un bus mientras suena estéreo (dB), o None si no se compensa."""
        if self.estereo is None or clave not in self.estereo_buses:
            return None
        return self.estereo_por_bus.get(clave, self.estereo)

    def cruce_de(self, bocina: str) -> float | None:
        """La frecuencia de cruce de una satélite, o None si va en rango completo."""
        propio = self.bocinas[bocina].cruce
        f = self.cruce if propio is None else propio
        return f or None


# ---------- Leer y validar ----------

def _numero(ruta: str, valor, rango: tuple[float, float]) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErrorElena(f"{ruta}: debe ser un número (es {valor!r}).")
    v = float(valor)
    if not (rango[0] <= v <= rango[1]) or math.isnan(v):
        raise ErrorElena(f"{ruta}: {v:g} fuera de rango ({rango[0]:g} a {rango[1]:g}).")
    return v


def _sobran(ruta: str, tabla: dict, permitidas: set[str]) -> None:
    if not isinstance(tabla, dict):
        raise ErrorElena(f"{ruta}: debe ser una tabla.")
    if sobran := set(tabla) - permitidas:
        raise ErrorElena(f"{ruta}: no conozco {', '.join(sorted(sobran))} (van: {', '.join(sorted(permitidas))}).")


def _bandas(ruta: str, tabla) -> tuple[Banda, ...]:
    if tabla is None:
        return ()
    _sobran(ruta, tabla, {"bandas"})
    bandas = tabla.get("bandas", [])
    if not isinstance(bandas, list):
        raise ErrorElena(f"{ruta}.bandas: debe ser una lista.")
    maximo = RANURAS_EQ_SUB if "eq_sub" in ruta else RANURAS_EQ
    if len(bandas) > maximo:
        raise ErrorElena(f"{ruta}: {len(bandas)} bandas; caben {maximo}.")
    salida = []
    for i, b in enumerate(bandas, start=1):
        r = f"{ruta}.bandas[{i}]"
        _sobran(r, b, {"tipo", "frecuencia", "ganancia", "q"})
        tipo = b.get("tipo", "pico")
        if tipo not in TIPOS:
            raise ErrorElena(f"{r}.tipo: «{tipo}» no existe (son {', '.join(TIPOS)}).")
        if "frecuencia" not in b or "ganancia" not in b:
            raise ErrorElena(f"{r}: falta la frecuencia o la ganancia.")
        salida.append(Banda(tipo, _numero(f"{r}.frecuencia", b["frecuencia"], RANGO_FRECUENCIA),
                            _numero(f"{r}.ganancia", b["ganancia"], RANGO_GANANCIA),
                            _numero(f"{r}.q", b.get("q", Q_POR_TIPO[tipo]), RANGO_Q)))
    return tuple(salida)


def desde_datos(datos: dict, ruta: str = "calibracion.toml") -> Calibracion:
    _sobran(ruta, datos, {"evitar_saturacion", "nivel", "bocinas", "lfe", "cruce", "eq", "eq_sub", "maximo", "estereo"})
    evitar = datos.get("evitar_saturacion", True)
    if not isinstance(evitar, bool):
        raise ErrorElena(f"{ruta}: evitar_saturacion es true o false.")
    nivel = _numero(f"{ruta}: nivel", datos.get("nivel", 0.0), RANGO_NIVEL)

    tabla = datos.get("bocinas", {})
    _sobran(f"{ruta}: [bocinas]", tabla, set(BOCINAS))
    bocinas = {}
    for b in BOCINAS:
        r = f"{ruta}: bocinas.{b}"
        d = tabla.get(b, {})
        _sobran(r, d, {"ganancia", "retardo", "distancia", "cruce"} if b != "sub" else {"ganancia", "retardo", "distancia"})
        if "retardo" in d and "distancia" in d:
            raise ErrorElena(f"{r}: lleva retardo (ms) o distancia (m), no los dos.")
        cruce = d.get("cruce")
        if cruce is not None and cruce != 0:
            cruce = _numero(f"{r}.cruce", cruce, RANGO_CRUCE)
        bocinas[b] = Bocina(
            _numero(f"{r}.ganancia", d.get("ganancia", 0.0), RANGO_GANANCIA),
            _numero(f"{r}.retardo", d["retardo"], RANGO_RETARDO_MS) if "retardo" in d else None,
            _numero(f"{r}.distancia", d["distancia"], RANGO_DISTANCIA) if "distancia" in d else None,
            None if cruce is None else float(cruce))

    lfe = datos.get("lfe", {})
    _sobran(f"{ruta}: [lfe]", lfe, {"nivel"})
    cruce = datos.get("cruce")
    frecuencia = None
    if cruce is not None:
        _sobran(f"{ruta}: [cruce]", cruce, {"frecuencia"})
        if "frecuencia" not in cruce:
            raise ErrorElena(f"{ruta}: [cruce] necesita frecuencia (o quita la sección: rango completo).")
        frecuencia = _numero(f"{ruta}: cruce.frecuencia", cruce["frecuencia"], RANGO_CRUCE)
    maximo = datos.get("maximo", {})
    _sobran(f"{ruta}: [maximo]", maximo, {"tope", "perilla"})
    tope = maximo.get("tope")
    perilla = maximo.get("perilla", "")
    if not isinstance(perilla, str):
        raise ErrorElena(f"{ruta}: maximo.perilla es un texto.")
    estereo = datos.get("estereo")
    compensacion, buses_estereo, por_bus = None, (), {}
    if estereo is not None:
        _sobran(f"{ruta}: [estereo]", estereo, {"compensacion", "buses", *CLAVES_BOCINAS})
        compensacion = _numero(f"{ruta}: estereo.compensacion", estereo.get("compensacion", -12.0), RANGO_ESTEREO)
        buses_estereo = estereo.get("buses", ["musica", "sistema"])
        if not isinstance(buses_estereo, list) or not set(buses_estereo) <= set(CLAVES_BOCINAS):
            raise ErrorElena(f"{ruta}: estereo.buses es una lista de {', '.join(CLAVES_BOCINAS)}.")
        buses_estereo = tuple(buses_estereo)
        for clave in CLAVES_BOCINAS:          # la propia de un bus: `sistema = -10.0`
            if clave in estereo:
                if clave not in buses_estereo:
                    raise ErrorElena(f"{ruta}: estereo.{clave} sin {clave} en estereo.buses.")
                por_bus[clave] = _numero(f"{ruta}: estereo.{clave}", estereo[clave], RANGO_ESTEREO)
    cal = Calibracion(
        nivel=nivel, evitar_saturacion=evitar, bocinas=bocinas,
        lfe=_numero(f"{ruta}: lfe.nivel", lfe.get("nivel", 0.0), RANGO_GANANCIA),
        cruce=frecuencia, eq=_bandas(f"{ruta}: [eq]", datos.get("eq")),
        eq_sub=_bandas(f"{ruta}: [eq_sub]", datos.get("eq_sub")),
        tope=None if tope is None else _numero(f"{ruta}: maximo.tope", tope, RANGO_TOPE), perilla=perilla,
        estereo=compensacion, estereo_buses=buses_estereo, estereo_por_bus=por_bus)
    retardos(cal)   # valida que las distancias no pidan más de 50 ms
    return cal


def leer(ruta: Path = TOML) -> Calibracion:
    if not ruta.exists():
        raise ErrorElena(f"No existe {ruta}.")
    try:
        datos = tomllib.loads(ruta.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ErrorElena(f"{ruta}: no es TOML válido ({e}).") from e
    return desde_datos(datos, ruta.name)


# ---------- Valores ----------

def retardos(cal: Calibracion) -> dict[str, float]:
    """Retardo de cada bocina en segundos. Con distancias: (la mayor − la suya) / 343 m/s."""
    distancias = [b.distancia for b in cal.bocinas.values() if b.distancia is not None]
    lejos = max(distancias, default=0.0)
    salida = {}
    for nombre, b in cal.bocinas.items():
        if b.distancia is not None:
            s = (lejos - b.distancia) / VELOCIDAD_SONIDO
        else:
            s = (b.retardo_ms or 0.0) / 1000
        if s > MAX_RETARDO + 1e-12:
            raise ErrorElena(f"bocinas.{nombre}: pide {s * 1000:.1f} ms de retardo (el máximo es "
                             f"{MAX_RETARDO * 1000:.0f} ms).")
        salida[nombre] = s
    return salida


def _subida_eq(bandas: tuple[Banda, ...]) -> float:
    """Lo más que puede subir un EQ: la suma de sus bandas positivas (conservador)."""
    return sum(max(b.ganancia, 0.0) for b in bandas)


def caminos(cal: Calibracion) -> dict[str, float]:
    """La subida máxima de cada camino hasta una bocina, en dB y antes del preamp."""
    eq, eq_sub, sub = _subida_eq(cal.eq), _subida_eq(cal.eq_sub), cal.bocinas["sub"].ganancia
    salida = {b: cal.nivel + cal.bocinas[b].ganancia + eq for b in SATELITES}
    salida["lfe"] = cal.nivel + cal.lfe + sub + eq_sub
    if any(cal.cruce_de(b) for b in SATELITES):
        salida["graves→sub"] = cal.nivel + eq + sub + eq_sub
    return salida


def preamp(cal: Calibracion) -> float:
    """Lo que baja todo para que ningún camino pase de 0 dB (0 si no hace falta o está apagado)."""
    if not cal.evitar_saturacion:
        return 0.0
    return min(0.0, -max(caminos(cal).values())) + 0.0   # + 0.0: sin «−0.0»


def ganancias(cal: Calibracion) -> dict[str, float]:
    """La ganancia de cada nodo `<bocina>_ganancia`, en dB: nivel + su ganancia + preamp."""
    p = preamp(cal)
    return {b: cal.nivel + cal.bocinas[b].ganancia + p for b in BOCINAS}


def a_lineal(db: float) -> float:
    return 10 ** (db / 20)


def controles(cal: Calibracion) -> dict[str, float]:
    """Cada control del filter-chain con su valor: lo que `pw-cli set-param` cambia en vivo."""
    c: dict[str, float] = {}
    r = retardos(cal)
    for b, db in ganancias(cal).items():
        c[f"{b}_ganancia:Mult"] = a_lineal(db)
        c[f"{b}_retardo:Delay (s)"] = r[b]
    c["lfe_nivel:Mult"] = a_lineal(cal.lfe)
    for entrada in ENTRADA_SUMA.values():
        c[f"sub_suma:Gain {entrada}"] = 1.0    # en 0 apaga en vivo la parte de una satélite (o el LFE)
    for b in SATELITES:
        if f := cal.cruce_de(b):
            for n in ("altos1", "altos2", "bajos1", "bajos2"):
                c[f"{b}_{n}:Freq"] = f
        c.update(_controles_eq(b, cal.eq, RANURAS_EQ))
    c.update(_controles_eq("sub", cal.eq_sub, RANURAS_EQ_SUB))
    return c


def coeficientes(b: Banda | None, tasa: int = TASA) -> tuple[float, ...]:
    """b0 b1 b2 a0 a1 a2 de una banda (RBJ, «Audio EQ Cookbook»), normalizados (a0 = 1). None: neutro."""
    if b is None:
        return NEUTRO
    a = 10 ** (b.ganancia / 40)
    w = 2 * math.pi * b.frecuencia / tasa
    cos, alfa = math.cos(w), math.sin(w) / (2 * b.q)
    if b.tipo == "pico":
        c = (1 + alfa * a, -2 * cos, 1 - alfa * a, 1 + alfa / a, -2 * cos, 1 - alfa / a)
    else:
        r = 2 * math.sqrt(a) * alfa
        if b.tipo == "estante_grave":
            c = (a * ((a + 1) - (a - 1) * cos + r), 2 * a * ((a - 1) - (a + 1) * cos), a * ((a + 1) - (a - 1) * cos - r),
                 (a + 1) + (a - 1) * cos + r, -2 * ((a - 1) + (a + 1) * cos), (a + 1) + (a - 1) * cos - r)
        else:
            c = (a * ((a + 1) + (a - 1) * cos + r), -2 * a * ((a - 1) + (a + 1) * cos), a * ((a + 1) + (a - 1) * cos - r),
                 (a + 1) - (a - 1) * cos + r, 2 * ((a - 1) - (a + 1) * cos), (a + 1) - (a - 1) * cos - r)
    return tuple(x / c[3] for x in c)


def respuesta_db(bandas: tuple[Banda, ...], f: float, tasa: int = TASA) -> float:
    """La ganancia de una cadena de bandas en `f` (dB), con los mismos coeficientes que suenan."""
    z = complex(math.cos(2 * math.pi * f / tasa), -math.sin(2 * math.pi * f / tasa))   # e^{-jω}
    h = 1 + 0j
    for b in bandas:
        b0, b1, b2, a0, a1, a2 = coeficientes(b, tasa)
        h *= (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
    return 20 * math.log10(abs(h))


def _controles_eq(prefijo: str, bandas: tuple[Banda, ...], ranuras: int) -> dict[str, float]:
    c = {}
    for i in range(1, ranuras + 1):
        valores = coeficientes(bandas[i - 1] if i <= len(bandas) else None)
        c.update({f"{prefijo}_eq{i}:{k}": v for k, v in zip(COEFICIENTES, valores)})
    return c


def avisos(cal: Calibracion) -> list[str]:
    salida = []
    cam = caminos(cal)
    alto = max(cam, key=cam.get)
    if cam[alto] > 0 and cal.evitar_saturacion:
        salida.append(f"Evitar saturación: el camino {alto} subía {cam[alto]:+.1f} dB; todo baja {-preamp(cal):.1f} dB para que "
                      "nada pase de 0 dB.")
    elif cam[alto] > 0:
        salida.append(f"evitar_saturacion = false: el camino {alto} sube {cam[alto]:+.1f} dB y puede saturar el DAC.")
    if cal.cruce is None and not any(cal.cruce_de(b) for b in SATELITES):
        salida.append("Sin cruce: las satélites van en rango completo y el sub solo recibe el LFE.")
    return salida


# ---------- El .conf ----------

def _num(v: float) -> str:
    return f"{v:.9g}"


@dataclass
class _Grafo:
    nodos: list[str] = field(default_factory=list)
    enlaces: list[str] = field(default_factory=list)

    def nodo(self, nombre: str, label: str, control: dict[str, float] | None = None,
             config: dict[str, float] | str | None = None) -> None:
        n = f"{{ type = builtin name = {nombre} label = {label}"
        if control:
            n += " control = { " + " ".join(f'"{k}" = {_num(v)}' for k, v in control.items()) + " }"
        if isinstance(config, str):
            n += f" config = {{ {config} }}"
        elif config:
            n += " config = { " + " ".join(f'"{k}" = {_num(v)}' for k, v in config.items()) + " }"
        self.nodos.append(n + " }")

    def enlace(self, salida: str, entrada: str) -> None:
        self.enlaces.append(f'{{ output = "{salida}" input = "{entrada}" }}')


def grafo(cal: Calibracion) -> tuple[_Grafo, list[str], list[str]]:
    """Los nodos y enlaces del filter-chain, y sus entradas y salidas en el orden de POSICIONES."""
    g = _Grafo()
    c = controles(cal)

    def cadena_eq(prefijo: str, ranuras: int, entrada: str) -> str:
        """Las ranuras del EQ en serie después de `entrada` (un puerto Out); devuelve la última salida.
        Cada una lleva sus coeficientes en `config` (sin ellos, bq_raw deja pasar silencio)."""
        for i in range(1, ranuras + 1):
            n = f"{prefijo}_eq{i}"
            valores = " ".join(f"{k} = {_num(c[f'{n}:{k}'])}" for k in COEFICIENTES)
            g.nodo(n, "bq_raw", config=f"coefficients = [ {{ rate = {TASA} {valores} }} ]")
            g.enlace(entrada, f"{n}:In")
            entrada = f"{n}:Out"
        return entrada

    def final(b: str, entrada: str) -> None:
        g.nodo(f"{b}_ganancia", "linear", {"Mult": c[f"{b}_ganancia:Mult"]})
        g.nodo(f"{b}_retardo", "delay", {"Delay (s)": c[f"{b}_retardo:Delay (s)"]}, {"max-delay": MAX_RETARDO})
        g.enlace(entrada, f"{b}_ganancia:In")
        g.enlace(f"{b}_ganancia:Out", f"{b}_retardo:In")

    g.nodo("lfe_nivel", "linear", {"Mult": c["lfe_nivel:Mult"]})
    g.nodo("sub_suma", "mixer", {k.split(":")[1]: v for k, v in c.items() if k.startswith("sub_suma:")})
    g.enlace("lfe_nivel:Out", f"sub_suma:In {ENTRADA_SUMA['lfe']}")
    for b in SATELITES:
        g.nodo(f"{b}_entrada", "copy")
        punto = cadena_eq(b, RANURAS_EQ, f"{b}_entrada:Out")
        if f := cal.cruce_de(b):
            for n, label in (("altos1", "bq_highpass"), ("altos2", "bq_highpass"),
                             ("bajos1", "bq_lowpass"), ("bajos2", "bq_lowpass")):
                g.nodo(f"{b}_{n}", label, {"Freq": f, "Q": Q_CRUCE})
            g.enlace(punto, f"{b}_altos1:In")
            g.enlace(f"{b}_altos1:Out", f"{b}_altos2:In")
            g.enlace(punto, f"{b}_bajos1:In")
            g.enlace(f"{b}_bajos1:Out", f"{b}_bajos2:In")
            g.enlace(f"{b}_bajos2:Out", f"sub_suma:In {ENTRADA_SUMA[b]}")
            punto = f"{b}_altos2:Out"
        final(b, punto)
    final("sub", cadena_eq("sub", RANURAS_EQ_SUB, "sub_suma:Out"))

    entrada_de = {**{b: f"{b}_entrada:In" for b in SATELITES}, "sub": "lfe_nivel:In"}
    entradas = [entrada_de[BOCINA_DE[p]] for p in POSICIONES]
    salidas = [f"{BOCINA_DE[p]}_retardo:Out" for p in POSICIONES]
    return g, entradas, salidas


def texto_estereo(cal: Calibracion) -> str:
    """«−15 dB» si todos los buses bajan lo mismo; si no, «Música −15 · Sistema −10 dB»."""
    from elena.modelo import BUSES
    valores = {b.etiqueta: cal.estereo_de(b.clave) for b in BUSES if b.clave in cal.estereo_buses}
    if len(set(valores.values())) <= 1:
        return f"{cal.estereo:+.0f} dB" if not valores else f"{next(iter(valores.values())):+.0f} dB"
    return " · ".join(f"{e} {v:+.0f}" for e, v in valores.items()) + " dB"


def resumen(cal: Calibracion) -> list[str]:
    g, r = ganancias(cal), retardos(cal)
    lineas = [f"nivel {cal.nivel:+.1f} dB · preamp {preamp(cal):+.1f} dB · LFE {cal.lfe:+.1f} dB · "
              f"cruce {f'{cal.cruce:g} Hz' if cal.cruce else 'no'}"]
    for b in BOCINAS:
        extra = f" · retardo {r[b] * 1000:.2f} ms" if r[b] else ""
        if b != "sub" and cal.bocinas[b].cruce is not None:
            extra += f" · cruce {cal.cruce_de(b) or 'rango completo'}"
        lineas.append(f"  {b:<3} {cal.bocinas[b].ganancia:+5.1f} dB → {g[b]:+6.1f} dB{extra}")
    lineas.append(f"EQ común: {len(cal.eq)} bandas · EQ del sub: {len(cal.eq_sub)} bandas")
    return lineas


def conf(cal: Calibracion, destino: str = SINK_FISICO, nombre: str = NODO, cliente: bool = False) -> str:
    """El `.conf`. `cliente=True` lo envuelve para correrlo aparte con `pipewire -c` (pruebas en caliente,
    con otro `nombre` y un sink nulo de `destino`)."""
    g, entradas, salidas = grafo(cal)
    q = " ".join
    sangria = "\n" + " " * 10
    modulo = f"""  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "ELENA · Calibración"
      media.name       = "ELENA · Calibración"
      filter.graph = {{
        nodes = [{sangria}{sangria.join(g.nodos)}
        ]
        links = [{sangria}{sangria.join(g.enlaces)}
        ]
        inputs  = [ {q(f'"{e}"' for e in entradas)} ]
        outputs = [ {q(f'"{s}"' for s in salidas)} ]
      }}
      capture.props = {{
        node.name        = "{nombre}"
        media.class      = "Audio/Sink"
        audio.position   = [ {q(POSICIONES)} ]
        priority.session = 1
      }}
      playback.props = {{
        node.name          = "{nombre}.salida"
        audio.position     = [ {q(POSICIONES)} ]
        target.object      = "{destino}"
        stream.dont-remix  = true
        node.passive       = true
        node.dont-fallback = true
        node.linger        = true
      }}
    }}
  }}"""
    encabezado = "\n".join([
        "# ELENA — calibración de las bocinas: elena_salida → físico.",
        "#",
        "# GENERADO por `elena calibracion generar` desde local/calibracion.toml: no se edita a mano.",
        "#",
        *(f"# {l}" for l in resumen(cal)),
        "#",
        "# flags = [ nofail ]: si el filter-chain no carga, PipeWire arranca igual (las bocinas esperan a",
        "# elena_salida en silencio, sin caer a otro sink).",
    ])
    if not cliente:
        return f"{encabezado}\n\ncontext.modules = [\n{modulo}\n]\n"
    return f"""{encabezado}
# Versión de prueba: se corre aparte con `pipewire -c <ruta absoluta>`.

context.properties = {{ log.level = 0 }}
context.spa-libs = {{
  audio.convert.* = audioconvert/libspa-audioconvert
  support.*       = support/libspa-support
}}
context.modules = [
  {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
{modulo}
]
"""


def validar(ruta: Path, modulo: str = "libpipewire-module-filter-chain") -> None:
    """Que PipeWire lea el archivo (sin reiniciar nada) y traiga `modulo`."""
    r = subprocess.run(["pw-config", "-n", str(ruta.resolve()), "-r", "list", "context.modules"],
                       capture_output=True, text=True)
    if r.returncode != 0 or modulo not in r.stdout:
        raise ErrorElena(f"PipeWire no lee {ruta}: {(r.stderr or r.stdout).strip()[:300]}")


def generar(toml: Path = TOML, salida: Path = CONF, destino: str = SINK_FISICO, nombre: str = NODO,
            cliente: bool = False) -> tuple[Calibracion, list[str]]:
    """Lee el TOML, escribe el .conf (primero a un temporal) y lo valida. El de antes solo se reemplaza
    si el nuevo se lee bien."""
    cal = leer(toml)
    texto = conf(cal, destino, nombre, cliente)
    salida.parent.mkdir(parents=True, exist_ok=True)
    # El temporal va fuera de pipewire.conf.d (PipeWire carga todo *.conf de ahí) y debe terminar en .conf
    # (pw-config lo exige)
    carpeta = Path(os.environ.get("TMPDIR") or ELENA / ".cache")
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / f"{salida.stem}.nuevo.conf"
    temporal.write_text(texto)
    try:
        validar(temporal)
    except ErrorElena:
        temporal.unlink(missing_ok=True)
        raise
    salida.write_text(temporal.read_text())
    temporal.unlink()
    return cal, avisos(cal)


# ---------- Integración: activar, desactivar y los buses ----------
#
# La calibración se activa con dos archivos que PipeWire lee al reiniciarse: este .conf y el destino
# de los tres buses de bocinas en 10-elena-buses.conf (los dos en local/). Que exista este .conf es lo
# que dice que está activa; `elena equipo generar` lo usa para elegir el destino de los buses.
# `activar` y `desactivar` los escriben juntos (nunca uno sin el otro: los buses esperarían a
# elena_salida en silencio) y no reinician nada: eso lo hace `mise run aplicar`, con la VM apagada.

BUSES_CONF = rutas.CONF_PIPEWIRE / "10-elena-buses.conf"
# node.name de Música, Juegos y Sistema (el chat va a los audífonos)
BUSES_BOCINAS = tuple(b.nodo for b in BUSES if b.clave in CLAVES_BOCINAS)
RESPALDO_BUSES = ELENA / ".cache" / "calibracion" / "buses-antes.json"


def activa(conf_ruta: Path | None = None) -> bool:
    return (conf_ruta or CONF).exists()


def destino_de_buses(texto: str) -> set[str]:
    """Los target.object de los buses de bocinas (para saber si apuntan a elena_salida)."""
    destinos = set()
    for bloque in texto.split("{ name = libpipewire-module-loopback")[1:]:
        if any(f'node.name   = "{b}"' in bloque for b in BUSES_BOCINAS):
            if m := re.search(r'target\.object\s*=\s*"([^"]*)"', bloque):
                destinos.add(m.group(1))
    return destinos


def activar(toml: Path = TOML, conf_ruta: Path = CONF) -> tuple[Calibracion, list[str]]:
    """Escribe el .conf de la calibración y manda los buses de bocinas a elena_salida. No reinicia."""
    from elena import equipo
    eq = equipo.leer()
    cal, avisos_ = generar(toml, conf_ruta, destino=eq.sink)
    try:
        equipo.generar(eq, destino_bocinas=NODO)
    except ErrorElena:
        conf_ruta.unlink(missing_ok=True)   # nunca el .conf sin los buses (ni al revés)
        raise
    return cal, avisos_


def desactivar(conf_ruta: Path = CONF) -> None:
    """Los buses de bocinas regresan al físico y se borra el .conf. No reinicia."""
    from elena import equipo
    eq = equipo.leer()
    equipo.generar(eq, destino_bocinas=eq.sink)
    conf_ruta.unlink(missing_ok=True)


def vm_corriendo() -> bool:
    """Si QEMU corre (la VM de Windows): reiniciar PipeWire puede dejarla sin audio."""
    return subprocess.run(["pgrep", "-x", "qemu-system-x86_64"], capture_output=True).returncode == 0


@dataclass(frozen=True)
class AjusteBus:
    nodo: str
    antes: tuple[float, ...]
    despues: tuple[float, ...]
    texto: str


def ajustes_buses(volumenes: dict[str, tuple[tuple[str, ...], tuple[float, ...]]], nivel: float) -> list[AjusteBus]:
    """Los buses de bocinas que todavía traen el balance por canal (de antes de la calibración) pasan a
    un solo volumen. Se conserva lo que sonaba en FL: el bus nuevo = FL de antes − `nivel` (la
    calibración ya baja `nivel`), sin pasar de 100 %. `volumenes`: nodo → (posiciones, lineales)."""
    from elena.modelo import lineal_a_db, lineal_a_pct
    salida = []
    for nodo in BUSES_BOCINAS:
        if nodo not in volumenes:
            continue
        posiciones, vols = volumenes[nodo]
        if len(set(round(v, 6) for v in vols)) <= 1 or "FL" not in posiciones:
            continue   # ya está en un solo volumen
        fl = lineal_a_db(vols[posiciones.index("FL")])
        db = min(fl - nivel, 0.0)
        nuevo = tuple(10 ** (db / 20) for _ in vols)
        texto = (f"{nodo}: {lineal_a_pct(max(vols)):.0f} % con balance por canal → {lineal_a_pct(nuevo[0]):.0f} % "
                 f"(FL suena igual{'' if fl - nivel <= 0 else f', {fl - nivel:.1f} dB más bajo por el tope de 100 %'})")
        salida.append(AjusteBus(nodo, vols, nuevo, texto))
    return salida


def ajustar_tras_aplicar(grafo) -> list[str]:
    """Lo que corre `aplicar` después de reiniciar PipeWire. Con la calibración activa: elena_salida al
    100 % y los buses de bocinas en un solo volumen (con respaldo del balance de antes). Sin ella:
    si hay respaldo, los buses regresan a su balance de antes (si no, irían al físico sin calibrar
    y en un solo volumen: FL sonaría ~21 dB más fuerte)."""
    import json

    from elena.acciones import cmd_silencio, cmd_volumen, ejecutar
    hechos = []
    vols = {n: (nodo.posiciones, nodo.volumenes) for n in BUSES_BOCINAS
            if (nodo := grafo.nodo(n)) and len(nodo.volumenes) == len(nodo.posiciones)}
    if activa():
        salida = grafo.nodo(NODO)
        if salida is None:
            raise ErrorElena(f"La calibración está activa pero no apareció {NODO}: las bocinas no suenan. Revisa "
                             f"{CONF.name} (journalctl --user -u pipewire) o desactívala: mise run calibracion -- "
                             "desactivar && mise run aplicar")
        ejecutar(cmd_volumen(salida, tuple(1.0 for _ in salida.volumenes)))
        ejecutar(cmd_silencio(salida, False))
        hechos.append(f"{NODO} (ELENA · Calibración) en 100 %.")
        ajustes = ajustes_buses(vols, leer().nivel)
        if ajustes and not RESPALDO_BUSES.exists():
            RESPALDO_BUSES.parent.mkdir(parents=True, exist_ok=True)
            RESPALDO_BUSES.write_text(json.dumps({a.nodo: list(a.antes) for a in ajustes}, indent=2) + "\n")
        for a in ajustes:
            ejecutar(cmd_volumen(grafo.nodo(a.nodo), a.despues))
            hechos.append(a.texto)
    elif RESPALDO_BUSES.exists():
        for nodo, antes in json.loads(RESPALDO_BUSES.read_text()).items():
            if (n := grafo.nodo(nodo)) and len(n.volumenes) == len(antes):
                ejecutar(cmd_volumen(n, tuple(antes)))
                hechos.append(f"{nodo}: regresa a su balance de antes de la calibración.")
        RESPALDO_BUSES.unlink()
    return hechos
