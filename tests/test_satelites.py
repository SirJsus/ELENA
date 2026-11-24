"""Prueba de las satélites: señal, análisis del mic, recorrido de teclas y validaciones."""

import numpy as np
import pytest

from elena import satelites as s
from elena.modelo import SINK_FISICO, ErrorElena


def test_frecuencias_de_200_a_40_cada_10():
    f = s.frecuencias()
    assert f[0] == 200 and f[-1] == 40 and len(f) == 17
    assert s.frecuencias(150, 60, 30) == [150, 120, 90, 60]


@pytest.mark.parametrize("desde,hasta,paso", [(200, 30, 10), (600, 40, 10), (40, 40, 10), (200, 40, 0)])
def test_frecuencias_fuera_de_rango(desde, hasta, paso):
    with pytest.raises(ErrorElena):
        s.frecuencias(desde, hasta, paso)


def test_tono_solo_en_su_canal_con_su_nivel_y_rampas():
    x = s.tono(100, canal=4, canales=6, nivel_db=-20)
    assert x.shape == (3 * s.TASA, 6) and x.dtype == np.float32
    assert np.count_nonzero(x[:, [0, 1, 2, 3, 5]]) == 0
    assert 20 * np.log10(np.abs(x[:, 4]).max()) == pytest.approx(-20, abs=0.01)
    assert abs(x[0, 4]) < 1e-6 and abs(x[-1, 4]) < 1e-6   # sin clic al empezar ni al terminar


def _senal(f, nivel_db, armonicos=(), ruido_db=None, canales=2, canal=1):
    t = np.arange(int(3.2 * s.TASA)) / s.TASA
    a = 10 ** (nivel_db / 20)
    x = a * np.sin(2 * np.pi * f * t)
    for n, rel_db in armonicos:
        x += a * 10 ** (rel_db / 20) * np.sin(2 * np.pi * n * f * t)
    if ruido_db is not None:
        x += np.random.default_rng(1).normal(0, 10 ** (ruido_db / 20), len(t))
    salida = np.zeros((len(t), canales))
    salida[:, canal] = x
    return salida


def test_analizar_nivel_y_canal_del_mic():
    m = s.analizar(_senal(63, -35, ruido_db=-70), 63)
    assert m.frecuencia == 63
    assert m.nivel_db == pytest.approx(-35, abs=0.2)
    assert m.distorsion_pct < 1


def test_analizar_distorsion():
    # 2.º armónico a −20 dB (10 %) y 3.º a −26 dB (5 %): √(10² + 5²) ≈ 11.2 %
    m = s.analizar(_senal(50, -30, armonicos=((2, -20), (3, -26.02))), 50)
    assert m.distorsion_pct == pytest.approx(11.18, abs=0.3)


def test_analizar_tono_perdido_en_el_ruido():
    # El tono queda casi al nivel del ruido de fondo: «ya no se oye», y su distorsión no cuenta
    m = s.analizar(_senal(70, -100, ruido_db=-60), 70)
    assert not m.audible
    claro = s.analizar(_senal(70, -40, ruido_db=-70), 70)
    assert claro.audible and claro.sobre_ruido_db > 30
    medidas = [s.Medida(100, -50, 30, 40), s.Medida(90, -70, 2000, 4)]
    assert s.distorsiona(medidas) == 100
    assert s.se_pierde(medidas) == 90


def test_analizar_grabacion_corta():
    assert s.analizar(np.zeros((1000, 2)), 100) is None


def test_caida_y_distorsiona():
    medidas = [s.Medida(200, -30, 1), s.Medida(100, -33, 4), s.Medida(70, -36.5, 12), s.Medida(60, -45, 40)]
    assert s.caida(medidas) == 70
    # La referencia es el más alto de los 3 primeros: 190 Hz sube por la sala
    assert s.caida([s.Medida(200, -50, 1), s.Medida(190, -46, 1), s.Medida(170, -51, 1), s.Medida(160, -53, 1)]) == 160
    assert s.distorsiona(medidas) == 70
    assert s.caida(medidas[:2]) is None


def test_recorrido_marca_y_limite():
    r = s.Recorrido(s.frecuencias(200, 40, 10))
    for _ in range(13):                      # 200 … 80 suenan bien
        r.termino_tono(cortado=False)
    assert r.actual == 70
    assert r.tecla(" ") == "cortar"
    r.termino_tono(cortado=True)
    assert (r.fin, r.marca, r.limite) == ("marcada", 70, 80)


def test_recorrido_repetir_anterior_y_saltar():
    r = s.Recorrido([200, 190, 180])
    r.termino_tono(False)
    assert r.tecla("r") == "cortar"
    r.termino_tono(True)
    assert r.actual == 190                    # repetir no avanza
    r.tecla("a")
    r.termino_tono(True)
    assert r.actual == 200                    # volvió al anterior
    r.tecla("x")
    assert r.fin is None                      # una tecla cualquiera no hace nada
    r.tecla("s")
    assert (r.fin, r.limite) == ("saltada", None)


def test_recorrido_completo_y_marca_en_el_primero():
    r = s.Recorrido([60, 50, 40])
    for _ in range(3):
        r.termino_tono(False)
    assert (r.fin, r.limite) == ("completa", 40)
    r = s.Recorrido([200, 190])
    r.tecla("\n")
    assert (r.fin, r.marca, r.limite) == ("marcada", 200, None)


def test_recomendacion():
    iguales = [s.Resultado("FL", "marcada", 70, 80), s.Resultado("FR", "marcada", 60, 70)]
    assert "→ 90 Hz" in s.recomendacion(iguales, 10)
    distintas = [*iguales, s.Resultado("RL", "marcada", 100, 110)]
    texto = s.recomendacion(distintas, 10)
    assert "RL se queda antes" in texto and "120 Hz" in texto
    assert "Solo se marcó FL" in s.recomendacion(iguales[:1], 10)
    assert "→ 90 Hz" in s.recomendacion([*iguales, s.Resultado("LFE", "marcada", 150, 160)], 10)  # el sub no cuenta
    assert "Sin límites" in s.recomendacion([s.Resultado("FC", "saltada", None, None)], 10)


def test_preparar_valida(grafo):
    pos, _ = s.preparar(grafo, s.Opciones(destino="elena_juegos", canales=("FL", "FC")))
    assert len(pos) == 6
    with pytest.raises(ErrorElena, match="física"):
        s.preparar(grafo, s.Opciones(destino=SINK_FISICO))
    with pytest.raises(ErrorElena, match="LFE"):
        s.preparar(grafo, s.Opciones(destino="elena_juegos", canales=("LFE",)))
    s.preparar(grafo, s.Opciones(destino="elena_juegos", canales=("LFE",), mic=True))   # con --mic sí entra
    with pytest.raises(ErrorElena, match="no tiene"):
        s.preparar(grafo, s.Opciones(destino="elena_sistema", canales=("RL",)))
    with pytest.raises(ErrorElena, match="nivel"):
        s.preparar(grafo, s.Opciones(destino="elena_juegos", nivel_db=-3))


def test_preparar_avisa_de_otras_apps(grafo):
    # En la foto, elena-prueba-pw suena en elena_juegos
    _, avisos = s.preparar(grafo, s.Opciones(destino="elena_juegos", canales=("FL",)))
    assert any("También suena" in a for a in avisos)


# ---------- Prueba de volumen máximo ----------

from elena import maximo as mx  # noqa: E402


def test_ruido_rosa_en_su_banda_con_pico_1():
    x = mx.ruido_rosa((100, 20000), semilla=1)
    assert len(x) == int(mx.RAFAGA * s.TASA)
    assert np.abs(x).max() == pytest.approx(1.0, abs=1e-6)
    espectro = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / s.TASA)
    assert espectro[f < 80].sum() < 1e-3 * espectro.sum()   # nada debajo de la banda
    # Rosa: cada octava lleva más o menos la misma energía (blanco tendría el doble por octava)
    octava = lambda a: espectro[(f >= a) & (f < 2 * a)].sum()  # noqa: E731
    assert 0.6 < octava(4000) / octava(500) < 1.6


def test_banda_de_cada_bocina():
    assert mx.banda_de("LFE") == mx.BANDA_SUB
    assert mx.banda_de("FL") == (200, 20000)   # arriba de lo que luego prueba la de las satélites


def test_rms_relativo():
    t = np.arange(48000) / 48000
    assert mx.rms_relativo(np.sin(2 * np.pi * 100 * t)) == pytest.approx(-3.01, abs=0.01)
    assert -14 < mx.rms_relativo(mx.ruido_rosa((200, 20000), semilla=2)) < -8


def test_ultimos_maximos(tmp_path):
    assert mx.ultimos_maximos(tmp_path) == {}
    (tmp_path / "volumen-20261111-090000.json").write_text(
        '{"resultados": [{"canal": "FL", "maximo": -30.0, "rms_rel": -11.0}]}')
    (tmp_path / "volumen-20261111-100000.json").write_text(
        '{"resultados": [{"canal": "FL", "maximo": -28.0, "rms_rel": -11.5}, {"canal": "FR", "maximo": null}]}')
    (tmp_path / "volumen-20261111-110000.json").write_text(
        '{"resultados": [{"canal": "RL", "maximo": -25.0, "rms_rel": -10.0}]}')
    assert mx.ultimos_maximos(tmp_path) == {"FL": (-28.0, -39.5), "RL": (-25.0, -35.0)}   # junta corridas


def test_con_volumen_iguala_el_rms_y_respeta_el_techo():
    maximos = {"FL": (-28.0, -39.5), "LFE": (2.0, 1.0)}
    op = s.con_volumen(s.Opciones(canales=("FL", "LFE"), mic=True), maximos, {"FL": 0.0})
    assert op.destino == SINK_FISICO
    assert op.niveles["FL"] == pytest.approx(-36.49)          # RMS del ruido + 3 dB de la senoidal
    assert op.niveles["LFE"] == s.TECHO_DBFS
    assert s.con_volumen(s.Opciones(relativo=-6), maximos, {}).niveles["FL"] == pytest.approx(-42.49)
    with pytest.raises(ErrorElena):
        s.con_volumen(s.Opciones(relativo=3), maximos, {})


def test_preparar_con_volumen_va_al_fisico(grafo):
    op = s.con_volumen(s.Opciones(canales=("FL", "RL")), {"FL": (-28.0, -39.5)}, {})
    with pytest.raises(ErrorElena, match="Falta la prueba de volumen de RL"):
        s.preparar(grafo, op)
    op = s.con_volumen(s.Opciones(canales=("FL",)), {"FL": (-28.0, -39.5)}, {})
    pos, _ = s.preparar(grafo, op)
    assert pos[:2] == ("FL", "FR") and len(pos) == 6


def test_nivel_de_hoy():
    # Música hoy: FL a −38.07 dB; picos de −9 dBFS; físico al 100 %
    assert mx.nivel_de_hoy({"FL": -38.07}, {"FL": 0.0}, "FL") == pytest.approx(-47.07)


def test_subida_pide_oir_cada_nivel_y_respeta_el_techo():
    sb = mx.Subida(hoy=-47.0, ahora=0.0)
    assert sb.tecla("+", 0.5) is None and sb.nivel == -47.0       # todavía no se oyó 1 s
    assert sb.tecla("+", 1.2) == "cambio" and sb.nivel == -45.0
    assert sb.tecla("+", 1.5) is None                               # tecla sostenida: no sube de golpe
    for t in range(2, 40):
        sb.tecla("+", t * 1.0)
    assert sb.nivel == pytest.approx(-17.0)                         # 30 dB sobre hoy
    assert "tope" in sb.aviso
    alto = mx.Subida(hoy=-5.0, ahora=0.0)
    for t in range(1, 10):
        alto.tecla("+", t * 1.0)
    assert alto.nivel <= mx.TECHO_DBFS                              # ni pasa de −1 dBFS


def test_subida_marcas():
    sb = mx.Subida(hoy=-47.0, ahora=0.0)
    sb.tecla("+", 1.1)
    sb.tecla("+", 2.2)
    assert sb.tecla(" ", 3.0) == "cortar"
    assert (sb.fin, sb.maximo) == ("distorsiona", -45.0)            # el paso anterior
    sb = mx.Subida(hoy=-47.0, ahora=0.0)
    sb.tecla("+", 1.1)
    sb.tecla("-", 1.2)
    sb.tecla("-", 1.3)
    assert sb.nivel == -49.0
    sb.tecla("\n", 2.0)
    assert (sb.fin, sb.maximo) == ("tope", -49.0)                   # este nivel


def test_tope_bus():
    # Unos datos medidos (2026-11-11): satélites cómodas 22 dB arriba de hoy; el sub distorsiona a +6
    maximos = {"FL": -25.07, "RL": -18.08, "LFE": -23.06}
    bus = {"FL": -38.07, "FR": -38.07, "RL": -31.08, "LFE": -18.06}
    h = mx.holguras(maximos, bus, {})
    assert h["FL"] == pytest.approx(22.0) and h["RL"] == pytest.approx(22.0)
    assert "LFE" not in h                                           # con música, el sub no limita
    assert mx.tope_bus(maximos, bus, {}) == pytest.approx(22.0)
    assert mx.tope_bus({}, bus, {}) is None
    assert mx.tope_bus({"FL": -60.0}, bus, {}) < 0                  # hoy ya lo pasa


# ---------- Prueba con música real ----------

from elena import escucha as es  # noqa: E402


def test_volumenes_sube_el_bus_y_aparte_el_sub():
    original = (0.0125, 0.0125, 0.0125, 0.125, 0.028, 0.028)   # el balance de hoy de elena_musica, más o menos
    pos = ("FL", "FR", "FC", "LFE", "RL", "RR")
    v = es.volumenes(original, pos, bus=20.0, sub=-6.0)
    assert v[0] == pytest.approx(0.125)
    assert v[3] == pytest.approx(0.125 * 10 ** (14 / 20))
    assert es.volumenes(original, pos, 0.0, 0.0) == pytest.approx(original)


def test_escucha_teclas_y_topes():
    e = es.Escucha(ahora=0.0)
    assert not e.tecla("+", 0.5) and e.bus == 0                     # hay que oír 1 s cada nivel
    assert e.tecla("+", 1.1) and e.bus == 2
    for t in range(2, 30):
        e.tecla("+", float(t))
    assert e.bus == es.BUS_MAX and "tope" in e.aviso
    assert e.tecla("[", 30.0) and e.sub == -2
    for _ in range(3):
        e.tecla("]", 30.0)
    assert e.sub == 4 and not e.tecla("]", 30.0)                    # el sub no sube más de +4
    e.tecla(" ", 31.0, -20.5)
    e.tecla("\n", 32.0, -24.0)
    assert [(m.nota, m.bus, m.sub, m.lfe_dac) for m in e.marcas] == [
        ("distorsiona", 24.0, 4.0, -20.5), ("bien", 24.0, 4.0, -24.0)]
    e.tecla("q", 33.0)
    assert e.fin


def test_escucha_registra_lo_mas_alto_sin_anotar():
    e = es.Escucha(ahora=0.0)
    e.registrar(-30.0)
    e.tecla("+", 1.5)
    e.registrar(-24.0)
    e.tecla("-", 2.0)
    e.registrar(-28.0)
    assert e.bus_max == 2.0
    assert (e.lfe_max.bus, e.lfe_max.lfe_dac) == (2.0, -24.0)


def test_guardar_y_restaurar_el_volumen(grafo, tmp_path, monkeypatch):
    ruta = tmp_path / "original.json"
    assert es.restaurar(grafo, ruta) is None                         # nada que regresar
    nodo = grafo.nodo("elena_juegos")
    es.guardar_original(nodo.nombre, (0.5,) * len(nodo.volumenes), ruta)
    hechos = []
    monkeypatch.setattr(es, "ejecutar", hechos.append)
    assert "regresado" in es.restaurar(grafo, ruta)
    assert hechos[0][:2] == ["pactl", "set-sink-volume"] and hechos[0][3:] == ["0.500000"] * len(nodo.volumenes)
    assert not ruta.exists()
