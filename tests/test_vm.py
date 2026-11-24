"""`elena vm` contra un monitor QMP falso, sin QEMU."""

import json
import socket
import threading
from pathlib import Path

import pytest

from elena import acciones, cli, vm
from elena.modelo import ErrorElena


class QmpFalso:
    """Un QEMU de mentira: saluda, anota cada orden y responde como QMP (con un evento de por medio)."""

    def __init__(self, ruta: Path, errores: dict[str, str] | None = None) -> None:
        self.ruta, self.errores = ruta, errores or {}
        self.ordenes: list[dict] = []
        self._servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._servidor.bind(str(ruta))
        self._servidor.listen()
        self._hilo = threading.Thread(target=self._atender, daemon=True)
        self._hilo.start()

    def _atender(self) -> None:
        while True:
            try:
                conexion, _ = self._servidor.accept()
            except OSError:
                return
            with conexion, conexion.makefile("rwb") as f:
                f.write(b'{"QMP": {"version": {"qemu": {"major": 11}}, "capabilities": []}}\n')
                f.flush()
                for linea in f:
                    orden = json.loads(linea)
                    self.ordenes.append(orden)
                    nombre = orden["execute"]
                    f.write(b'{"event": "NIC_RX_FILTER_CHANGED", "data": {}}\n')
                    if nombre in self.errores:
                        respuesta = {"error": {"class": "GenericError", "desc": self.errores[nombre]}}
                    elif nombre == "query-status":
                        respuesta = {"return": {"status": "running", "running": True}}
                    else:
                        respuesta = {"return": {}}
                    f.write(json.dumps(respuesta).encode() + b"\n")
                    f.flush()

    def nombres(self) -> list[str]:
        return [o["execute"] for o in self.ordenes]

    def cerrar(self) -> None:
        self._servidor.close()


@pytest.fixture
def qmp(tmp_path, monkeypatch):
    ruta = tmp_path / "qmp.sock"
    falso = QmpFalso(ruta)
    monkeypatch.setattr(vm, "ruta_qmp", lambda: ruta)
    monkeypatch.setattr(vm, "pid", lambda: 4242)
    yield falso
    falso.cerrar()


def test_orden_negocia_y_salta_eventos(qmp):
    with vm.Qmp() as q:
        assert q.orden("query-status") == {"status": "running", "running": True}
    assert qmp.nombres() == ["qmp_capabilities", "query-status"]


def test_orden_con_error(tmp_path, monkeypatch):
    ruta = tmp_path / "qmp.sock"
    falso = QmpFalso(ruta, errores={"set_link": "Device 'red' not found"})
    monkeypatch.setattr(vm, "ruta_qmp", lambda: ruta)
    monkeypatch.setattr(vm, "pid", lambda: 4242)
    with pytest.raises(ErrorElena, match="QEMU rechazó set_link: Device 'red' not found"):
        vm.red(False)
    falso.cerrar()


def test_sin_monitor(tmp_path):
    with pytest.raises(ErrorElena, match="No puedo abrir el monitor"):
        with vm.Qmp(tmp_path / "no-existe.sock"):
            pass


def test_estado(qmp, monkeypatch):
    assert vm.estado() == "corriendo (running)"
    monkeypatch.setattr(vm, "pid", lambda: None)
    assert vm.estado() == "apagada"


def test_red(qmp):
    vm.red(False)
    vm.red(True)
    assert [o.get("arguments") for o in qmp.ordenes if o["execute"] == "set_link"] == [
        {"name": "red", "up": False}, {"name": "red", "up": True}]


def test_red_sin_vm(monkeypatch):
    monkeypatch.setattr(vm, "pid", lambda: None)
    with pytest.raises(ErrorElena, match="no está corriendo"):
        vm.red(False)


def test_apagar_espera_a_que_termine(qmp, monkeypatch):
    vivos = iter([4242, 4242, 4242, None])  # antes de mandar, y luego hasta que QEMU termina
    monkeypatch.setattr(vm, "pid", lambda: next(vivos))
    assert vm.apagar(esperar=5, cada=0.01) is True
    assert qmp.nombres() == ["qmp_capabilities", "system_powerdown"]


def test_apagar_se_rinde(qmp):
    assert vm.apagar(esperar=0.05, cada=0.01) is False   # pid() siempre 4242


def test_apagar_sin_vm_no_conecta(monkeypatch):
    # El ExecStop del servicio corre también cuando Windows ya se apagó solo
    monkeypatch.setattr(vm, "pid", lambda: None)
    monkeypatch.setattr(vm, "Qmp", lambda *a, **k: pytest.fail("no debía conectar"))
    assert vm.apagar() is True


def test_cli(qmp, capsys, monkeypatch):
    assert cli.main(["vm"]) == 0
    assert capsys.readouterr().out == "VM: corriendo (running)\n"
    assert cli.main(["vm", "red", "off"]) == 0
    assert "desconectada" in capsys.readouterr().out
    assert cli.main(["vm", "red"]) == 1
    assert "Falta on u off" in capsys.readouterr().err
    monkeypatch.setattr(vm, "pid", lambda: None)
    assert cli.main(["vm", "apagar"]) == 0
    assert capsys.readouterr().out == "La VM ya está apagada.\n"


def test_cmd_vm_con_y_sin_servicio(monkeypatch):
    monkeypatch.setattr(acciones, "RAIZ", Path("/repo"))
    monkeypatch.setattr(acciones, "servicio_vm_instalado", lambda: True)
    assert acciones.cmd_vm() == ["systemctl", "--user", "start", "elena-vm.service"]
    monkeypatch.setattr(acciones, "servicio_vm_instalado", lambda: False)
    assert acciones.cmd_vm()[-1] == "/repo/vm/arrancar.sh"
    assert acciones.cmd_apagar_vm()[-3:] == ["/repo/.venv/bin/elena", "vm", "apagar"]
