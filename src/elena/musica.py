"""El puente de música: Apple Music de la VM como un reproductor más de Linux.

En Windows, `vm/windows/puente-musica.ps1` lee los controles multimedia (SMTC) y los manda por
COM1, que QEMU conecta a `.cache/vm/musica.sock`. Aquí se lee ese socket y `elena.mpris` publica
el reproductor MPRIS en el bus de sesión (`org.mpris.MediaPlayer2.elena_applemusic`): Noctalia,
las teclas multimedia y playerctl lo ven sin configurar nada. Las órdenes (play/pausa,
siguiente…) hacen el camino de vuelta. Guía: docs/guias/vm-apple-music-atmos.md

- Protocolo: JSON por líneas, versión 1 (ver el plan). Lo que no es JSON se descarta: el
  firmware de la VM escribe su texto de arranque en COM1.
- `EstadoMusica` es el modelo puro: recibe los mensajes y calcula lo que MPRIS necesita. Se prueba
  sin bus ni socket.
- `Puente` conecta el socket (reintenta cada 2 s), saluda (Windows contesta con todo), alimenta
  el modelo, quita el reproductor tras 15 s sin noticias y manda las órdenes.
- La carátula se guarda en `.cache/vm/caratulas/<sha1>.jpg` (las últimas 20) y va en `mpris:artUrl`.
- Lo último que llegó queda en `.cache/vm/musica-estado.json`, para `elena vm musica estado`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import fcntl
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from elena.modelo import ErrorElena

RAIZ = Path(os.environ.get("ELENA") or Path(__file__).resolve().parents[2])
VM = RAIZ / ".cache" / "vm"
SOCKET = VM / "musica.sock"
CARATULAS = VM / "caratulas"
ARCHIVO_ESTADO = VM / "musica-estado.json"
CANDADO = VM / "musica.lock"

VERSION = 1
REINTENTO = 2.0          # s entre intentos de conectar al socket
SIN_NOTICIAS = 15.0      # s sin mensajes de Windows: se quita el reproductor
SALTO_MS = 2000          # un cambio de posición mayor que esto es un salto (señal Seeked)
MAX_CARATULAS = 20
LIMITE_LINEA = 1 << 20   # una carátula de 300×300 en base64 ocupa de 6 a ~80 KB
SEPARADOR = " — "        # Apple Music manda «Artista — Álbum» en el campo del artista
SIN_PISTA = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
ESTADOS = {"reproduciendo": "Playing", "pausa": "Paused", "detenido": "Stopped"}
_SHA1 = re.compile(r"[0-9a-f]{40}")


def log(texto: str) -> None:
    print(texto, flush=True)  # en el servicio, va al journal (journalctl --user -u elena-vm-musica)


def separar(artista: str, album: str) -> tuple[str, str]:
    """(artista, álbum): Apple Music junta los dos en el artista y deja el álbum vacío."""
    if not album and SEPARADOR in artista:
        primero, resto = artista.split(SEPARADOR, 1)
        return primero.strip(), resto.strip()
    return artista, album


@dataclass(frozen=True)
class Pista:
    titulo: str
    artista: str = ""
    album: str = ""
    artista_album: str = ""
    numero: int = 0
    duracion_ms: int = 0
    caratula: str | None = None   # sha1 de la carátula

    @property
    def clave(self) -> tuple[str, str, str, int]:
        return self.titulo, self.artista, self.album, self.duracion_ms

    @property
    def trackid(self) -> str:
        h = hashlib.sha1("\n".join(map(str, self.clave)).encode()).hexdigest()[:16]
        return f"/org/elena/applemusic/pista/p{h}"


@dataclass
class Cambios:
    """Lo que la capa D-Bus tiene que hacer después de un mensaje."""

    registrar: bool = False    # pedir el nombre en el bus (aparece el reproductor)
    quitar: bool = False       # soltar el nombre (desaparece)
    propiedades: set[str] = field(default_factory=set)   # PropertiesChanged
    salto: bool = False        # señal Seeked

    def __bool__(self) -> bool:
        return self.registrar or self.quitar or bool(self.propiedades) or self.salto


class EstadoMusica:
    """El reproductor tal como lo ve MPRIS, armado con los mensajes de Windows."""

    def __init__(self, carpeta: Path = CARATULAS, reloj: Callable[[], float] = time.monotonic) -> None:
        self.carpeta = carpeta
        self.reloj = reloj
        self.app: str | None = None
        self.activo = False            # hay reproductor publicado
        self.estado = "detenido"
        self.pista: Pista | None = None
        self.puede: dict[str, bool] = {}
        self.ultimo: float | None = None   # cuándo llegó el último mensaje
        self._pos_ms = 0.0
        self._pos_t = reloj()

    # ---------- lo que lee MPRIS ----------

    @property
    def playback_status(self) -> str:
        return ESTADOS.get(self.estado, "Stopped") if self.activo else "Stopped"

    def posicion_ms(self) -> float:
        """La última posición de Windows más lo que ha pasado si está sonando."""
        pos = self._pos_ms
        if self.activo and self.estado == "reproduciendo":
            pos += (self.reloj() - self._pos_t) * 1000
        if self.pista and self.pista.duracion_ms > 0:
            pos = min(pos, self.pista.duracion_ms)
        return max(0.0, pos)

    def posicion_us(self) -> int:
        return int(self.posicion_ms() * 1000)

    def ruta_caratula(self) -> Path | None:
        if self.pista and self.pista.caratula:
            ruta = self.carpeta / f"{self.pista.caratula}.jpg"
            if ruta.is_file():
                return ruta
        return None

    def metadata(self) -> dict[str, tuple[str, object]]:
        """Metadata de MPRIS: clave → (firma D-Bus, valor)."""
        p = self.pista
        if not self.activo or p is None:
            return {"mpris:trackid": ("o", SIN_PISTA)}
        m: dict[str, tuple[str, object]] = {"mpris:trackid": ("o", p.trackid), "xesam:title": ("s", p.titulo)}
        if p.artista:
            m["xesam:artist"] = ("as", [p.artista])
        if p.album:
            m["xesam:album"] = ("s", p.album)
        if p.artista_album:
            m["xesam:albumArtist"] = ("as", [p.artista_album])
        if p.numero > 0:
            m["xesam:trackNumber"] = ("i", p.numero)
        if p.duracion_ms > 0:
            m["mpris:length"] = ("x", p.duracion_ms * 1000)
        if arte := self.ruta_caratula():
            m["mpris:artUrl"] = ("s", arte.as_uri())
        return m

    @property
    def controles(self) -> dict[str, bool]:
        """Los Can* de MPRIS. Play/pausa también valen con «alternar» (Apple Music lo permite siempre)."""
        a, p = self.activo, self.puede
        return {
            "CanPlay": a and bool(p.get("play") or p.get("alternar")),
            "CanPause": a and bool(p.get("pausa") or p.get("alternar")),
            "CanGoNext": a and bool(p.get("siguiente")),
            "CanGoPrevious": a and bool(p.get("anterior")),
            "CanSeek": a and bool(p.get("posicion")),
            "CanControl": True,
        }

    def orden_para(self, accion: str) -> str | None:
        """La orden del protocolo para un método de MPRIS, o None si Apple Music no lo permite."""
        if not self.activo:
            return None
        p, sonando = self.puede, self.estado == "reproduciendo"
        if accion == "PlayPause":
            if p.get("alternar"):
                return "alternar"
            accion = "Pause" if sonando else "Play"
        if accion == "Play":
            return "play" if p.get("play") else ("alternar" if p.get("alternar") and not sonando else None)
        if accion == "Pause":
            return "pausa" if p.get("pausa") else ("alternar" if p.get("alternar") and sonando else None)
        permitido = {"Stop": "detener", "Next": "siguiente", "Previous": "anterior"}.get(accion)
        return permitido if permitido and p.get(permitido) else None

    # ---------- mensajes de Windows ----------

    def recibir(self, m: dict) -> Cambios:
        self.ultimo = self.reloj()
        tipo = m.get("t")
        if tipo == "hola":
            self.app = m.get("app") if isinstance(m.get("app"), str) else None
        elif tipo == "estado":
            return self._estado(m)
        elif tipo == "caratula":
            return self._caratula(m)
        elif tipo == "sin_sesion":
            return self.olvidar()
        return Cambios()

    def vencido(self) -> bool:
        return self.ultimo is not None and self.reloj() - self.ultimo > SIN_NOTICIAS

    def olvidar(self) -> Cambios:
        """Sin reproductor: Apple Music cerrada, Windows sin noticias o el socket cortado."""
        antes = self.activo
        self.activo, self.estado, self.pista, self.puede = False, "detenido", None, {}
        self._pos_ms, self._pos_t = 0.0, self.reloj()
        return Cambios(quitar=antes)

    def _estado(self, m: dict) -> Cambios:
        try:
            artista, album = separar(str(m.get("artista") or ""), str(m.get("album") or ""))
            artista_album, _ = separar(str(m.get("artista_album") or ""), "")
            caratula = m.get("caratula")
            pista = Pista(
                titulo=str(m.get("titulo") or ""), artista=artista, album=album, artista_album=artista_album,
                numero=int(m.get("pista") or 0), duracion_ms=int(m.get("duracion_ms") or 0),
                caratula=caratula if isinstance(caratula, str) and _SHA1.fullmatch(caratula) else None)
            posicion = float(m.get("posicion_ms") or 0)
            puede = {str(k): bool(v) for k, v in dict(m.get("puede") or {}).items()}
        except (TypeError, ValueError):
            return Cambios()
        if not pista.titulo:
            # Apple Music recién abierta: «detenido», sin título; el widget mostraría un reproductor vacío
            return self.olvidar()
        antes = (self.metadata(), self.playback_status, self.controles)
        misma = self.activo and self.pista is not None and self.pista.clave == pista.clave
        esperada = self.posicion_ms()
        cambios = Cambios(registrar=not self.activo)
        estado = m.get("estado")
        self.activo, self.pista, self.puede = True, pista, puede
        self.estado = estado if estado in ESTADOS else "detenido"
        self._pos_ms, self._pos_t = max(0.0, posicion), self.reloj()
        cambios.salto = misma and abs(posicion - esperada) > SALTO_MS
        if self.metadata() != antes[0]:
            cambios.propiedades.add("Metadata")
        if self.playback_status != antes[1]:
            cambios.propiedades.add("PlaybackStatus")
        cambios.propiedades |= {k for k, v in self.controles.items() if antes[2].get(k) != v}
        return cambios

    def _caratula(self, m: dict) -> Cambios:
        ident = m.get("id")
        if not isinstance(ident, str) or not _SHA1.fullmatch(ident):
            return Cambios()
        try:
            datos = base64.b64decode(str(m.get("datos") or ""), validate=True)
        except (binascii.Error, ValueError):
            return Cambios()
        if hashlib.sha1(datos).hexdigest() != ident:
            return Cambios()   # llegó incompleta o dañada
        antes = self.metadata()
        guardar_caratula(self.carpeta, ident, datos)
        return Cambios(propiedades={"Metadata"} if self.metadata() != antes else set())

    def resumen(self) -> dict:
        p = self.pista
        return {
            "app": self.app, "activo": self.activo, "estado": self.estado,
            "titulo": p.titulo if p else None, "artista": p.artista if p else None,
            "album": p.album if p else None, "duracion_ms": p.duracion_ms if p else None,
            "posicion_ms": round(self.posicion_ms()), "caratula": str(self.ruta_caratula() or "") or None,
            "controles": {k: v for k, v in self.controles.items() if k != "CanControl"},
        }


def guardar_caratula(carpeta: Path, ident: str, datos: bytes) -> Path:
    """Guarda la carátula (o la marca como reciente) y deja solo las últimas MAX_CARATULAS."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{ident}.jpg"
    if ruta.is_file():
        os.utime(ruta)
    else:
        temporal = ruta.with_suffix(".parcial")
        temporal.write_bytes(datos)
        temporal.replace(ruta)
    viejas = sorted(carpeta.glob("*.jpg"), key=lambda r: r.stat().st_mtime, reverse=True)[MAX_CARATULAS:]
    for vieja in viejas:
        vieja.unlink(missing_ok=True)
    return ruta


def decodificar(linea: bytes) -> dict | None:
    """El mensaje de una línea, o None si no es del puente (p. ej. el texto del firmware)."""
    try:
        m = json.loads(linea)
    except ValueError:
        return None
    return m if isinstance(m, dict) and isinstance(m.get("t"), str) else None


class Puente:
    """La conexión con Windows: alimenta `modelo` y avisa los cambios a `publicar` (la capa D-Bus)."""

    def __init__(self, publicar: Callable[[Cambios, EstadoMusica], None] = lambda c, m: None,
                 ruta: Path = SOCKET, modelo: EstadoMusica | None = None,
                 archivo_estado: Path | None = ARCHIVO_ESTADO, escribir: Callable[[str], None] = log,
                 reintento: float = REINTENTO, sin_noticias: float = SIN_NOTICIAS) -> None:
        self.publicar = publicar
        self.ruta = ruta
        self.modelo = modelo or EstadoMusica()
        self.archivo_estado = archivo_estado
        self.escribir = escribir
        self.reintento = reintento
        self.sin_noticias = sin_noticias
        self.conectado = False
        self._escritor: asyncio.StreamWriter | None = None

    async def correr(self, una_vez: bool = False) -> None:
        """Conecta (y reconecta) al socket de la VM para siempre; con `una_vez`, hasta la primera desconexión."""
        avisado = False
        self._guardar()
        while True:
            try:
                lector, escritor = await asyncio.open_unix_connection(str(self.ruta), limit=LIMITE_LINEA)
            except OSError:
                if not avisado:
                    self.escribir(f"Esperando la VM ({self.ruta})…")
                    avisado = True
                await asyncio.sleep(self.reintento)
                continue
            avisado = False
            self.escribir("Conectado a la VM; se saluda a Windows")
            self._escritor, self.conectado = escritor, True
            self._guardar()
            try:
                self._mandar({"t": "hola", "v": VERSION})
                await self._leer(lector)
            except OSError as e:
                self.escribir(f"Error en el socket: {e}")
            finally:
                self._escritor, self.conectado = None, False
                escritor.close()
                with contextlib.suppress(OSError):
                    await escritor.wait_closed()
                self._aplicar(self.modelo.olvidar())
                self.modelo.ultimo = None
                self._guardar()   # «sin conexión», aunque no hubiera reproductor que quitar
                self.escribir("Se cerró la conexión con la VM")
            if una_vez:
                return
            await asyncio.sleep(self.reintento)

    async def _leer(self, lector: asyncio.StreamReader) -> None:
        vencido = descartado = False
        while True:
            try:
                linea = await asyncio.wait_for(lector.readline(), timeout=self.sin_noticias)
            except TimeoutError:
                if not vencido:
                    # Sin reproductor (Windows arrancando o ya apagándose) no hay nada que avisar
                    if self.modelo.activo:
                        self.escribir(f"Sin noticias de Windows en {self.sin_noticias:.0f} s: se quita el reproductor")
                    self._aplicar(self.modelo.olvidar())
                    vencido = True
                continue
            except ValueError:
                self.escribir("Se descartó una línea demasiado larga")
                continue
            if not linea:
                return   # QEMU cerró el socket (la VM se apagó)
            m = decodificar(linea)
            if m is None:
                if not descartado:
                    self.escribir("Se descarta texto que no es del puente (el firmware escribe en COM1 al arrancar)")
                    descartado = True
                continue
            if vencido:
                vencido = False
                self._mandar({"t": "hola", "v": VERSION})   # que mande todo otra vez
            if m["t"] == "hola":
                self.escribir(f"Windows saludó: {m.get('agente') or '?'}, app {m.get('app') or '(sin sesión)'}")
            self._aplicar(self.modelo.recibir(m))

    def orden(self, nombre: str, **extra: object) -> bool:
        """Manda una orden a Windows; False si no hay con quién."""
        if self._escritor is None or not self.modelo.activo:
            return False
        self._mandar({"t": "orden", "orden": nombre, **extra})
        return True

    def _mandar(self, mensaje: dict) -> None:
        if self._escritor is not None:
            self._escritor.write(json.dumps(mensaje).encode() + b"\n")

    def _aplicar(self, cambios: Cambios) -> None:
        if not cambios:
            return
        m = self.modelo
        if cambios.registrar:
            self.escribir("Reproductor publicado")
        if cambios.quitar:
            self.escribir("Reproductor quitado")
        if "Metadata" in cambios.propiedades and m.pista:
            self.escribir(f"Pista: {m.pista.titulo} · {m.pista.artista} · {m.pista.album}"
                          + ("" if m.ruta_caratula() else " (sin carátula)"))
        try:
            self.publicar(cambios, m)
        except Exception as e:   # un error en D-Bus no debe tumbar la conexión con Windows
            self.escribir(f"Error al publicar en D-Bus: {e!r}")
        self._guardar()

    def _guardar(self) -> None:
        if self.archivo_estado is None:
            return
        datos = {"pid": os.getpid(), "hora": time.time(), "conectado": self.conectado} | self.modelo.resumen()
        try:
            self.archivo_estado.parent.mkdir(parents=True, exist_ok=True)
            temporal = self.archivo_estado.with_suffix(".parcial")
            temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporal.replace(self.archivo_estado)
        except OSError as e:
            self.escribir(f"No pude guardar {self.archivo_estado}: {e}")


class PuenteOcupado(ErrorElena):
    """Ya hay otro puente corriendo. La CLI sale con OCUPADO y el servicio no se reinicia por eso."""


OCUPADO = 75   # EX_TEMPFAIL; en el servicio, RestartPreventExitStatus=75


@contextlib.contextmanager
def candado(ruta: Path | None = None) -> Iterator[IO[str]]:
    """Un solo puente a la vez: QEMU atiende a un cliente por socket y el otro se quedaría colgado."""
    ruta = ruta or CANDADO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PuenteOcupado("Ya hay un puente de música corriendo (¿el servicio elena-vm-musica?).") from None
        yield f


def _vivo(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        return b"musica" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


def _minutos(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def texto_estado(ruta: Path | None = None, ahora: Callable[[], float] = time.time) -> str:
    """`elena vm musica estado`: lo último que dejó el puente."""
    ruta = ruta or ARCHIVO_ESTADO
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"El puente de música no ha corrido (no existe {ruta})."
    except (OSError, ValueError) as e:
        raise ErrorElena(f"No pude leer {ruta}: {e}") from e
    hace = max(0.0, ahora() - float(d.get("hora") or 0))
    lineas = [f"Puente: {'corriendo' if _vivo(d.get('pid')) else 'detenido'} (PID {d.get('pid')}; dato de hace {hace:.0f} s)",
              f"Windows: {'conectado' if d.get('conectado') else 'sin conexión'}"]
    if not d.get("activo"):
        lineas.append("Reproductor: no publicado (Apple Music cerrada, sin canción o la VM apagada)")
        return "\n".join(lineas)
    pos = float(d.get("posicion_ms") or 0)
    if d.get("estado") == "reproduciendo" and _vivo(d.get("pid")):
        pos += hace * 1000
    if d.get("duracion_ms"):
        pos = min(pos, float(d["duracion_ms"]))
    controles = d.get("controles") or {}
    lineas += [
        f"Reproductor: publicado ({d.get('app') or '?'})",
        f"Suena: {d.get('titulo')} · {d.get('artista')} · {d.get('album') or '(sin álbum)'}",
        f"Estado: {d.get('estado')}, {_minutos(pos)} de {_minutos(float(d.get('duracion_ms') or 0))}",
        "Controles: " + (", ".join(k.removeprefix("Can") for k, v in controles.items() if v) or "ninguno"),
        f"Carátula: {d.get('caratula') or 'no'}",
    ]
    return "\n".join(lineas)


def correr(ruta: Path | None = None) -> None:
    """`elena vm musica`: el puente en primer plano (lo corre el servicio elena-vm-musica)."""
    from elena import mpris   # dbus-fast solo se carga aquí

    with candado():
        asyncio.run(mpris.servir(ruta or SOCKET))
