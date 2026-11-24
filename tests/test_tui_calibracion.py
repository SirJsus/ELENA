"""La calibración en la consola: curva del EQ, estado estéreo, cabecera, marca en los buses y la pantalla `a`."""

import copy
from pathlib import Path

import pytest

from conftest import PROCESOS, nodo_crudo
from elena import calibracion as cb
from elena.modelo import Grafo
from elena.tui import calibracion as tc
from elena.tui.app import ElenaApp
from elena.tui.vista import filas

RAIZ = Path(__file__).resolve().parent.parent
TOML = RAIZ / "tests" / "datos" / "calibracion.toml"


def _cal(**cambios):
    return cb.desde_datos({**__import__("tomllib").loads(TOML.read_text()), **cambios})


def _volumen_salida(dump, bus, lineal):
    for p in nodo_crudo(dump, **{"node.name": f"{bus}.salida"})["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"] = [lineal] * len(p["channelVolumes"])


def _calibrado(dump, sistema_db=None):
    """La foto con los buses yendo a elena_salida, elena_salida creado y, si se pide, Sistema compensado."""
    for bus in ("elena_musica", "elena_juegos", "elena_sistema"):
        nodo_crudo(dump, **{"node.name": f"{bus}.salida"})["info"]["props"]["target.object"] = "elena_salida"
    fisico = nodo_crudo(dump, **{"node.name": "alsa_output.pci-0000_2f_00.4.analog-surround-51"})
    salida = copy.deepcopy(fisico)
    salida["id"] = 99999
    salida["info"]["props"].update({"node.name": "elena_salida", "node.description": "ELENA · Calibración"})
    dump.append(salida)
    if sistema_db is not None:
        _volumen_salida(dump, "elena_sistema", 10 ** (sistema_db / 20))
    return Grafo.desde_dump(dump, proceso_de=PROCESOS.get)


# ---------- Sin Textual ----------

def test_curva_plana_sin_bandas():
    filas_ = tc.curva((), ancho=40, alto=9)
    assert len(filas_) == 10                                   # 9 filas y el eje
    assert filas_[4].plain[4:] == "─" * 40                     # la línea de 0 dB, en la fila de en medio
    assert "1k" in filas_[-1].plain and "20" in filas_[-1].plain


def test_curva_sube_y_baja():
    pico = (cb.Banda("pico", 1000.0, 6.0, 1.0),)
    f = tc.curva(pico, ancho=60, alto=9)
    x = 4 + round(60 * __import__("math").log(1000 / 20) / __import__("math").log(1000) - 0.5)
    columna = [fila.plain[x] for fila in f[:9]]
    assert "█" in columna[:4] and columna[5:] == [" "] * 4       # lleno arriba de 0 dB, nada abajo
    assert f[0].plain.lstrip().startswith("+6 ")
    corte = tc.curva((cb.Banda("pico", 1000.0, -6.0, 1.0),), ancho=60, alto=9)
    columna = [fila.plain[x] for fila in corte[:9]]
    assert "█" in columna[5:] and columna[:4] == [" "] * 4


def test_curva_marca_la_banda_elegida():
    f = tc.curva((), ancho=60, alto=9, marca=1000.0)
    assert any("┊" in fila.plain for fila in f)


def test_estereo_se_lee_de_la_salida_del_bus(dump):
    g = _calibrado(dump, sistema_db=-15.0)
    texto = tc.texto_estereo(g, _cal(estereo={"compensacion": -15.0, "buses": ["musica", "sistema"]})).plain
    assert "Música 5.1" in texto and "Sistema ♪ estéreo -15 dB" in texto
    assert "sin compensación" in tc.texto_estereo(g, _cal()).plain


def test_lineas_de_la_cabecera(dump):
    cal = _cal(estereo={"compensacion": -15.0, "buses": ["musica", "sistema"]})
    sin = Grafo.desde_dump(copy.deepcopy(dump), proceso_de=PROCESOS.get)
    assert "inactiva" in tc.linea_bocinas(sin, cal).plain
    g = _calibrado(dump)
    bocinas = tc.linea_bocinas(g, cal).plain
    assert "calibración activa ✓" in bocinas and "cruce 110 Hz" in bocinas and "tope 136 %" in bocinas
    assert "EQ 0 común + 0 sub" in bocinas and bocinas.endswith("[a]")
    estereo = tc.linea_estereo(g, cal, vigilante=False).plain
    assert estereo.startswith("-15 dB al sonar estéreo: Música 5.1") and "el vigilante no corre" in estereo
    assert "⚠ roto" in tc.linea_bocinas(sin, None, error="roto").plain


def test_la_cabecera_muestra_la_de_cada_bus(dump):
    cal = _cal(estereo={"compensacion": -15.0, "buses": ["musica", "sistema"], "sistema": -10.0})
    estereo = tc.linea_estereo(_calibrado(dump), cal).plain
    assert estereo.startswith("Música -15 · Sistema -10 dB al sonar estéreo:")


def test_marca_estereo_en_la_fila_del_bus(dump):
    g = _calibrado(dump, sistema_db=-15.0)
    (sistema,) = [f for f in filas(g) if f.clave == ("nodo", "elena_sistema")]
    (musica,) = [f for f in filas(g) if f.clave == ("nodo", "elena_musica")]
    assert "♪ estéreo -15 dB" in sistema.texto.plain and "♪" not in musica.texto.plain


def test_lector_relee_solo_si_cambia(tmp_path):
    ruta = tmp_path / "calibracion.toml"
    ruta.write_text(TOML.read_text())
    lector = tc.Lector(ruta)
    assert lector.leer().nivel == -21.0
    ruta.write_text("roto [")
    import os
    os.utime(ruta, (1, 1))
    assert lector.leer() is None and "no es TOML" in lector.error


# ---------- La pantalla ----------

@pytest.fixture
def consola(dump):
    comandos, guardadas = [], []
    app = ElenaApp(grafo=_calibrado(dump), ejecutar=lambda cmd: comandos.append(cmd) or "")
    app.lector_calibracion = tc.Lector(TOML)
    app.guardar_calibracion = guardadas.append
    return app, comandos, guardadas


async def test_pantalla_agrega_y_sube_una_banda_en_vivo(consola):
    app, comandos, guardadas = consola
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        assert type(app.screen).__name__ == "CalibracionPantalla"
        await pilot.press("a", "right", "right")                 # una banda pico en 1 kHz, +1 dB
        await pilot.pause(0.1)
        eq = app.screen.query_one(tc.Ecualizador)
        assert eq.cal.eq == (cb.Banda("pico", 1000.0, 1.0, 1.0),)
        await pilot.press("shift+right", "t")                    # una sexta de octava arriba; estante grave
        await pilot.pause(0.1)
        assert eq.cal.eq[0].tipo == "estante_grave" and eq.cal.eq[0].frecuencia == pytest.approx(1122.5, abs=0.1)
        await pilot.press("escape")                               # al cerrar se guarda (sin esperar el segundo)
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1
    vivos = [c for c in comandos if c[:2] == ["pw-cli", "set-param"]]
    assert vivos and vivos[0][2] == "99999" and '"fl_eq1:b0"' in vivos[-1][4] and '"rr_eq1:a1"' in vivos[-1][4]
    assert guardadas and guardadas[-1].eq[0].tipo == "estante_grave"


async def test_pantalla_sub_quitar_y_deshacer(consola):
    app, comandos, guardadas = consola
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("s", "a", "left", "left")               # EQ del sub: pico en 60 Hz, −1 dB
        await pilot.pause(0.1)
        eq = app.screen.query_one(tc.Ecualizador)
        assert eq.cal.eq_sub == (cb.Banda("pico", 60.0, -1.0, 1.0),) and eq.cal.eq == ()
        await pilot.press("x")
        await pilot.pause(0.1)
        assert eq.cal.eq_sub == ()
        await pilot.press("a", "u")                                # deshacer: como al abrir
        await pilot.pause(0.1)
        assert eq.cal == tc.Lector(TOML).leer()
        await pilot.press("escape")
        await pilot.pause(0.2)
    assert any('"sub_eq1:b0"' in c[4] for c in comandos if c[:2] == ["pw-cli", "set-param"])


async def test_cabecera_muestra_la_calibracion(consola):
    app, _, _ = consola
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        cabecera = str(app.query_one("#cabecera").render())
        assert "Bocinas   calibración activa ✓" in cabecera and "Tarjeta   5.1 ✓" in cabecera


async def test_el_eq_se_ve_completo_y_crece_con_las_bandas(consola):
    # Regresión (2026-11-13): sin `height: auto`, el EQ quedaba en 8 filas y no se veía la curva
    app, _, _ = consola
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("a")
        await pilot.pause(0.2)
        eq = app.screen.query_one(tc.Ecualizador)
        alto = eq.region.height
        assert alto >= 14                                   # título, curva de 9 filas + eje y la lista
        await pilot.press("a", "a")
        await pilot.pause(0.2)
        assert eq.region.height == alto + 1                 # «Sin bandas» se cambia por 2 bandas


def test_la_fila_del_bus_muestra_el_camino_por_la_calibracion(dump):
    for bus in ("elena_musica", "elena_juegos", "elena_sistema"):
        nodo_crudo(dump, **{"node.name": f"{bus}.salida"})["info"]["props"]["target.object"] = "elena_salida"
    fisico = nodo_crudo(dump, **{"node.name": "alsa_output.pci-0000_2f_00.4.analog-surround-51"})
    salida = copy.deepcopy(fisico)
    salida["id"] = 99999
    salida["info"]["props"].update({"node.name": "elena_salida", "node.description": "ELENA · Calibración"})
    stream = copy.deepcopy(nodo_crudo(dump, **{"node.name": "elena_musica.salida"}))
    stream["id"] = 99998
    stream["info"]["props"].update({"node.name": "elena_salida.salida"})
    dump += [salida, stream]
    # Música.salida → elena_salida → elena_salida.salida → el físico
    musica_salida = nodo_crudo(dump, **{"node.name": "elena_musica.salida"})["id"]
    enlaces = [o for o in dump if o.get("type") == "PipeWire:Interface:Link"
               and o["info"]["output-node-id"] == musica_salida]
    hacia_fisico = copy.deepcopy(enlaces[0])
    hacia_fisico["id"] = 99997
    hacia_fisico["info"]["output-node-id"] = 99998                 # elena_salida.salida → el físico
    for o in enlaces:                                               # cada canal de Música → elena_salida
        o["info"]["input-node-id"] = 99999
    dump.append(hacia_fisico)
    g = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)
    (musica,) = [f for f in filas(g) if f.clave == ("nodo", "elena_musica")]
    assert "→ ELENA · Calibración → Bocinas 5.1" in musica.texto.plain
    assert "elena_musica" not in musica.texto.plain            # el nombre interno va en el detalle
