"""Afinar en vivo: cambios, neutro, el TOML línea por línea y la herramienta interactiva."""

import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from elena import afinar as af
from elena import calibracion as cb
from elena.modelo import ErrorElena

RAIZ = Path(__file__).resolve().parent.parent
TEXTO = (RAIZ / "tests" / "datos" / "calibracion.toml").read_text()


@pytest.fixture
def cal():
    return cb.desde_datos(tomllib.loads(TEXTO))


def test_a_datos_ida_y_vuelta(cal):
    assert cb.desde_datos(af.a_datos(cal)) == cal


def test_con_bocina(cal):
    n = af.con_bocina(cal, "sub", "ganancia", 6.5)
    assert n.bocinas["sub"].ganancia == 6.5 and n.bocinas["fl"] == cal.bocinas["fl"]
    r = af.con_bocina(cal, "fc", "retardo", 2.0)
    d = af.con_bocina(r, "fc", "distancia", 1.5)                      # retardo o distancia: una u otra
    assert d.bocinas["fc"].distancia == 1.5 and d.bocinas["fc"].retardo_ms is None
    with pytest.raises(ErrorElena, match="fuera de rango"):
        af.con_bocina(cal, "sub", "ganancia", 30)
    with pytest.raises(ErrorElena, match="Bocina desconocida"):
        af.con_bocina(cal, "centro", "ganancia", 1)


def test_editar_toml_conserva_comentarios_y_cambia_solo_lo_pedido(cal):
    nueva = af.con_bocina(replace(cal, nivel=-20.5, cruce=120.0), "sub", "ganancia", 6.5)
    texto = af.editar_toml(TEXTO, nueva)
    assert cb.desde_datos(tomllib.loads(texto)) == nueva
    viejas, nuevas = TEXTO.split("\n"), texto.split("\n")
    distintas = [(a, b) for a, b in zip(viejas, nuevas) if a != b]
    assert len(viejas) == len(nuevas) and len(distintas) == 3
    sub = next(b for a, b in distintas if b.startswith("sub"))
    assert sub.startswith("sub = { ganancia = 6.5 }") and "# la bocina del sub" in sub
    assert any(b.startswith("frecuencia = 120 ") and "#" in b for _, b in distintas)
    assert any(b.startswith("nivel = -20.5") for _, b in distintas)


def test_editar_toml_con_retardo_y_cruce_por_bocina(cal):
    nueva = af.validar(replace(cal, bocinas={**cal.bocinas,
                                             "rr": replace(cal.bocinas["rr"], retardo_ms=3.0, cruce=120.0)}))
    texto = af.editar_toml(TEXTO, nueva)
    assert "rr  = { ganancia = 9.0, retardo = 3.0, cruce = 120 }" in texto
    assert cb.desde_datos(tomllib.loads(texto)) == nueva


def test_estructura_detecta_lo_que_no_es_en_vivo(cal):
    assert af.estructura(replace(cal, cruce=120.0)) == af.estructura(cal)
    sin_cruce_fc = af.validar(replace(cal, bocinas={**cal.bocinas, "fc": replace(cal.bocinas["fc"], cruce=0.0)}))
    assert af.estructura(sin_cruce_fc) != af.estructura(cal)
    con_banda = replace(cal, eq=(cb.Banda("pico", 1000.0, 2.0, 1.0),))
    assert af.estructura(con_banda) == af.estructura(cal)            # el EQ va en ranuras fijas: en vivo


def test_neutro_mismo_nivel_sin_balance_ni_cruce(cal):
    n = af.neutro(cal)
    for b in cb.BOCINAS:
        assert n[f"{b}_ganancia:Mult"] == pytest.approx(10 ** (-21 / 20))   # todas al nivel de FL
    assert n["lfe_nivel:Mult"] == pytest.approx(10 ** 0.5)                # el LFE conserva su nivel
    assert n["fl_altos1:Freq"] == af.APAGADO_HZ and n["sub_suma:Gain 1"] == 0.0
    assert n["fl_bajos1:Freq"] == 110.0 and n["sub_suma:Gain 6"] == 1.0   # el LFE sigue entrando a la suma
    # Y de regreso a lo calibrado se restauran todos
    vuelta = af.diferencias(n, cb.controles(cal))
    assert vuelta["fl_altos1:Freq"] == 110.0 and vuelta["sub_suma:Gain 1"] == 1.0


def test_diferencias_y_params():
    assert af.diferencias({"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 3.0, "c": 0.0}) == {"b": 3.0, "c": 0.0}
    assert af.params({"fl_ganancia:Mult": 0.5, "fr_retardo:Delay (s)": 0.001}) == \
        '{ params = [ "fl_ganancia:Mult" 0.5 "fr_retardo:Delay (s)" 0.001 ] }'


def test_afinacion_teclas(cal):
    a = af.Afinacion(cal)
    assert a.elegido == "sub"
    a.tecla("-"); a.tecla("-")
    assert a.cal.bocinas["sub"].ganancia == 7.5 and a.pendientes() == ["sub +8.5→+7.5"]
    a.tecla("a")
    assert a.neutro and a.controles()["sub_suma:Gain 1"] == 0.0
    a.tecla("+")
    assert "neutro" in a.aviso and a.cal.bocinas["sub"].ganancia == 7.5   # en neutro no se ajusta
    a.tecla("a"); a.tecla("c"); a.tecla("+")
    assert a.cal.cruce == 115.0
    a.tecla("n"); a.tecla("+")
    assert a.cal.nivel == -20.5
    a.tecla("r")
    assert a.cal == cal and not a.pendientes()
    a.tecla("g")
    assert a.guardar
    a.tecla("q")
    assert a.fin


def test_afinacion_avisa_fuera_de_rango(cal):
    a = af.Afinacion(replace(cal, bocinas={**cal.bocinas, "sub": replace(cal.bocinas["sub"], ganancia=24.0)}))
    a.tecla("+")
    assert "fuera de rango" in a.aviso and a.cal.bocinas["sub"].ganancia == 24.0


def test_cambiar_en_vivo_y_guarda(tmp_path, monkeypatch, cal):
    toml = tmp_path / "calibracion.toml"
    toml.write_text(TEXTO)
    enviados, generados = [], []
    monkeypatch.setattr(cb, "activa", lambda: True)
    monkeypatch.setattr(af, "id_salida", lambda: 42)
    monkeypatch.setattr(af, "en_vivo", lambda ident, c: enviados.append((ident, c)))
    monkeypatch.setattr(af, "controles_vivos", lambda ident: set(cb.controles(cal)))   # ya con las ranuras
    monkeypatch.setattr(cb, "generar", lambda t, s: generados.append(t))
    hechos = af.cambiar(af.con_bocina(cal, "sub", "ganancia", 6.5), toml)
    assert enviados == [(42, {"sub_ganancia:Mult": pytest.approx(10 ** (-14.5 / 20))})]   # solo lo que cambió
    assert cb.leer(toml).bocinas["sub"].ganancia == 6.5 and generados == [toml]
    assert any("En vivo: 1 control" in h for h in hechos)
    # Un cambio que mueve el preamp ajusta todas las ganancias
    enviados.clear()
    af.cambiar(af.validar(replace(cb.leer(toml), nivel=0.0)), toml)
    assert len([k for k in enviados[0][1] if k.endswith("_ganancia:Mult")]) == 6
    with pytest.raises(ErrorElena, match="no es en vivo"):
        actual = cb.leer(toml)
        af.cambiar(af.validar(replace(actual, bocinas={**actual.bocinas, "fc": replace(actual.bocinas["fc"], cruce=0.0)})), toml)
    # Una banda del EQ sí es en vivo: solo cambian los coeficientes de su ranura en cada satélite
    enviados.clear()
    af.cambiar(replace(cb.leer(toml), eq=(cb.Banda("pico", 1000.0, 2.0, 1.0),)), toml)
    assert set(enviados[0][1]) == {f"{b}_eq1:{k}" for b in cb.SATELITES for k in cb.COEFICIENTES} - \
        {f"{b}_eq1:a0" for b in cb.SATELITES}                       # a0 sigue en 1 (normalizados)
    assert cb.leer(toml).eq == (cb.Banda("pico", 1000.0, 2.0, 1.0),)


def test_editar_toml_cambia_la_compensacion_estereo():
    texto = TEXTO + '\n[estereo]\ncompensacion = -12.0          # dB\nbuses = ["musica", "sistema"]\n'
    cal = cb.desde_datos(tomllib.loads(texto))
    nuevo = af.editar_toml(texto, af.validar(replace(cal, estereo=-9.5)))
    assert "compensacion = -9.5           # dB" in nuevo
    assert cb.desde_datos(tomllib.loads(nuevo)).estereo_buses == ("musica", "sistema")


def test_editar_toml_agrega_y_cambia_la_de_un_bus():
    texto = TEXTO + '\n[estereo]\ncompensacion = -15.0\nbuses = ["musica", "sistema"]\n\n[otra]\n'
    texto = texto.replace("\n[otra]\n", "\n")
    cal = cb.desde_datos(tomllib.loads(texto))
    nuevo = af.editar_toml(texto, af.validar(replace(cal, estereo_por_bus={"sistema": -10.0})))
    assert "sistema = -10" in nuevo and cb.desde_datos(tomllib.loads(nuevo)).estereo_de("sistema") == -10.0
    otra = af.editar_toml(nuevo, af.validar(replace(cal, estereo_por_bus={"sistema": -8.5})))
    assert otra.count("sistema = ") == 1 and cb.desde_datos(tomllib.loads(otra)).estereo_de("sistema") == -8.5


def test_editar_toml_escribe_las_bandas(cal):
    eq = (cb.Banda("pico", 1000.0, 2.0, 1.0), cb.Banda("estante_agudo", 8000.0, -1.5, 0.707))
    nueva = replace(cal, eq=eq, eq_sub=(cb.Banda("pico", 45.0, -3.0, 2.0),))
    texto = af.editar_toml(TEXTO, nueva)
    assert '  { tipo = "pico", frecuencia = 1000, ganancia = 2.0, q = 1.0 },' in texto
    assert cb.desde_datos(tomllib.loads(texto)) == nueva
    # Y de regreso a sin bandas (desde varias líneas)
    vacio = af.editar_toml(texto, cal)
    assert cb.desde_datos(tomllib.loads(vacio)) == cal and vacio.count("bandas = []") == 2


def test_eq_nueva_agregar_cambiar_quitar_limpiar(cal):
    c = af.eq_nueva(cal, ["agregar", "pico", "1000", "2"])
    assert c.eq == (cb.Banda("pico", 1000.0, 2.0, 1.0),)
    c = af.eq_nueva(c, ["agregar", "agudo", "8000", "-1.5"])
    assert c.eq[1] == cb.Banda("estante_agudo", 8000.0, -1.5, 0.707)
    c = af.eq_nueva(c, ["1", "ganancia", "3"])
    c = af.eq_nueva(c, ["2", "tipo", "grave"])
    assert c.eq[0].ganancia == 3.0 and c.eq[1].tipo == "estante_grave"
    c = af.eq_nueva(c, ["quitar", "1"])
    assert len(c.eq) == 1 and c.eq[0].tipo == "estante_grave"
    s = af.eq_nueva(c, ["agregar", "pico", "45", "-3"], sub=True, q=2.0)
    assert s.eq_sub == (cb.Banda("pico", 45.0, -3.0, 2.0),) and s.eq == c.eq
    assert af.eq_nueva(c, ["limpiar"]).eq == ()


@pytest.mark.parametrize("partes,mensaje", [
    ([], "Falta"), (["agregar", "pico", "1000"], "Uso"), (["agregar", "campana", "1000", "2"], "Tipo desconocido"),
    (["3", "ganancia", "1"], "No hay banda 3"), (["quitar", "1"], "No hay banda 1"),
    (["agregar", "pico", "30000", "2"], "fuera de rango"), (["agregar", "pico", "mil", "2"], "inválido"),
])
def test_eq_nueva_errores(cal, partes, mensaje):
    with pytest.raises(ErrorElena, match=mensaje):
        af.eq_nueva(cal, partes)


def test_texto_eq(cal):
    c = af.eq_nueva(cal, ["agregar", "pico", "1000", "6"])
    texto = "\n".join(af.texto_eq(c))
    assert "1 de 8 bandas" in texto and "1k +6.0" in texto and "0 de 4 bandas" in texto


def test_cambiar_avisa_si_faltan_las_ranuras(tmp_path, monkeypatch, cal):
    toml = tmp_path / "calibracion.toml"
    toml.write_text(TEXTO)
    enviados = []
    monkeypatch.setattr(cb, "activa", lambda: True)
    monkeypatch.setattr(cb, "generar", lambda t, s: None)
    monkeypatch.setattr(af, "id_salida", lambda: 42)
    monkeypatch.setattr(af, "controles_vivos", lambda ident: {"sub_ganancia:Mult"})   # sin ranuras del EQ
    monkeypatch.setattr(af, "en_vivo", lambda ident, c: enviados.append(c))
    hechos = af.cambiar(af.eq_nueva(cal, ["agregar", "pico", "1000", "2"]), toml)
    assert enviados == [{}] and any("mise run aplicar" in h for h in hechos)
    assert cb.leer(toml).eq                                   # se guardó igual
