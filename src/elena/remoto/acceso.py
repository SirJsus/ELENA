"""Quién puede usar el control remoto: el token, los hosts permitidos y la IP de la PC.

- El **token** (`.cache/remoto/token`, permisos 600) va en la URL del QR (`/?t=…`). Al abrirla, el
  servidor pone una cookie `HttpOnly; SameSite=Strict` con el token, y el resto de las peticiones
  la exigen. `elena remoto token nuevo` lo cambia: los teléfonos emparejados dejan de entrar.
- **Host** permitido: la IP de la PC en la LAN, `localhost`, `127.0.0.1` o `<hostname>.local`,
  con el puerto. Contra DNS rebinding: una página de internet que resuelve su nombre a la IP de la
  PC manda su propio nombre en `Host`.
- **Origin**: los POST y el WebSocket deben venir de la propia página (contra CSRF).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

from elena.modelo import ErrorElena

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[3])
CARPETA = RAIZ / ".cache" / "remoto"
COOKIE = "elena_remoto"
PUERTO = int(os.environ.get("ELENA_REMOTO_PUERTO") or 8765)
CACHE_HOSTS = 30.0   # s antes de volver a leer las IPs de la PC


class Token:
    """El token en disco; se relee si cambió (así `token nuevo` aplica sin reiniciar el servidor)."""

    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = ruta or CARPETA / "token"
        self._valor: str | None = None
        self._mtime: float | None = None

    def valor(self, crear: bool = True) -> str | None:
        try:
            mtime = self.ruta.stat().st_mtime
        except FileNotFoundError:
            return self.nuevo() if crear else None
        if mtime != self._mtime:
            self._valor, self._mtime = self.ruta.read_text().strip() or None, mtime
        return self._valor

    def nuevo(self) -> str:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        valor = secrets.token_urlsafe(32)
        temporal = self.ruta.with_suffix(".nuevo")
        descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as f:
            f.write(valor + "\n")
        temporal.replace(self.ruta)
        self._valor, self._mtime = valor, self.ruta.stat().st_mtime
        return valor

    def valido(self, dado: str | None) -> bool:
        valor = self.valor(crear=False)
        return bool(dado and valor) and secrets.compare_digest(dado.encode(), valor.encode())


def ip_local() -> str:
    """La IPv4 de la PC en la LAN: la de la ruta por defecto. No manda paquetes (UDP sin enviar)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))   # TEST-NET-1: solo se consulta la tabla de rutas
            return s.getsockname()[0]
        except OSError as e:
            raise ErrorElena(f"No encuentro la IP de la PC en la red: {e}.") from e


def ips_locales() -> set[str]:
    """Todas las IPv4 de la PC (`ip -j -4 addr`)."""
    try:
        salida = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True,
                                check=True).stdout
        return {a["local"] for i in json.loads(salida) for a in i.get("addr_info", []) if a.get("local")}
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        return {"127.0.0.1"}


class Hosts:
    """Los valores de `Host` aceptados. Si llega uno desconocido, relee las IPs (cambió el DHCP)."""

    def __init__(self, puerto: int, leer_ips=ips_locales, reloj=time.monotonic) -> None:
        self.puerto = puerto
        self.leer_ips = leer_ips
        self.reloj = reloj
        self._permitidos: set[str] = set()
        self._leido = -CACHE_HOSTS

    def _calcular(self) -> None:
        nombres = {"localhost", "127.0.0.1", f"{socket.gethostname()}.local", *self.leer_ips()}
        self._permitidos = {f"{n}:{self.puerto}" for n in nombres}
        self._leido = self.reloj()

    def permitido(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower()
        if host in self._permitidos:
            return True
        if self.reloj() - self._leido >= CACHE_HOSTS:
            self._calcular()
        return host in self._permitidos


def url(token: str | None = None, puerto: int = PUERTO, ip: str | None = None) -> str:
    base = f"http://{ip or ip_local()}:{puerto}/"
    return f"{base}?t={token}" if token else base
