"""Paso 6: reglas fijas, escenas y el vigilante."""

import copy

import pytest

from conftest import PROCESOS, nodo_crudo
from elena import escenas, reglas
from elena.modelo import ErrorElena, Grafo, Tipo, buscar_apps, lineal_a_db, pct_a_lineal
from elena.monitor import Lote
from elena.reglas import Regla
from elena.vigilante import Vigilante

# ---------- reglas ----------


def test_coincidencia_con_comodines_y_sin_mayusculas(grafo):
    (pw,) = buscar_apps(grafo, "elena-prueba-pw")
    assert Regla("juegos", app="ELENA-prueba-*").coincide(pw)
    assert Regla("juegos", proceso="pw-cat").coincide(pw)
    assert Regla("juegos", app="elena-prueba-pw", proceso="pw-cat").coincide(pw)       # los dos
    assert not Regla("juegos", app="elena-prueba-pw", proceso="pacat").coincide(pw)    # uno falla
    assert not Regla("juegos").coincide(pw)                                            # sin criterio


def test_criterio_por_nombre_o_por_proceso(grafo, dump):
    (pw,) = buscar_apps(grafo, "elena-prueba-pw")
    assert reglas.criterio(pw) == {"app": "elena-prueba-pw"}
    crudo = nodo_crudo(dump, **{"application.name": "elena-prueba-pulse"})
    crudo["info"]["props"]["application.name"] = " "   # como Wine
    g = Grafo.desde_dump(dump, proceso_de={50233: "Control_DX12.exe", 50232: "pw-cat"}.get)
    wine = next(a for a in g.de_tipo(Tipo.APP) if a.pid == 50233)
    assert reglas.criterio(wine) == {"proceso": "Control_DX12.exe"}


def test_fijar_pone_primero_y_reemplaza(grafo):
    (pw,) = buscar_apps(grafo, "elena-prueba-pw")
    lista = [Regla("chat", app="otra"), Regla("sistema", proceso="pw-cat")]
    lista = reglas.fijar(lista, pw, "juegos")
    assert lista == [Regla("juegos", app="elena-prueba-pw"), Regla("chat", app="otra")]
    assert reglas.destino(lista, pw).bus == "juegos"
    assert reglas.quitar(lista, pw) == [Regla("chat", app="otra")]


def test_archivo_ida_y_vuelta(tmp_path):
    ruta = tmp_path / "reglas.toml"
    assert reglas.cargar(ruta) == []
    lista = [Regla("chat", app="Discord"), Regla("juegos", proceso="ToyShire*")]
    reglas.guardar(lista, ruta)
    assert ruta.read_text().startswith("# Reglas fijas de ELENA")
    assert reglas.cargar(ruta) == lista
    reglas.guardar([], ruta)
    assert reglas.cargar(ruta) == []


def test_archivo_invalido(tmp_path):
    ruta = tmp_path / "reglas.toml"
    ruta.write_text('[[regla]]\nbus = "chat"\n')
    with pytest.raises(ErrorElena, match="al menos uno"):
        reglas.cargar(ruta)
    ruta.write_text("esto no es toml = = =")
    with pytest.raises(ErrorElena, match="TOML"):
        reglas.cargar(ruta)


# ---------- escenas ----------


def test_capturar_y_aplicar_conserva_el_balance(grafo, dump, tmp_path):
    escena = escenas.capturar(grafo, "ahora")
    assert escena.volumen == {"musica": 100, "juegos": 100, "sistema": 50, "chat": 100, "audifonos": 100}
    assert escena.silencio == {"musica": False, "juegos": False, "sistema": False, "chat": False, "audifonos": False}
    ruta = tmp_path / "escenas.toml"
    escenas.guardar({"ahora": escena}, ruta)
    assert escenas.cargar(ruta)["ahora"].volumen == escena.volumen

    # Juegos con balance (FL a −6 dB): la escena lo lleva al 80 % sin perder la diferencia
    bus = nodo_crudo(dump, **{"node.name": "elena_juegos", "media.class": "Audio/Sink"})
    for p in bus["info"]["params"]["Props"]:
        if "channelVolumes" in p:
            p["channelVolumes"] = [0.501187, 1, 1, 1, 1, 1]
    g = Grafo.desde_dump(dump, proceso_de=PROCESOS.get)
    (cmd, silencio) = escenas.comandos(g, escenas.Escena("x", {"juegos": 80}, {"juegos": True}))
    vols = [float(v) for v in cmd[3:]]
    assert max(vols) == pytest.approx(pct_a_lineal(80), abs=1e-6)
    assert lineal_a_db(vols[1]) - lineal_a_db(vols[0]) == pytest.approx(6.0, abs=0.01)
    assert silencio == ["pactl", "set-sink-mute", "elena_juegos", "1"]


def test_escena_con_bus_desconocido(tmp_path):
    ruta = tmp_path / "escenas.toml"
    ruta.write_text("[escenas.x]\nvolumen = { musica = 50, cocina = 20 }\n")
    with pytest.raises(ErrorElena, match="cocina"):
        escenas.cargar(ruta)


# ---------- vigilante ----------


async def _flujo(*lotes):
    for lote in lotes:
        yield lote


def _vigilante(tmp_path, lista):
    ruta = tmp_path / "reglas.toml"
    reglas.guardar(lista, ruta)
    comandos, lineas = [], []
    v = Vigilante(ruta, ejecutar=lambda c: comandos.append(c) or "", escribir=lineas.append)
    return v, comandos, lineas


async def test_vigilante_mueve_al_arrancar_y_las_nuevas(tmp_path, dump):
    v, comandos, lineas = _vigilante(tmp_path, [Regla("musica", app="elena-prueba-pulse"), Regla("juegos", proceso="pw-cat")])
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    nueva = copy.deepcopy(nodo_crudo(dump, **{"application.name": "elena-prueba-pulse"}))
    nueva["id"] = 7777
    nueva["info"]["props"]["object.serial"] = 7777
    await v.correr(_flujo(Lote(dump, reinicio=True), Lote([nueva])))
    # Al arrancar: la de pacat va a Música (estaba en Sistema); la de pw-cat ya estaba en Juegos
    # La nueva (serial 7777) también va a Música
    assert comandos == [["pactl", "move-sink-input", "1332", "elena_musica"],
                        ["pactl", "move-sink-input", "7777", "elena_musica"]]
    assert any("elena-prueba-pulse (serial 1332) → ELENA · Música" in l for l in lineas)


async def test_vigilante_no_pelea_con_movimientos_a_mano(tmp_path, dump):
    v, comandos, _ = _vigilante(tmp_path, [Regla("musica", app="elena-prueba-pulse")])
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    await v.correr(_flujo(Lote(dump, reinicio=True)))
    assert len(comandos) == 1
    v.aplicar(Lote([]))             # cambios después: la app ya vista no se vuelve a mover
    v.aplicar(Lote([]))
    assert len(comandos) == 1


async def test_vigilante_reintenta_y_se_rinde(tmp_path, dump):
    ruta = tmp_path / "reglas.toml"
    reglas.guardar([Regla("musica", app="elena-prueba-pulse")], ruta)
    intentos, lineas = [], []

    def falla(cmd):
        intentos.append(cmd)
        raise ErrorElena("pactl todavía no la conoce")

    v = Vigilante(ruta, ejecutar=falla, escribir=lineas.append)
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    v.aplicar(Lote(dump, reinicio=True))
    for _ in range(10):
        v.aplicar(Lote([]))
    assert len(intentos) == 5 and any("no la pude mover" in l for l in lineas)


async def test_vigilante_relee_las_reglas(tmp_path, dump):
    import os
    v, comandos, _ = _vigilante(tmp_path, [])
    v.grafo = Grafo(proceso_de=PROCESOS.get)
    v.aplicar(Lote(dump, reinicio=True))
    assert comandos == []
    reglas.guardar([Regla("chat", app="elena-prueba-pulse")], v.ruta)
    os.utime(v.ruta, (1, 1))         # mtime distinto aunque el reloj no avance
    nueva = copy.deepcopy(nodo_crudo(dump, **{"application.name": "elena-prueba-pulse"}))
    nueva["id"] = 8888
    nueva["info"]["props"]["object.serial"] = 8888
    v.aplicar(Lote([nueva]))
    assert comandos == [["pactl", "move-sink-input", "8888", "elena_chat"]]


# ---------- CLI ----------


@pytest.fixture
def config(monkeypatch, tmp_path, grafo):
    """La CLI con la foto, los comandos anotados y la config en un directorio temporal."""
    from elena import cli
    comandos = []
    monkeypatch.setattr(reglas, "RUTA", tmp_path / "reglas.toml")
    monkeypatch.setattr(escenas, "RUTA", tmp_path / "escenas.toml")
    monkeypatch.setattr(cli, "cargar_grafo", lambda: grafo)
    monkeypatch.setattr(cli, "ejecutar", lambda c: comandos.append(c) or "")
    return cli, comandos


def test_cli_regla_fija_mueve_y_se_quita(config, capsys):
    cli, comandos = config
    assert cli.main(["regla", "pacat", "juegos"]) == 0
    assert reglas.cargar() == [Regla("juegos", app="elena-prueba-pulse")]
    assert comandos == [["pactl", "move-sink-input", "1332", "elena_juegos"]]   # y la mueve ya
    cli.main(["reglas"])
    assert "1. app = elena-prueba-pulse → juegos" in capsys.readouterr().out
    cli.main(["regla", "pacat", "--quitar"])
    assert reglas.cargar() == []


def test_cli_escenas(config, capsys):
    cli, comandos = config
    cli.main(["escena"])
    assert "(sin escenas)" in capsys.readouterr().out
    cli.main(["escena", "noche", "--guardar"])
    assert escenas.cargar()["noche"].volumen["sistema"] == 50
    assert cli.main(["escena", "noche"]) == 0
    assert any(c[:3] == ["pactl", "set-sink-volume", "elena_sistema"] for c in comandos)
    assert cli.main(["escena", "nada"]) == 1
    cli.main(["escena", "noche", "--borrar"])
    assert escenas.cargar() == {}
