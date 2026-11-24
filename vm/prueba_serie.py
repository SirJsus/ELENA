"""Prueba el puerto serie de la VM (COM1 de Windows ↔ .cache/vm/musica.sock).

Es el lado de Linux de `vm/windows/prueba-serie.ps1`. Se corre con `mise run vm:prueba-serie`
cuando el script de Windows dice «Ahora corre en Linux», no antes: su primer paso mide qué
pasa cuando Windows escribe sin nadie conectado en Linux.

JSON por líneas, como el puente de música:
  Windows → {"t": "hola", "n": N} cada 2 s     Linux → {"t": "hola", "eco": N}
  Windows → inicio y bloque (100 KB de base64)  Linux → {"t": "resultado", …}
  Linux → inicio y bloque (10 KB)               Windows → {"t": "recibido", …}
Las líneas «relleno» son del paso sin nadie conectado: si llegan aquí, QEMU las guardó o Linux
se conectó antes de tiempo, y ese paso no vale.

Uso: python vm/prueba_serie.py [socket]   (por defecto, .cache/vm/musica.sock)
El reporte queda en .cache/vm/prueba-serie.txt.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[1])
SOCKET = RAIZ / ".cache" / "vm" / "musica.sock"
REPORTE = RAIZ / ".cache" / "vm" / "prueba-serie.txt"
ESPERA = 600.0       # segundos, para todo
BLOQUE_LINUX = 7680  # bytes al azar → 10 240 de base64


class Lineas:
    """Lee líneas del socket y anota cuándo llegó el primer byte de cada una."""

    def __init__(self, s: socket.socket) -> None:
        self.s = s
        self.buf = b""
        self.inicio: float | None = None

    def leer(self, limite: float) -> tuple[bytes, float, float]:
        while b"\n" not in self.buf:
            resto = limite - time.monotonic()
            if resto <= 0:
                raise TimeoutError
            self.s.settimeout(resto)
            datos = self.s.recv(65536)
            if not datos:
                raise ConnectionError("QEMU cerró el socket (¿se apagó la VM?)")
            if not self.buf:
                self.inicio = time.monotonic()
            self.buf += datos
        linea, _, self.buf = self.buf.partition(b"\n")
        fin = time.monotonic()
        inicio = self.inicio if self.inicio is not None else fin
        self.inicio = fin if self.buf else None
        return linea, inicio, fin


def conectar(ruta: Path, limite: float) -> socket.socket:
    avisado = False
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(ruta))
            return s
        except (FileNotFoundError, ConnectionRefusedError):
            s.close()
            if time.monotonic() > limite:
                raise TimeoutError from None
            if not avisado:
                print(f"Esperando {ruta} (¿la VM está encendida?)…", flush=True)
                avisado = True
            time.sleep(1)


def sha1(texto: str) -> str:
    return hashlib.sha1(texto.encode("ascii")).hexdigest()


def main(ruta: Path = SOCKET) -> int:
    reporte: list[str] = []

    def anota(texto: str) -> None:
        print(texto, flush=True)
        reporte.append(texto)

    anota(f"ELENA, prueba del puerto serie (lado Linux): {time.strftime('%Y-%m-%d %H:%M:%S')}, {ruta}")
    limite = time.monotonic() + ESPERA
    rellenos = basura = 0
    terminado = False
    try:
        s = conectar(ruta, limite)
        anota("Conectado. Esperando el «hola» de Windows…")
        lector = Lineas(s)

        def manda(mensaje: dict) -> None:
            s.sendall(json.dumps(mensaje).encode() + b"\n")

        saludado = False
        llego_inicio: float | None = None
        while not terminado:
            linea, inicio, fin = lector.leer(limite)
            try:
                m = json.loads(linea)
            except ValueError:
                basura += 1
                continue
            tipo = m.get("t") if isinstance(m, dict) else None
            if tipo == "relleno":
                rellenos += 1
            elif tipo == "hola":
                manda({"t": "hola", "de": "linux", "eco": m.get("n")})
                if not saludado:
                    anota(f"Windows → Linux: «hola» n={m.get('n')}; contestado (Linux → Windows)")
                    saludado = True
            elif tipo == "inicio":
                llego_inicio = fin
            elif tipo == "bloque":
                datos = m.get("datos", "")
                ok = sha1(datos) == m.get("sha1")
                total = fin - (llego_inicio if llego_inicio is not None else inicio)
                tam = len(linea) + 1
                anota(f"Windows → Linux: {tam} bytes en {total:.2f} s ({tam / 1024 / max(total, 1e-6):.1f} KB/s), "
                      f"sha1 {'bien' if ok else 'MAL'}; solo la línea del bloque: {fin - inicio:.2f} s")
                manda({"t": "resultado", "bytes": tam, "segundos": round(total, 3), "sha1_ok": ok})
                datos = base64.b64encode(os.urandom(BLOQUE_LINUX)).decode("ascii")
                manda({"t": "inicio"})
                manda({"t": "bloque", "sha1": sha1(datos), "datos": datos})
                anota(f"Linux → Windows: {len(datos)} bytes de base64 mandados; Windows mide")
            elif tipo == "recibido":
                anota(f"Windows recibió: {m.get('bytes')} bytes en {m.get('segundos')} s, sha1 "
                      f"{'bien' if m.get('sha1_ok') else 'MAL'}")
                terminado = True
    except TimeoutError:
        anota(f"Se acabó la espera ({ESPERA:.0f} s) sin terminar la prueba.")
    except (ConnectionError, OSError) as e:
        anota(f"Se cortó el socket: {e}")
    anota(f"Líneas de relleno recibidas: {rellenos}"
          + (" (QEMU guardó lo que Windows escribió sin nadie conectado, o Linux se conectó antes"
             " de tiempo: el paso 1 de Windows no vale)" if rellenos else
             " (lo que Windows escribió sin nadie conectado no llegó: QEMU lo descartó)"))
    if basura:
        anota(f"Líneas que no son JSON: {basura} (una línea cortada al conectar es normal)")
    anota("Prueba completa." if terminado else "Prueba INCOMPLETA.")
    REPORTE.parent.mkdir(parents=True, exist_ok=True)
    REPORTE.write_text("\n".join(reporte) + "\n", encoding="utf-8")
    print(f"Reporte: {REPORTE}")
    return 0 if terminado else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else SOCKET))
