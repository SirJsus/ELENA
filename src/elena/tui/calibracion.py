"""La calibración en la consola: estado en la cabecera, la marca de estéreo y la pantalla del EQ.

- **Estado estéreo:** se lee del volumen de `<bus>.salida`, que es lo que mueve el vigilante: si
  está abajo de 0 dB, ese bus está compensado. Así la consola muestra lo que pasa de verdad, sin
  hablar con el vigilante.
- **La curva del EQ** se calcula con los mismos coeficientes que suenan (`calibracion.respuesta_db`).
- **La pantalla `a`** cambia el EQ en vivo (ranuras `bq_raw`: solo coeficientes, por la cola de
  comandos de la consola) y lo guarda en el TOML un segundo después del último cambio.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Label, Static

from elena import afinar
from elena import calibracion as cb
from elena.modelo import BUSES, CALIBRACION, ErrorElena, Grafo, lineal_a_db, lineal_a_pct
from elena.tui.estilo import ACENTO, ALERTA, APAGADO, AVISO, CURSOR, VACIO  # noqa: F401

if TYPE_CHECKING:
    from elena.tui.app import ElenaApp

COMPENSADO_DB = -0.05
GUARDAR_TRAS = 1.0             # s sin cambios antes de guardar en el TOML
PASO_DB = 0.5
PASO_OCTAVA = 1 / 6
PASO_Q = 1.25
TIPOS = ("pico", "estante_grave", "estante_agudo")
CORTO = {"pico": "pico", "estante_grave": "est. grave", "estante_agudo": "est. agudo"}
MARCAS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
MARCAS_SUB = (20, 30, 50, 70, 100, 150, 250)


# ---------- Lo que se lee (sin Textual) ----------

class Lector:
    """`local/calibracion.toml`, releído solo si cambia. None si no existe o está roto."""

    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = ruta or cb.TOML
        self._mtime: float | None = None
        self.cal: cb.Calibracion | None = None
        self.error: str | None = None

    def leer(self) -> cb.Calibracion | None:
        try:
            mtime = self.ruta.stat().st_mtime
        except OSError:
            self._mtime, self.cal, self.error = None, None, None
            return None
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self.cal, self.error = cb.leer(self.ruta), None
            except ErrorElena as e:
                self.cal, self.error = None, str(e)
        return self.cal


def compensacion_db(grafo: Grafo, clave: str) -> float | None:
    """Cuánto está bajando ahora la salida de un bus (dB, ≤ 0), o None si no existe."""
    d = next(b for b in BUSES if b.clave == clave)
    s = grafo.nodo(d.salida)
    if s is None or not s.volumenes:
        return None
    return lineal_a_db(max(s.volumenes))


def calibrada(grafo: Grafo) -> bool:
    """Si los buses de bocinas van a elena_salida y existe."""
    return grafo.nodo(CALIBRACION) is not None and any(
        (s := grafo.nodo(b.salida)) is not None and s.objetivo == CALIBRACION for b in BUSES)


def texto_estereo(grafo: Grafo, cal: cb.Calibracion | None) -> Text:
    """«Música 5.1 · Sistema ♪ estéreo −15 dB»: lo que la compensación hace ahora en cada bus."""
    t = Text()
    if cal is None or cal.estereo is None:
        return t.append("sin compensación estéreo", style="dim")
    for i, clave in enumerate(cal.estereo_buses):
        etiqueta = next(b.etiqueta for b in BUSES if b.clave == clave)
        db = compensacion_db(grafo, clave)
        if i:
            t.append(" · ", style="dim")
        t.append(f"{etiqueta} ", style="dim")
        if db is None:
            t.append("-", style="dim")
        elif db < COMPENSADO_DB:
            t.append(f"♪ estéreo {db:+.0f} dB", style=f"bold {AVISO}")
        else:
            t.append("5.1", style=ACENTO)
    return t


def linea_bocinas(grafo: Grafo, cal: cb.Calibracion | None, error: str | None = None) -> Text:
    """La línea «Bocinas» de la cabecera: si la calibración está activa, el cruce, el EQ y el tope."""
    t = Text()
    if error:
        return t.append(f"⚠ {error}", style=ALERTA)
    if cal is None:
        return t.append("sin calibración (local/calibracion.toml)", style="dim")
    if not calibrada(grafo):
        t.append("calibración ", style="dim")
        t.append("inactiva", style=APAGADO)
        t.append(" (mise run calibracion -- activar)", style="dim")
    else:
        t.append("calibración ", style="dim")
        t.append("activa ✓", style=f"bold {ACENTO}")
        t.append(f" · cruce {cal.cruce:g} Hz" if cal.cruce else " · sin cruce", style="dim")
        t.append(f" · EQ {len(cal.eq)} común + {len(cal.eq_sub)} sub", style="dim")
        if cal.tope is not None:
            t.append(f" · tope {lineal_a_pct(10 ** (cal.tope / 20)):.0f} %", style="dim")
    return t.append("  [a]", style="dim")


def linea_estereo(grafo: Grafo, cal: cb.Calibracion, vigilante: bool | None = None) -> Text:
    """La línea «Estéreo» de la cabecera: cuánto baja y qué hace ahora en cada bus."""
    t = Text(f"{cb.texto_estereo(cal)} al sonar estéreo: ", style="dim") if cal.estereo is not None else Text()
    t.append_text(texto_estereo(grafo, cal))
    if cal.estereo is not None and vigilante is False:
        t.append("  ⚠ el vigilante no corre: nadie la aplica", style=AVISO)
    return t


def curva(bandas: tuple[cb.Banda, ...], ancho: int = 64, alto: int = 9, fmin: float = 20.0, fmax: float = 20000.0,
          marca: float | None = None, marcas: tuple[int, ...] = MARCAS) -> list[Text]:
    """La respuesta del EQ en `alto` filas (dos «píxeles» por fila con ▀ ▄ █), eje log de fmin a fmax.
    Arriba de 0 dB en verde, abajo en amarillo; `marca` = la frecuencia de la banda elegida."""
    columnas = [fmin * (fmax / fmin) ** (i / (ancho - 1)) for i in range(ancho)]
    valores = [cb.respuesta_db(bandas, f) if bandas else 0.0 for f in columnas]
    rango = min(24.0, max(6.0, math.ceil(max((abs(v) for v in valores), default=0) / 3) * 3))
    pixeles = 2 * alto
    paso = 2 * rango / pixeles
    cero = pixeles // 2                       # el borde entre el píxel cero-1 y cero es 0 dB
    llenos = []
    for v in valores:
        n = round(min(abs(v), rango) / paso)
        llenos.append(set(range(cero - n, cero)) if v >= 0 else set(range(cero, cero + n)))
    col_marca = None if marca is None else round(math.log(marca / fmin) / math.log(fmax / fmin) * (ancho - 1))
    filas = []
    for r in range(alto):
        etiqueta = f"{rango:+3.0f} " if r == 0 else (" 0  " if 2 * r < cero <= 2 * r + 1 else
                                                     (f"{-rango:+3.0f} " if r == alto - 1 else "    "))
        t = Text(etiqueta.replace("+0 ", " 0 "), style="dim")
        for i, v in enumerate(valores):
            arriba, abajo = 2 * r in llenos[i], 2 * r + 1 in llenos[i]
            estilo = ACENTO if v >= 0 else AVISO
            if arriba and abajo:
                t.append("█", style=estilo)
            elif arriba:
                t.append("▀", style=estilo)
            elif abajo:
                t.append("▄", style=estilo)
            elif 2 * r < cero <= 2 * r + 1:
                t.append("─", style=VACIO)
            elif i == col_marca:
                t.append("┊", style="dim")
            else:
                t.append(" ")
        filas.append(t)
    eje = [" "] * ancho
    for f in marcas:
        if not fmin <= f <= fmax:
            continue
        x = round(math.log(f / fmin) / math.log(fmax / fmin) * (ancho - 1))
        texto = f"{f // 1000}k" if f >= 1000 else str(f)
        x = min(max(0, x - len(texto) // 2), ancho - len(texto))
        if all(c == " " for c in eje[max(0, x - 1):x + len(texto) + 1]):
            eje[x:x + len(texto)] = texto
    filas.append(Text("    " + "".join(eje), style="dim"))
    return filas


def resumen(cal: cb.Calibracion) -> Text:
    g = cb.ganancias(cal)
    t = Text()
    t.append(f"Tu 100 %: FL {cal.nivel:+.1f} dB hasta el DAC", style="bold")
    t.append(f" · LFE {cal.lfe:+.1f}" + (f" · cruce {cal.cruce:g} Hz" if cal.cruce else " · sin cruce"))
    if (p := cb.preamp(cal)) < 0:
        t.append(f" · preamp {p:+.1f} dB", style=AVISO)
    t.append("\nBocinas (sobre FL → hasta el DAC): ", style="dim")
    for i, b in enumerate(cb.BOCINAS):
        if i:
            t.append(" · ", style="dim")
        t.append(f"{b} {cal.bocinas[b].ganancia:+.1f}")
        t.append(f" ({g[b]:+.1f})", style="dim")
    return t


# ---------- La pantalla ----------

class Ecualizador(Widget, can_focus=True):
    """La curva y la lista de bandas de un EQ (común o del sub), con cursor."""

    BINDINGS = [
        Binding("up,k", "elegir(-1)", show=False),
        Binding("down,j", "elegir(1)", show=False),
        Binding("left", "ganancia(-1)", "−0.5 dB"),
        Binding("right", "ganancia(1)", "+0.5 dB"),
        Binding("shift+left", "frecuencia(-1)", "Hz −"),
        Binding("shift+right", "frecuencia(1)", "Hz +"),
        Binding("left_square_bracket", "q(-1)", "Q −", key_display="["),
        Binding("right_square_bracket", "q(1)", "Q +", key_display="]"),
        Binding("t", "tipo", "Tipo"),
        Binding("a,plus", "agregar", "Agregar"),
        Binding("x,delete", "quitar", "Quitar"),
        Binding("s", "cambiar_eq", "Común/sub"),
        Binding("u", "deshacer", "Deshacer"),
    ]

    def __init__(self, cal: cb.Calibracion, al_cambiar) -> None:
        super().__init__()
        self.cal = cal
        self.al_cambiar = al_cambiar
        self.sub = False
        self.actual = 0

    @property
    def bandas(self) -> tuple[cb.Banda, ...]:
        return self.cal.eq_sub if self.sub else self.cal.eq

    def render(self) -> Text:
        bandas = self.bandas
        elegida = bandas[self.actual] if bandas else None
        titulo = "EQ del sub (después de la suma de graves)" if self.sub else "EQ común (las 5 satélites)"
        ranuras = cb.RANURAS_EQ_SUB if self.sub else cb.RANURAS_EQ
        t = Text.assemble((titulo, f"bold {ACENTO}"), (f"  {len(bandas)} de {ranuras} bandas", "dim"),
                          ("   s: cambia a " + ("EQ común" if self.sub else "EQ del sub"), "dim"), "\n\n")
        rango = (20.0, 250.0, MARCAS_SUB) if self.sub else (20.0, 20000.0, MARCAS)
        ancho = max(30, min(72, (self.size.width or 72) - 6))
        t.append_text(Text("\n").join(curva(bandas, ancho, 9, rango[0], rango[1],
                                            elegida.frecuencia if elegida else None, rango[2])))
        t.append("\n\n")
        if not bandas:
            return t.append("  Sin bandas: a agrega una.", style="dim")
        filas = []
        for i, b in enumerate(bandas):
            fila = Text(f"  {i + 1}  {CORTO[b.tipo]:<10} {b.frecuencia:>7.0f} Hz  {b.ganancia:+5.1f} dB  Q {b.q:.2f}")
            if i == self.actual:
                fila.stylize(CURSOR)
            filas.append(fila)
        return t.append_text(Text("\n").join(filas))

    def _cambiar(self, bandas: list[cb.Banda]) -> None:
        nueva = replace(self.cal, eq_sub=tuple(bandas)) if self.sub else replace(self.cal, eq=tuple(bandas))
        try:
            nueva = afinar.validar(nueva)
        except ErrorElena as e:
            self.app.notify(str(e).split(": ", 1)[-1], severity="warning", timeout=3)
            return
        self.cal = nueva
        self.actual = min(self.actual, max(len(self.bandas) - 1, 0))
        self.refresh(layout=True)
        self.al_cambiar(nueva)

    def _banda(self, **cambios) -> None:
        if not self.bandas:
            self.app.notify("No hay bandas: a agrega una.", severity="warning", timeout=2)
            return
        bandas = list(self.bandas)
        bandas[self.actual] = replace(bandas[self.actual], **cambios)
        self._cambiar(bandas)

    def action_elegir(self, paso: int) -> None:
        self.actual = max(0, min(self.actual + paso, len(self.bandas) - 1))
        self.refresh(layout=True)

    def action_ganancia(self, pasos: int) -> None:
        if self.bandas:
            self._banda(ganancia=round(self.bandas[self.actual].ganancia + pasos * PASO_DB, 2))

    def action_frecuencia(self, pasos: int) -> None:
        if self.bandas:
            f = self.bandas[self.actual].frecuencia * 2 ** (pasos * PASO_OCTAVA)
            self._banda(frecuencia=round(min(max(f, 20.0), 20000.0), 1))

    def action_q(self, pasos: int) -> None:
        if self.bandas:
            q = self.bandas[self.actual].q * PASO_Q ** pasos
            self._banda(q=round(min(max(q, 0.1), 10.0), 3))

    def action_tipo(self) -> None:
        if self.bandas:
            tipo = TIPOS[(TIPOS.index(self.bandas[self.actual].tipo) + 1) % len(TIPOS)]
            self._banda(tipo=tipo, q=cb.Q_POR_TIPO[tipo])

    def action_agregar(self) -> None:
        if len(self.bandas) >= (cb.RANURAS_EQ_SUB if self.sub else cb.RANURAS_EQ):
            self.app.notify("No caben más bandas.", severity="warning", timeout=2)
            return
        nueva = cb.Banda("pico", 60.0 if self.sub else 1000.0, 0.0, 1.0)
        self.actual = len(self.bandas)
        self._cambiar([*self.bandas, nueva])

    def action_quitar(self) -> None:
        if self.bandas:
            bandas = list(self.bandas)
            bandas.pop(self.actual)
            self._cambiar(bandas)

    def action_cambiar_eq(self) -> None:
        self.sub = not self.sub
        self.actual = 0
        self.refresh(layout=True)

    def action_deshacer(self) -> None:
        pantalla = self.screen
        if isinstance(pantalla, CalibracionPantalla):
            self.cal = pantalla.al_abrir
            self.actual = 0
            self.refresh(layout=True)
            self.al_cambiar(self.cal)


class CalibracionPantalla(ModalScreen[None]):
    """`a`: el resumen de la calibración, la compensación estéreo en vivo y el EQ."""

    BINDINGS = [Binding("escape,q", "cerrar", "Cerrar")]
    app: ElenaApp

    def __init__(self, cal: cb.Calibracion, aviso: str | None = None) -> None:
        super().__init__()
        self.al_abrir = cal
        self.aviso = aviso

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo ancho"):
            yield Label(Text("Calibración", style="bold"))
            with VerticalScroll(id="cal-cuerpo"):     # si la terminal es chica, se desplaza
                yield Static(id="cal-resumen")
                yield Static(id="cal-estereo")
                if self.aviso:
                    yield Label(Text(f"⚠ {self.aviso}", style=AVISO))
                yield Ecualizador(self.al_abrir, self.app.calibrar_eq)
            yield Label("↑↓ banda · ←→ ±0.5 dB · shift+←→ frecuencia · [ ] Q · t tipo · a agrega · x quita\n"
                        "s común/sub · u deshace · Esc cierra · en vivo; se guarda en local/calibracion.toml",
                        classes="pie")

    def on_mount(self) -> None:
        self.actualizar()
        self.query_one(Ecualizador).focus()
        self.set_interval(0.5, self.actualizar)

    def actualizar(self) -> None:
        cal = self.query_one(Ecualizador).cal
        self.query_one("#cal-resumen", Static).update(resumen(cal))
        t = Text("Estéreo: ", style="dim")
        if cal.estereo is not None:
            t.append(f"{cb.texto_estereo(cal)} mientras suena estéreo · ", style="dim")
        t.append_text(texto_estereo(self.app.grafo, cal))
        self.query_one("#cal-estereo", Static).update(t)

    def action_cerrar(self) -> None:
        self.app.guardar_calibracion_ya()
        self.dismiss(None)
