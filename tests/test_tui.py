"""La consola: filas de la vista y la TUI de Textual en modo headless, sobre la foto de pw-dump."""

import numpy as np
import pytest

from elena.modelo import pct_a_lineal
from elena.tui.app import ElenaApp
from elena.tui.vista import clave_de, filas

# ---------- vista (sin Textual) ----------


def test_filas_en_orden(grafo):
    tipos = [(f.tipo, f.texto.plain.strip()[:20]) for f in filas(grafo) if f.tipo in ("bus", "app", "titulo")]
    assert [t for t, _ in tipos] == ["titulo", "bus", "bus", "app", "bus", "app", "bus", "bus", "titulo", "titulo"]
    assert tipos[0][1] == "Buses" and tipos[1][1].startswith("Música")
    assert tipos[3][1].startswith("▸ elena-prueba-pw")


def test_claves_estables(grafo):
    claves = [f.clave for f in filas(grafo) if f.clave]
    assert ("nodo", "elena_juegos") in claves
    assert ("app", "1331") in claves
    assert len(claves) == len(set(claves))


def test_marcadas_e_internos(grafo):
    (app,) = [f for f in filas(grafo) if f.clave == ("app", "1332")]
    marcada = [f for f in filas(grafo, frozenset({app.clave})) if f.clave == app.clave][0]
    assert "◆" in marcada.texto.plain
    assert not any(f.tipo == "interno" for f in filas(grafo))
    assert any(f.clave == ("nodo", "elena_juegos.salida") for f in filas(grafo, internos=True))


def test_chat_esperando(grafo):
    (chat,) = [f for f in filas(grafo) if f.clave == ("nodo", "elena_chat")]
    assert "esperando a usb-Corsair_CORSAIR_VIRTUOSO" in chat.texto.plain


# ---------- TUI ----------


@pytest.fixture
def consola(grafo):
    comandos: list[list[str]] = []
    app = ElenaApp(grafo=grafo, ejecutar=lambda cmd: comandos.append(cmd) or "")
    return app, comandos


async def test_cursor_y_volumen(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        assert app.mezclador.cursor == ("nodo", "elena_musica")
        await pilot.press("down", "down", "right")  # Juegos → elena-prueba-pw (serial 1331)
        await pilot.pause(0.1)
        assert app.mezclador.cursor == ("app", "1331")
    assert comandos[0][:3] == ["pactl", "set-sink-input-volume", "1331"]
    assert float(comandos[0][3]) == pytest.approx(pct_a_lineal(105), abs=1e-6)


async def test_teclas_seguidas_se_acumulan(consola):
    """Cada tecla parte del volumen pedido (optimista), no del que PipeWire aún no confirma."""
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("down", "down", "down", "left", "left", "left")  # Sistema: 50 % → 35 %
        await pilot.pause(0.2)
        nodo = app.grafo.nodo("elena_sistema")
        assert max(app.volumenes_de(nodo)) == pytest.approx(pct_a_lineal(35))
    assert float(comandos[-1][3]) == pytest.approx(pct_a_lineal(35), abs=1e-6)


async def test_silenciar_y_mic(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("s", "v", "n")
        await pilot.pause(0.1)
    assert comandos[0] == ["pactl", "set-sink-mute", "elena_musica", "toggle"]
    assert comandos[1][-2:] == ["voz", "on"]    # estaba en off
    assert comandos[2][-2:] == ["ruido", "off"]  # estaba en on


async def test_mover_app_a_musica(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("down", "down", "m")
        await pilot.pause(0.1)
        await pilot.press("enter")  # la primera opción es Música
        await pilot.pause(0.1)
    assert comandos == [["pactl", "move-sink-input", "1331", "elena_musica"]]


async def test_mover_marcadas(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("down", "down", "space")  # marca la de Juegos y baja
        await pilot.press("down", "space")          # Sistema → marca la de Sistema
        await pilot.press("m")
        await pilot.pause(0.1)
        await pilot.press("down", "enter")          # Juegos
        await pilot.pause(0.1)
    assert sorted(c[2] for c in comandos) == ["1331", "1332"]
    assert {c[3] for c in comandos} == {"elena_juegos"}


async def test_mover_sin_app_avisa(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("m")  # el cursor está en un bus
        await pilot.pause(0.1)
        assert len(app.screen_stack) == 1
    assert comandos == []


async def test_balance_por_canal(consola):
    app, comandos = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("down", "c", "left", "left")  # Juegos, FL −1 dB
        await pilot.pause(0.2)
        await pilot.press("escape")
    assert comandos[-1][:4] == ["pactl", "set-sink-volume", "elena_juegos", "0.891251"]
    assert comandos[-1][4:] == ["1.000000"] * 5


async def test_detalle_e_internos(consola):
    app, _ = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("d")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "DetallePantalla"
        await pilot.press("escape", "i")
        await pilot.pause(0.1)
        assert any(f.tipo == "interno" for f in app.mezclador.filas)


async def test_cursor_sobrevive_a_un_cambio(consola, grafo):
    app, _ = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.press("down", "down", "down", "down")  # elena-prueba-pulse
        await pilot.pause(0.1)
        assert app.mezclador.cursor == ("app", "1332")
        nav = grafo.nodos[next(i for i, n in grafo.nodos.items() if n.serial == 1332)]
        grafo.aplicar([{"id": nav.id, "info": None}])  # la app se cierra
        app.redibujar()
        await pilot.pause(0.1)
        assert app.mezclador.cursor is not None and app.mezclador.cursor != clave_de(nav)


# ---------- medidores ----------

from elena.medidor import Nivel  # noqa: E402
from elena.tui.puente import columna  # noqa: E402
from elena.tui.vista import bloques, deseados  # noqa: E402


def test_deseados(grafo):
    d = deseados(grafo)
    assert set(d) == {("nodo", "elena_juegos"), ("app", "1331"), ("nodo", "elena_sistema"),
                      ("app", "1332"), ("nodo", "elena_mic")}
    assert d[("nodo", "elena_juegos")].monitor and d[("nodo", "elena_juegos")].canales == 6
    assert d[("app", "1331")].target == "1331" and not d[("app", "1331")].monitor
    assert d[("app", "1331")].posiciones == ("FL", "FR", "FC", "LFE", "SL", "SR")
    assert ("nodo", "elena_musica") not in d  # bus sin apps: no se mide
    extra = grafo.nodo("elena_musica")
    assert ("nodo", "elena_musica") in deseados(grafo, extra)


def test_bloques_del_puente(grafo):
    """Un bloque por bus con apps (Música no tiene) y el mic al final; sin fila de canales en la lista."""
    bs = bloques(grafo)
    assert [(b.titulo, b.nodo.nombre, b.entrada) for b in bs] == [
        ("Juegos", "elena_juegos", False), ("Sistema", "elena_sistema", False), ("Mic", "elena_mic", True)]
    assert bs[0].etiquetas == ("FL", "FR", "FC", "LFE", "RL", "RR")
    assert len(bs[2].etiquetas) == 1
    assert not any(f.tipo == "canales" for f in filas(grafo))


def test_columna_vertical():
    # −30 dB = la mitad: 40 octavos, las 5 filas de abajo llenas y las de arriba vacías
    c = columna(-30.0, -30.0, False)
    assert c.alto == 40 and c.marca is None and c.numero == "-30"
    assert [c.celda(f)[0] for f in range(10)] == ["·"] * 5 + ["█"] * 5
    # pico sostenido en −3 dB, arriba de la barra: marca en la fila de 0 a −6, en alerta
    c = columna(-45.0, -3.0, True)
    assert c.marca == 0 and c.celda(0)[0] == "─" and c.satura
    assert c.celda(7)[0] == "▄"  # −45 dB = 20 octavos: la fila 7 (16 a 24 octavos) lleva 4 de 8
    assert columna(-90.0, -90.0, False).numero == " -∞"


class MedidoresFalsos:
    """Niveles fijos por clave; registra lo que se le pide medir."""

    def __init__(self, niveles: dict) -> None:
        self.niveles = niveles
        self.pedidos: list[set] = []

    def sincronizar(self, deseados) -> None:
        self.pedidos.append(set(deseados))

    def nivel(self, clave):
        return self.niveles.get(clave)

    @property
    def activos(self) -> int:
        return len(self.niveles)

    def cerrar(self) -> None:
        pass


def _nivel(dbs):
    n = Nivel.nuevo(len(dbs))
    n.actualizar(np.array(dbs, float), np.array(dbs, float) - 3, ahora=0.0)
    return n


def _linea_puente(app, y: int) -> str:
    return "".join(s.text for s in app.puente.render_line(y))


async def test_puente_muestra_la_salida_del_bus(grafo):
    """Juegos al 100 % sin silencio: la salida es igual a la entrada (−20 … −45 dB)."""
    falsos = MedidoresFalsos({("nodo", "elena_juegos"): _nivel([-20, -25, -30, -35, -40, -45])})
    app = ElenaApp(grafo=grafo, ejecutar=lambda c: "", medidores=falsos)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert app.puente.display
        assert _linea_puente(app, 0).split() == ["Música", "Juegos", "Sistema", "Mic"][1:]
        etiquetas = _linea_puente(app, 11).split()
        assert etiquetas[:6] == ["FL", "FR", "FC", "LFE", "RL", "RR"] and etiquetas[-1] == "M"
        assert _linea_puente(app, 12).split()[:6] == ["-20", "-25", "-30", "-35", "-40", "-45"]
        assert _linea_puente(app, 12).split()[6:] == ["-∞"] * 3  # Sistema y el mic, sin lectura
        # fila 4 (de −24 a −30 dB): FL (−20) llena, FR (−25) a 7/8, FC (−30) justo abajo y vacía
        barras = _linea_puente(app, 1 + 4)
        assert barras.startswith("-24 ███ ▇▇▇  ·  ")
        assert ("nodo", "elena_juegos") in falsos.pedidos[-1]


async def test_puente_resalta_y_elige_el_bus(consola):
    app, _ = consola
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("down", "down")  # Juegos → su app
        await pilot.pause(0.1)
        assert app.mezclador.clave_de_bloque == ("nodo", "elena_juegos")
        titulo = app.puente.render_line(0)
        assert any("Juegos" in s.text and s.style and s.style.bgcolor for s in titulo)
        # clic en el bloque del mic: el cursor va a su fila de Entradas
        x_mic = _linea_puente(app, 0).index("Mic")
        await pilot.click("#puente", offset=(1 + x_mic, 1))
        await pilot.pause(0.1)
        assert app.mezclador.cursor == ("nodo", "elena_mic")


async def test_salida_de_bus_silenciado_es_menos_infinito(grafo, dump):
    from conftest import nodo_crudo
    bus = nodo_crudo(dump, **{"node.name": "elena_juegos", "media.class": "Audio/Sink"})
    for p in bus["info"]["params"]["Props"]:
        if "mute" in p:
            p["mute"] = True
    grafo.aplicar([bus])
    falsos = MedidoresFalsos({("nodo", "elena_juegos"): _nivel([-20] * 6)})
    app = ElenaApp(grafo=grafo, ejecutar=lambda c: "", medidores=falsos)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        lectura = app.lectura(grafo.nodo("elena_juegos"))
        entrada = app.lectura(grafo.nodo("elena_juegos"), salida=False)
    assert float(lectura.pico.max()) <= -90 and float(entrada.pico.max()) == -20


async def test_titulos_conservan_su_estilo(consola):
    """Regresión: Text.render omite el estilo base si no hay tramos (render_line lo convierte)."""
    app, _ = consola
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause(0.1)
        m = app.mezclador
        y = next(i for i, f in enumerate(m.filas) if f.tipo == "titulo")
        assert any(s.style and s.style.underline for s in m.render_line(y) if s.text.strip())


# ---------- avisos y acciones ----------


class ProcesoFalso:
    def __init__(self):
        self.terminado = False

    def poll(self):
        return 0 if self.terminado else None

    def wait(self, timeout=None):
        import time
        limite = time.monotonic() + 5  # nunca colgar a pytest
        while not self.terminado and time.monotonic() < limite:
            time.sleep(0.01)
        return 0


@pytest.fixture(autouse=True)
def _detener_falso(monkeypatch):
    """detener() usa killpg sobre un PID real; con procesos falsos solo los marca."""
    import elena.tui.pantallas as pantallas
    real = pantallas.detener
    monkeypatch.setattr(pantallas, "detener",
                        lambda p: setattr(p, "terminado", True) if isinstance(p, ProcesoFalso) else real(p))


async def test_panel_de_avisos(grafo):
    app = ElenaApp(grafo=grafo, ejecutar=lambda c: "")
    app.steam = lambda: [{"HOME": "/x"}]  # Steam abierto sin ELENA
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        panel = app.query_one("#avisos")
        assert panel.display and "Steam está abierto sin ELENA" in str(panel.render())
        app.steam = lambda: []
        app._steam = []
        app.redibujar()
        await pilot.pause(0.1)
        assert not panel.display  # sin avisos, el panel no ocupa lugar


async def test_probar_bocina(consola):
    app, _ = consola
    lanzados = []

    def lanzar(cmd, log=None):
        lanzados.append(cmd)
        return ProcesoFalso()

    app.lanzar = lanzar
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("down", "p")          # Juegos
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "ProbarPantalla"
        await pilot.press("down", "enter")      # la primera bocina: FL
        await pilot.pause(0.1)
        await pilot.press("escape")             # corta lo que suena (no cierra)
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "ProbarPantalla"
        await pilot.press("escape")             # ahora sí cierra
        await pilot.pause(0.1)
        assert len(app.screen_stack) == 1
    assert lanzados[0][-2:] == ["elena_juegos", "1"]


async def test_probar_en_una_app_avisa(consola):
    app, _ = consola
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("down", "down", "p")  # una app
        await pilot.pause(0.1)
        assert len(app.screen_stack) == 1


async def test_menu_aplicar_con_confirmacion(consola):
    app, _ = consola
    corridos = []
    app.vm_corriendo = lambda: True
    app.ejecutar_con_salida = lambda cmd: corridos.append(cmd) or (0, "Listo")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("colon")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "AccionesPantalla"
        opciones = app.screen.query_one("#acciones")
        assert opciones.get_option("vm").disabled            # la VM ya corre
        assert not opciones.get_option("vm-apagar").disabled  # y se puede apagar
        opciones.highlighted = opciones.get_option_index("aplicar")
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "ConfirmarPantalla"
        await pilot.press("n")                               # no: no corre nada
        await pilot.pause(0.1)
        assert corridos == [] and len(app.screen_stack) == 1
        await pilot.press("colon")
        await pilot.pause(0.1)
        opciones = app.screen.query_one("#acciones")
        opciones.highlighted = opciones.get_option_index("quitar")
        await pilot.press("enter", "s")
        await pilot.pause(0.3)
        assert type(app.screen).__name__ == "ResultadoPantalla"
    assert corridos and corridos[0][-1].endswith("scripts/quitar.sh")


async def test_menu_steam_se_lanza_separado(consola):
    app, _ = consola
    lanzados = []
    app.vm_corriendo = lambda: False
    app.lanzar = lambda cmd, log=None: lanzados.append((cmd, log)) or ProcesoFalso()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("colon")
        await pilot.pause(0.1)
        opciones = app.screen.query_one("#acciones")
        assert opciones.get_option("vm-apagar").disabled     # sin VM, nada que apagar
        opciones.highlighted = opciones.get_option_index("steam")
        await pilot.press("enter")
        await pilot.pause(0.1)
    assert lanzados[0][0][-1].endswith("scripts/steam.sh") and lanzados[0][1] == "steam"


# ---------- escenas y reglas ----------


async def test_regla_desde_la_tui(consola, tmp_path):
    from elena import reglas as mod_reglas
    app, comandos = consola
    app.ruta_reglas = tmp_path / "reglas.toml"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        app.mezclador.cursor = ("app", "1332")       # elena-prueba-pulse, en Sistema
        await pilot.press("r")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "ReglaPantalla"
        opciones = app.screen.query_one("#buses")
        opciones.highlighted = opciones.get_option_index("juegos")
        await pilot.press("enter")
        await pilot.pause(0.2)
        fila = next(f for f in app.mezclador.filas if f.clave == ("app", "1332"))
        assert "⚑ Juegos" in fila.texto.plain
    assert mod_reglas.cargar(app.ruta_reglas)[0].bus == "juegos"
    assert ["pactl", "move-sink-input", "1332", "elena_juegos"] in comandos


async def test_escenas_desde_la_tui(consola, tmp_path):
    from elena import escenas as mod_escenas
    app, comandos = consola
    app.ruta_escenas = tmp_path / "escenas.toml"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause(0.1)
        await pilot.press("g")
        await pilot.press(*"tarde", "enter")
        await pilot.pause(0.2)
        assert "tarde" in mod_escenas.cargar(app.ruta_escenas)
        await pilot.press("enter")                   # aplica la resaltada (la única)
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1
    assert any(c[1] == "set-sink-volume" for c in comandos)


async def test_aviso_de_reglas_sin_vigilante(consola, tmp_path):
    from elena import reglas as mod_reglas
    app, _ = consola
    app.ruta_reglas = tmp_path / "reglas.toml"
    mod_reglas.guardar([mod_reglas.Regla("chat", app="Discord")], app.ruta_reglas)
    async with app.run_test(size=(120, 40)) as pilot:
        app._vigilante = False
        app.redibujar()
        await pilot.pause(0.1)
        assert any(a.clave == "vigilante" for a in app.avisos)
        app._vigilante = True
        app.redibujar()
        await pilot.pause(0.1)
        assert not any(a.clave == "vigilante" for a in app.avisos)


async def test_puente_mic_a_la_derecha_y_redibujo_al_cambiar_de_ancho(consola):
    """El mic va pegado al borde derecho. Regresión: al cambiar el ancho, las líneas sin cambios
    (las filas vacías y las etiquetas) se quedaban con la geometría anterior."""
    app, _ = consola
    async with app.run_test(size=(40, 40)) as pilot:  # angosto: barras de 2
        await pilot.pause(0.2)
        assert app.puente._geometria()[0] == 2
        await pilot.resize_terminal(120, 40)
        await pilot.pause(0.3)
        p = app.puente
        barra, _, ubicados = p._geometria()
        assert barra == 3 and ubicados[-1].bloque.entrada
        assert ubicados[-1].x + ubicados[-1].ancho == p.size.width  # el mic, en el borde derecho
        assert _linea_puente(app, 11).rstrip().endswith("M") and len(_linea_puente(app, 11).rstrip()) == p.size.width
        # cada línea se volvió a dibujar con el ancho nuevo (su firma lleva la geometría)
        assert len(p._firmas) == 13 and all(f[0][0] == p.size.width for f in p._firmas.values())


# ---------- mezcla para visualizadores (elena_visual) ----------


@pytest.fixture
def consola_visual(dump):
    from conftest import PROCESOS, con_visual
    from elena.modelo import Grafo
    comandos: list[list[str]] = []
    grafo = Grafo.desde_dump(con_visual(dump), proceso_de=PROCESOS.get)
    return ElenaApp(grafo=grafo, ejecutar=lambda cmd: comandos.append(cmd) or ""), comandos


async def test_cabecera_visual(consola_visual, consola):
    app, _ = consola_visual
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.1)
        cabecera = app._cabecera().plain
    assert "Visual    música on · juegos on · sistema off  ← nadie escucha  [w]" in cabecera
    sin, _ = consola
    assert "sin elena_visual" in sin._cabecera().plain


async def test_dialogo_visual_alterna(consola_visual):
    app, comandos = consola_visual
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("w")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "VisualPantalla"
        opciones = app.screen.query_one("#visual-buses")
        assert [opciones.get_option_at_index(i).id for i in range(3)] == ["musica", "juegos", "sistema"]
        assert "no entra" in str(opciones.get_option_at_index(2).prompt)
        assert "Noctalia Spectrum" in str(app.screen.query_one("#visual-oyentes").render())  # graba un bus directo
        await pilot.press("3")                       # sistema
        await pilot.press("enter")                   # la resaltada: música
        await pilot.pause(0.2)
        app.redibujar()                              # se pone al día sin cerrarse
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "VisualPantalla"
        await pilot.press("escape")
    assert comandos == [["pactl", "set-sink-input-mute", "9003", "toggle"],
                        ["pactl", "set-sink-input-mute", "9001", "toggle"]]


async def test_grabacion_mover_y_regla_visual(consola_visual, tmp_path):
    from elena import reglas as mod_reglas
    app, comandos = consola_visual
    app.ruta_reglas = tmp_path / "reglas.toml"
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        app.mezclador.cursor = ("app", "721")        # Noctalia, en «Grabaciones»
        await pilot.press("m")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "MoverPantalla"
        await pilot.press("enter")                   # la primera opción: Visual
        await pilot.pause(0.1)
        await pilot.press("r")
        await pilot.pause(0.1)
        assert type(app.screen).__name__ == "ReglaPantalla"
        await pilot.press("enter")                   # la única opción: Visual
        await pilot.pause(0.2)
        fila = next(f for f in app.mezclador.filas if f.clave == ("app", "721"))
        assert "⚑ Visual" in fila.texto.plain
    assert mod_reglas.cargar(app.ruta_reglas) == [mod_reglas.Regla("visual", app="Noctalia Spectrum")]
    assert comandos == [["pactl", "move-source-output", "721", "elena_visual.monitor"]] * 2
