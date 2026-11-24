"""Generador de la calibración: TOML → filter-chain."""

import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from elena import calibracion as cb
from elena.modelo import ErrorElena

RAIZ = Path(__file__).resolve().parent.parent

# Unos valores medidos de verdad (2026-11-13), para las pruebas
DATOS = {
    "evitar_saturacion": True, "nivel": -21.0,
    "bocinas": {"fl": {"ganancia": 0.0}, "fr": {"ganancia": 0.5}, "fc": {"ganancia": 5.5},
                "rl": {"ganancia": 6.0}, "rr": {"ganancia": 9.0}, "sub": {"ganancia": 8.5}},
    "lfe": {"nivel": 10.0}, "cruce": {"frecuencia": 110}, "maximo": {"tope": 8.0, "perilla": ""},
    "eq": {"bandas": []}, "eq_sub": {"bandas": []},
}


def _datos(**cambios):
    d = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DATOS.items()}
    d["bocinas"] = {k: dict(v) for k, v in DATOS["bocinas"].items()}
    d.update(cambios)
    return d


def test_el_toml_de_ejemplo_es_valido_y_neutro():
    cal = cb.leer(RAIZ / "config" / "calibracion.ejemplo.toml")
    assert {b.ganancia for b in cal.bocinas.values()} == {0.0} and cal.eq == () and cal.eq_sub == ()
    assert cb.preamp(cal) == 0.0 and cb.avisos(cal) == []         # el LFE +10 cabe en el nivel −10


def test_el_toml_de_prueba_da_las_ganancias_medidas():
    cal = cb.leer(RAIZ / "tests" / "datos" / "calibracion.toml")
    assert cal == cb.desde_datos(DATOS)
    assert cb.ganancias(cal) == {"fl": -21.0, "fr": -20.5, "fc": -15.5, "rl": -15.0, "rr": -12.0, "sub": -12.5}
    assert cb.preamp(cal) == 0.0                     # todas negativas: el preamp no baja nada
    assert cal.tope == 8.0 and cal.cruce == 110.0


def test_caminos_y_preamp_de_d2():
    cal = cb.desde_datos(_datos(nivel=0.0))
    c = cb.caminos(cal)
    assert c["rr"] == 9.0 and c["lfe"] == 18.5 and c["graves→sub"] == 8.5
    assert cb.preamp(cal) == -18.5                   # el camino más alto, el LFE
    assert cb.ganancias(cal)["fl"] == -18.5 and cb.ganancias(cal)["sub"] == -10.0
    assert any("lfe subía +18.5" in a for a in cb.avisos(cal))
    # Con el interruptor apagado no baja nada, solo avisa
    libre = cb.desde_datos(_datos(nivel=0.0, evitar_saturacion=False))
    assert cb.preamp(libre) == 0.0 and cb.ganancias(libre)["fl"] == 0.0
    assert any("puede saturar" in a for a in cb.avisos(libre))


def test_el_eq_cuenta_en_d2_con_sus_bandas_positivas():
    eq = {"bandas": [{"tipo": "pico", "frecuencia": 1000, "ganancia": 4.0},
                     {"tipo": "estante_agudo", "frecuencia": 8000, "ganancia": -3.0}]}
    cal = cb.desde_datos(_datos(nivel=-10.0, eq=eq))
    assert cb.caminos(cal)["rr"] == pytest.approx(-10 + 9 + 4)     # solo cuenta la banda positiva
    assert cb.caminos(cal)["fl"] == pytest.approx(-6.0)
    assert cb.preamp(cal) == -8.5                                  # el más alto sigue siendo el LFE
    assert cal.eq[0].q == 1.0 and cal.eq[1].q == 0.707   # la Q por omisión de cada tipo


def test_retardos_en_ms_y_en_distancias():
    d = _datos()
    d["bocinas"]["fl"] = {"ganancia": 0.0, "distancia": 3.0}
    d["bocinas"]["rl"] = {"ganancia": 6.0, "distancia": 1.285}
    d["bocinas"]["fc"] = {"ganancia": 5.5, "retardo": 2.5}
    r = cb.retardos(cb.desde_datos(d))
    assert r["fl"] == 0.0                                       # la más lejana
    assert r["rl"] == pytest.approx((3.0 - 1.285) / 343)        # 5 ms
    assert r["fc"] == pytest.approx(0.0025) and r["fr"] == 0.0


@pytest.mark.parametrize("cambio,mensaje", [
    ({"nivel": -70}, "fuera de rango"),
    ({"nivel": "fuerte"}, "número"),
    ({"evitar_saturacion": "sí"}, "true o false"),
    ({"volumen": 3}, "no conozco volumen"),
    ({"cruce": {"frecuencia": 300}}, "fuera de rango"),
    ({"cruce": {}}, "necesita frecuencia"),
    ({"lfe": {"nivel": 30}}, "fuera de rango"),
    ({"eq": {"bandas": [{"tipo": "campana", "frecuencia": 100, "ganancia": 1}]}}, "no existe"),
    ({"eq": {"bandas": [{"tipo": "pico", "frecuencia": 100}]}}, "falta"),
    ({"eq": {"bandas": [{"tipo": "pico", "frecuencia": 100, "ganancia": 1, "q": 20}]}}, "fuera de rango"),
    ({"maximo": {"tope": 12}}, "fuera de rango"),
])
def test_valida(cambio, mensaje):
    with pytest.raises(ErrorElena, match=mensaje):
        cb.desde_datos(_datos(**cambio))


def test_valida_bocinas():
    d = _datos()
    d["bocinas"]["fl"] = {"ganancia": 0.0, "retardo": 1.0, "distancia": 2.0}
    with pytest.raises(ErrorElena, match="no los dos"):
        cb.desde_datos(d)
    d = _datos()
    d["bocinas"]["sub"] = {"ganancia": 0.0, "cruce": 80}
    with pytest.raises(ErrorElena, match="no conozco cruce"):
        cb.desde_datos(d)
    d = _datos()
    d["bocinas"]["fl"] = {"ganancia": 0.0, "distancia": 18.0}
    d["bocinas"]["rl"] = {"ganancia": 0.0, "distancia": 0.5}      # la cercana: 51 ms
    with pytest.raises(ErrorElena, match="máximo es 50 ms"):
        cb.desde_datos(d)
    d = _datos()
    d["bocinas"]["centro"] = {"ganancia": 1.0}
    with pytest.raises(ErrorElena, match="no conozco centro"):
        cb.desde_datos(d)


def test_cruce_por_bocina_y_rango_completo():
    d = _datos()
    d["bocinas"]["rr"] = {"ganancia": 9.0, "cruce": 120}
    d["bocinas"]["fc"] = {"ganancia": 5.5, "cruce": 0}
    cal = cb.desde_datos(d)
    assert cal.cruce_de("fl") == 110.0 and cal.cruce_de("rr") == 120.0 and cal.cruce_de("fc") is None
    c = cb.controles(cal)
    assert c["rr_altos1:Freq"] == 120.0 and "fc_altos1:Freq" not in c
    sin = cb.desde_datos({k: v for k, v in DATOS.items() if k != "cruce"})
    assert not any(k.endswith(":Freq") for k in cb.controles(sin))
    assert "graves→sub" not in cb.caminos(sin)
    assert any("Sin cruce" in a for a in cb.avisos(sin))


def test_controles_con_nombres_del_plan():
    c = cb.controles(cb.desde_datos(DATOS))
    assert c["fl_ganancia:Mult"] == pytest.approx(10 ** (-21 / 20))
    assert c["sub_ganancia:Mult"] == pytest.approx(10 ** (-12.5 / 20))
    assert c["lfe_nivel:Mult"] == pytest.approx(10 ** 0.5)
    assert c["rr_retardo:Delay (s)"] == 0.0
    assert {c[f"fl_{n}:Freq"] for n in ("altos1", "altos2", "bajos1", "bajos2")} == {110.0}


def _enlaces(texto):
    return re.findall(r'output = "([^"]+)" input = "([^"]+)"', texto)


def test_conf_grafo_bien_formado():
    eq = {"bandas": [{"tipo": "pico", "frecuencia": 1000, "ganancia": -2.0}]}
    texto = cb.conf(cb.desde_datos(_datos(eq=eq, eq_sub={"bandas": [{"tipo": "estante_grave", "frecuencia": 40,
                                                                       "ganancia": -1.0}]})))
    nodos = re.findall(r"name = (\w+) label = (\w+)", texto)
    nombres = [n for n, _ in nodos]
    assert len(nombres) == len(set(nombres))                       # sin nombres repetidos
    enlaces = _enlaces(texto)
    # Todo enlace une nodos que existen
    for salida, entrada in enlaces:
        assert salida.split(":")[0] in nombres and entrada.split(":")[0] in nombres
    # Cada satélite: entrada → EQ → (altos ×2 → ganancia → retardo) y (bajos ×2 → suma)
    assert ("fl_entrada:Out", "fl_eq1:In") in enlaces and ("fl_eq7:Out", "fl_eq8:In") in enlaces
    assert ("fl_eq8:Out", "fl_altos1:In") in enlaces and ("fl_eq8:Out", "fl_bajos1:In") in enlaces
    assert ("fl_altos2:Out", "fl_ganancia:In") in enlaces and ("fl_bajos2:Out", "sub_suma:In 1") in enlaces
    assert ("rr_bajos2:Out", "sub_suma:In 5") in enlaces and ("lfe_nivel:Out", "sub_suma:In 6") in enlaces
    assert ("sub_suma:Out", "sub_eq1:In") in enlaces and ("sub_eq4:Out", "sub_ganancia:In") in enlaces
    assert ("sub_eq1", "bq_raw") in nodos and ("fl_eq1", "bq_raw") in nodos and ("fl_eq8", "bq_raw") in nodos
    # Cada ranura lleva sus coeficientes (sin ellos, bq_raw deja pasar silencio); las libres, en neutro
    assert "name = fl_eq8 label = bq_raw config = { coefficients = [ { rate = 48000 b0 = 1 b1 = 0 b2 = 0 a0 = 1 a1 = 0 a2 = 0 } ] }" in texto
    # Entradas y salidas en el orden de los canales del sink: FL FR FC LFE RL RR
    entradas = re.search(r"inputs  = \[ (.*) \]", texto).group(1)
    assert entradas.split() == ['"fl_entrada:In"', '"fr_entrada:In"', '"fc_entrada:In"', '"lfe_nivel:In"',
                                '"rl_entrada:In"', '"rr_entrada:In"']
    salidas = re.search(r"outputs = \[ (.*) \]", texto).group(1)
    assert salidas.split() == [f'"{b}_retardo:Out"' for b in ("fl", "fr", "fc", "sub", "rl", "rr")]
    assert 'node.name        = "elena_salida"' in texto and "priority.session = 1" in texto
    assert "flags = [ nofail ]" in texto and "GENERADO" in texto
    assert "node.dont-fallback = true" in texto and "node.linger        = true" in texto


def test_conf_sin_cruce_ni_eq():
    texto = cb.conf(cb.desde_datos({k: v for k, v in DATOS.items() if k != "cruce"}))
    enlaces = _enlaces(texto)
    assert ("fl_eq8:Out", "fl_ganancia:In") in enlaces                # las ranuras del EQ siempre están
    assert not any("bajos" in a or "altos" in a for a, _ in enlaces)
    assert ("sub_eq4:Out", "sub_ganancia:In") in enlaces


def test_conf_cliente_para_probar_aparte():
    texto = cb.conf(cb.desde_datos(DATOS), destino="elena_prueba_fisico", nombre="elena_salida_prueba", cliente=True)
    assert "libpipewire-module-protocol-native" in texto and 'target.object      = "elena_prueba_fisico"' in texto
    assert 'node.name        = "elena_salida_prueba"' in texto


@pytest.mark.skipif(not shutil.which("pw-config"), reason="sin pw-config")
def test_generar_valida_con_pw_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    toml = tmp_path / "calibracion.toml"
    toml.write_text((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    salida = tmp_path / "conf.d" / "40-elena-calibracion.conf"
    cal, avisos = cb.generar(toml, salida)
    assert salida.exists() and avisos == [] and cal.nivel == -21.0
    assert not list((tmp_path / "tmp").glob("*.conf"))           # el temporal no queda


def test_generar_no_reemplaza_si_falla(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    toml = tmp_path / "calibracion.toml"
    toml.write_text((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    salida = tmp_path / "40-elena-calibracion.conf"
    salida.write_text("el de antes")

    def falla(ruta):
        raise ErrorElena("PipeWire no lee")
    monkeypatch.setattr(cb, "validar", falla)
    with pytest.raises(ErrorElena):
        cb.generar(toml, salida)
    assert salida.read_text() == "el de antes" and not list(tmp_path.glob("*.nuevo.conf"))


# ---------- Integración ----------

def _equipo_en(tmp_path, monkeypatch):
    """Un local/ de prueba con el equipo de conftest: las plantillas reales, todo lo demás en tmp_path."""
    from elena import rutas
    from conftest import EQUIPO_DE_PRUEBA
    local = tmp_path / "local"
    monkeypatch.setattr(rutas, "EQUIPO", local / "equipo.toml")
    monkeypatch.setattr(rutas, "CONF_PIPEWIRE", local / "pipewire")
    monkeypatch.setattr(rutas, "CONF_WIREPLUMBER", local / "wireplumber")
    monkeypatch.setattr(cb, "CONF", local / "pipewire" / "40-elena-calibracion.conf")
    monkeypatch.setattr(cb, "TOML", local / "calibracion.toml")
    local.mkdir()
    (local / "equipo.toml").write_text("".join(f'{k} = "{v}"\n' for k, v in EQUIPO_DE_PRUEBA.items()))
    cb.TOML.write_text((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    return local / "pipewire" / "10-elena-buses.conf"


@pytest.mark.skipif(not shutil.which("pw-config"), reason="sin pw-config")
def test_activar_y_desactivar_escriben_los_dos_archivos(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    buses = _equipo_en(tmp_path, monkeypatch)
    cb.activar(cb.TOML, cb.CONF)
    assert cb.CONF.exists() and cb.destino_de_buses(buses.read_text()) == {"elena_salida"}
    assert "CORSAIR_VIRTUOSO" in buses.read_text()                       # el chat no cambia
    cb.desactivar(cb.CONF)
    assert not cb.CONF.exists() and cb.destino_de_buses(buses.read_text()) == {cb.SINK_FISICO}


def test_activar_no_deja_el_conf_si_los_buses_fallan(tmp_path, monkeypatch):
    from elena import equipo
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    _equipo_en(tmp_path, monkeypatch)
    monkeypatch.setattr(cb, "validar", lambda ruta, modulo="": None)
    monkeypatch.setattr(equipo, "validar", lambda ruta: (_ for _ in ()).throw(ErrorElena("no")))
    with pytest.raises(ErrorElena):
        cb.activar(cb.TOML, cb.CONF)
    assert not cb.CONF.exists()


POS6 = ("FL", "FR", "FC", "LFE", "RL", "RR")


def _db(*dbs):
    return tuple(10 ** (d / 20) for d in dbs)


def test_ajustes_buses_conservan_fl_y_no_pasan_de_100():
    vols = {
        "elena_musica": (POS6, _db(-38.07, -38.07, -38.07, -18.06, -31.08, -31.08)),   # la mañana, al 50 %
        "elena_juegos": (POS6, _db(-20, -20, -20, 0, -13, -13)),                        # el diario
        "elena_sistema": (("FL", "FR"), _db(0, 0)),                                     # ya en un solo volumen
    }
    a = {x.nodo: x for x in cb.ajustes_buses(vols, -21.0)}
    assert set(a) == {"elena_musica", "elena_juegos"}
    assert 20 * np.log10(a["elena_musica"].despues[0]) == pytest.approx(-17.07)    # −38.07 + 21
    assert len(set(a["elena_musica"].despues)) == 1
    assert a["elena_juegos"].despues == (1.0,) * 6 and "1.0 dB más bajo" in a["elena_juegos"].texto
    assert "FL suena igual" in a["elena_musica"].texto


class _Nodo:
    def __init__(self, nombre, posiciones, volumenes):
        self.nombre, self.posiciones, self.volumenes = nombre, posiciones, volumenes


class _Grafo:
    def __init__(self, *nodos):
        self.nodos = {n.nombre: n for n in nodos}

    def nodo(self, nombre):
        return self.nodos.get(nombre)


def test_ajustar_tras_aplicar_activa_y_luego_inactiva(tmp_path, monkeypatch):
    from elena import acciones
    hechos = []
    monkeypatch.setattr(acciones, "ejecutar", hechos.append)
    monkeypatch.setattr(acciones, "cmd_volumen", lambda n, v: ("volumen", n.nombre, tuple(round(x, 4) for x in v)))
    monkeypatch.setattr(acciones, "cmd_silencio", lambda n, s: ("silencio", n.nombre, s))
    monkeypatch.setattr(cb, "RESPALDO_BUSES", tmp_path / "buses-antes.json")
    monkeypatch.setattr(cb, "leer", lambda: cb.desde_datos(DATOS))
    juegos = _Nodo("elena_juegos", POS6, _db(-20, -20, -20, 0, -13, -13))
    salida = _Nodo("elena_salida", POS6, _db(-6, -6, -6, -6, -6, -6))

    monkeypatch.setattr(cb, "activa", lambda: True)
    with pytest.raises(ErrorElena, match="no apareció elena_salida"):
        cb.ajustar_tras_aplicar(_Grafo(juegos))
    textos = cb.ajustar_tras_aplicar(_Grafo(juegos, salida))
    assert ("volumen", "elena_salida", (1.0,) * 6) in hechos and ("silencio", "elena_salida", False) in hechos
    assert ("volumen", "elena_juegos", (1.0,) * 6) in hechos and any("elena_juegos" in t for t in textos)
    assert cb.RESPALDO_BUSES.exists()

    # Desactivada: los buses regresan a su balance de antes y se borra el respaldo
    hechos.clear()
    monkeypatch.setattr(cb, "activa", lambda: False)
    plano = _Nodo("elena_juegos", POS6, (1.0,) * 6)
    cb.ajustar_tras_aplicar(_Grafo(plano))
    assert hechos == [("volumen", "elena_juegos", tuple(round(x, 4) for x in _db(-20, -20, -20, 0, -13, -13)))]
    assert not cb.RESPALDO_BUSES.exists()


def test_comparar_y_musica_se_niegan_con_la_calibracion_activa(dump):
    from conftest import PROCESOS, nodo_crudo
    from elena import escucha
    from elena.modelo import Grafo
    escucha.sin_calibracion(Grafo.desde_dump(dump, proceso_de=PROCESOS.get), "x")   # sin calibrar: pasa
    nodo_crudo(dump, **{"node.name": "elena_musica.salida"})["info"]["props"]["target.object"] = "elena_salida"
    with pytest.raises(ErrorElena, match="dos veces"):
        escucha.sin_calibracion(Grafo.desde_dump(dump, proceso_de=PROCESOS.get), "La comparación")


@pytest.mark.parametrize("tipo,f,g,q,puntos", [
    ("pico", 1000, 6.0, 1.0, [(1000, 6.0), (20, 0.0), (20000, 0.0)]),
    ("pico", 60, -4.0, 2.0, [(60, -4.0), (1000, 0.0)]),
    ("estante_grave", 100, 3.0, 0.707, [(20, 3.0), (100, 1.5), (5000, 0.0)]),
    ("estante_agudo", 8000, -2.0, 0.707, [(20000, -2.0), (8000, -1.0), (200, 0.0)]),
])
def test_coeficientes_dan_la_respuesta_pedida(tipo, f, g, q, puntos):
    banda = cb.Banda(tipo, float(f), g, q)
    for frecuencia, esperado in puntos:
        assert cb.respuesta_db((banda,), frecuencia) == pytest.approx(esperado, abs=0.15), frecuencia


def test_coeficientes_neutros_y_normalizados():
    assert cb.coeficientes(None) == cb.NEUTRO
    assert cb.coeficientes(cb.Banda("pico", 500.0, 3.0, 1.0))[3] == 1.0
    plano = cb.coeficientes(cb.Banda("pico", 500.0, 0.0, 1.0))      # ganancia 0: deja pasar todo igual
    assert cb.respuesta_db((cb.Banda("pico", 500.0, 0.0, 1.0),), 500) == pytest.approx(0.0, abs=1e-9)
    assert plano[0] == pytest.approx(1.0)


def test_maximo_de_bandas():
    banda = {"tipo": "pico", "frecuencia": 1000, "ganancia": 1.0}
    with pytest.raises(ErrorElena, match="caben 8"):
        cb.desde_datos(_datos(eq={"bandas": [banda] * 9}))
    with pytest.raises(ErrorElena, match="caben 4"):
        cb.desde_datos(_datos(eq_sub={"bandas": [banda] * 5}))
