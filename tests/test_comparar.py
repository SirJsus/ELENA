"""Comparación desde neutro: ganancias absolutas, fases a ciegas, rondas y resultado."""

import numpy as np
import pytest

from elena import comparar as cp

POS = ("FL", "FR", "FC", "LFE", "RL", "RR")
BALANCE = {"FR": 0.5, "FC": 5.5, "RL": 6.0, "RR": 9.0}


def test_ganancias_absolutas_con_balance_lfe_y_fisico():
    g = cp.ganancias_db(POS, -20.0, BALANCE, sub=1.0, fisico={})
    assert g == (-20.0, -19.5, -14.5, -9.0, -14.0, -11.0)     # LFE: −20 + 10 + 1
    # El volumen del físico se descuenta: el DAC recibe lo mismo
    g = cp.ganancias_db(POS, -20.0, BALANCE, sub=0.0, fisico={"FL": -3.0, "LFE": 2.0})
    assert g[0] == -17.0 and g[3] == -12.0
    assert cp.a_lineal((0.0, -20.0)) == pytest.approx((1.0, 0.1))


def test_no_depende_del_volumen_del_bus():
    # Las ganancias no reciben el volumen actual del bus: solo nivel, balance, sub y físico
    a = cp.ganancias_db(POS, -25.0, BALANCE, 0.0, {})
    b = cp.ganancias_db(POS, -25.0, BALANCE, 0.0, {})
    assert a == b


def test_es_estereo():
    assert cp.es_estereo({"FL": -12.0, "FR": -14.0, "FC": None, "LFE": None, "RL": -90.3, "RR": None}) is True
    assert cp.es_estereo({"FL": -12.0, "FR": -14.0, "FC": -30.0, "LFE": -90.0, "RL": None, "RR": None}) is False
    assert cp.es_estereo({"FL": None, "FR": None, "FC": None}) is None
    assert cp.es_estereo({"FL": -75.0, "FR": None}) is None                   # casi silencio


def test_fase_teclas_espera_y_limites():
    f = cp.Fase("cien", -20.0, cp.NIVEL_MIN, -19.0)
    assert f.tecla("+", 10.0) and f.valor == -19.0
    assert not f.tecla("+", 10.2) and "espera" in f.aviso            # muy pronto
    assert not f.tecla("+", 11.0) and "más alto" in f.aviso          # el límite
    assert f.tecla("-", 11.1) and f.valor == -20.0                   # bajar no espera
    assert not f.tecla("s", 12.0) and f.fin is None                  # solo el máximo se salta
    f.tecla("\n", 12.0)
    assert f.fin == "marcada"
    m = cp.Fase("maximo", -15.0, cp.NIVEL_MIN, cp.NIVEL_MAX)
    m.tecla("s", 0.0)
    assert m.fin == "saltada"
    b = cp.Fase("sub", cp.SUB_MIN, cp.SUB_MIN, cp.SUB_MAX)
    assert not b.tecla("-", 0.0) and "más bajo" in b.aviso
    b.tecla("q", 0.0)
    assert b.fin == "salir"


def test_al_azar_en_rango():
    rng = np.random.default_rng(1)
    valores = {cp.al_azar(rng, cp.INICIO_CIEN) for _ in range(400)}
    assert min(valores) == cp.INICIO_CIEN[0] and max(valores) == cp.INICIO_CIEN[1]


def test_rondas_tercera_y_resultado():
    r1 = cp.Ronda(1, cien=-22.0, sub=1.0, maximo=-17.0)
    r2 = cp.Ronda(2, cien=-18.0, sub=2.0, maximo=-16.0)
    assert cp.difieren([r1, r2]) == ["cien"]
    assert cp.difieren([r1]) == []
    r3 = cp.Ronda(3, cien=-21.0)
    res = cp.resultado([r1, r2, r3])
    assert (res.cien, res.sub, res.maximo) == (-21.0, 1.5, -16.5)
    assert res.lfe == 11.5 and res.tope == pytest.approx(4.5)
    sin_max = cp.resultado([cp.Ronda(1, cien=-20.0, sub=0.0)])
    assert sin_max.maximo is None and sin_max.tope is None


def test_texto_resultado():
    rondas = [cp.Ronda(1, cien=-20.0, sub=1.0, maximo=-16.0), cp.Ronda(2, cien=-20.0, sub=1.0, maximo=-16.0)]
    texto = "\n".join(cp.texto_resultado(cp.resultado(rondas), rondas, BALANCE))
    assert "FL -20.0 dB hasta el DAC (rondas -20, -20)" in texto and "~-29 dBFS" in texto
    assert "LFE +11.0 dB sobre FL" in texto
    assert "+4.0 dB sobre tu 100 %" in texto and "su tope es 117 %" in texto
    assert "fl -20.0 · fr -19.5 · fc -14.5 · rl -14.0 · rr -11.0 · lfe -9.0 dB" in texto


def test_avisa_si_el_maximo_queda_debajo_del_cien():
    rondas = [cp.Ronda(1, cien=-19.0, sub=1.0, maximo=-20.0)]
    assert "debajo de tu 100 %" in "\n".join(cp.texto_resultado(cp.resultado(rondas), rondas, BALANCE))
