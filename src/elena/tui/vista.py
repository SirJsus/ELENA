"""Las filas de la consola a partir del grafo (sin Textual, para probarlas aparte).

Cada fila seleccionable tiene una `clave` estable entre redibujados: ("nodo", node.name) para
buses, salidas, fuentes e internos, y ("app", serial) para los streams. Así el cursor y las apps
marcadas no se pierden cuando el grafo cambia.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from elena.formato import nombre_corto, nombre_salida, texto_canales
from elena.medidor import Objetivo
from elena.modelo import AUDIFONOS, BUSES, CALIBRACION, MICS, Grafo, Nodo, Tipo, lineal_a_db
from elena.reglas import Regla
from elena.reglas import destino as regla_de
from elena.tui.estilo import ACENTO, ALERTA, APAGADO, AVISO, CURSOR, VACIO  # noqa: F401

Clave = tuple[str, str]


@dataclass(frozen=True)
class Fila:
    tipo: str                 # bus, app, salida, fuente, grabacion, interno, titulo, nota, vacia
    texto: Text               # la parte izquierda; la derecha (volumen) la dibuja el widget
    nodo: Nodo | None = None
    clave: Clave | None = None

    @property
    def con_volumen(self) -> bool:
        return self.nodo is not None and bool(self.nodo.volumenes) and self.tipo != "interno"


def clave_de(nodo: Nodo) -> Clave:
    if nodo.tipo in (Tipo.APP, Tipo.GRABACION):
        return ("app", str(nodo.serial if nodo.serial is not None else nodo.id))
    return ("nodo", nodo.nombre or str(nodo.id))


def _fila_nodo(tipo: str, nodo: Nodo, texto: Text) -> Fila:
    return Fila(tipo=tipo, texto=texto, nodo=nodo, clave=clave_de(nodo))


ETIQUETAS = {b.clave: b.etiqueta for b in BUSES} | {b.nodo: b.etiqueta for b in BUSES} | {"visual": "Visual"}


def _texto_app(app: Nodo, marcada: bool, flecha: str = "▸", regla: Regla | None = None) -> Text:
    t = Text("  ")
    t.append("◆ " if marcada else f"{flecha} ", style="bold magenta" if marcada else "")
    t.append(app.visible, style="bold")
    if app.media and app.media != app.visible:
        t.append(f" · {app.media}", style="dim")
    t.append(f" · {texto_canales(app)}", style="cyan" if (app.canales or 0) > 2 else "")
    t.append(f" · {app.api or '-'}", style=AVISO if app.api == "pw" else "dim")
    if regla is not None:
        t.append(f" · ⚑ {ETIQUETAS.get(regla.bus, regla.bus)}", style=ACENTO)
    return t


def _titulo(texto: str) -> Fila:
    return Fila(tipo="titulo", texto=Text(texto, style="bold underline"))


def _nota(texto: str) -> Fila:
    return Fila(tipo="nota", texto=Text(f"  {texto}", style="dim italic"))


VACIA = Fila(tipo="vacia", texto=Text(""))
ANCHO_BUS = 11     # la columna de las etiquetas de los buses (la más larga, «Audífonos», y dos espacios)


def _camino(grafo: Grafo, destino: Nodo) -> str:
    """El destino de un bus; si es la calibración o Audífonos, también a dónde salen ellas."""
    texto = nombre_salida(destino)
    if destino.nombre in (CALIBRACION, AUDIFONOS) and (s := grafo.nodo(f"{destino.nombre}.salida")) \
            and (fin := grafo.sinks_de(s)):
        texto += " → " + ", ".join(nombre_salida(f) for f in fin)
    return texto


def filas(grafo: Grafo, marcadas: frozenset[Clave] = frozenset(), internos: bool = False,
          reglas: list[Regla] | None = None) -> list[Fila]:
    resultado: list[Fila] = []

    def apps(lista: list[Nodo], flecha: str = "▸") -> None:
        for app in lista:
            clave = clave_de(app)
            regla = regla_de(reglas, app) if reglas else None
            resultado.append(Fila("app", _texto_app(app, clave in marcadas, flecha, regla), app, clave))

    resultado.append(_titulo("Buses"))
    for bus in grafo.buses():
        d = bus.definicion
        if bus.nodo is None:
            resultado.append(Fila("nota", Text.assemble((f"{d.etiqueta:<{ANCHO_BUS}}", "bold"),
                                                       (f"{d.nodo} no existe: ¿falta mise run aplicar?", ALERTA))))
            resultado.append(VACIA)
            continue
        # El nombre interno (elena_musica…) está en el detalle (d); aquí, solo lo que se usa
        t = Text.assemble((f"{d.etiqueta:<{ANCHO_BUS}}", f"bold {ACENTO}"), (f"{texto_canales(bus.nodo)} ", "dim"))
        if bus.destinos:
            t.append("→ " + ", ".join(_camino(grafo, x) for x in bus.destinos), style=ACENTO)
        elif bus.esperando:
            t.append(f"→ esperando a {nombre_corto(bus.esperando)}", style=AVISO)
        else:
            t.append("→ sin destino", style=ALERTA)
        # La compensación estéreo baja la salida del bus: se ve aquí mientras está activa
        if (s := grafo.nodo(d.salida)) and s.volumenes and (db := lineal_a_db(max(s.volumenes))) < -0.05:
            t.append(f"  ♪ estéreo {db:+.0f} dB", style=f"bold {AVISO}")
        resultado.append(_fila_nodo("bus", bus.nodo, t))
        apps(bus.apps)
        # «Solo audífonos»: los buses de bocinas que salen aquí
        enviados = (au.enviados if d.nodo == AUDIFONOS and (au := grafo.audifonos()) else [])
        if enviados:
            resultado.append(Fila("nota", Text.assemble(("  ◆ ", f"bold {ACENTO}"),
                                                       ("solo audífonos: ", "bold"),
                                                       (", ".join(ETIQUETAS[c] for c in enviados), ACENTO))))
        elif not bus.apps:
            resultado.append(_nota("(sin apps)"))
        resultado.append(VACIA)

    if otras := grafo.otras_salidas():
        resultado.append(_titulo("Directo a una salida"))
        for salida, lista in otras:
            resultado.append(_fila_nodo("salida", salida, Text(f"  {nombre_salida(salida)}", style="bold")))
            apps(lista)
        resultado.append(VACIA)

    if sueltas := grafo.apps_sueltas():
        resultado.append(_titulo("Sin conectar"))
        apps(sueltas)
        resultado.append(VACIA)

    if fuentes := grafo.de_tipo(Tipo.FUENTE):
        resultado.append(_titulo("Entradas"))
        for f in sorted(fuentes, key=lambda f: (not f.nombre.startswith("elena_"), f.nombre)):
            t = Text.assemble(("  " + f.visible, "bold" if f.nombre.startswith("elena_") else ""),
                              (f" · {texto_canales(f)}", "dim"))
            if f.nombre == grafo.fuente_por_defecto:
                t.append(" · por defecto", style=ACENTO)
            resultado.append(_fila_nodo("fuente", f, t))
        resultado.append(VACIA)

    if grabaciones := grafo.de_tipo(Tipo.GRABACION):
        resultado.append(_titulo("Grabaciones"))
        for g in grabaciones:
            t = _texto_app(g, clave_de(g) in marcadas, "◂", regla_de(reglas, g) if reglas else None)
            t.append(" ← " + (", ".join(o.nombre for o in grafo.origenes(g)) or "-"), style="dim")
            resultado.append(_fila_nodo("grabacion", g, t))
        resultado.append(VACIA)

    if internos:
        resultado.append(_titulo("Internos de ELENA"))
        for n in sorted(grafo.de_tipo(Tipo.INTERNO), key=lambda n: n.nombre):
            destino = ", ".join(d.nombre for d in grafo.destinos(n)) or "-"
            t = Text(f"  {n.nombre}", style="dim")
            t.append(f" · {texto_canales(n)} · {n.estado or '-'} → {destino}", style="dim")
            resultado.append(_fila_nodo("interno", n, t))

    while resultado and resultado[-1] is VACIA:
        resultado.pop()
    return resultado


@dataclass(frozen=True)
class Bloque:
    """Un bloque del puente de medidores: una columna vertical por canal de `nodo`."""

    titulo: str
    nodo: Nodo
    etiquetas: tuple[str, ...]
    entrada: bool = False     # el mic: va al final, separado de los buses

    @property
    def clave(self) -> Clave:
        return clave_de(self.nodo)


def bloques(grafo: Grafo) -> list[Bloque]:
    """Los bloques del puente: cada bus con apps (lo que sale de él, por canal) y el mic de ELENA.
    Un bus sin apps no tiene bloque: no se mide (ver `deseados`)."""
    resultado = []
    for bus in grafo.buses():
        if bus.nodo is not None and bus.apps and bus.nodo.canales:
            resultado.append(Bloque(bus.definicion.etiqueta, bus.nodo, _etiquetas(bus.nodo)))
    if (mic := grafo.nodo(MICS[0])) and mic.canales:
        etiquetas = ("M",) if mic.canales == 1 else _etiquetas(mic)
        resultado.append(Bloque("Mic", mic, etiquetas, entrada=True))
    return resultado


def _etiquetas(nodo: Nodo) -> tuple[str, ...]:
    if len(nodo.posiciones) == nodo.canales:
        return tuple(nodo.posiciones)
    return tuple(str(i + 1) for i in range(nodo.canales or 0))


def objetivo_de(nodo: Nodo) -> Objetivo | None:
    """Cómo medir un nodo: los sinks por su monitor, el resto directo. Por serial si es un stream."""
    if not nodo.canales:
        return None
    posiciones = nodo.posiciones if len(nodo.posiciones) == nodo.canales else ()
    if nodo.tipo in (Tipo.APP, Tipo.GRABACION):
        if nodo.serial is None:
            return None
        return Objetivo(str(nodo.serial), nodo.canales, posiciones)
    if not nodo.nombre:
        return None
    return Objetivo(nodo.nombre, nodo.canales, posiciones, monitor=nodo.clase.startswith("Audio/Sink"))


def deseados(grafo: Grafo, extra: Nodo | None = None) -> dict[Clave, Objetivo]:
    """Lo que hay que medir: los buses con apps, sus apps, las apps directas a una salida, el mic
    de ELENA y `extra` (el nodo del detalle abierto). Un bus sin apps no se mide: así se puede
    suspender."""
    nodos: list[Nodo] = []
    for bus in grafo.buses():
        if bus.nodo and bus.apps:
            nodos += [bus.nodo, *bus.apps]
    for _, apps in grafo.otras_salidas():
        nodos += apps
    if mic := grafo.nodo(MICS[0]):
        nodos.append(mic)
    if extra is not None:
        nodos.append(extra)
    resultado = {}
    for nodo in nodos:
        if objetivo := objetivo_de(nodo):
            resultado[clave_de(nodo)] = objetivo
    return resultado
