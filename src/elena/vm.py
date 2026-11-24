"""La VM de Windows por su monitor QMP: estado, apagado ordenado y red.

`vm/qemu.sh` abre el monitor en `.cache/vm/qmp.sock` (`-qmp unix:…,server=on,wait=off`). QEMU
corre dentro de la caja elena-vm, pero el socket queda en el repo y el host lo abre igual
(probado el 2026-11-06). El protocolo es JSON por líneas: QEMU saluda, se negocia con
`qmp_capabilities` y después cada orden recibe un `return` o un `error`; entre medio pueden
llegar eventos (`event`), que se saltan.

- apagar: `system_powerdown` es el botón de encendido por ACPI. Windows se apaga solo si el
  botón está en «Apagar» (lo deja así `pulir.ps1`), y QEMU termina cuando Windows se apaga.
- red: `set_link` sobre la red `red` (`-nic user,id=red`) conecta o desconecta el cable virtual.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from elena.modelo import ErrorElena

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[2])
RED = "red"            # id de la red en vm/qemu.sh (-nic user,id=red)
NOMBRE = b"elena-windows"  # -name de QEMU en vm/qemu.sh


def ruta_qmp() -> Path:
    return RAIZ / ".cache" / "vm" / "qmp.sock"


def pid() -> int | None:
    """El PID del QEMU de ELENA (`qemu-system-x86_64 -name elena-windows`), o None."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"qemu-system-x86_64") and NOMBRE in argv:
            return int(proc.name)
    return None


class Qmp:
    """Una conexión al monitor: `with Qmp() as q: q.orden("query-status")`."""

    def __init__(self, ruta: Path | None = None, espera: float = 5.0) -> None:
        self.ruta = ruta or ruta_qmp()
        self.espera = espera

    def __enter__(self) -> Qmp:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(self.espera)
        try:
            self._sock.connect(str(self.ruta))
        except OSError as e:
            self._sock.close()
            raise ErrorElena(f"No puedo abrir el monitor de la VM ({self.ruta}): {e.strerror or e}.") from e
        self._archivo = self._sock.makefile("rwb")
        saludo = self._leer()
        if "QMP" not in saludo:
            self.__exit__()
            raise ErrorElena("El monitor de la VM no respondió como QMP.")
        self.orden("qmp_capabilities")
        return self

    def __exit__(self, *_) -> None:
        for cosa in (getattr(self, "_archivo", None), self._sock):
            try:
                if cosa:
                    cosa.close()
            except OSError:
                pass

    def _leer(self) -> dict:
        try:
            linea = self._archivo.readline()
        except (OSError, TimeoutError) as e:
            raise ErrorElena(f"El monitor de la VM no respondió: {e}.") from e
        if not linea:
            raise ErrorElena("El monitor de la VM cerró la conexión.")
        return json.loads(linea)

    def orden(self, nombre: str, **argumentos) -> object:
        mensaje = {"execute": nombre} | ({"arguments": argumentos} if argumentos else {})
        self._archivo.write(json.dumps(mensaje).encode() + b"\n")
        self._archivo.flush()
        while True:
            respuesta = self._leer()
            if "event" in respuesta:
                continue
            if "error" in respuesta:
                error = respuesta["error"]
                raise ErrorElena(f"QEMU rechazó {nombre}: {error.get('desc', error)}.")
            return respuesta.get("return")


def estado() -> str:
    """Texto corto: «apagada», o «corriendo (running)» con el estado de QEMU."""
    if pid() is None:
        return "apagada"
    try:
        with Qmp() as q:
            info = q.orden("query-status")
    except ErrorElena as e:
        return f"corriendo, sin monitor ({e})"
    return f"corriendo ({info.get('status', '?')})"


def apagar(esperar: float = 120.0, cada: float = 1.0) -> bool:
    """Apaga Windows por ACPI y espera hasta `esperar` segundos a que QEMU termine.

    Sin VM no hace nada y devuelve True: el `ExecStop` del servicio elena-vm también corre
    cuando Windows ya se apagó solo. Devuelve False si QEMU sigue vivo al terminar la espera.
    """
    if pid() is None:
        return True
    with Qmp() as q:
        q.orden("system_powerdown")
    limite = time.monotonic() + esperar
    while time.monotonic() < limite:
        if pid() is None:
            return True
        time.sleep(cada)
    return pid() is None


def red(conectada: bool) -> None:
    """Conecta o desconecta el cable de red virtual de Windows."""
    if pid() is None:
        raise ErrorElena("La VM no está corriendo.")
    with Qmp() as q:
        q.orden("set_link", name=RED, up=conectada)
