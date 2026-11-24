"""El temporizador para dormir (modelo puro, reloj falso)."""

import json

import pytest

from elena.modelo import ErrorElena, escalar, lineal_a_pct, pct_a_lineal
from elena.remoto import temporizador as tz
from elena.remoto.temporizador import Accion, Entorno, Temporizador

VOL = (pct_a_lineal(60), pct_a_lineal(60), pct_a_lineal(48), pct_a_lineal(48), pct_a_lineal(60), pct_a_lineal(60))


class Reloj:
    def __init__(self) -> None:
        self.t = 1_762_700_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def t(reloj):
    return Temporizador(reloj=reloj)


def tipos(acciones: list[Accion]) -> list[str]:
    return [a.tipo for a in acciones if a.tipo != "aviso"]


def volumenes(acciones: list[Accion]) -> list[tuple[float, ...]]:
    return [a.volumen for a in acciones if a.tipo == "volumen"]


def pct(v: tuple[float, ...]) -> float:
    return lineal_a_pct(max(v))


def sonando(volumen=VOL, **kw) -> Entorno:
    return Entorno(volumen=volumen, musica="Playing", pista="/x/1", **kw)


def hasta_desvanecer(t, reloj, entorno=None):
    """Lleva un temporizador de minutos hasta el final del desvanecido; devuelve todas las acciones."""
    e = entorno or sonando()
    todas = []
    while t.programa and t.programa.fase in ("contando", "desvaneciendo"):
        acciones = t.paso(e)
        todas += acciones
        if vols := volumenes(acciones):
            e = Entorno(volumen=vols[-1], musica=e.musica, pista=e.pista, vm=e.vm)
        reloj.t += 0.5
    return todas, e


# ---------- programar ----------

def test_validaciones(t):
    with pytest.raises(ErrorElena, match="nivel"):
        t.programar("dormir", minutos=10)
    with pytest.raises(ErrorElena, match="minutos"):
        t.programar(minutos=0)
    with pytest.raises(ErrorElena, match="minutos"):
        t.programar(minutos=13 * 60)
    with pytest.raises(ErrorElena, match="canción sonando"):
        t.programar(fin_cancion=True, entorno=Entorno(volumen=VOL, musica="Paused", restante_ms=5000))
    with pytest.raises(ErrorElena, match="No hay"):
        t.mas(10)
    assert t.vista() is None and t.paso(sonando()) == [] and t.cancelar() == []


def test_vista_y_mas(t, reloj):
    t.programar("vm", minutos=30)
    assert t.vista() == {"vence": int((reloj.t + 1800) * 1000), "modo": "minutos", "nivel": "vm", "fase": "contando"}
    t.mas(10)
    assert t.vista()["vence"] == int((reloj.t + 2400) * 1000)
    t.programar(minutos=5)                      # uno nuevo reemplaza al anterior
    assert t.vista()["nivel"] == "pausar"


# ---------- el camino normal ----------

def test_desvanece_pausa_y_restaura(t, reloj):
    t.programar(minutos=1)
    assert t.paso(sonando()) == []
    reloj.t += 29.9
    assert t.paso(sonando()) == []              # falta poco más de 30 s: todavía no
    reloj.t += 0.1
    todas, e = hasta_desvanecer(t, reloj)
    vols = volumenes(todas)
    assert pct(vols[0]) == pytest.approx(60)
    assert all(pct(a) >= pct(b) for a, b in zip(vols, vols[1:]))     # siempre bajando
    assert pct(vols[-1]) == pytest.approx(0, abs=1e-6)
    assert tipos(todas)[-1] == "pausa" and t.programa.fase == "pausando"
    mitad = vols[len(vols) // 2]
    # conserva el balance: las traseras siguen igual de abajo (en dB) que las frontales
    assert mitad[2] / mitad[0] == pytest.approx(VOL[2] / VOL[0])

    silencio = Entorno(volumen=vols[-1], musica="Paused", pista="/x/1")
    assert t.paso(silencio) == [] and t.programa.fase == "restaurando"
    reloj.t += 1.4
    assert t.paso(silencio) == []               # todavía dentro del margen
    reloj.t += 0.1
    final = t.paso(silencio)
    assert volumenes(final) == [VOL] and t.programa is None


def test_sin_musica_al_vencer_no_desvanece(t, reloj):
    t.programar(minutos=1)
    reloj.t += 60
    acciones = t.paso(Entorno(volumen=VOL, musica="Paused"))
    assert tipos(acciones) == [] and t.programa is None
    assert any("no había música" in a.texto for a in acciones)


def test_pausar_antes_de_vencer_no_cancela(t, reloj):
    t.programar("vm", minutos=10)
    reloj.t += 300
    assert t.paso(Entorno(volumen=VOL, musica="Paused", vm=True)) == []
    assert t.programa.fase == "contando"
    reloj.t += 300
    assert tipos(t.paso(Entorno(volumen=VOL, musica="Paused", vm=True))) == ["apagar_vm"]


# ---------- manda quien escucha ----------

def test_cambio_de_volumen_ajeno_cancela_sin_restaurar(t, reloj):
    t.programar(minutos=1)
    reloj.t += 30
    t.paso(sonando())
    reloj.t += 5
    acciones = t.paso(sonando(volumen=escalar(VOL, 75)))   # alguien lo subió en la consola
    assert t.programa is None and volumenes(acciones) == []
    assert "75 %" in acciones[0].texto


def test_pausa_del_usuario_durante_el_desvanecido(t, reloj):
    t.programar("apagar", minutos=1)
    reloj.t += 30
    vols = volumenes(t.paso(sonando()))
    reloj.t += 10
    vols = volumenes(t.paso(sonando(volumen=vols[-1])))
    reloj.t += 0.5
    acciones = t.paso(Entorno(volumen=vols[-1], musica="Paused", pista="/x/1"))
    assert t.programa is None and volumenes(acciones) == [VOL]       # cancela y regresa el volumen
    assert "apagar_pc" not in tipos(acciones)


def test_cancelar_a_media_bajada_restaura(t, reloj):
    t.programar(minutos=1)
    reloj.t += 40
    t.paso(sonando())
    assert volumenes(t.cancelar()) == [VOL] and t.programa is None
    t.programar(minutos=1)
    assert t.cancelar() == []                   # contando: no hay nada que regresar


def test_no_se_programa_otro_mientras_termina(t, reloj):
    t.programar(minutos=1)
    reloj.t += 40
    t.paso(sonando())
    with pytest.raises(ErrorElena, match="terminando"):
        t.programar(minutos=5)
    with pytest.raises(ErrorElena, match="alargar"):
        t.mas(5)


# ---------- reproductor rebelde ----------

def test_si_no_pausa_reintenta_y_silencia(t, reloj):
    t.programar(minutos=1)
    reloj.t += 30
    todas, e = hasta_desvanecer(t, reloj)
    sigue = Entorno(volumen=e.volumen, musica="Playing", pista="/x/1")
    assert t.paso(sigue) == []
    reloj.t += 3
    assert tipos(t.paso(sigue)) == ["pausa"]     # segundo intento
    reloj.t += 3
    acciones = t.paso(sigue)
    assert tipos(acciones) == ["silencio", "volumen"] and volumenes(acciones) == [VOL]
    assert t.programa is None


# ---------- niveles ----------

@pytest.mark.parametrize("nivel, final", [("vm", None), ("suspender", "suspender"), ("apagar", "apagar_pc")])
def test_niveles_con_vm(t, reloj, nivel, final):
    t.programar(nivel, minutos=1)
    reloj.t += 30
    todas, e = hasta_desvanecer(t, reloj, sonando(vm=True))
    pausado = Entorno(volumen=e.volumen, musica="Paused", vm=True)
    t.paso(pausado)
    reloj.t += 1.5
    acciones = t.paso(pausado)
    assert tipos(acciones) == ["volumen", "apagar_vm"] and t.programa.fase == "apagando_vm"
    reloj.t += 10
    assert t.paso(pausado) == []                 # Windows todavía se está apagando
    acciones = t.paso(Entorno(volumen=VOL, musica=None, vm=False))
    assert tipos(acciones) == ([final] if final else []) and t.programa is None


def test_sin_vm_suspende_directo(t, reloj):
    t.programar("suspender", minutos=1)
    reloj.t += 60
    assert tipos(t.paso(Entorno(volumen=VOL, musica=None, vm=False))) == ["suspender"]


def test_si_la_vm_no_termina_apaga_igual(t, reloj):
    t.programar("apagar", minutos=1)
    reloj.t += 60
    assert tipos(t.paso(Entorno(volumen=VOL, musica="Paused", vm=True))) == ["apagar_vm"]
    reloj.t += tz.ESPERA_VM
    assert tipos(t.paso(Entorno(volumen=VOL, musica="Paused", vm=True))) == ["apagar_pc"]


# ---------- al terminar esta canción ----------

def test_fin_de_cancion(t, reloj):
    t.programar(fin_cancion=True, entorno=sonando(restante_ms=60_000))
    assert t.vista()["modo"] == "fin_cancion"
    assert t.paso(sonando(restante_ms=60_000)) == []
    reloj.t += 30
    assert t.paso(Entorno(volumen=VOL, musica="Paused", pista="/x/1", restante_ms=30_000)) == []
    reloj.t += 600                              # en pausa diez minutos: lo que falta no bajó
    assert t.paso(Entorno(volumen=VOL, musica="Paused", pista="/x/1", restante_ms=30_000)) == []
    assert t.programa.fase == "contando"
    reloj.t += 21
    assert t.paso(sonando(restante_ms=9_000)) == []
    reloj.t += 1
    acciones = t.paso(sonando(restante_ms=8_000))
    assert t.programa.fase == "desvaneciendo"
    assert t.programa.duracion == pytest.approx(7.5)        # 8 s menos el medio segundo antes del final


def test_fin_de_cancion_si_ya_cambio_pausa_de_inmediato(t, reloj):
    t.programar(fin_cancion=True, entorno=sonando(restante_ms=60_000))
    reloj.t += 50
    acciones = t.paso(Entorno(volumen=VOL, musica="Playing", pista="/x/2", restante_ms=200_000))
    assert tipos(acciones) == ["volumen", "pausa"] and pct(volumenes(acciones)[0]) == 0


# ---------- guardar y retomar ----------

def test_guardar_y_retomar(t, reloj):
    t.programar("suspender", minutos=20)
    datos = json.loads(json.dumps(t.a_dict()))
    otro = Temporizador(reloj=reloj)
    assert [a.texto for a in otro.cargar(datos)] == ["Temporizador retomado."]
    assert otro.vista() == t.vista()


def test_retomar_desvanecido_a_medias_restaura(t, reloj):
    t.programar(minutos=1)
    reloj.t += 40
    t.paso(sonando())
    datos = json.loads(json.dumps(t.a_dict()))
    otro = Temporizador(reloj=reloj)
    assert volumenes(otro.cargar(datos)) == [VOL] and otro.programa is None


def test_retomar_vencido_o_roto(t, reloj):
    t.programar(minutos=1)
    datos = t.a_dict()
    reloj.t += 3600
    otro = Temporizador(reloj=reloj)
    assert "vencido" in otro.cargar(datos)[0].texto and otro.programa is None
    assert "ilegible" in otro.cargar({"x": 1})[0].texto
    assert otro.cargar(None) == []
