"""La consola ELENA (Textual).

- Sigue el grafo en vivo con `monitor.seguir` y redibuja a lo más cada 50 ms: un solo cambio en
  PipeWire llega en 10 a 20 lotes seguidos.
- Los comandos (pactl y mic.sh) pasan por una cola y se ejecutan de uno en uno, en orden, en un
  hilo aparte. Si hay varios cambios de volumen pendientes para el mismo nodo, solo se manda el
  último.
- El volumen se muestra «optimista»: el valor pedido se ve al instante, sin esperar a que
  PipeWire lo confirme, y cada tecla parte del valor pedido y no del viejo.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Offset, Region
from textual.widgets import Footer, Static

from elena import acciones, afinar, audifonos, avisos, escenas, reglas, tope, vigilante, xruns
from elena import calibracion as cb
from elena.acciones import (
    cmd_aplicar, cmd_apagar_vm, cmd_mic, cmd_mover, cmd_mover_grabacion, cmd_quitar, cmd_silencio, cmd_steam,
    cmd_vm, cmd_volumen,
)
from elena.formato import nombre_corto, nombre_salida
from elena.medidor import Medidores, db_con_volumen
from elena.modelo import (
    CALIBRACION, PERFIL_51, BUSES, VISUAL, ErrorElena, Grafo, Nodo, Tipo, alimentador_visual, escalar, lineal_a_pct,
)
from elena.monitor import seguir
from elena.tui.estilo import ACENTO, ALERTA, APAGADO, AVISO, CURSOR, VACIO  # noqa: F401
from elena.tui.mezclador import Mezclador
from elena.tui.pantallas import (
    AccionesPantalla, AyudaPantalla, BalancePantalla, ConfirmarPantalla, DetallePantalla,
    EscenasPantalla, MoverPantalla, ProbarPantalla, ReglaPantalla, ResultadoPantalla, VisualPantalla,
)
from elena.tui.calibracion import (
    GUARDAR_TRAS, CalibracionPantalla, Lector, calibrada, linea_bocinas, linea_estereo,
)
from elena.tui.puente import Puente
from elena.tui.vista import Clave, bloques, clave_de, deseados, filas

OPTIMISTA_SEGUNDOS = 1.5
FPS_MEDIDORES = 20
VIGILANCIA_SEGUNDOS = 2.0  # cada cuánto se revisan los cortes (pw-top) y Steam
ETIQUETAS_BUS = {b.clave: b.etiqueta for b in BUSES}
ANCHO_ETIQUETA = 10        # la columna de las etiquetas de la cabecera


@dataclass(frozen=True)
class Lectura:
    """Nivel por canal en dBFS. En un bus es lo que **sale** (monitor + volumen del bus)."""

    pico: np.ndarray
    rms: np.ndarray
    retenido: np.ndarray
    satura: np.ndarray


class ElenaApp(App[None]):
    TITLE = "ELENA"
    ENABLE_COMMAND_PALETTE = False   # ^p no se usa y ocupaba lugar en el pie
    # Sin fondos propios: la consola deja ver el de la terminal (en kitty, translúcido sobre el
    # wallpaper) y usa sus 16 colores ANSI (los de Noctalia). Ver tui/estilo.py.
    CSS = """
    Screen, ModalScreen { background: ansi_default; }
    #cabecera { height: auto; padding: 0 1; border: round ansi_bright_black;
                border-title-color: ansi_green; border-title-style: bold; }
    #lista { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1;
             scrollbar-background: ansi_default; scrollbar-color: ansi_bright_black; }
    Mezclador { height: auto; }
    #puente { height: auto; padding: 0 1; border-top: solid ansi_bright_black; }
    #avisos { height: auto; max-height: 8; padding: 0 1; border-top: solid ansi_bright_black; }
    ModalScreen { align: center middle; }
    .dialogo { width: 80; max-width: 95%; height: auto; max-height: 85%;
               border: round ansi_green; background: ansi_default; padding: 1 2; }
    .dialogo VerticalScroll { scrollbar-size-vertical: 1; scrollbar-background: ansi_default;
                              scrollbar-color: ansi_bright_black; }
    .dialogo.alto { height: 85%; }
    .dialogo.ancho { width: 90; }
    .pie { color: ansi_bright_black; margin-top: 1; }
    Input { background: ansi_default; border: round ansi_bright_black; margin-top: 1; }
    Input:focus { border: round ansi_green; }
    OptionList, OptionList:focus { background: ansi_default; border: none; background-tint: ansi_default 0%; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted {
        background: ansi_black; color: ansi_default; text-style: bold; }
    Footer { background: ansi_default; }
    #destinos { height: auto; max-height: 20; margin-top: 1; }
    Canales { height: auto; margin-top: 1; }
    Ecualizador { height: auto; margin-top: 1; }
    #cal-cuerpo { height: auto; max-height: 100%; }
    NivelesDetalle { height: auto; margin: 1 0; }
    """
    BINDINGS = [
        # En el pie, solo lo esencial: mic, visual e internos tienen su tecla en la cabecera y en la ayuda
        Binding("e", "escenas", "Escenas"),
        Binding("a", "calibracion", "Calibración"),
        Binding("colon", "acciones", "Acciones", key_display=":"),
        Binding("question_mark", "ayuda", "Ayuda", key_display="?"),
        Binding("q", "quit", "Salir"),
        Binding("v", "mic('voz')", "Voz", show=False),
        Binding("n", "mic('ruido')", "Ruido", show=False),
        Binding("b", "mic('puente')", "Puente", show=False),
        Binding("w", "visual", "Visual", show=False),
        Binding("h", "audifonos('alternar')", "Solo audífonos", show=False),
        Binding("H", "audifonos('camino')", "Binaural / directo", show=False),
        Binding("i", "internos", "Internos", show=False),
    ]

    def __init__(self, grafo: Grafo | None = None, ejecutar: Callable[[list[str]], str] = acciones.ejecutar,
                 medidores: Medidores | None = None) -> None:
        """Sin `grafo`, sigue PipeWire en vivo y mide; con `grafo` (pruebas), usa ese, no lo
        cambia y solo mide si recibe `medidores`."""
        super().__init__(ansi_color=True)
        self.theme = "ansi-dark"
        self._cursor_visible = False
        if medidores is None and grafo is None:
            medidores = Medidores(iniciar=lambda c: self.run_worker(c, group="medidores", exit_on_error=False))
        self.medidores = medidores
        self._detalle: Nodo | None = None
        self.grafo = grafo if grafo is not None else Grafo()
        self._en_vivo = grafo is None
        self._ejecutar = ejecutar
        self._cola: asyncio.Queue[list[str]] = asyncio.Queue()
        self._optimista: dict[Clave, tuple[tuple[float, ...], float]] = {}
        self._redibujo_pendiente = False
        self.internos = False
        # Lo que toca el sistema fuera de PipeWire; las pruebas lo reemplazan
        self.steam: Callable[[], list[dict[str, str]]] = avisos.entornos_steam if self._en_vivo else list
        self.vm_corriendo: Callable[[], bool] = acciones.vm_corriendo
        self.lanzar = acciones.lanzar
        self.ejecutar_con_salida = acciones.ejecutar_con_salida
        self._steam: list[dict[str, str]] = []
        self.ruta_reglas = reglas.RUTA
        self.ruta_escenas = escenas.RUTA
        self.reglas: list[reglas.Regla] = []
        self._reglas_mtime: float | None = None
        self._vigilante: bool | None = None  # None: no se sabe (pruebas) y no se avisa
        self._cortes = xruns.Cortes()
        self.avisos: list[avisos.Aviso] = []
        # Calibración: el TOML releído si cambia, y el EQ en vivo (las pruebas lo reemplazan)
        self.lector_calibracion = Lector()
        self.lector_audifonos = audifonos.Lector()
        self.guardar_calibracion = afinar.guardar
        self.controles_vivos: Callable[[int], set[str]] = afinar.controles_vivos
        self._cal_sonando: cb.Calibracion | None = None
        self._cal_pendiente: cb.Calibracion | None = None
        self._cal_timer = None

    # ---------- armado ----------

    def compose(self) -> ComposeResult:
        yield Static(id="cabecera")
        with VerticalScroll(id="lista"):
            yield Mezclador(id="mezclador")
        yield Puente(id="puente")
        yield Static(id="avisos")
        yield Footer()

    @property
    def mezclador(self) -> Mezclador:
        return self.query_one(Mezclador)

    @property
    def puente(self) -> Puente:
        return self.query_one(Puente)

    def on_mount(self) -> None:
        self.query_one("#cabecera", Static).border_title = "ELENA"
        self.run_worker(self._ejecutor(), group="ejecutor", name="ejecutor")
        if self._en_vivo:
            self.run_worker(self._seguir(), group="monitor", name="monitor")
            self.run_worker(self._vigilar(), group="vigilancia", name="vigilancia")
        else:
            self._steam = self.steam()
        self.redibujar()
        self.mezclador.focus()
        self.set_interval(1 / FPS_MEDIDORES, self._tic)

    def on_unmount(self) -> None:
        if self.medidores is not None:
            self.medidores.cerrar()

    def _tic(self) -> None:
        self._ubicar_cursor()
        # Con una pantalla encima (detalle, mover, balance) la lista no se ve: no se redibuja.
        # El detalle tiene su propio medidor. Al volver, las firmas ponen al día lo que cambió.
        if len(self.screen_stack) > 1:
            return
        try:
            mezclador, puente = self.mezclador, self.puente
        except NoMatches:  # al cerrar, el temporizador puede disparar con la pantalla ya desmontada
            return
        if self.medidores is not None and self.medidores.activos:
            mezclador.refrescar_medidores()
        puente.refrescar()  # también sin medidores: resalta el bloque de la fila elegida

    # ---------- cursor de la terminal ----------

    def _ubicar_cursor(self) -> None:
        """Pone el cursor real de la terminal en el margen izquierdo de la fila elegida.

        Textual lo oculta y lo mueve a `cursor_position` en cada actualización; aquí se muestra
        para que la terminal lo anime al cambiar de fila (en kitty, `cursor_trail`). Se oculta
        si hay una pantalla encima o si la fila no está a la vista.
        """
        if self.is_headless or self._driver is None:
            return
        lista = self.query_one("#lista", VerticalScroll)
        m = self.mezclador
        linea = m._linea_cursor()
        destino: Offset | None = None
        if linea is not None and len(self.screen_stack) == 1:
            y = m.region.y + linea
            if lista.region.y <= y < lista.region.bottom:
                destino = Offset(lista.region.x, y)  # la columna del padding de la lista
        if destino is not None and destino != self.cursor_position:
            self.cursor_position = destino
            m.refresh(Region(0, linea, 1, 1))  # el cursor se escribe con la siguiente actualización
        if (destino is not None) != self._cursor_visible:
            self._cursor_visible = destino is not None
            self._driver.write("\x1b[?25h" if self._cursor_visible else "\x1b[?25l")
            self._driver.flush()

    async def _seguir(self) -> None:
        async for lote in seguir():
            self.grafo.aplicar(lote.objetos, completo=lote.reinicio)
            self._programar_redibujo()

    async def _vigilar(self) -> None:
        """Cada 2 s: los cortes nuevos (pw-top) y los procesos de Steam, para los avisos."""
        while True:
            self._steam = await asyncio.to_thread(self.steam)
            self._vigilante = await asyncio.to_thread(vigilante.corriendo)
            try:
                proceso = await asyncio.create_subprocess_exec(
                    "pw-top", "-b", "-n", "2", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                salida, _ = await proceso.communicate()
                self._cortes.actualizar(xruns.leer(salida.decode(errors="replace")))
            except OSError:
                pass
            self._actualizar_avisos()
            await asyncio.sleep(VIGILANCIA_SEGUNDOS)

    def _actualizar_avisos(self) -> None:
        self.avisos = avisos.calcular(self.grafo, self._steam, self._cortes.recientes(), len(self.reglas), self._vigilante)
        panel = self.query_one("#avisos", Static)
        panel.display = bool(self.avisos)
        t = Text()
        for i, a in enumerate(self.avisos):
            if i:
                t.append("\n")
            icono, estilo = {"alerta": ("⚠ ", f"bold {ALERTA}"), "aviso": ("⚠ ", AVISO)}.get(a.nivel, ("ℹ ", "dim"))
            t.append(icono, style=estilo)
            t.append(a.texto, style="" if a.nivel != "info" else "dim")
        panel.update(t)

    def _programar_redibujo(self) -> None:
        if not self._redibujo_pendiente:
            self._redibujo_pendiente = True
            self.set_timer(0.05, self.redibujar)

    def redibujar(self) -> None:
        self._redibujo_pendiente = False
        self._limpiar_optimista()
        self._recargar_reglas()
        self.query_one("#cabecera", Static).update(self._cabecera())
        self._actualizar_avisos()
        m = self.mezclador
        m.actualizar(filas(self.grafo, frozenset(m.marcadas), self.internos, self.reglas))
        self.puente.actualizar(bloques(self.grafo))
        if isinstance(self.screen, VisualPantalla):
            self.screen.actualizar()
        if self.medidores is not None:
            detalle = self.grafo.nodos.get(self._detalle.id) if self._detalle else None
            self.medidores.sincronizar(deseados(self.grafo, detalle))

    def lectura(self, nodo: Nodo, salida: bool = True) -> Lectura | None:
        """El nivel medido de un nodo; para un bus, con su volumen aplicado (salvo `salida=False`)."""
        nivel = self.medidores.nivel(clave_de(nodo)) if self.medidores is not None else None
        if nivel is None:
            return None
        if salida and nodo.tipo == Tipo.BUS:
            vols = self.volumenes_de(nodo)
            def aplicar(db: np.ndarray) -> np.ndarray:
                return db_con_volumen(db, vols, nodo.silencio)
            return Lectura(aplicar(nivel.pico), aplicar(nivel.rms), aplicar(nivel.retenido), nivel.satura)
        return Lectura(nivel.pico, nivel.rms, nivel.retenido, nivel.satura)

    def _cabecera(self) -> Text:
        """Una línea por tema, con su etiqueta alineada y su tecla al final: Tarjeta, Defecto, Mic,
        Visual, Audífonos («solo audífonos»), Bocinas (la calibración) y Estéreo (la compensación)."""
        g = self.grafo
        lineas: list[Text] = []

        def linea(etiqueta: str) -> Text:
            t = Text(f"{etiqueta:<{ANCHO_ETIQUETA}}", style="bold")
            lineas.append(t)
            return t

        def estado(t: Text, nombre: str, valor: str | None, tecla: str | None = None) -> None:
            if len(t) > ANCHO_ETIQUETA:
                t.append(" · ", style="dim")
            t.append(f"{nombre} ", style="dim")
            texto = "?" if valor is None else valor
            t.append(texto, style=f"bold {ACENTO}" if texto == "on" else
                     (f"bold {ALERTA}" if texto in ("?", "inválido", "mixto") else APAGADO))
            if tecla:
                t.append(f" [{tecla}]", style="dim")

        t = linea("Tarjeta")
        perfil = g.perfil()
        if perfil == PERFIL_51:
            t.append("5.1 ✓", style=ACENTO)
        else:
            t.append(f"⚠ perfil {perfil or 'sin tarjeta'}: no es 5.1", style=f"bold {ALERTA}")

        t = linea("Defecto")
        defecto = g.nodo(g.sink_por_defecto or "")
        fuente = g.nodo(g.fuente_por_defecto or "")
        t.append("salida ", style="dim")
        t.append(nombre_salida(defecto) if defecto else (g.sink_por_defecto or "-"), style="bold")
        t.append(" · entrada ", style="dim")
        t.append(fuente.visible if fuente else (g.fuente_por_defecto or "-"), style="bold")

        t = linea("Mic")
        if mic := g.mic():
            for nombre, valor, tecla in (("voz", mic.voz, "v"), ("ruido", mic.ruido, "n"), ("puente", mic.puente, "b")):
                estado(t, nombre, valor, tecla)
        else:
            t.append("sin mic virtual (¿falta mise run aplicar?)", style=AVISO)

        t = linea("Visual")
        if visual := g.visual():
            for clave, entra in visual.buses.items():
                estado(t, ETIQUETAS_BUS[clave].lower(), None if entra is None else ("on" if entra else "off"))
            oyentes = ", ".join(sorted({o.visible for o in visual.oyentes}))
            t.append("  ← ", style="dim")
            t.append(oyentes or "nadie escucha", style="" if oyentes else "dim")
            t.append("  [w]", style="dim")
        else:
            t.append(f"sin {VISUAL} (¿falta mise run aplicar?)", style=AVISO)

        t = linea("Audífonos")
        if (au := g.audifonos()) is not None:
            modo = self.lector_audifonos.leer()
            estado(t, "solo", "on" if modo.activo else "off", "h")
            t.append(" · ", style="dim")
            t.append(au.camino or f"{modo.camino}?", style="bold" if au.camino else f"bold {ALERTA}")
            t.append(" [H]", style="dim")
            t.append(f" · graves {modo.graves:+.0f} · LFE {modo.lfe:+.0f}", style="dim")
            if au.destinos:
                t.append("  → " + ", ".join(nombre_salida(d) for d in au.destinos), style=ACENTO)
            else:
                t.append(f"  → esperando a {nombre_corto(au.esperando) if au.esperando else '?'} (¿apagados?)",
                         style=AVISO)
            if modo.activo and len(au.enviados) < len(au.salidas):
                faltan = ", ".join(ETIQUETAS_BUS[c] for c in au.salidas if c not in au.enviados)
                t.append(f"  ⚠ faltan: {faltan}", style=f"bold {ALERTA}")
            elif not modo.activo and au.enviados:
                t.append("  ⚠ todavía en audífonos: " + ", ".join(ETIQUETAS_BUS[c] for c in au.enviados),
                         style=f"bold {ALERTA}")
        else:
            t.append("sin elena_audifonos (¿falta mise run aplicar?)", style=AVISO)

        lector = self.lector_calibracion
        cal = lector.leer()
        linea("Bocinas").append_text(linea_bocinas(g, cal, lector.error))
        if cal is not None and cal.estereo is not None:
            linea("Estéreo").append_text(linea_estereo(g, cal, self._vigilante))
        return Text("\n").join(lineas)

    # ---------- volumen optimista ----------

    def volumenes_de(self, nodo: Nodo) -> tuple[float, ...]:
        pedido = self._optimista.get(clave_de(nodo))
        if pedido and time.monotonic() - pedido[1] < OPTIMISTA_SEGUNDOS:
            return pedido[0]
        return nodo.volumenes

    def _limpiar_optimista(self) -> None:
        ahora = time.monotonic()
        actuales = {clave_de(n): n.volumenes for n in self.grafo.nodos.values()}
        for clave, (vols, cuando) in list(self._optimista.items()):
            real = actuales.get(clave)
            confirmado = real is not None and len(real) == len(vols) and all(abs(a - b) < 1e-4 for a, b in zip(real, vols))
            if confirmado or ahora - cuando >= OPTIMISTA_SEGUNDOS:
                del self._optimista[clave]

    # ---------- acciones (las llama el mezclador y las pantallas) ----------

    def encolar(self, cmd: list[str]) -> None:
        self._cola.put_nowait(cmd)

    async def _ejecutor(self) -> None:
        while True:
            pendientes = [await self._cola.get()]
            while not self._cola.empty():
                pendientes.append(self._cola.get_nowait())
            ultimo_volumen = {(c[1], c[2]): i for i, c in enumerate(pendientes) if c[1].endswith("-volume")}
            for i, cmd in enumerate(pendientes):
                if cmd[1].endswith("-volume") and ultimo_volumen[(cmd[1], cmd[2])] != i:
                    continue  # hay un volumen más nuevo para el mismo nodo
                try:
                    await asyncio.to_thread(self._ejecutar, cmd)
                except ErrorElena as e:
                    self.notify(str(e), severity="error", timeout=8)

    def fijar_volumenes(self, nodo: Nodo, vols: tuple[float, ...]) -> None:
        # El tope de Música y Juegos: no pasa de tu máximo, salvo que ya estuviera arriba
        actuales = self.volumenes_de(nodo)
        vols, aviso = tope.acotar_volumenes(self.grafo, nodo, vols, lineal_a_pct(max(actuales, default=0.0)))
        if aviso:
            self.notify(aviso, severity="warning", timeout=4)
        try:
            cmd = cmd_volumen(nodo, vols)
        except ErrorElena as e:
            self.notify(str(e), severity="error")
            return
        self._optimista[clave_de(nodo)] = (vols, time.monotonic())
        self.encolar(cmd)
        self.mezclador.refresh()

    def cambiar_volumen(self, nodo: Nodo, delta: float) -> None:
        actuales = self.volumenes_de(nodo)
        pct = min(max(lineal_a_pct(max(actuales)) + delta, 0.0), 150.0)
        self.fijar_volumenes(nodo, escalar(actuales, pct))

    def silenciar(self, nodo: Nodo) -> None:
        self.encolar(cmd_silencio(nodo, None))

    def elegir_destino(self, apps: list[Nodo]) -> None:
        destinos: list[tuple[str, str]] = []
        for d in BUSES:
            if bus := self.grafo.nodo(d.nodo):
                destinos.append((bus.nombre, f"{d.etiqueta}  ({bus.nombre}, {bus.canales or '?'} ch)"))
        for s in self.grafo.de_tipo(Tipo.SALIDA):
            destinos.append((s.nombre, f"{nombre_salida(s)}  (directo, {s.canales or '?'} ch)"))

        def mover(destino: str | None) -> None:
            if not destino or not (sink := self.grafo.nodo(destino)):
                return
            for app in apps:
                try:
                    self.encolar(cmd_mover(app, sink))
                except ErrorElena as e:
                    self.notify(str(e), severity="error")
            self.mezclador.marcadas.clear()
            self.notify(f"{', '.join(sorted({a.visible for a in apps}))} → {nombre_salida(sink)}")

        self.push_screen(MoverPantalla(apps, destinos), mover)

    def abrir_detalle(self, nodo: Nodo) -> None:
        self._detalle = nodo  # se mide mientras el detalle está abierto

        def cerrar(_: None) -> None:
            self._detalle = None
            self.redibujar()

        self.push_screen(DetallePantalla(nodo, self.grafo.crudo(nodo.id), self.grafo.destinos(nodo),
                                         self.grafo.origenes(nodo)), cerrar)
        self.redibujar()

    def abrir_balance(self, nodo: Nodo) -> None:
        self.push_screen(BalancePantalla(nodo))

    def action_mic(self, interruptor: str) -> None:
        mic = self.grafo.mic()
        if mic is None:
            self.notify("No hay mic virtual. Se carga con: mise run aplicar", severity="error")
            return
        encender = getattr(mic, interruptor) != "on"
        self.encolar(cmd_mic(interruptor, encender))

    def action_audifonos(self, orden: str) -> None:
        """h: «solo audífonos» on/off; H: binaural o directo."""
        actual = self.lector_audifonos.leer()
        if orden == "camino":
            orden = "directo" if actual.camino == "binaural" else "binaural"
        nuevo = audifonos.siguiente(actual, orden)
        try:
            hechos = audifonos.cambiar(self.grafo, nuevo, self.encolar)
        except ErrorElena as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        if orden in audifonos.CAMINOS:
            titulo = f"Audífonos: {nuevo.camino}"
        else:
            titulo = ("Solo audífonos: Música, Juegos y Sistema van a los audífonos" if nuevo.activo
                      else "Solo audífonos apagado: todo regresa a las bocinas")
        avisos_ = [h for h in hechos if h.startswith("⚠")]
        if nuevo.activo and self._vigilante is False:
            avisos_.append("⚠ El vigilante no corre: si PipeWire se reinicia, todo regresa a las bocinas.")
        self.notify("\n".join([titulo, *avisos_]), severity="warning" if avisos_ else "information",
                    timeout=6 if avisos_ else 3)
        self.redibujar()

    def action_visual(self) -> None:
        self.push_screen(VisualPantalla())

    def alternar_visual(self, clave: str) -> None:
        """Que un bus entre o no a elena_visual: alterna el silencio de su alimentador."""
        if self.grafo.nodo(VISUAL) is None or (alimentador := self.grafo.nodo(alimentador_visual(clave))) is None:
            self.notify(f"No existe {alimentador_visual(clave)}. Se carga con: mise run aplicar", severity="error")
            return
        self.encolar(cmd_silencio(alimentador, None))
        self.notify(f"{ETIQUETAS_BUS[clave]} {'entra a' if alimentador.silencio else 'sale de'} la mezcla de visualizadores")

    def elegir_origen(self, grabacion: Nodo) -> None:
        """Qué graba una grabación: la mezcla para visualizadores o el monitor de un bus."""
        origenes: list[tuple[str, str]] = []
        if self.grafo.nodo(VISUAL):
            origenes.append((VISUAL, "Visual  (elena_visual: la mezcla para visualizadores)"))
        for d in BUSES:
            if self.grafo.nodo(d.nodo):
                origenes.append((d.nodo, f"{d.etiqueta}  (lo que suena en {d.nodo})"))

        def mover(nombre: str | None) -> None:
            if not nombre or not (sink := self.grafo.nodo(nombre)):
                return
            try:
                self.encolar(cmd_mover_grabacion(grabacion, sink))
            except ErrorElena as e:
                self.notify(str(e), severity="error")
                return
            self.notify(f"{grabacion.visible} ← {nombre_salida(sink)}")

        self.push_screen(MoverPantalla([grabacion], origenes), mover)

    # ---------- calibración: el EQ en vivo ----------

    def action_calibracion(self) -> None:
        cal = self.lector_calibracion.leer()
        if cal is None:
            self.notify(self.lector_calibracion.error or "No hay local/calibracion.toml.", severity="error")
            return
        self._cal_sonando = cal
        aviso = None
        salida = self.grafo.nodo(CALIBRACION)
        if not calibrada(self.grafo):
            aviso = "La calibración no está activa: el EQ se guarda, pero no suena."
        elif salida is not None and self._en_vivo and "fl_eq1:b0" not in self.controles_vivos(salida.id):
            aviso = "La calibración que suena es de antes de las ranuras del EQ: se oirá tras mise run aplicar."
        self.push_screen(CalibracionPantalla(cal, aviso))

    def calibrar_eq(self, nueva: cb.Calibracion) -> None:
        """Manda en vivo solo los coeficientes que cambiaron (por la cola, en orden) y guarda después."""
        anterior = self._cal_sonando or nueva
        cambios = afinar.diferencias(cb.controles(anterior), cb.controles(nueva))
        salida = self.grafo.nodo(CALIBRACION)
        if cambios and salida is not None:
            self.encolar(["pw-cli", "set-param", str(salida.id), "Props", afinar.params(cambios)])
        self._cal_sonando = nueva
        self._cal_pendiente = nueva
        if self._cal_timer is not None:
            self._cal_timer.stop()
        self._cal_timer = self.set_timer(GUARDAR_TRAS, self.guardar_calibracion_ya)

    def guardar_calibracion_ya(self) -> None:
        if self._cal_timer is not None:
            self._cal_timer.stop()
            self._cal_timer = None
        cal, self._cal_pendiente = self._cal_pendiente, None
        if cal is None:
            return

        async def guardar() -> None:
            try:
                await asyncio.to_thread(self.guardar_calibracion, cal)
            except ErrorElena as e:
                self.notify(f"No se guardó el EQ: {e}", severity="error", timeout=8)
        self.run_worker(guardar(), group="calibracion")

    def action_internos(self) -> None:
        self.internos = not self.internos
        self.redibujar()

    def action_ayuda(self) -> None:
        self.push_screen(AyudaPantalla())

    # ---------- probar bocinas y el menú «:» ----------

    def abrir_probar(self, nodo: Nodo) -> None:
        self.push_screen(ProbarPantalla(nodo))

    def action_acciones(self) -> None:
        self.push_screen(AccionesPantalla(self.vm_corriendo(), bool(self._steam)), self._accion)

    def _accion(self, cual: str | None) -> None:
        if cual == "vm":
            registro = ("journalctl --user -u elena-vm" if acciones.servicio_vm_instalado()
                        else ".cache/tmp/consola-vm.log")
            self._separado(cmd_vm(), "vm", f"Arrancando la VM. Su salida va a {registro}")
        elif cual == "vm-apagar":
            self._separado(cmd_apagar_vm(), "vm-apagar", "Apagando Windows (como el botón de encendido). Puede tardar un minuto.")
        elif cual == "steam":
            self._separado(cmd_steam(), "steam", "Abriendo Steam (ELENA 5.1). Si ya estaba abierto sin ELENA, Steam avisa.")
        elif cual in ("aplicar", "quitar"):
            textos = {
                "aplicar": ("¿Aplicar ELENA?", "Corre «aplicar todo»: enlaza la config, pone la tarjeta en 5.1 y carga los buses y el mic virtual."),
                "quitar": ("¿Quitar ELENA?", "Corre «quitar»: borra los buses y el mic virtual y deja el audio como estaba antes de ELENA."),
            }
            titulo, texto = textos[cual]
            texto += ("\n\nReinicia PipeWire: el audio se corta un momento, y la VM puede quedarse sin sonido hasta reiniciarla."
                      + ("\nLa VM está corriendo." if self.vm_corriendo() else ""))
            self.push_screen(ConfirmarPantalla(titulo, texto), lambda si: si and self._correr_script(cual))

    def _separado(self, cmd: list[str], log: str, mensaje: str) -> None:
        try:
            self.lanzar(cmd, log)
        except ErrorElena as e:
            self.notify(str(e), severity="error")
            return
        self.notify(mensaje)

    def _correr_script(self, cual: str) -> None:
        cmd = cmd_aplicar() if cual == "aplicar" else cmd_quitar()
        self.notify(f"Corriendo «{cual}»…")

        async def correr() -> None:
            try:
                codigo, salida = await asyncio.to_thread(self.ejecutar_con_salida, cmd)
            except ErrorElena as e:
                codigo, salida = 1, str(e)
            self.push_screen(ResultadoPantalla(cual.capitalize(), codigo, salida))

        self.run_worker(correr(), group="script", exclusive=True)

    # ---------- escenas y reglas fijas ----------

    def _recargar_reglas(self) -> None:
        """Relee local/reglas.toml si cambió (también si lo editas a mano con la consola abierta)."""
        try:
            mtime = self.ruta_reglas.stat().st_mtime if self.ruta_reglas.exists() else None
        except OSError:
            return
        if mtime != self._reglas_mtime:
            self._reglas_mtime = mtime
            try:
                self.reglas = reglas.cargar(self.ruta_reglas)
            except ErrorElena as e:
                self.notify(str(e), severity="error", timeout=10)

    def escenas(self) -> dict[str, escenas.Escena]:
        try:
            return escenas.cargar(self.ruta_escenas)
        except ErrorElena as e:
            self.notify(str(e), severity="error", timeout=10)
            return {}

    def action_escenas(self) -> None:
        self.push_screen(EscenasPantalla())

    def aplicar_escena(self, nombre: str) -> None:
        escena = self.escenas().get(nombre)
        if escena is None:
            return
        topados: list[str] = []
        for cmd in escenas.comandos(self.grafo, escena, topados):
            self.encolar(cmd)
        self.notify(f"Escena «{nombre}»: {escena.resumen}")
        for aviso in topados:
            self.notify(aviso, severity="warning", timeout=4)

    def guardar_escena(self, nombre: str) -> None:
        try:
            todas = escenas.cargar(self.ruta_escenas)
            todas[nombre] = escenas.capturar(self.grafo, nombre)
            escenas.guardar(todas, self.ruta_escenas)
        except ErrorElena as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"Guardada «{nombre}»: {todas[nombre].resumen}")

    def borrar_escena(self, nombre: str) -> None:
        todas = self.escenas()
        if todas.pop(nombre, None) is not None:
            escenas.guardar(todas, self.ruta_escenas)
            self.notify(f"Borrada «{nombre}».")

    def abrir_regla(self, app_nodo: Nodo) -> None:
        try:
            criterio = reglas.criterio(app_nodo)
        except ErrorElena as e:
            self.notify(str(e), severity="error")
            return
        actual = reglas.destino(self.reglas, app_nodo)
        if app_nodo.tipo == Tipo.GRABACION:
            if self.grafo.nodo(VISUAL) is None:
                self.notify(f"No existe {VISUAL}: se carga con mise run aplicar.", severity="error")
                return
            buses = [(reglas.VISUAL, "Visual (la mezcla para visualizadores)")]
        else:
            buses = [(d.clave, d.etiqueta) for d in BUSES if self.grafo.nodo(d.nodo)]
        self.push_screen(ReglaPantalla(app_nodo, criterio, actual.bus if actual else None, buses),
                         lambda eleccion: self._fijar_regla(app_nodo, eleccion))

    def _fijar_regla(self, app_nodo: Nodo, eleccion: str | None) -> None:
        if not eleccion:
            return
        try:
            lista = reglas.cargar(self.ruta_reglas)
            if eleccion == "quitar":
                lista = reglas.quitar(lista, app_nodo)
                mensaje = f"Sin regla para {app_nodo.visible}."
            else:
                lista = reglas.fijar(lista, app_nodo, eleccion)
                if eleccion == reglas.VISUAL:
                    sink = self.grafo.nodo(VISUAL)
                    if sink and sink not in self.grafo.origenes(app_nodo):
                        self.encolar(cmd_mover_grabacion(app_nodo, sink))
                else:
                    sink = self.grafo.nodo(next(d.nodo for d in BUSES if d.clave == eleccion))
                    if sink and sink not in self.grafo.sinks_de(app_nodo):
                        self.encolar(cmd_mover(app_nodo, sink))
                mensaje = f"Regla: {lista[0].descripcion}."
                if self._vigilante is False:
                    mensaje += " Ojo: el vigilante no corre (mise run vigilante)."
            reglas.guardar(lista, self.ruta_reglas)
        except ErrorElena as e:
            self.notify(str(e), severity="error")
            return
        self.notify(mensaje)
        self.redibujar()
