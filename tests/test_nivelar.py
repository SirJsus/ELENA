"""Prueba de nivelación: señal del A/B, ajuste a ciegas, rondas, mic y resultado."""

import numpy as np
import pytest

from elena import nivelar as nv
from elena.satelites import TASA

POS = ("FL", "FR", "FC", "LFE", "RL", "RR")


def _rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))))


def test_nivel_referencia():
    assert nv.nivel_referencia({"FL": (-25.07, -37.56)}) == pytest.approx(-47.56)   # 10 dB abajo del ideal
    assert nv.nivel_referencia({"RL": (-18.0, -30.0)}) == nv.NIVEL_SIN_VOLUMEN


def test_ciclo_fl_y_luego_la_otra_con_su_nivel():
    base = nv.a_rms_1(nv.ruido_rosa(nv.BANDA, nv.RAFAGA, semilla=1))
    x = nv.ciclo(base, POS, "RL", -45.0, 7.0, {"FL": 0.0, "RL": -3.0})
    n, p = len(base), int(nv.PAUSA * TASA)
    assert x.shape == (2 * n + p + int(nv.FINAL * TASA), 6) and x.dtype == np.float32
    assert _rms_db(x[:n, 0]) == pytest.approx(-45.0, abs=0.01)
    assert np.count_nonzero(x[n:, 0]) == 0                      # FL solo al principio
    # RL: −45 + 7, y el volumen del físico (−3 dB en RL) se descuenta
    assert _rms_db(x[n + p:2 * n + p, 4]) == pytest.approx(-35.0, abs=0.01)
    assert np.count_nonzero(x[:n + p, 4]) == 0
    assert np.count_nonzero(x[:, [1, 2, 3, 5]]) == 0


def test_techo_ajuste_respeta_el_pico():
    assert nv.techo_ajuste(-47.5, 12.0) == nv.AJUSTE_MAX
    assert nv.techo_ajuste(-20.0, 12.0) == pytest.approx(7.0)    # −20 + 12 de cresta + 7 = −1 dBFS


def test_inicio_al_azar_en_rango():
    rng = np.random.default_rng(3)
    inicios = {nv.inicio_al_azar(rng) for _ in range(500)}
    assert min(inicios) == -nv.DESFASE_MAX and max(inicios) == nv.DESFASE_MAX
    assert all(v == int(v) for v in inicios)


def test_ajuste_teclas_y_limites():
    a = nv.Ajuste(inicio=3.0, techo=5.0)
    assert a.tecla("+") == "cambio" and a.ajuste == 4.0
    assert a.tecla("+") == "cambio" and a.tecla("+") is None and a.ajuste == 5.0 and "más alto" in a.aviso
    assert a.tecla("-") == "cambio" and a.ajuste == 4.0 and a.aviso == ""
    assert a.tecla("x") is None
    assert a.tecla("\n") == "cortar" and a.fin == "igual"
    b = nv.Ajuste(inicio=-nv.AJUSTE_MAX)
    assert b.tecla("-") is None and "más bajo" in b.aviso
    assert nv.Ajuste(inicio=6.0, techo=2.0).ajuste == 2.0          # el inicio nunca pasa del techo
    c = nv.Ajuste(0.0)
    assert c.tecla("s") == "cortar" and c.fin == "saltada"
    assert nv.Ajuste(0.0).tecla("q") == "cortar"


def test_combinar_y_tercera_ronda():
    assert nv.combinar([]) is None
    assert nv.combinar([4.0, 5.0]) == 4.5
    assert nv.combinar([4.0, 9.0, 5.0]) == 5.0                    # la mediana ignora la ronda suelta
    assert not nv.necesita_tercera([4.0, 6.0])
    assert nv.necesita_tercera([4.0, 7.0])
    assert not nv.necesita_tercera([4.0])


def _ruido_en(canal, nivel_db, canales=2, segundos=2.2):
    x = np.zeros((int(segundos * TASA), canales))
    x[:, canal] = nv.a_rms_1(nv.ruido_rosa(nv.BANDA, segundos, semilla=5)) * 10 ** (nivel_db / 20)
    return x


def test_nivel_banda_compara_y_elige_el_canal():
    a = nv.nivel_banda(_ruido_en(1, -30))
    b = nv.nivel_banda(_ruido_en(0, -36))
    assert a - b == pytest.approx(6.0, abs=0.1)
    assert nv.nivel_banda(_ruido_en(0, -30, segundos=1.0)) is None   # corta


def test_ganancias_mic():
    niveles = {"FL": -10.0, "FR": -10.5, "RL": -16.0, "RR": -40.0}
    g = nv.ganancias_mic(niveles, silencio=-35.0)
    assert g == {"FR": 0.5, "RL": 6.0, "RR": None}                 # RR casi no se distingue del silencio
    assert nv.ganancias_mic({"FR": -10.0}, None) == {}
    assert nv.ganancias_mic({"FL": -30.0, "FR": -10.0}, -35.0) == {"FR": None}   # FL tampoco se distingue


def test_texto_resultado():
    rondas = [nv.Ronda("RL", 1, -3, 4.0, "igual"), nv.Ronda("RL", 2, 5, 5.0, "igual"),
              nv.Ronda("FR", 1, 2, 0.0, "igual"), nv.Ronda("FR", 2, -6, 1.0, "igual"),
              nv.Ronda("RR", 1, 0, None, "saltada")]
    resultado = {"FR": 0.5, "RL": 4.5, "RR": None}
    texto = "\n".join(nv.texto_resultado(resultado, rondas, {"FR": 0.3, "RL": 9.0}, {"FR": 0.0, "RL": 7.0}, -25.07))
    assert "RL  trasera izquierda   +4.5 dB  (rondas +4, +5) · mic +9.0 · hoy +7.0 (-2.5 dB contra hoy)" in texto
    assert "contra hoy" not in texto.split("FR", 1)[1].split("\n")[0]   # FR coincide con hoy
    assert "RR  trasera derecha" in texto and "sin anotar" in texto
    assert "difieren más de 3 dB en RL" in texto
    assert "RL -20.6" in texto and "FR -24.6" in texto


def test_guardar_y_ultimos_balances(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "CARPETA", tmp_path)
    assert nv.ultimos_balances(tmp_path) == {}
    (tmp_path / "nivelar-20261112-090000.json").write_text('{"resultado_db": {"FR": 0.5, "RL": 4.0}}')
    ruta = nv.guardar(-47.5, [nv.Ronda("RL", 1, 2, 5.0, "igual")], {"RL": 5.0, "RR": None}, {}, None, {}, {})
    assert ruta.parent == tmp_path and ruta.name > "nivelar-20261112-090000.json"
    assert nv.ultimos_balances(tmp_path) == {"FR": 0.5, "RL": 5.0}   # junta corridas; la más reciente gana


def test_balance_de_hoy(grafo):
    hoy = nv.balance_de_hoy(grafo)
    assert hoy.get("FL") == 0.0
