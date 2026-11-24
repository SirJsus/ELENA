"""El puente de medidores: columnas verticales por canal de los buses con apps y del mic.

Va fijo abajo de la lista. Cada bus con apps tiene un bloque con una columna por canal (lo que
**sale** del bus, con su volumen), y el mic de ELENA uno al final, separado por una línea. Un bus
sin apps no tiene bloque y los demás se recorren a la izquierda.

    título    Música                    Mic
    10 filas  ███ ███ ... de 0 a −60 dBFS, 6 dB por fila, con bloques de octavos (~0.75 dB)
    etiqueta   FL  FR  FC LFE  RL  RR     M
    número     -8  -8  -∞ -25 -13 -13   -20      (el pico sostenido)

Como el mezclador, usa la Line API: 20 veces por segundo calcula la «firma» de cada línea (lo
que muestra, ya cuantizado a octavos de celda) y solo redibuja las que cambiaron.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from elena.tui.estilo import ACENTO, ALERTA, AVISO, CURSOR, VACIO
from elena.tui.mezclador import MIN_DB
from elena.tui.vista import Bloque

if TYPE_CHECKING:
    from elena.tui.app import ElenaApp

FILAS = 10                     # filas de barra: 6 dB cada una
OCTAVOS = FILAS * 8
DB_FILA = -MIN_DB / FILAS
ALTO = FILAS + 3               # título, barras, etiquetas y números
ESCALA = 4                     # «-54 »
ENTRE_BLOQUES = 3
SEPARADOR_MIC = " │ "
PARCIALES = " ▁▂▃▄▅▆▇█"
MARCA = "─"                    # el pico sostenido, si queda arriba de la barra


def _color_fila(fila: int) -> str:
    """Color de la fila `fila` (0 = arriba): las zonas del medidor horizontal (−6 y −18 dB)."""
    techo = -fila * DB_FILA
    return ALERTA if techo > -6 else (AVISO if techo > -18 else ACENTO)


def octavos(db: float) -> int:
    return max(0, min(OCTAVOS, round((db - MIN_DB) / -MIN_DB * OCTAVOS)))


@dataclass(frozen=True)
class Columna:
    """Lo que muestra un canal, cuantizado: cambia solo si cambia algo a la vista."""

    alto: int                  # octavos llenos (0 a 80)
    marca: int | None          # fila del pico sostenido, si queda arriba de la barra
    satura: bool
    numero: str                # pico sostenido en dB, 3 caracteres

    def celda(self, fila: int) -> tuple[str, str]:
        """(carácter, estilo) de la columna en la fila de barra `fila` (0 = arriba)."""
        lleno = max(0, min(8, self.alto - (FILAS - 1 - fila) * 8))
        if lleno:
            return PARCIALES[lleno], _color_fila(fila)
        if fila == self.marca:
            return MARCA, _color_fila(fila)
        return "·", VACIO


SIN_SEÑAL = Columna(0, None, False, " -∞")


def columna(pico: float, retenido: float, satura: bool) -> Columna:
    alto, alto_ret = octavos(pico), octavos(retenido)
    marca = None
    if alto_ret > 0:
        fila_ret = FILAS - 1 - (alto_ret - 1) // 8
        fila_tope = FILAS - 1 - (alto - 1) // 8 if alto else FILAS
        if fila_ret < fila_tope:
            marca = fila_ret
    numero = " -∞" if retenido <= MIN_DB else f"{round(retenido):3d}"
    return Columna(alto, marca, satura, numero)


@dataclass(frozen=True)
class _Ubicado:
    bloque: Bloque
    x: int                     # columna donde empieza el bloque
    ancho: int


class Puente(Widget):
    app: ElenaApp

    DEFAULT_CSS = """
    Puente { height: auto; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.bloques: list[Bloque] = []
        self._firmas: dict[int, tuple] = {}
        self._cache: list[tuple[tuple, Text]] | None = None  # las líneas del cuadro actual
        self._estado: tuple | None = None  # lo que mostraba el último cuadro armado
        self._cache_ancho = 0              # el ancho con el que se armó `_cache`

    def actualizar(self, bloques: list[Bloque]) -> None:
        self.display = bool(bloques)
        if bloques != self.bloques:
            self.bloques = bloques
            self._firmas.clear()
            self._cache = self._estado = None
            self.refresh(layout=True)

    def on_resize(self) -> None:
        self._cache = self._estado = None

    def get_content_height(self, container, viewport, width: int) -> int:
        return ALTO

    # ---------- geometría ----------

    def _geometria(self) -> tuple[int, bool, list[_Ubicado]]:
        """(ancho de barra, con números, bloques ubicados). Los buses van de izquierda a derecha y
        el mic pegado al borde derecho. Si no cabe con barras de 3, usa 2 y quita los números (no
        caben en 2 celdas)."""
        ancho_total = self.size.width
        for barra in (3, 2):
            x, ubicados = ESCALA, []
            for b in (b for b in self.bloques if not b.entrada):
                if ubicados:
                    x += ENTRE_BLOQUES
                ancho = len(b.etiquetas) * (barra + 1) - 1
                ubicados.append(_Ubicado(b, x, ancho))
                x += ancho
            for b in (b for b in self.bloques if b.entrada):
                ancho = len(b.etiquetas) * (barra + 1) - 1
                minimo = x + len(SEPARADOR_MIC) if ubicados else ESCALA
                x = max(minimo, ancho_total - ancho)
                ubicados.append(_Ubicado(b, x, ancho))
                x += ancho
            if x <= ancho_total or barra == 2:
                return barra, barra == 3, ubicados
        raise AssertionError

    def _columnas(self, bloque: Bloque) -> list[Columna]:
        lectura = self.app.lectura(bloque.nodo)
        if lectura is None:
            return [SIN_SEÑAL] * len(bloque.etiquetas)
        return [columna(float(lectura.pico[i]), float(lectura.retenido[i]), bool(lectura.satura[i]))
                if i < len(lectura.pico) else SIN_SEÑAL for i in range(len(bloque.etiquetas))]

    # ---------- dibujo ----------

    def _leer(self) -> tuple:
        """Lo que hay que mostrar, ya cuantizado: (geometría, bloque resaltado, columnas)."""
        geometria = self._geometria()
        columnas = tuple(tuple(self._columnas(u.bloque)) for u in geometria[2])
        return geometria, self.app.mezclador.clave_de_bloque, columnas

    def _lineas(self, estado: tuple | None = None) -> list[tuple[tuple, Text]]:
        """Todas las líneas con su firma. Se arman juntas: cada línea cruza todos los bloques."""
        (barra, con_numeros, ubicados), resaltado, columnas = estado or self._leer()
        lineas: list[Text] = [Text(" " * ESCALA) for _ in range(ALTO)]
        for f in range(FILAS):
            lineas[1 + f] = Text(f"{-f * DB_FILA:3.0f} ", style=VACIO)
        x = ESCALA
        for k, (u, cols) in enumerate(zip(ubicados, columnas)):
            if u.x > x:  # entre bloques; justo antes del mic, una línea vertical a la altura de las barras
                separa = u.bloque.entrada and k > 0
                for y, linea in enumerate(lineas[1:], 1):  # el título se ubica aparte
                    if separa and y <= FILAS:
                        linea.append(" " * (u.x - x - len(SEPARADOR_MIC)) + SEPARADOR_MIC, style=VACIO)
                    else:
                        linea.append(" " * (u.x - x))
            estilo = "bold" if u.bloque.entrada else f"bold {ACENTO}"
            # El título puede ocupar el hueco hasta el siguiente bloque (en modo angosto no cabe);
            # el del mic, pegado a la derecha, se alinea a la derecha y crece hacia la izquierda
            if u.bloque.entrada:
                titulo = u.bloque.titulo
                inicio = max(lineas[0].cell_len + (1 if k else 0), u.x + u.ancho - len(titulo))
                titulo = titulo[:max(u.ancho, self.size.width - inicio)]
            else:
                libre = (ubicados[k + 1].x - 1 if k + 1 < len(ubicados) else self.size.width) - u.x
                titulo, inicio = u.bloque.titulo[:max(libre, u.ancho)], u.x
            lineas[0].append(" " * max(0, inicio - lineas[0].cell_len))
            lineas[0].append(titulo, style=f"{estilo} {CURSOR}" if u.bloque.clave == resaltado else estilo)
            for i, (etiqueta, col) in enumerate(zip(u.bloque.etiquetas, cols)):
                hueco = " " if i else ""
                for f in range(FILAS):
                    caracter, estilo_celda = col.celda(f)
                    if caracter == "·":
                        lineas[1 + f].append(hueco + " " * ((barra - 1) // 2) + "·" + " " * (barra // 2), style=VACIO)
                    else:
                        lineas[1 + f].append(hueco)
                        lineas[1 + f].append(caracter * barra, style=estilo_celda)
                lineas[FILAS + 1].append(hueco + f"{etiqueta[:barra]:>{barra}}",
                                         style=f"bold {ALERTA}" if col.satura else "dim")
                lineas[FILAS + 2].append(hueco + (col.numero if con_numeros else " " * barra),
                                         style=f"bold {ALERTA}" if col.satura else "")
            x = u.x + u.ancho
        # Todas las firmas llevan la geometría: si cambia (el ancho de la ventana), se redibuja todo
        geo = (self.size.width, barra, tuple(u.x for u in ubicados))
        firmas = [(geo, resaltado)] + [
            (geo, f, tuple(c.celda(f) for cols in columnas for c in cols)) for f in range(FILAS)
        ] + [(geo, tuple(c.satura for cols in columnas for c in cols)),
             (geo, tuple((c.satura, c.numero) for cols in columnas for c in cols))]
        return list(zip(firmas, lineas))

    def render_line(self, y: int) -> Strip:
        estilo = self.visual_style.rich_style
        if not self.bloques or y >= ALTO:
            return Strip.blank(self.size.width, estilo)
        if self._cache is None or self._cache_ancho != self.size.width:  # o armada con otro ancho
            self._cache, self._cache_ancho = self._lineas(), self.size.width
        firma, texto = self._cache[y]
        self._firmas[y] = firma
        texto = texto.copy()
        texto.truncate(self.size.width, overflow="crop")
        strip = Strip(texto.render(self.app.console), texto.cell_len)
        return strip.adjust_cell_length(self.size.width, estilo).apply_style(estilo)

    def refrescar(self) -> None:
        """Redibuja solo las líneas que cambiaron (también el título, si cambió la fila elegida)."""
        if not self.bloques or not self.display:
            return
        estado = self._leer()
        if estado == self._estado:
            return  # nada cambió a la vista: ni se arma el texto
        self._estado, self._cache, self._cache_ancho = estado, self._lineas(estado), self.size.width
        cambiadas = [Region(0, y, self.size.width, 1) for y, (firma, _) in enumerate(self._cache)
                     if self._firmas.get(y) != firma]
        if cambiadas:
            self.refresh(*cambiadas)

    # ---------- mouse ----------

    def on_click(self, evento: events.Click) -> None:
        """Clic en un bloque: elige su bus (o el mic) en la lista."""
        offset = evento.get_content_offset(self)
        if offset is None:
            return
        _, _, ubicados = self._geometria()
        for u in ubicados:
            if u.x <= offset.x < u.x + u.ancho:
                self.app.mezclador.elegir(u.bloque.clave)
                return
