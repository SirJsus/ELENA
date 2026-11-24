"""Cambios sobre el grafo: mover apps, volumen, silencio e interruptores del mic.

Cada acción se arma como una lista de argumentos (`cmd_*`) y se ejecuta aparte (`ejecutar`), así
las pruebas revisan los comandos sin tocar el audio. Todo pasa por `pactl` (pipewire-pulse):
- mover: `pactl move-sink-input <object.serial> <sink>` sirve para streams pulse y nativos, y
  WirePlumber lo recuerda al reabrir la app.
- volumen: siempre un valor por canal, como factor lineal (`0.125000` = 50 % = -18 dB). Un solo
  valor iguala los canales y borra el balance.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

from elena import vm
from elena.modelo import ErrorElena, Nodo, Tipo

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[2])

# Por media.class: (comando de pactl, cómo se identifica el nodo)
_PACTL = {
    "Audio/Sink": ("sink", "nombre"),
    "Audio/Source": ("source", "nombre"),
    "Stream/Output/Audio": ("sink-input", "serial"),
    "Stream/Input/Audio": ("source-output", "serial"),
}


def _pactl(nodo: Nodo) -> tuple[str, str]:
    clase = next((c for c in _PACTL if nodo.clase.startswith(c)), None)
    if clase is None:
        raise ErrorElena(f"No sé cambiar {nodo.visible} ({nodo.clase or 'sin media.class'}).")
    tipo, ident = _PACTL[clase]
    valor = nodo.nombre if ident == "nombre" else nodo.serial
    if not valor:
        raise ErrorElena(f"{nodo.visible} no tiene {'node.name' if ident == 'nombre' else 'object.serial'}.")
    return tipo, str(valor)


def cmd_mover(app: Nodo, destino: Nodo) -> list[str]:
    if app.tipo != Tipo.APP:
        raise ErrorElena(f"{app.visible} no es una app que reproduce audio.")
    if not destino.clase.startswith("Audio/Sink"):
        raise ErrorElena(f"{destino.visible} no es una salida.")
    return ["pactl", "move-sink-input", str(app.serial), destino.nombre]


def cmd_mover_grabacion(grabacion: Nodo, sink: Nodo) -> list[str]:
    """Una grabación al monitor de un sink (los visualizadores a elena_visual).

    `pactl move-source-output` sirve para grabaciones pulse (Waywallen) y nativas (Noctalia)
    (verificado el 2026-11-11).
    """
    if grabacion.tipo != Tipo.GRABACION:
        raise ErrorElena(f"{grabacion.visible} no es una app que graba audio.")
    if not sink.clase.startswith("Audio/Sink"):
        raise ErrorElena(f"{sink.visible} no es una salida.")
    return ["pactl", "move-source-output", str(grabacion.serial), f"{sink.nombre}.monitor"]


def cmd_volumen(nodo: Nodo, volumenes: tuple[float, ...]) -> list[str]:
    """`volumenes` lineales, uno por canal y en el orden del nodo (channelMap)."""
    if not volumenes or len(volumenes) != len(nodo.volumenes):
        raise ErrorElena(f"{nodo.visible} tiene {len(nodo.volumenes)} canales y recibí {len(volumenes)} valores.")
    tipo, ident = _pactl(nodo)
    return ["pactl", f"set-{tipo}-volume", ident, *(f"{max(v, 0.0):.6f}" for v in volumenes)]


def cmd_silencio(nodo: Nodo, silenciar: bool | None) -> list[str]:
    """`silenciar` None = alternar."""
    tipo, ident = _pactl(nodo)
    valor = "toggle" if silenciar is None else ("1" if silenciar else "0")
    return ["pactl", f"set-{tipo}-mute", ident, valor]


INTERRUPTORES_MIC = ("voz", "ruido", "puente")


def cmd_mic(interruptor: str, encender: bool) -> list[str]:
    """Los interruptores del mic los maneja scripts/mic.sh (la referencia verificada)."""
    if interruptor not in INTERRUPTORES_MIC:
        raise ErrorElena(f"Interruptor desconocido: {interruptor}. Usa: {', '.join(INTERRUPTORES_MIC)}.")
    return [str(RAIZ / "scripts" / "mic.sh"), interruptor, "on" if encender else "off"]


def ejecutar(cmd: list[str]) -> str:
    try:
        resultado = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise ErrorElena(f"No encuentro {cmd[0]}.") from e
    except subprocess.CalledProcessError as e:
        detalle = (e.stderr or e.stdout or "").strip()
        raise ErrorElena(f"Falló `{' '.join(cmd)}`" + (f": {detalle}" if detalle else "")) from e
    return resultado.stdout


# ---------- Acciones de la consola ----------

# Canales de scripts/probar-canales.sh (orden ALSA del 5.1): 1=FL 2=FR 3=RL 4=RR 5=FC 6=LFE
CANAL_PROBAR = {"FL": 1, "FR": 2, "RL": 3, "RR": 4, "SL": 3, "SR": 4, "FC": 5, "LFE": 6}


def con_mise(cmd: list[str]) -> list[str]:
    """Los scripts necesitan el entorno de mise.toml (ELENA, TMPDIR, ELENA_*)."""
    return ["mise", "exec", "-C", str(RAIZ), "--", *cmd]


def cmd_probar(nodo: Nodo, posicion: str | None) -> list[str]:
    """Una bocina (ruido rosa; 50 Hz en el LFE) o, sin `posicion`, todas con voz."""
    if not nodo.clase.startswith("Audio/Sink") or not nodo.nombre:
        raise ErrorElena(f"{nodo.visible} no es una salida: elige un bus.")
    cmd = [str(RAIZ / "scripts" / "probar-canales.sh"), nodo.nombre]
    if posicion is not None:
        if posicion not in CANAL_PROBAR:
            raise ErrorElena(f"No sé probar el canal {posicion}.")
        cmd.append(str(CANAL_PROBAR[posicion]))
    return con_mise(cmd)


def cmd_aplicar() -> list[str]:
    return con_mise([str(RAIZ / "scripts" / "aplicar.sh"), "todo"])


def cmd_quitar() -> list[str]:
    return con_mise([str(RAIZ / "scripts" / "quitar.sh")])


SERVICIO_VM = "elena-vm.service"


def servicio_vm_instalado() -> bool:
    """¿Está el servicio elena-vm (`vm/lanzador.sh instalar`)?"""
    return (Path.home() / ".config" / "systemd" / "user" / SERVICIO_VM).is_symlink()


def cmd_vm() -> list[str]:
    """Arranca la VM: con el servicio elena-vm si está (Linux la apaga en orden al cerrar sesión)."""
    if servicio_vm_instalado():
        return ["systemctl", "--user", "start", SERVICIO_VM]
    return con_mise([str(RAIZ / "vm" / "arrancar.sh")])


def cmd_apagar_vm() -> list[str]:
    """Apaga Windows por ACPI y espera a que QEMU termine (`elena vm apagar`)."""
    return con_mise([str(RAIZ / ".venv" / "bin" / "elena"), "vm", "apagar"])


def cmd_steam() -> list[str]:
    return con_mise([str(RAIZ / "scripts" / "steam.sh")])


def vm_corriendo() -> bool:
    """¿Hay un QEMU con `-name elena-windows`? (vm/qemu.sh)"""
    return vm.pid() is not None


def lanzar(cmd: list[str], log: str | None = None) -> subprocess.Popen:
    """Arranca un proceso en su propia sesión, separado de la consola (sobrevive si se cierra).

    Con `log`, su salida va a .cache/tmp/consola-<log>.log; sin él, se descarta.
    """
    if log:
        ruta = RAIZ / ".cache" / "tmp" / f"consola-{log}.log"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        salida = open(ruta, "wb")
    else:
        salida = subprocess.DEVNULL
    try:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=salida, stderr=subprocess.STDOUT,
                                start_new_session=True, cwd=RAIZ)
    except FileNotFoundError as e:
        raise ErrorElena(f"No encuentro {cmd[0]}.") from e
    finally:
        if log:
            salida.close()


def detener(proceso: subprocess.Popen, gracia: float = 0.5) -> None:
    """Corta un proceso lanzado con `lanzar` y todo lo que abrió (su grupo).

    SIGTERM primero y, a los `gracia` segundos, SIGKILL a lo que quede del grupo: speaker-test
    a veces ignora SIGTERM (ver scripts/probar-canales.sh), y si solo mueren mise, el script y
    `timeout`, queda huérfano sonando hasta el final.
    """
    grupo = proceso.pid

    def senal(s: int) -> None:
        try:
            os.killpg(grupo, s)
        except ProcessLookupError:
            pass  # el grupo ya no tiene procesos

    senal(signal.SIGTERM)
    remate = threading.Timer(gracia, senal, args=(signal.SIGKILL,))
    remate.daemon = True
    remate.start()


def ejecutar_con_salida(cmd: list[str]) -> tuple[int, str]:
    """Para aplicar y quitar: corre hasta el final y devuelve el código y toda la salida."""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=RAIZ)
    except FileNotFoundError as e:
        raise ErrorElena(f"No encuentro {cmd[0]}.") from e
    return r.returncode, (r.stdout + r.stderr).strip()
