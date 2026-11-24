"""Tu equipo (local/equipo.toml): leerlo, detectarlo, iniciar local/ y generar los .conf desde plantillas/."""

import shutil
import tomllib
from pathlib import Path

import pytest
from conftest import EQUIPO_DE_PRUEBA

from elena import equipo, escenas, reglas, rutas
from elena import calibracion as cb
from elena.modelo import ErrorElena

RAIZ = Path(__file__).resolve().parents[1]
CON_PW_CONFIG = pytest.mark.skipif(not shutil.which("pw-config"), reason="sin pw-config")


def _eq(**cambios) -> equipo.Equipo:
    datos = {**EQUIPO_DE_PRUEBA, **cambios}
    return equipo.desde_datos(datos)


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Un local/ vacío en tmp_path (las plantillas y los ejemplos, los del repo)."""
    carpeta = tmp_path / "local"
    monkeypatch.setattr(rutas, "LOCAL", carpeta)
    monkeypatch.setattr(rutas, "EQUIPO", carpeta / "equipo.toml")
    monkeypatch.setattr(rutas, "CONF_PIPEWIRE", carpeta / "pipewire" / "pipewire.conf.d")
    monkeypatch.setattr(rutas, "CONF_WIREPLUMBER", carpeta / "wireplumber" / "wireplumber.conf.d")
    monkeypatch.setattr(cb, "CONF", carpeta / "pipewire" / "pipewire.conf.d" / "40-elena-calibracion.conf")
    monkeypatch.setattr(cb, "TOML", carpeta / "calibracion.toml")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    return carpeta


# ---------- Leer ----------

def test_lee_el_equipo_y_chat_cae_a_las_bocinas_sin_audifonos():
    eq = _eq()
    assert eq.sink == EQUIPO_DE_PRUEBA["ELENA_SINK_FISICO"] and eq.chat == eq.audifonos
    sin = _eq(ELENA_AUDIFONOS="", ELENA_MIC_FISICO="")
    assert sin.chat == sin.sink and sin.mic == ""


@pytest.mark.parametrize("datos, mensaje", [
    ({"ELENA_TARJETA": {"a": "b"}}, "primer nivel"),
    ({"ELENA_OTRA": "x"}, "desconocidas: ELENA_OTRA"),
    ({"ELENA_SINK_FISICO": ""}, "falta ELENA_SINK_FISICO"),
    ({"ELENA_MIC_FISICO": 'a"b'}, "comillas"),
    ({"ELENA_AUDIFONOS": "con espacio"}, "espacios"),
])
def test_rechaza_un_equipo_mal_escrito(datos, mensaje):
    with pytest.raises(ErrorElena, match=mensaje):
        equipo.desde_datos({**EQUIPO_DE_PRUEBA, **datos})


def test_sin_equipo_dice_que_correr(local):
    with pytest.raises(ErrorElena, match="mise run iniciar"):
        equipo.leer()


# ---------- Plantillas ----------

def test_rellenar_no_deja_marcadores_ni_acepta_desconocidos_o_vacios():
    vals = equipo.valores(_eq(), "elena_salida")
    for a in equipo.archivos(_eq()):
        texto = equipo.rellenar(a.plantilla.read_text(), vals, a.plantilla.name)
        assert not equipo.MARCADOR.search(texto)
    with pytest.raises(ErrorElena, match="@OTRO@"):
        equipo.rellenar("x = @OTRO@", vals)
    with pytest.raises(ErrorElena, match="vacío"):
        equipo.rellenar("x = @MIC@", {**vals, "MIC": ""})


def test_prioridades_sin_repetir():
    assert equipo.prioridades("output:a+input:b") == '"output:a+input:b"\n          "output:a"'
    assert equipo.prioridades("output:a") == '"output:a"'


def test_los_opcionales_dependen_del_mic_y_los_audifonos():
    nombres = lambda eq: {a.destino.name for a in equipo.archivos(eq)}  # noqa: E731
    assert nombres(_eq()) == {"10-elena-buses.conf", "20-elena-mic.conf", "30-elena-visual.conf",
                              "50-elena-audifonos.conf", "10-elena-perfil.conf"}
    assert nombres(_eq(ELENA_MIC_FISICO="", ELENA_AUDIFONOS="")) == {
        "10-elena-buses.conf", "30-elena-visual.conf", "10-elena-perfil.conf"}


@CON_PW_CONFIG
def test_generar_escribe_valida_y_borra_lo_que_ya_no_va(local):
    eq = _eq()
    hechos = equipo.generar(eq)
    pw = rutas.CONF_PIPEWIRE
    assert len(hechos) == 5 and (pw / "20-elena-mic.conf").exists() and (pw / "50-elena-audifonos.conf").exists()
    buses = (pw / "10-elena-buses.conf").read_text()
    assert cb.destino_de_buses(buses) == {eq.sink} and f'target.object      = "{eq.audifonos}"' in buses
    perfil = (rutas.CONF_WIREPLUMBER / "10-elena-perfil.conf").read_text()
    assert f'device.name = "{eq.tarjeta}"' in perfil and '"output:analog-surround-51"' in perfil
    assert equipo.generar(eq) == []                                   # sin cambios, no reescribe nada

    sin = _eq(ELENA_MIC_FISICO="", ELENA_AUDIFONOS="")
    hechos = equipo.generar(sin)
    assert not (pw / "20-elena-mic.conf").exists() and not (pw / "50-elena-audifonos.conf").exists()
    assert any("Borrado" in h and "ELENA_MIC_FISICO" in h for h in hechos)
    assert f'target.object      = "{sin.sink}"' in (pw / "10-elena-buses.conf").read_text()   # Chat a las bocinas


@CON_PW_CONFIG
def test_generar_manda_los_buses_a_la_calibracion_si_esta_activa(local):
    cb.TOML.parent.mkdir(parents=True)
    cb.TOML.write_text((RAIZ / "tests" / "datos" / "calibracion.toml").read_text())
    cb.generar(cb.TOML, cb.CONF, destino="otro_sink")
    hechos = equipo.generar(_eq())
    assert cb.destino_de_buses((rutas.CONF_PIPEWIRE / "10-elena-buses.conf").read_text()) == {cb.NODO}
    assert f'target.object      = "{_eq().sink}"' in cb.CONF.read_text()   # la calibración sigue al sink
    assert any("40-elena-calibracion.conf" in h for h in hechos)


def test_generar_no_toca_nada_si_una_plantilla_no_valida(local, monkeypatch):
    def falla(ruta):
        if "perfil" in ruta.name:
            raise ErrorElena("PipeWire no lee")
    monkeypatch.setattr(equipo, "validar", falla)
    with pytest.raises(ErrorElena):
        equipo.generar(_eq())
    assert not rutas.CONF_PIPEWIRE.exists()


# ---------- Detectar ----------

TARJETAS = [
    {"name": "alsa_card.usb-Mic_USB-00", "profiles": {"output:analog-stereo+input:mono-fallback": {}}},
    {"name": "alsa_card.pci-0000_00_1f.3", "properties": {"device.description": "Audio interno"},
     "profiles": {"output:analog-stereo": {"priority": 6500},
                  "output:analog-surround-51": {"priority": 1300, "available": True},
                  "output:analog-surround-51+input:analog-stereo": {"priority": 1365, "available": True},
                  "output:iec958-ac3-surround-51+input:analog-stereo": {"priority": 100}}},
]
SINKS = [
    {"name": "elena_musica", "properties": {}},
    {"name": "alsa_output.pci-0000_00_1f.3.analog-stereo", "properties": {"device.class": "sound"}},
    {"name": "alsa_output.usb-Audifonos-00.analog-stereo", "description": "Audífonos",
     "properties": {"device.class": "sound"}},
]
FUENTES = [
    {"name": "alsa_output.usb-Audifonos-00.analog-stereo.monitor", "properties": {"device.class": "monitor"}},
    {"name": "elena_mic", "properties": {}},
    {"name": "alsa_input.pci-0000_00_1f.3.analog-stereo", "properties": {"device.class": "sound"}},
    {"name": "alsa_input.usb-Mic_USB-00.mono-fallback", "description": "Mic USB",
     "properties": {"device.class": "sound"}},
]


def test_detectar_elige_el_51_con_entrada_y_el_unico_mic_usb():
    d = equipo.detectar(TARJETAS, SINKS, FUENTES)
    eq = d.equipo
    assert eq.tarjeta == "alsa_card.pci-0000_00_1f.3"
    assert eq.perfil == "output:analog-surround-51+input:analog-stereo"
    assert eq.sink == "alsa_output.pci-0000_00_1f.3.analog-surround-51"
    assert eq.mic == "alsa_input.usb-Mic_USB-00.mono-fallback" and eq.audifonos == ""
    assert [o.nombre for o in d.salidas] == ["alsa_output.usb-Audifonos-00.analog-stereo"]   # ni buses ni la tarjeta
    assert len(d.mics) == 2


def test_el_toml_detectado_se_lee_igual_y_lista_las_alternativas():
    d = equipo.detectar(TARJETAS, SINKS, FUENTES)
    texto = equipo.texto_toml(d)
    assert equipo.desde_datos(tomllib.loads(texto)) == d.equipo
    assert '#   "alsa_output.usb-Audifonos-00.analog-stereo"  Audífonos' in texto


def test_sin_tarjeta_51_lo_dice():
    with pytest.raises(ErrorElena, match="analog-surround-51"):
        equipo.texto_toml(equipo.detectar(TARJETAS[:1], [], []))


def test_iniciar_copia_los_ejemplos_y_no_pisa_nada(local, monkeypatch):
    detectada = equipo.detectar(TARJETAS, SINKS, FUENTES)
    monkeypatch.setattr(equipo, "detectar", lambda: detectada)
    hechos = equipo.iniciar()
    assert {p.name for p in local.iterdir()} == {"equipo.toml", "calibracion.toml", "reglas.toml", "escenas.toml"}
    assert any("revísalo" in h for h in hechos)
    (local / "reglas.toml").write_text("# mías\n")
    hechos = equipo.iniciar()
    assert (local / "reglas.toml").read_text() == "# mías\n" and any("Ya existe" in h for h in hechos)


# ---------- Los ejemplos de config/ ----------

def test_los_ejemplos_se_leen():
    ejemplos = RAIZ / "config"
    equipo.leer(ejemplos / "equipo.ejemplo.toml")
    cb.leer(ejemplos / "calibracion.ejemplo.toml")
    assert reglas.cargar(ejemplos / "reglas.ejemplo.toml") == []
    assert escenas.cargar(ejemplos / "escenas.ejemplo.toml") == {}
