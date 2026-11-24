"""El widget principal: buses, apps, entradas y grabaciones, con cursor, volumen y marcas.

Dibuja una línea por fila (Line API de Textual) y guarda el cursor por clave, no por número
de línea, para que no salte cuando cambia el grafo. La parte derecha de cada fila es el volumen:
silencio (M), balance (≠), barra y %. La rueda del mouse sobre esa zona cambia el volumen; en el
resto de la fila desplaza la lista.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from elena.modelo import lineal_a_pct
from elena.tui.estilo import ACENTO, ALERTA, APAGADO, AVISO, CURSOR, VACIO
from elena.tui.vista import Clave, Fila

if TYPE_CHECKING:
    from elena.tui.app import ElenaApp

DERECHA = 20  # " M≠ ▮▮▮▮▮▮▮▮▮▮ 100% "
NIVEL = 12    # " ███████│·· " : nivel del canal más alto, con su pico sostenido
CELDAS = 10
MIN_DB = -60.0
CON_NIVEL = ("bus", "app", "fuente")


def _celdas(db: float, ancho: int) -> int:
    return max(0, min(ancho, round((db - MIN_DB) / -MIN_DB * ancho)))


def _color(db: float) -> str:
    return ACENTO if db < -18 else (AVISO if db < -6 else ALERTA)


@lru_cache(maxsize=64)
def _tramos(ancho: int) -> tuple[tuple[int, int, str], ...]:
    """Zonas de color de una barra de `ancho` celdas: (desde, hasta, color)."""
    colores = [_color(MIN_DB + (i + 1) / ancho * -MIN_DB) for i in range(ancho)]
    tramos, inicio = [], 0
    for i in range(1, ancho + 1):
        if i == ancho or colores[i] != colores[inicio]:
            tramos.append((inicio, i, colores[inicio]))
            inicio = i
    return tuple(tramos)


def barra_nivel(pico: float, retenido: float, ancho: int) -> Text:
    """Barra de -60 a 0 dBFS: lleno hasta el pico actual, `│` en el pico sostenido.

    Se arma por tramos (uno por zona de color), no celda por celda: se dibuja 20 veces por
    segundo por cada medidor.
    """
    llenas, marca = _celdas(pico, ancho), _celdas(retenido, ancho) - 1
    t = Text()
    for desde, hasta, color in _tramos(ancho):
        if desde < llenas:
            t.append("█" * (min(hasta, llenas) - desde), style=color)
    if marca >= llenas and retenido > MIN_DB:
        t.append("·" * (marca - llenas), style=VACIO)
        t.append("│", style=_color(retenido))
        t.append("·" * (ancho - marca - 1), style=VACIO)
    else:
        t.append("·" * (ancho - llenas), style=VACIO)
    return t


def texto_db_corto(db: float) -> str:
    return "  -∞" if db <= MIN_DB else f"{db:4.0f}"


class Mezclador(Widget, can_focus=True):
    BINDINGS = [
        Binding("up,k", "cursor(-1)", "Arriba", show=False),
        Binding("down,j", "cursor(1)", "Abajo", show=False),
        Binding("home", "cursor(-10000)", show=False),
        Binding("end", "cursor(10000)", show=False),
        Binding("left", "volumen(-5)", "Vol −"),
        Binding("right", "volumen(5)", "Vol +"),
        Binding("shift+left", "volumen(-1)", show=False),
        Binding("shift+right", "volumen(1)", show=False),
        Binding("s", "silenciar", "Silenciar"),
        Binding("m", "mover", "Mover"),
        Binding("space", "marcar", "Marcar", show=False),
        Binding("d,enter", "detalle", "Detalle"),
        # En la ayuda (?), no en el pie
        Binding("c", "balance", "Balance", show=False),
        Binding("p", "probar", "Probar", show=False),
        Binding("r", "regla", "Regla", show=False),
    ]

    app: ElenaApp

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.filas: list[Fila] = []
        self.cursor: Clave | None = None
        self.marcadas: set[Clave] = set()
        self._firmas: dict[int, tuple | None] = {}  # lo que muestra el nivel de cada línea dibujada

    # ---------- datos ----------

    def actualizar(self, filas: list[Fila]) -> None:
        """Nuevas filas; conserva el cursor por clave o, si su fila desapareció, la más cercana."""
        linea_previa = self._linea_cursor()
        self.filas = filas
        self._firmas.clear()
        claves = {f.clave for f in filas if f.clave}
        self.marcadas &= claves
        if self.cursor not in claves:
            seleccionables = [i for i, f in enumerate(filas) if f.clave]
            if seleccionables:
                cerca = min(seleccionables, key=lambda i: abs(i - (linea_previa or 0)))
                self.cursor = filas[cerca].clave
            else:
                self.cursor = None
        self.refresh(layout=True)

    @property
    def clave_de_bloque(self) -> Clave | None:
        """La clave del nodo cuyo bloque del puente se resalta: el bus de la fila elegida (la del
        bus o la de una de sus apps) o la fila misma (el mic en Entradas)."""
        linea = self._linea_cursor()
        if linea is None:
            return None
        for fila in reversed(self.filas[:linea + 1]):
            if fila.tipo == "bus":
                return fila.clave
            if fila.tipo != "app":
                break
        return self.filas[linea].clave

    @property
    def fila_actual(self) -> Fila | None:
        return next((f for f in self.filas if f.clave == self.cursor and self.cursor), None)

    def _linea_cursor(self) -> int | None:
        return next((i for i, f in enumerate(self.filas) if f.clave == self.cursor and self.cursor), None)

    # ---------- dibujo ----------

    # Line API: cada línea se dibuja por separado, y los medidores (20 veces por segundo) solo
    # redibujan las líneas cuyo nivel cambió a la vista: misma cantidad de celdas, mismo pico
    # sostenido y mismo número = misma «firma» = no se dibuja.

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(len(self.filas), 1)

    def render_line(self, y: int) -> Strip:
        ancho = max(self.size.width, DERECHA + NIVEL + 10)
        estilo = self.visual_style.rich_style
        if y < len(self.filas):
            texto = self._linea(self.filas[y], ancho)
            self._firmas[y] = self._firma(self.filas[y], ancho)
        elif y == 0:
            texto = Text("(sin datos de PipeWire todavía)", style="dim")
        else:
            return Strip.blank(self.size.width, estilo)
        if texto.style:  # Text.render omite el estilo base si el texto no tiene tramos: se vuelve tramo
            texto.stylize(texto.style)
            texto.style = ""
        strip = Strip(texto.render(self.app.console), texto.cell_len)
        return strip.adjust_cell_length(self.size.width, estilo).apply_style(estilo)

    def refrescar_medidores(self) -> None:
        """Redibuja solo las líneas con nivel (buses, apps y entradas) que cambiaron."""
        ancho = max(self.size.width, DERECHA + NIVEL + 10)
        regiones = [Region(0, y, self.size.width, 1) for y, f in enumerate(self.filas)
                    if f.tipo in CON_NIVEL and self._firma(f, ancho) != self._firmas.get(y, ())]
        if regiones:
            self.refresh(*regiones)

    def _firma(self, fila: Fila, ancho: int) -> tuple | None:
        if fila.nodo is None or fila.tipo not in CON_NIVEL:
            return None
        lectura = self.app.lectura(fila.nodo)
        if lectura is None:
            return ("sin lectura",)
        w = NIVEL - 2
        return (_celdas(float(lectura.pico.max()), w), _celdas(float(lectura.retenido.max()), w))

    def _linea(self, fila: Fila, ancho: int) -> Text:
        izquierda = fila.texto.copy()
        izquierda.truncate(ancho - DERECHA - NIVEL, overflow="ellipsis", pad=True)
        linea = izquierda + self._nivel(fila) + (self._volumen(fila) if fila.con_volumen else Text(" " * DERECHA))
        if fila.clave and fila.clave == self.cursor:
            linea.stylize(CURSOR)
        return linea

    def _nivel(self, fila: Fila) -> Text:
        lectura = self.app.lectura(fila.nodo) if fila.nodo and fila.tipo in CON_NIVEL else None
        if lectura is None:
            return Text(" " * NIVEL)
        i = int(lectura.retenido.argmax())
        return Text(" ") + barra_nivel(float(lectura.pico.max()), float(lectura.retenido[i]), NIVEL - 2) + " "

    def _volumen(self, fila: Fila) -> Text:
        nodo = fila.nodo
        assert nodo is not None
        vols = self.app.volumenes_de(nodo)
        pct = lineal_a_pct(max(vols))
        llenas = min(round(pct / 100 * CELDAS), CELDAS)
        balanceado = len({round(v, 6) for v in vols}) <= 1
        color = APAGADO if nodo.silencio else (ALERTA if pct > 100.5 else ACENTO)
        return Text.assemble(
            " ",
            ("M" if nodo.silencio else " ", f"bold {ALERTA}"),
            ("≠" if not balanceado else " ", f"bold {AVISO}"),
            " ",
            ("▮" * llenas, color),
            ("▯" * (CELDAS - llenas), VACIO),
            (f" {pct:3.0f}% ", "bold" if not nodo.silencio else APAGADO),
        )

    # ---------- teclado ----------

    def action_cursor(self, paso: int) -> None:
        seleccionables = [i for i, f in enumerate(self.filas) if f.clave]
        if not seleccionables:
            return
        actual = self._linea_cursor()
        if actual is None or actual not in seleccionables:
            nuevo = seleccionables[0]
        else:
            pos = seleccionables.index(actual) + paso
            nuevo = seleccionables[max(0, min(pos, len(seleccionables) - 1))]
        self.cursor = self.filas[nuevo].clave
        self._mostrar_linea(nuevo)
        self.refresh()

    def elegir(self, clave: Clave) -> None:
        """Lleva el cursor a la fila de `clave` (clic en un bloque del puente)."""
        linea = next((i for i, f in enumerate(self.filas) if f.clave == clave), None)
        if linea is not None:
            self.cursor = clave
            self._mostrar_linea(linea)
            self.refresh()

    def _mostrar_linea(self, linea: int) -> None:
        if self.parent is not None and hasattr(self.parent, "scroll_to_region"):
            region = Region(0, self.virtual_region.y + linea, max(self.size.width, 1), 1)
            self.parent.scroll_to_region(region, animate=False, spacing=None)

    def action_volumen(self, delta: float) -> None:
        if (f := self.fila_actual) and f.con_volumen:
            self.app.cambiar_volumen(f.nodo, delta)

    def action_silenciar(self) -> None:
        if (f := self.fila_actual) and f.con_volumen:
            self.app.silenciar(f.nodo)

    def action_marcar(self) -> None:
        if (f := self.fila_actual) and f.tipo == "app":
            self.marcadas ^= {f.clave}
            self.app.redibujar()
            self.action_cursor(1)

    def action_mover(self) -> None:
        marcadas = [f.nodo for f in self.filas if f.clave in self.marcadas and f.tipo == "app"]
        if not marcadas and (f := self.fila_actual) and f.tipo == "app":
            marcadas = [f.nodo]
        if not marcadas and (f := self.fila_actual) and f.tipo == "grabacion" and f.nodo:
            self.app.elegir_origen(f.nodo)  # una grabación: qué graba (Visual o un bus)
            return
        if not marcadas:
            self.app.notify("Elige una app (o márcalas con espacio) para moverla.", severity="warning")
            return
        self.app.elegir_destino(marcadas)

    def action_balance(self) -> None:
        f = self.fila_actual
        if f and f.tipo == "bus" and f.nodo and len(f.nodo.volumenes) > 1:
            self.app.abrir_balance(f.nodo)
        else:
            self.app.notify("El balance es por bus: elige Música, Juegos, Sistema o Chat.", severity="warning")

    def action_probar(self) -> None:
        f = self.fila_actual
        if f and f.tipo in ("bus", "salida") and f.nodo:
            self.app.abrir_probar(f.nodo)
        else:
            self.app.notify("Para probar bocinas, elige un bus o una salida.", severity="warning")

    def action_regla(self) -> None:
        f = self.fila_actual
        if f and f.tipo in ("app", "grabacion") and f.nodo:
            self.app.abrir_regla(f.nodo)
        else:
            self.app.notify("Las reglas fijas son por app: elige una app (o una grabación, para Visual).",
                            severity="warning")

    def action_detalle(self) -> None:
        if (f := self.fila_actual) and f.nodo:
            self.app.abrir_detalle(f.nodo)

    # ---------- mouse ----------

    def _fila_en(self, evento: events.MouseEvent) -> tuple[int, Fila] | None:
        offset = evento.get_content_offset(self)
        if offset is None or not 0 <= offset.y < len(self.filas):
            return None
        return offset.x, self.filas[offset.y]

    def on_click(self, evento: events.Click) -> None:
        if (r := self._fila_en(evento)) and r[1].clave:
            self.cursor = r[1].clave
            self.refresh()
            if evento.chain == 2:
                self.action_detalle()

    def _rueda(self, evento: events.MouseEvent, delta: float) -> None:
        r = self._fila_en(evento)
        if r and r[1].con_volumen and r[0] >= self.size.width - DERECHA:
            evento.stop()
            evento.prevent_default()
            self.cursor = r[1].clave
            self.app.cambiar_volumen(r[1].nodo, delta)

    def on_mouse_scroll_up(self, evento: events.MouseScrollUp) -> None:
        self._rueda(evento, +2)

    def on_mouse_scroll_down(self, evento: events.MouseScrollDown) -> None:
        self._rueda(evento, -2)
