"""Afinar la calibración en vivo, sin reiniciar PipeWire.

Cada cambio se calcula como una calibración nueva; lo que difiere de la que suena se manda con
`pw-cli set-param` a `elena_salida` (verificado: sin cortes). Si el cambio mueve el
preamp de `evitar_saturacion`, también se ajustan las demás ganancias, porque salen de los controles completos.

- **Cambios sueltos:** `elena calibracion sub ganancia 7`, `nivel -21`, `lfe 10`, `cruce 120`…
  Se aplican en vivo, se guardan en `local/calibracion.toml` y se regenera el `.conf`, así que
  sobreviven a reiniciar PipeWire.
- **`elena calibracion afinar` (o `ab`):** interactiva, mientras suena la música. Se elige qué
  ajustar, + y − lo mueven, `a` alterna con **neutro** (sin balance, sin cruce y sin EQ, con el
  mismo `nivel`: suena como antes de calibrar, al mismo volumen en FL), `g` guarda y `q` sale
  regresando a lo guardado si no se guardó.

No es en vivo (pide `generar` y `mise run aplicar`): agregar o quitar bandas del EQ, o encender o
apagar el cruce de una bocina. El TOML se edita línea por línea y conserva los comentarios.
Guía: docs/guias/calibracion.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from elena import calibracion as cb
from elena.modelo import ErrorElena

BOCINAS = cb.BOCINAS
APAGADO_HZ = 5.0       # el pasa altos «apagado» en vivo (a 40 Hz deja pasar −0.35 dB)


# ---------- Calibraciones nuevas (sin E/S) ----------

def con_bocina(cal: cb.Calibracion, bocina: str, campo: str, valor: float) -> cb.Calibracion:
    """`campo`: ganancia (dB), retardo (ms) o distancia (m). retardo y distancia se excluyen."""
    if bocina not in BOCINAS:
        raise ErrorElena(f"Bocina desconocida: {bocina}. Son {' '.join(BOCINAS)}.")
    b = cal.bocinas[bocina]
    if campo == "ganancia":
        b = replace(b, ganancia=valor)
    elif campo == "retardo":
        b = replace(b, retardo_ms=valor, distancia=None)
    elif campo == "distancia":
        b = replace(b, distancia=valor, retardo_ms=None)
    else:
        raise ErrorElena(f"Campo desconocido: {campo} (ganancia, retardo o distancia).")
    return validar(replace(cal, bocinas={**cal.bocinas, bocina: b}))


def validar(cal: cb.Calibracion) -> cb.Calibracion:
    """La misma validación que al leer el TOML."""
    return cb.desde_datos(a_datos(cal))


def a_datos(cal: cb.Calibracion) -> dict:
    """La calibración como el dict del TOML (para validarla igual que al leer)."""
    def bocina(nombre: str, b: cb.Bocina) -> dict:
        d: dict = {"ganancia": b.ganancia}
        if b.retardo_ms is not None:
            d["retardo"] = b.retardo_ms
        if b.distancia is not None:
            d["distancia"] = b.distancia
        if b.cruce is not None and nombre != "sub":
            d["cruce"] = b.cruce
        return d

    def bandas(bs: tuple[cb.Banda, ...]) -> dict:
        return {"bandas": [{"tipo": b.tipo, "frecuencia": b.frecuencia, "ganancia": b.ganancia, "q": b.q} for b in bs]}

    datos = {"evitar_saturacion": cal.evitar_saturacion, "nivel": cal.nivel,
             "bocinas": {n: bocina(n, b) for n, b in cal.bocinas.items()}, "lfe": {"nivel": cal.lfe},
             "eq": bandas(cal.eq), "eq_sub": bandas(cal.eq_sub),
             "maximo": {"perilla": cal.perilla, **({"tope": cal.tope} if cal.tope is not None else {})}}
    if cal.cruce is not None:
        datos["cruce"] = {"frecuencia": cal.cruce}
    if cal.estereo is not None:
        datos["estereo"] = {"compensacion": cal.estereo, "buses": list(cal.estereo_buses), **cal.estereo_por_bus}
    return datos


def estructura(cal: cb.Calibracion) -> tuple:
    """Lo que define los nodos del grafo: si cambia, no se puede en vivo. El EQ va en ranuras fijas
    (`bq_raw`), así que sus bandas siempre son en vivo; solo el cruce de una bocina no lo es."""
    return tuple(bool(cal.cruce_de(b)) for b in cb.SATELITES)


def neutro(cal: cb.Calibracion) -> dict[str, float]:
    """Los controles «sin calibrar»: mismo `nivel` (FL igual), sin balance, retardo ni EQ, el LFE con su
    nivel y el cruce apagado (pasa altos a 5 Hz y los graves de las satélites fuera de la suma)."""
    plano = replace(cal, bocinas={n: replace(b, ganancia=0.0, retardo_ms=None, distancia=None)
                                  for n, b in cal.bocinas.items()},
                    eq=tuple(replace(b, ganancia=0.0) for b in cal.eq),
                    eq_sub=tuple(replace(b, ganancia=0.0) for b in cal.eq_sub))
    c = cb.controles(plano)
    for b in cb.SATELITES:
        if cal.cruce_de(b):
            for n in ("altos1", "altos2"):
                c[f"{b}_{n}:Freq"] = APAGADO_HZ
            c[f"sub_suma:Gain {cb.ENTRADA_SUMA[b]}"] = 0.0
    return c


def diferencias(antes: dict[str, float], despues: dict[str, float]) -> dict[str, float]:
    return {k: v for k, v in despues.items() if abs(antes.get(k, float("nan")) - v) > 1e-9 or k not in antes}


# ---------- El TOML, línea por línea (conserva los comentarios) ----------

def _num(v: float, entero: bool = False) -> str:
    if entero and float(v).is_integer():
        return str(int(v))
    return repr(round(float(v), 3))


def _seccion(lineas: list[str], nombre: str | None) -> tuple[int, int]:
    """[inicio, fin) de las líneas de una sección (None = antes de la primera)."""
    inicio = 0
    if nombre is not None:
        inicio = next((i + 1 for i, l in enumerate(lineas) if re.match(rf"^\[{re.escape(nombre)}\]", l)), None)
        if inicio is None:
            raise ErrorElena(f"calibracion.toml no tiene [{nombre}]: edítalo a mano.")
    fin = next((i for i in range(inicio, len(lineas)) if re.match(r"^\[", lineas[i])), len(lineas))
    return inicio, fin


def _poner(lineas: list[str], seccion: str | None, clave: str, valor: str) -> None:
    inicio, fin = _seccion(lineas, seccion)
    for i in range(inicio, fin):
        if m := re.match(rf"^(\s*{re.escape(clave)}\s*=\s*)([^#\n]*?)(\s*#.*)?$", lineas[i]):
            antes = m.group(2)
            comentario = m.group(3) or ""
            # Conserva la columna del comentario si cabe
            relleno = max(len(antes) - len(valor), 0) if comentario else 0
            lineas[i] = f"{m.group(1)}{valor}{' ' * relleno}{comentario}"
            return
    raise ErrorElena(f"calibracion.toml no tiene {clave} en {f'[{seccion}]' if seccion else 'el inicio'}: edítalo a mano.")


def _poner_o_agregar(lineas: list[str], seccion: str, clave: str, valor: str) -> None:
    """Como `_poner`, pero si la clave no está, la agrega al final de la sección (antes de los blancos)."""
    try:
        _poner(lineas, seccion, clave, valor)
    except ErrorElena:
        inicio, fin = _seccion(lineas, seccion)
        while fin > inicio and not lineas[fin - 1].strip():
            fin -= 1
        lineas.insert(fin, f"{clave} = {valor}")


def _texto_banda(b: cb.Banda) -> str:
    return (f'{{ tipo = "{b.tipo}", frecuencia = {_num(b.frecuencia, entero=True)}, '
            f"ganancia = {_num(b.ganancia)}, q = {_num(b.q)} }}")


def _poner_bandas(lineas: list[str], seccion: str, bandas: tuple[cb.Banda, ...]) -> None:
    """Reescribe `bandas = [...]` de una sección (de una o varias líneas)."""
    inicio, fin = _seccion(lineas, seccion)
    i = next((i for i in range(inicio, fin) if re.match(r"^\s*bandas\s*=", lineas[i])), None)
    if i is None:
        raise ErrorElena(f"calibracion.toml no tiene bandas en [{seccion}]: edítalo a mano.")
    j, balance = i, 0
    while True:
        sin_comentario = re.sub(r"#.*$", "", lineas[j])
        balance += sin_comentario.count("[") - sin_comentario.count("]")
        if balance <= 0:
            break
        j += 1
        if j >= fin:
            raise ErrorElena(f"calibracion.toml: las bandas de [{seccion}] no cierran: edítalo a mano.")
    nuevas = ["bandas = []"] if not bandas else ["bandas = [", *(f"  {_texto_banda(b)}," for b in bandas), "]"]
    lineas[i:j + 1] = nuevas


def editar_toml(texto: str, cal: cb.Calibracion) -> str:
    """El texto del TOML con los valores de `cal` (los que se cambian en vivo)."""
    lineas = texto.split("\n")
    _poner(lineas, None, "nivel", _num(cal.nivel))
    for nombre, b in cal.bocinas.items():
        partes = [f"ganancia = {_num(b.ganancia)}"]
        if b.retardo_ms is not None:
            partes.append(f"retardo = {_num(b.retardo_ms)}")
        if b.distancia is not None:
            partes.append(f"distancia = {_num(b.distancia)}")
        if b.cruce is not None and nombre != "sub":
            partes.append(f"cruce = {_num(b.cruce, entero=True)}")
        _poner(lineas, "bocinas", nombre, "{ " + ", ".join(partes) + " }")
    _poner(lineas, "lfe", "nivel", _num(cal.lfe))
    if cal.cruce is not None:
        _poner(lineas, "cruce", "frecuencia", _num(cal.cruce, entero=True))
    if cal.estereo is not None:
        _poner(lineas, "estereo", "compensacion", _num(cal.estereo))
        for clave, db in cal.estereo_por_bus.items():
            _poner_o_agregar(lineas, "estereo", clave, _num(db))
    _poner_bandas(lineas, "eq", cal.eq)
    _poner_bandas(lineas, "eq_sub", cal.eq_sub)
    nuevo = "\n".join(lineas)
    if cb.desde_datos(tomllib.loads(nuevo)) != cal:
        raise ErrorElena("No pude guardar el cambio en calibracion.toml sin perder algo: edítalo a mano.")
    return nuevo


# ---------- E/S: en vivo y guardar ----------

def params(cambios: dict[str, float]) -> str:
    return "{ params = [ " + " ".join(f'"{k}" {v:.9g}' for k, v in cambios.items()) + " ] }"


def id_salida() -> int | None:
    from elena.cli import cargar_grafo
    nodo = cargar_grafo().nodo(cb.NODO)
    return nodo.id if nodo else None


def en_vivo(ident: int, cambios: dict[str, float]) -> None:
    if not cambios:
        return
    r = subprocess.run(["pw-cli", "set-param", str(ident), "Props", params(cambios)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorElena(f"pw-cli no aplicó el cambio: {(r.stderr or r.stdout).strip()[:200]}")


def guardar(cal: cb.Calibracion, toml: Path = cb.TOML) -> None:
    """Guarda en el TOML y, si la calibración está activa, regenera el .conf (sin reiniciar)."""
    toml.write_text(editar_toml(toml.read_text(), cal))
    if cb.activa():
        cb.generar(toml, cb.CONF)


def cambiar(nueva: cb.Calibracion, toml: Path = cb.TOML) -> list[str]:
    """Un cambio suelto: en vivo (si se puede) y guardado. Devuelve qué pasó."""
    actual = cb.leer(toml)
    if estructura(nueva) != estructura(actual):
        raise ErrorElena("Ese cambio enciende o apaga el cruce de una bocina: no es en vivo. "
                         "Edita local/calibracion.toml, corre mise run calibracion -- generar y mise run aplicar.")
    hechos = []
    cambios = diferencias(cb.controles(actual), cb.controles(nueva))
    if cb.activa() and (ident := id_salida()) is not None:
        # Si elena_salida es de antes de las ranuras del EQ, esos controles no existen todavía
        if faltan := set(cambios) - controles_vivos(ident):
            cambios = {k: v for k, v in cambios.items() if k not in faltan}
            hechos.append("⚠ La calibración que suena es de antes de las ranuras del EQ: el EQ se oirá después de "
                          "mise run aplicar (una sola vez, con la VM apagada).")
        en_vivo(ident, cambios)
        hechos.append(f"En vivo: {len(cambios)} control{'es' if len(cambios) != 1 else ''}.")
    else:
        hechos.append("La calibración no está activa: solo se guarda.")
    guardar(nueva, toml)
    hechos.append(f"Guardado en {toml.name}" + (f" y {cb.CONF.name}." if cb.activa() else "."))
    if p := cb.preamp(nueva):
        hechos.append(f"Evitar saturación: todo baja {-p:.1f} dB para que nada pase de 0 dB.")
    return hechos


# ---------- La herramienta interactiva ----------

ELEMENTOS = [("1", "fl"), ("2", "fr"), ("3", "fc"), ("4", "rl"), ("5", "rr"), ("6", "sub"),
             ("n", "nivel"), ("l", "lfe"), ("c", "cruce")]
PASO_DB, PASO_HZ = 0.5, 5.0


@dataclass
class Afinacion:
    """Estado de la herramienta (sin E/S). `cal` es lo que suena en modo calibrado."""

    guardada: cb.Calibracion
    cal: cb.Calibracion = None  # type: ignore[assignment]
    elegido: str = "sub"
    neutro: bool = False
    fin: bool = False
    aviso: str = ""
    guardar: bool = False           # la tecla g pidió guardar

    def __post_init__(self) -> None:
        self.cal = self.cal or self.guardada

    def valor(self, elemento: str) -> float | None:
        if elemento in BOCINAS:
            return self.cal.bocinas[elemento].ganancia
        return {"nivel": self.cal.nivel, "lfe": self.cal.lfe, "cruce": self.cal.cruce}[elemento]

    def _mover(self, signo: int) -> None:
        e = self.elegido
        try:
            if e in BOCINAS:
                self.cal = con_bocina(self.cal, e, "ganancia", self.cal.bocinas[e].ganancia + signo * PASO_DB)
            elif e == "nivel":
                self.cal = validar(replace(self.cal, nivel=self.cal.nivel + signo * PASO_DB))
            elif e == "lfe":
                self.cal = validar(replace(self.cal, lfe=self.cal.lfe + signo * PASO_DB))
            elif self.cal.cruce is None:
                self.aviso = "no hay cruce: se enciende en el TOML (generar y aplicar)"
            else:
                self.cal = validar(replace(self.cal, cruce=self.cal.cruce + signo * PASO_HZ))
        except ErrorElena as ex:
            self.aviso = str(ex).split(": ", 1)[-1]

    def tecla(self, k: str) -> None:
        self.aviso = ""
        if (e := next((el for t, el in ELEMENTOS if t == k), None)) is not None:
            self.elegido = e
        elif k in ("+", "=", "-"):
            if self.neutro:
                self.aviso = "estás oyendo neutro: a regresa a lo calibrado para ajustar"
            else:
                self._mover(1 if k != "-" else -1)
        elif k == "a":
            self.neutro = not self.neutro
        elif k == "g":
            self.guardar = True
        elif k == "r":
            self.cal = self.guardada
            self.aviso = "regresó a lo guardado"
        elif k in ("q", "\x03"):
            self.fin = True

    def controles(self) -> dict[str, float]:
        return neutro(self.cal) if self.neutro else cb.controles(self.cal)

    def pendientes(self) -> list[str]:
        """Lo que cambió respecto a lo guardado, en texto."""
        g, c = self.guardada, self.cal
        cambios = [f"{b} {g.bocinas[b].ganancia:+.1f}→{c.bocinas[b].ganancia:+.1f}" for b in BOCINAS
                   if g.bocinas[b].ganancia != c.bocinas[b].ganancia]
        if g.nivel != c.nivel:
            cambios.append(f"nivel {g.nivel:+.1f}→{c.nivel:+.1f}")
        if g.lfe != c.lfe:
            cambios.append(f"LFE {g.lfe:+.1f}→{c.lfe:+.1f}")
        if g.cruce != c.cruce:
            cambios.append(f"cruce {g.cruce:g}→{c.cruce:g} Hz")
        return cambios


def _linea(a: Afinacion) -> str:
    v = a.valor(a.elegido)
    texto = f"{v:g} Hz" if a.elegido == "cruce" else f"{v:+.1f} dB"
    modo = "NEUTRO (sin calibrar)" if a.neutro else "calibrado"
    pend = a.pendientes()
    linea = (f"\r\033[K  ▸ {a.elegido} {texto} · oyes: {modo} · "
             + (f"sin guardar: {', '.join(pend)}" if pend else "todo guardado"))
    return linea + (f"   ⚠ {a.aviso}" if a.aviso else "")


def correr(toml: Path = cb.TOML) -> int:
    from elena.satelites import teclado
    if not sys.stdin.isatty():
        raise ErrorElena("Afinar es interactivo: córrelo en una terminal.")
    if not cb.activa():
        raise ErrorElena("La calibración no está activa: mise run calibracion -- activar y mise run aplicar.")
    ident = id_salida()
    if ident is None:
        raise ErrorElena(f"No existe {cb.NODO}: ¿falta mise run aplicar?")
    guardada = cb.leer(toml)
    a = Afinacion(guardada)
    print("Afinar la calibración en vivo: pon música y ajusta al oído. Nada se guarda hasta que oprimas g.")
    print("  1 FL · 2 FR · 3 FC · 4 RL · 5 RR · 6 sub · n nivel (tu 100 %) · l LFE · c cruce   ← elige")
    print(f"  + / −  {PASO_DB:g} dB ({PASO_HZ:g} Hz el cruce) · a  A/B: calibrado ↔ neutro · r  regresa a lo guardado")
    print("  g  guarda (en el TOML y el .conf) · q  sale (si no guardaste, regresa a lo guardado)")
    print("Neutro: mismo volumen en FL, sin balance, sin cruce y sin EQ (como antes de calibrar).")
    sonando = cb.controles(guardada)
    try:
        with teclado() as leer:
            while not a.fin:
                objetivo = a.controles()
                if cambios := diferencias(sonando, objetivo):
                    en_vivo(ident, cambios)
                    sonando = objetivo
                if a.guardar:
                    a.guardar = False
                    if a.pendientes():
                        guardar(a.cal, toml)
                        a.guardada = a.cal
                        a.aviso = "guardado"
                    else:
                        a.aviso = "no hay cambios que guardar"
                print(_linea(a), end="", flush=True)
                if (k := leer(0.2)) is not None:
                    a.tecla(k)
    except KeyboardInterrupt:
        pass
    finally:
        # Lo que queda sonando es lo guardado (aunque se haya salido en neutro o con cambios)
        if cambios := diferencias(sonando, cb.controles(a.guardada)):
            en_vivo(ident, cambios)
        print()
    if a.pendientes():
        print(f"Sin guardar (regresó a lo guardado): {', '.join(a.pendientes())}")
    print("Suena lo guardado en local/calibracion.toml.")
    return 0


# ---------- El EQ desde la CLI (todo en vivo: ranuras fijas bq_raw) ----------

ALIAS_TIPO = {"pico": "pico", "grave": "estante_grave", "estante_grave": "estante_grave",
              "agudo": "estante_agudo", "estante_agudo": "estante_agudo"}
CAMPOS_BANDA = ("tipo", "frecuencia", "ganancia", "q")
PUNTOS = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
PUNTOS_SUB = (20, 31, 45, 63, 90, 125)


def _tipo(texto: str) -> str:
    if (t := ALIAS_TIPO.get(texto.lower())) is None:
        raise ErrorElena(f"Tipo desconocido: {texto}. Son pico, grave (estante grave) o agudo (estante agudo).")
    return t


def _numero(texto: str, que: str) -> float:
    try:
        return float(texto)
    except ValueError as e:
        raise ErrorElena(f"{que} inválido: «{texto}».") from e


def eq_nueva(cal: cb.Calibracion, partes: list[str], sub: bool = False, q: float | None = None) -> cb.Calibracion:
    """`partes`: agregar <tipo> <Hz> <dB> · <n> tipo|frecuencia|ganancia|q <valor> · quitar <n> · limpiar."""
    bandas = list(cal.eq_sub if sub else cal.eq)
    if not partes:
        raise ErrorElena("Falta qué hacer: agregar, <n> <campo> <valor>, quitar <n> o limpiar.")
    orden = partes[0]
    if orden == "agregar":
        if len(partes) != 4:
            raise ErrorElena("Uso: eq agregar <pico|grave|agudo> <Hz> <dB> [--q Q] [--sub]")
        tipo = _tipo(partes[1])
        bandas.append(cb.Banda(tipo, _numero(partes[2], "Frecuencia"), _numero(partes[3], "Ganancia"),
                               q if q is not None else cb.Q_POR_TIPO[tipo]))
    elif orden == "limpiar":
        bandas = []
    else:
        quitar = orden == "quitar"
        texto_n = partes[1] if quitar and len(partes) > 1 else orden
        if not texto_n.isdigit() or not 1 <= int(texto_n) <= len(bandas):
            raise ErrorElena(f"No hay banda {texto_n}: hay {len(bandas)} (mira la lista con: eq).")
        i = int(texto_n) - 1
        if quitar:
            bandas.pop(i)
        else:
            if len(partes) != 3 or partes[1] not in CAMPOS_BANDA:
                raise ErrorElena(f"Uso: eq <n> <{'|'.join(CAMPOS_BANDA)}> <valor> [--sub]")
            campo, valor = partes[1], partes[2]
            b = bandas[i]
            bandas[i] = replace(b, tipo=_tipo(valor)) if campo == "tipo" else \
                replace(b, **{campo: _numero(valor, campo.capitalize())})
    nueva = replace(cal, eq_sub=tuple(bandas)) if sub else replace(cal, eq=tuple(bandas))
    return validar(nueva)


def texto_eq(cal: cb.Calibracion) -> list[str]:
    lineas = []
    for nombre, bandas, ranuras, puntos in (("EQ común (5 satélites)", cal.eq, cb.RANURAS_EQ, PUNTOS),
                                            ("EQ del sub", cal.eq_sub, cb.RANURAS_EQ_SUB, PUNTOS_SUB)):
        lineas.append(f"{nombre}: {len(bandas)} de {ranuras} bandas")
        for i, b in enumerate(bandas, start=1):
            lineas.append(f"  {i}  {b.tipo:<13} {b.frecuencia:>7g} Hz  {b.ganancia:+5.1f} dB  Q {b.q:g}")
        if bandas:
            curva = " · ".join(f"{f if f < 1000 else f'{f // 1000}k'} {cb.respuesta_db(bandas, f):+.1f}" for f in puntos)
            lineas.append(f"  respuesta (Hz dB): {curva}")
    return lineas


def controles_vivos(ident: int) -> set[str]:
    r = subprocess.run(["pw-cli", "enum-params", str(ident), "Props"], capture_output=True, text=True)
    return set(re.findall(r'String "([a-z_0-9]+:[^"]+)"', r.stdout))
