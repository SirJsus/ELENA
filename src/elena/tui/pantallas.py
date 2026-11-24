"""Pantallas modales: mover apps, detalle de un nodo, balance por canal y ayuda."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from elena.acciones import CANAL_PROBAR, cmd_probar, detener
from elena.formato import nombre_salida, posiciones, texto_canales, texto_db, texto_volumen
from elena.modelo import (
    BUSES, VISUAL, VISUAL_BUSES, ErrorElena, Nodo, Tipo, lineal_a_db, lineal_a_pct, pct_a_lineal,
)
from elena.tui.estilo import ACENTO, ALERTA, APAGADO, AVISO, CURSOR, VACIO  # noqa: F401

if TYPE_CHECKING:
    from elena.tui.app import ElenaApp

CERRAR = [Binding("escape,q", "dismiss(None)", "Cerrar")]
ETIQUETA_BUS = {b.clave: b.etiqueta for b in BUSES}


class MoverPantalla(ModalScreen[str | None]):
    """Elige el destino (node.name de un sink) para una o varias apps."""

    BINDINGS = CERRAR

    def __init__(self, apps: list[Nodo], destinos: list[tuple[str, str]]) -> None:
        super().__init__()
        self.apps = apps
        self.destinos = destinos  # (node.name, texto)

    def compose(self) -> ComposeResult:
        nombres = ", ".join(sorted({a.visible for a in self.apps}))
        with Vertical(classes="dialogo"):
            yield Label(Text.assemble("Mover ", (nombres, "bold"), " a…"))
            yield OptionList(*(Option(texto, id=nombre) for nombre, texto in self.destinos), id="destinos")
            yield Label("Enter elige · Esc cancela", classes="pie")

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        self.dismiss(evento.option.id)


class DetallePantalla(ModalScreen[None]):
    """Todo lo que se sabe de un nodo, con sus props crudas de pw-dump."""

    BINDINGS = [*CERRAR, Binding("d", "dismiss(None)", show=False)]

    def __init__(self, nodo: Nodo, crudo: dict | None, destinos: list[Nodo], origenes: list[Nodo]) -> None:
        super().__init__()
        self.nodo, self.crudo, self.destinos, self.origenes = nodo, crudo, destinos, origenes

    def compose(self) -> ComposeResult:
        n = self.nodo
        filas = [
            ("node.name", n.nombre), ("descripción", n.descripcion), ("media.class", n.clase),
            ("tipo", n.tipo.value), ("id · serial", f"{n.id} · {n.serial}"),
            ("app · proceso · pid", f"{n.app or '-'} · {n.proceso or '-'} · {n.pid or '-'}"),
            ("api", n.api or "-"), ("media.name", n.media or "-"),
            ("formato", f"{n.formato or '-'} · {n.tasa or '-'} Hz · {texto_canales(n)}"),
            ("posiciones", " ".join(n.posiciones) or "-"), ("estado", n.estado or "-"),
            ("volumen", texto_volumen(n)),
            ("por canal (dB)", " ".join(f"{c} {texto_db(v)}" for c, v in zip(posiciones(n), n.volumenes)) or "-"),
            ("target.object", n.objetivo or "-"),
            ("va a", ", ".join(d.nombre for d in self.destinos) or "-"),
            ("viene de", ", ".join(o.nombre for o in self.origenes) or "-"),
        ]
        props = ((self.crudo or {}).get("info") or {}).get("props") or {}
        contenido = Group(
            _tabla(filas),
            Text("\nprops (pw-dump)", style="bold underline"),
            _tabla([(clave, json.dumps(props[clave], ensure_ascii=False)) for clave in sorted(props)]),
        )
        with Vertical(classes="dialogo alto"):
            yield Label(Text(n.visible, style="bold"))
            if n.canales:
                yield NivelesDetalle(n)
            with VerticalScroll():
                yield Static(contenido)
            yield Label("↑↓ desplaza · Esc cierra", classes="pie")


class NivelesDetalle(Widget):
    """Nivel por canal en vivo. En un bus: lo que entra (monitor) y lo que sale (con su volumen)."""

    app: ElenaApp

    def __init__(self, nodo: Nodo) -> None:
        super().__init__()
        self.nodo = nodo

    def on_mount(self) -> None:
        self.set_interval(1 / 15, self.refresh)

    def render(self) -> Text:
        from elena.tui.mezclador import barra_nivel, texto_db_corto  # evita un import circular

        n = self.nodo
        es_bus = n.tipo.value == "bus"
        entrada = self.app.lectura(n, salida=False)
        salida = self.app.lectura(n) if es_bus else None
        etiquetas = list(n.posiciones) or [str(i + 1) for i in range(n.canales or 0)]
        columnas = 2 if es_bus else 1
        # Cada columna: barra + « -15 · -20  » (14); a la izquierda, la etiqueta del canal (5)
        ancho = max(8, (self.size.width - 5) // columnas - 14)
        t = Text("nivel en dBFS (pico sostenido · rms)", style="dim")
        if es_bus:
            t.append("\n     " + "entrada (lo que llega)".ljust(ancho + 14) + "salida (con el volumen del bus)", style="dim")
        if entrada is None:
            t.append("\n(esperando señal…)", style="dim italic")
            return t
        for i, c in enumerate(etiquetas[: len(entrada.pico)]):
            t.append(f"\n{c:>4} ", style="bold red" if entrada.satura[i] else "dim")
            for lectura in (entrada, salida)[:columnas]:
                if lectura is None:
                    continue
                t.append_text(barra_nivel(float(lectura.pico[i]), float(lectura.retenido[i]), ancho))
                t.append(f" {texto_db_corto(float(lectura.retenido[i]))} · {texto_db_corto(float(lectura.rms[i]))}  ")
        return t


def _tabla(filas: list[tuple[str, str]]) -> Table:
    """Dos columnas; los valores largos sin espacios (media.name, URLs) se cortan dentro de la suya."""
    tabla = Table.grid(padding=(0, 2))
    tabla.add_column(style="dim", no_wrap=True)
    tabla.add_column(overflow="fold")
    for clave, valor in filas:
        tabla.add_row(clave, str(valor))
    return tabla


class Canales(Widget, can_focus=True):
    """Lista de canales con su volumen en dB; ←/→ cambian el canal elegido."""

    PASO_DB = 0.5
    MIN_DB = -60.0
    MAX = pct_a_lineal(150)  # el tope de pavucontrol: +10.6 dB

    BINDINGS = [
        Binding("up,k", "elegir(-1)", show=False),
        Binding("down,j", "elegir(1)", show=False),
        Binding("left", "cambiar(-1)", "−0.5 dB"),
        Binding("right", "cambiar(1)", "+0.5 dB"),
        Binding("shift+left", "cambiar(-0.2)", show=False),
        Binding("shift+right", "cambiar(0.2)", show=False),
        Binding("r", "igualar", "Igualar"),
    ]

    def __init__(self, nodo: Nodo, al_cambiar) -> None:
        super().__init__()
        self.nodo = nodo
        self.etiquetas = posiciones(nodo)
        self.vols = list(nodo.volumenes)
        self.actual = 0
        self.al_cambiar = al_cambiar

    def render(self) -> Text:
        lineas = []
        for i, (c, v) in enumerate(zip(self.etiquetas, self.vols)):
            db = lineal_a_db(v)
            llenas = 0 if v <= 0 else max(0, min(30, round((db + 30) / 30 * 30)))  # -30..0 dB
            t = Text.assemble(f" {c:>4}  ", ("█" * llenas, ACENTO), ("░" * (30 - llenas), VACIO),
                              f"  {texto_db(v):>6} dB  ", (f"{lineal_a_pct(v):3.0f}%", "dim"))
            if i == self.actual:
                t.stylize(CURSOR)
            lineas.append(t)
        return Text("\n").join(lineas)

    def action_elegir(self, paso: int) -> None:
        self.actual = max(0, min(self.actual + paso, len(self.vols) - 1))
        self.refresh()

    def action_cambiar(self, pasos: float) -> None:
        v = self.vols[self.actual]
        db = (lineal_a_db(v) if v > 0 else self.MIN_DB) + pasos * self.PASO_DB
        self.vols[self.actual] = min(10 ** (max(db, self.MIN_DB) / 20), self.MAX)
        self.refresh()
        self.al_cambiar(tuple(self.vols))

    def action_igualar(self) -> None:
        self.vols = [max(self.vols)] * len(self.vols)
        self.refresh()
        self.al_cambiar(tuple(self.vols))


class BalancePantalla(ModalScreen[None]):
    """Balance por canal de un bus. Para calibrar bocinas está `elena calibracion` (un filter-chain)."""

    BINDINGS = CERRAR
    app: ElenaApp

    def __init__(self, nodo: Nodo) -> None:
        super().__init__()
        self.nodo = nodo

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label(Text.assemble("Balance de ", (nombre_salida(self.nodo), "bold")))
            yield Label("Ajuste de esta fuente. Las bocinas se calibran con elena calibracion.", classes="pie")
            yield Canales(self.nodo, lambda vols: self.app.fijar_volumenes(self.nodo, vols))
            yield Label("↑↓ canal · ←→ ±0.5 dB (shift: ±0.1) · r iguala · Esc cierra", classes="pie")


AYUDA = """\
[b]Consola ELENA[/b]

[b u]La lista[/b u] (buses, apps, entradas y grabaciones)
[b]↑ ↓[/b] · clic          elegir
[b]← →[/b] · rueda         volumen ±5 % (shift: ±1 %; la rueda, sobre la barra)
[b]s[/b]                   silenciar o activar
[b]m[/b]                   mover la app (o las marcadas) a otro bus; en una grabación, qué graba
[b]espacio[/b]             marcar apps para moverlas juntas
[b]r[/b]                   regla fija: esta app siempre a un bus (⚑); en una grabación, a Visual
[b]d[/b] · Enter · doble clic   detalle del nodo
[b]c[/b]                   balance por canal de un bus (en dB)
[b]p[/b]                   probar las bocinas del bus (todas con voz, o una con ruido rosa)

[b u]La cabecera[/b u] (cada línea, con su tecla)
[b]v n b[/b]               Mic: voz, supresión de ruido, puente
[b]w[/b]                   Visual: qué buses entran a elena_visual y quién la escucha
[b]h[/b]                   Audífonos: «solo audífonos» on/off (Música, Juegos y Sistema a donde va Chat)
[b]H[/b]                   Audífonos: binaural (surround virtual con HRTF) o directo (estéreo)
[b]a[/b]                   Bocinas: la calibración, el EQ en vivo con su curva, la compensación estéreo

[b u]En cualquier momento[/b u]
[b]e[/b]                   escenas: aplicar, guardar la actual, borrar
[b]:[/b]                   acciones: arrancar o apagar la VM, abrir Steam (ELENA 5.1), aplicar, quitar
[b]i[/b]                   mostrar u ocultar los nodos internos de ELENA
[b]?[/b]                   esta ayuda
[b]q[/b]                   salir

[b u]Cómo leerla[/b u]
Volumen en % como pavucontrol (cúbico): 50 % = −18 dB. Música y Juegos no pasan de tu máximo (tope).
M = silenciado · ≠ = canales con volúmenes distintos · barra roja = más de 100 %.
♪ estéreo −15 dB = el bus suena estéreo y la compensación lo baja (lo hace el vigilante).
Abajo, el puente: lo que sale por cada bocina de los buses con apps (con su volumen) y el mic,
de 0 a −60 dBFS; ─ = pico sostenido, etiqueta roja = satura. Clic en un bloque: elige su bus.
"""


class AyudaPantalla(ModalScreen[None]):
    BINDINGS = [*CERRAR, Binding("question_mark", "dismiss(None)", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Static(AYUDA)


# ---------- Probar bocina, acciones, confirmación y resultado ----------


class ProbarPantalla(ModalScreen[None]):
    """Probar las bocinas de un bus: todas con voz o una sola (ruido rosa; 50 Hz en el LFE).

    Usa scripts/probar-canales.sh. Elegir otra opción corta la que suena; Esc corta y, si no
    suena nada, cierra.
    """

    BINDINGS = [Binding("escape,q", "parar_o_cerrar", "Cortar / cerrar")]
    app: ElenaApp

    def __init__(self, nodo: Nodo) -> None:
        super().__init__()
        self.nodo = nodo
        self._proceso = None

    def compose(self) -> ComposeResult:
        opciones = [Option("Todas: una voz dice el nombre de cada bocina", id="todas")]
        for c in self.nodo.posiciones:
            if c in CANAL_PROBAR:
                detalle = "senoidal de 50 Hz" if c == "LFE" else "ruido rosa"
                opciones.append(Option(f"{c}  ({detalle}, 10 s)", id=c))
        with Vertical(classes="dialogo"):
            yield Label(Text.assemble("Probar bocinas de ", (nombre_salida(self.nodo), "bold"),
                                      (f" (volumen del bus: {texto_volumen(self.nodo)})", "dim")))
            yield OptionList(*opciones, id="canales")
            yield Label("Enter suena · Esc corta (o cierra si no suena nada)", id="estado-prueba", classes="pie")

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        self._parar()
        posicion = None if evento.option.id == "todas" else evento.option.id
        try:
            proceso = self.app.lanzar(cmd_probar(self.nodo, posicion))
        except ErrorElena as e:
            self.app.notify(str(e), severity="error")
            return
        self._proceso = proceso
        self.query_one("#estado-prueba", Label).update(f"Sonando: {posicion or 'todas'}… (Esc corta)")
        self.run_worker(self._esperar(proceso), group="prueba", exclusive=True)

    async def _esperar(self, proceso) -> None:
        await asyncio.to_thread(proceso.wait)
        if proceso is self._proceso:
            self._proceso = None
            self.query_one("#estado-prueba", Label).update("Listo. Enter suena otra · Esc cierra")

    def _parar(self) -> bool:
        if self._proceso is not None and self._proceso.poll() is None:
            detener(self._proceso)
            self._proceso = None
            return True
        return False

    def action_parar_o_cerrar(self) -> None:
        if self._parar():
            self.query_one("#estado-prueba", Label).update("Cortado. Enter suena otra · Esc cierra")
        else:
            self.dismiss(None)

    def on_unmount(self) -> None:
        self._parar()


class AccionesPantalla(ModalScreen[str | None]):
    """El menú «:»: arrancar o apagar la VM, abrir Steam (ELENA 5.1), aplicar y quitar."""

    BINDINGS = CERRAR

    def __init__(self, vm_corriendo: bool, steam_abierto: bool) -> None:
        super().__init__()
        self.vm_corriendo = vm_corriendo
        self.steam_abierto = steam_abierto

    def compose(self) -> ComposeResult:
        opciones = [
            Option("Arrancar la VM (Apple Music)" + ("  · ya está corriendo" if self.vm_corriendo else ""),
                   id="vm", disabled=self.vm_corriendo),
            Option("Apagar la VM (apaga Windows en orden)" + ("" if self.vm_corriendo else "  · no está corriendo"),
                   id="vm-apagar", disabled=not self.vm_corriendo),
            Option("Abrir Steam (ELENA 5.1)" + ("  · Steam ya está abierto" if self.steam_abierto else ""), id="steam"),
            Option("Aplicar ELENA (aplicar todo) · reinicia PipeWire", id="aplicar"),
            Option("Quitar ELENA (quitar) · reinicia PipeWire y deja el audio como antes", id="quitar"),
        ]
        with Vertical(classes="dialogo"):
            yield Label(Text("Acciones", style="bold"))
            yield OptionList(*opciones, id="acciones")
            yield Label("Enter elige · Esc cancela", classes="pie")

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        self.dismiss(evento.option.id)


class ConfirmarPantalla(ModalScreen[bool]):
    BINDINGS = [
        Binding("s,y,enter", "dismiss(True)", "Sí"),
        Binding("n,escape,q", "dismiss(False)", "No"),
    ]

    def __init__(self, titulo: str, texto: str) -> None:
        super().__init__()
        self.titulo, self.texto = titulo, texto

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label(Text(self.titulo, style="bold"))
            yield Static(self.texto)
            yield Label("s / Enter: sí · n / Esc: no", classes="pie")


class ResultadoPantalla(ModalScreen[None]):
    BINDINGS = CERRAR

    def __init__(self, titulo: str, codigo: int, salida: str) -> None:
        super().__init__()
        self.titulo, self.codigo, self.salida = titulo, codigo, salida

    def compose(self) -> ComposeResult:
        estado = Text("terminó bien", style=ACENTO) if self.codigo == 0 else Text(f"falló (código {self.codigo})", style=ALERTA)
        with Vertical(classes="dialogo alto"):
            yield Label(Text.assemble((self.titulo, "bold"), " · ", estado))
            with VerticalScroll():
                yield Static(Text(self.salida or "(sin salida)", overflow="fold"))
            yield Label("Esc cierra", classes="pie")


# ---------- Escenas y reglas fijas ----------


class EscenasPantalla(ModalScreen[None]):
    """Enter aplica la escena · g guarda la actual · x borra la elegida."""

    BINDINGS = [*CERRAR, Binding("g", "guardar", "Guardar la actual"), Binding("x,delete", "borrar", "Borrar")]
    app: ElenaApp

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label(Text("Escenas", style="bold"))
            yield OptionList(id="escenas")
            yield Input(placeholder="Nombre de la escena nueva (Enter guarda · Esc cancela)", id="nombre")
            yield Label("Enter aplica · g guarda la actual · x borra · Esc cierra", classes="pie")

    def on_mount(self) -> None:
        self.query_one("#nombre", Input).display = False
        self._llenar()

    def _llenar(self) -> None:
        lista = self.query_one("#escenas", OptionList)
        lista.clear_options()
        todas = self.app.escenas()
        if todas:
            lista.add_options(Option(Text.assemble((n, "bold"), (f"  {e.resumen}", "dim")), id=n) for n, e in todas.items())
            lista.highlighted = 0
        else:
            lista.add_option(Option("(sin escenas: g guarda la actual)", disabled=True))
        lista.focus()

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        if evento.option.id:
            self.app.aplicar_escena(evento.option.id)
            self.dismiss(None)

    def action_guardar(self) -> None:
        entrada = self.query_one("#nombre", Input)
        entrada.display = True
        entrada.value = ""
        entrada.focus()

    def on_input_submitted(self, evento: Input.Submitted) -> None:
        nombre = evento.value.strip()
        evento.input.display = False
        if nombre:
            self.app.guardar_escena(nombre)
        self._llenar()

    def action_borrar(self) -> None:
        lista = self.query_one("#escenas", OptionList)
        if lista.highlighted is None or not (nombre := lista.get_option_at_index(lista.highlighted).id):
            return

        def borrar(si: bool | None) -> None:
            if si:
                self.app.borrar_escena(nombre)
                self._llenar()

        self.app.push_screen(ConfirmarPantalla(f"¿Borrar la escena «{nombre}»?", "Se borra de local/escenas.toml."), borrar)

    def action_dismiss(self, resultado=None) -> None:
        entrada = self.query_one("#nombre", Input)
        if entrada.display:  # Esc con el nombre abierto: solo cancela el nombre
            entrada.display = False
            self.query_one("#escenas", OptionList).focus()
        else:
            self.dismiss(None)


class ReglaPantalla(ModalScreen[str | None]):
    """«Esta app siempre a este bus», o quitar su regla. Devuelve la clave del bus o «quitar»."""

    BINDINGS = CERRAR

    def __init__(self, app_nodo: Nodo, criterio: dict[str, str], actual: str | None, buses: list[tuple[str, str]]) -> None:
        super().__init__()
        self.app_nodo, self.criterio, self.actual, self.buses = app_nodo, criterio, actual, buses

    def compose(self) -> ComposeResult:
        opciones = [Option(f"Siempre a {etiqueta}" + ("  · regla actual" if clave == self.actual else ""), id=clave)
                    for clave, etiqueta in self.buses]
        if self.actual:
            opciones.append(Option("Quitar la regla", id="quitar"))
        como = " y ".join(f"{k} = «{v}»" for k, v in self.criterio.items())
        explicacion = f"Reconoce la app por {como}. La aplica el vigilante a cada app nueva."
        if self.app_nodo.tipo == Tipo.GRABACION:
            explicacion += " En una grabación, la regla la manda a la mezcla para visualizadores."
        with Vertical(classes="dialogo"):
            yield Label(Text.assemble("Regla fija para ", (self.app_nodo.visible, "bold")))
            yield Static(Text(explicacion, style="dim"))
            yield OptionList(*opciones, id="buses")
            yield Label("Enter elige · Esc cancela · para comodines, edita local/reglas.toml", classes="pie")

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        self.dismiss(evento.option.id)


# ---------- Mezcla para visualizadores (elena_visual) ----------


class VisualPantalla(ModalScreen[None]):
    """Qué buses entran a elena_visual y quién la escucha. Enter (o 1, 2, 3) alterna un bus.

    Se pone al día sola: la app llama a `actualizar` en cada redibujo mientras está abierta.
    """

    BINDINGS = [*CERRAR, Binding("w", "dismiss(None)", show=False),
                *(Binding(str(i + 1), f"alternar('{clave}')", show=False) for i, clave in enumerate(VISUAL_BUSES))]
    app: ElenaApp

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialogo"):
            yield Label(Text("Visualizadores", style="bold"))
            yield Static(Text("Lo que ven las apps que reaccionan al sonido (Waywallen, el espectro de Noctalia): "
                              "la mezcla elena_visual, antes del volumen de cada bus.", style="dim"))
            yield OptionList(id="visual-buses")
            yield Static(id="visual-oyentes")
            yield Label("Enter o 1 2 3 alterna · Esc cierra · r en una grabación: regla «visual»", classes="pie")

    def on_mount(self) -> None:
        self.actualizar()
        self.query_one("#visual-buses", OptionList).focus()

    def actualizar(self) -> None:
        lista = self.query_one("#visual-buses", OptionList)
        oyentes = self.query_one("#visual-oyentes", Static)
        grafo = self.app.grafo
        visual = grafo.visual()
        marcada = lista.highlighted
        lista.clear_options()
        if visual is None:
            lista.add_option(Option(Text(f"{VISUAL} no existe: falta «aplicar» (menú :)", style=ALERTA), disabled=True))
            oyentes.update("")
            return
        for i, (clave, entra) in enumerate(visual.buses.items()):
            texto = Text.assemble((f"{i + 1}  ", "dim"), (f"{ETIQUETA_BUS[clave]:<9}", "bold"))
            if entra is None:
                texto.append("sin alimentador", style=ALERTA)
            else:
                texto.append("entra" if entra else "no entra", style=f"bold {ACENTO}" if entra else APAGADO)
            lista.add_option(Option(texto, id=clave, disabled=entra is None))
        lista.highlighted = marcada if marcada is not None else 0
        t = Text("\nEscuchan: ", style="bold")
        t.append(", ".join(sorted({o.visible for o in visual.oyentes})) or "nadie",
                 style="" if visual.oyentes else "dim")
        # Grabaciones de un bus que podrían ir aquí (otros visualizadores, aún sin regla)
        otras = sorted({g.visible for g in grafo.de_tipo(Tipo.GRABACION)
                        if any(o.tipo == Tipo.BUS for o in grafo.origenes(g))})
        if otras:
            t.append("\nGraban un bus directo: ", style="dim")
            t.append(", ".join(otras))
            t.append("  (en su fila, m las mueve aquí y r fija la regla)", style="dim")
        oyentes.update(t)

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        if evento.option.id:
            self.action_alternar(evento.option.id)

    def action_alternar(self, clave: str) -> None:
        self.app.alternar_visual(clave)
