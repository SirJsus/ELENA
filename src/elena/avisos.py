"""Avisos: lo que hoy se descubre con `estado` o `medir:canales`, dicho en una línea.

Se calculan a partir del grafo, de los procesos de Steam y de los cortes recientes. Cada aviso
tiene un nivel (alerta, aviso o info) y una clave estable, para no repetirlos.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from elena.formato import nombre_salida, texto_volumen
from elena.modelo import BUSES, BUSES_BOCINAS, CALIBRACION, PERFIL_51, SINK_FISICO, Grafo, Tipo

BUS_JUEGOS = "elena_juegos"
BUS_CHAT = "elena_chat"


@dataclass(frozen=True)
class Aviso:
    nivel: str   # alerta | aviso | info
    clave: str
    texto: str


def entornos_steam() -> list[dict[str, str]]:
    """El entorno de cada proceso `steam` (como `pgrep -x steam` en scripts/steam.sh)."""
    entornos = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "steam":
                continue
            crudo = (proc / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        entornos.append(dict(v.decode(errors="replace").split("=", 1) for v in crudo if b"=" in v))
    return entornos


def _es_wine(proceso: str | None) -> bool:
    return bool(proceso) and proceso.lower().endswith(".exe")


def calcular(grafo: Grafo, steam: Iterable[dict[str, str]] = (), cortes: dict[str, int] | None = None,
             reglas: int = 0, vigilante: bool | None = None) -> list[Aviso]:
    """`reglas`: cuántas hay; `vigilante`: si corre (None = no se sabe, no se avisa)."""
    avisos: list[Aviso] = []

    # ELENA y la tarjeta
    faltan = [b for b in BUSES if grafo.nodo(b.nodo) is None]
    if len(faltan) == len(BUSES):
        avisos.append(Aviso("alerta", "sin-elena", "ELENA no está aplicado: no hay buses. Aplícalo desde el menú «:»."))
    else:
        for b in faltan:
            avisos.append(Aviso("alerta", f"falta-{b.clave}", f"Falta el bus {b.etiqueta} ({b.nodo})."))
    perfil = grafo.perfil()
    if perfil != PERFIL_51:
        avisos.append(Aviso("alerta", "perfil", f"La tarjeta no está en 5.1 (perfil: {perfil or 'sin tarjeta'}). Aplica ELENA desde el menú «:»."))
    if (fisico := grafo.nodo(SINK_FISICO)) and fisico.volumen_pct is not None:
        if round(fisico.volumen_pct) != 100 or fisico.silencio:
            avisos.append(Aviso("aviso", "fisico", f"El sink físico está en {texto_volumen(fisico)}: debe ir al 100 % (el volumen se controla en los buses)."))

    # Los audífonos (a donde sale Chat) también van fijos al 100 %: el volumen se maneja en los buses
    # Audífonos y Chat, y así la referencia no se pierde si algo los mueve (su volumen es por hardware)
    if (chat := grafo.nodo("elena_chat.salida")) is not None:
        for sink in grafo.sinks_de(chat):
            if sink.volumen_pct is not None and (round(sink.volumen_pct) != 100 or sink.silencio):
                avisos.append(Aviso("aviso", "audifonos-volumen", f"{sink.visible} está en {texto_volumen(sink)}: debe ir "
                                    "al 100 % (el volumen se controla en los buses Audífonos y Chat; el vigilante lo regresa solo)."))

    # «Solo audífonos»: prendido, pero sin a dónde sonar
    from elena import audifonos
    if audifonos.leer().activo:
        if (au := grafo.audifonos()) is None:
            avisos.append(Aviso("alerta", "audifonos-sin-bus", "«Solo audífonos» está prendido, pero no existe "
                                "elena_audifonos: aplica ELENA desde el menú «:» (con la VM apagada)."))
        elif not au.destinos:
            avisos.append(Aviso("aviso", "audifonos-apagados", "«Solo audífonos» está prendido y los audífonos "
                                "no están conectados: Música, Juegos y Sistema no suenan en ningún lado (h lo apaga)."))

    # Calibración: los buses de bocinas que van a elena_salida
    calibrados = [b for b in BUSES if b.clave in BUSES_BOCINAS
                  and (s := grafo.nodo(b.salida)) and s.objetivo == CALIBRACION]
    calibracion = grafo.nodo(CALIBRACION)
    if calibrados and calibracion is None:
        avisos.append(Aviso("alerta", "sin-calibracion", "Las bocinas esperan a ELENA · Calibración (elena_salida), que no existe: no suenan. "
                            "Revisa 40-elena-calibracion.conf o desactívala (mise run calibracion -- desactivar y aplica ELENA)."))
    elif calibracion and calibracion.volumen_pct is not None and (round(calibracion.volumen_pct) != 100 or calibracion.silencio):
        avisos.append(Aviso("aviso", "calibracion-volumen", f"ELENA · Calibración está en {texto_volumen(calibracion)}: debe ir al 100 % "
                            "(el volumen se controla en los buses)."))
    from elena import tope
    for b in calibrados:
        n = grafo.nodo(b.nodo)
        if n and n.volumen_pct is not None and (limite := tope.limite_pct(grafo, n)) is not None \
                and n.volumen_pct > limite + tope.TOLERANCIA:
            avisos.append(Aviso("aviso", f"tope-{b.clave}", f"{b.etiqueta} está en {n.volumen_pct:.0f} %, arriba de tu "
                                f"máximo ({limite:.0f} %): no subirá más; bájalo cuando quieras."))
        if n and not n.balanceado:
            avisos.append(Aviso("aviso", f"doble-{b.clave}", f"{b.etiqueta} tiene volumen por canal y además va calibrado: el balance se "
                                "aplica dos veces. Ponlo en un solo volumen (o aplica ELENA)."))

    # Steam: abierto sin ELENA (sin PULSE_SINK) o sin winepulse (PROTON_USE_PIPEWIRE=0)
    steam = list(steam)
    if steam and not any(e.get("PULSE_SINK") == BUS_JUEGOS for e in steam):
        avisos.append(Aviso("aviso", "steam", "Steam está abierto sin ELENA: los juegos irán a Sistema en estéreo. Ciérralo y ábrelo con «Steam (ELENA 5.1)» (menú «:»)."))
    elif steam and not any(e.get("PROTON_USE_PIPEWIRE") == "0" for e in steam):
        avisos.append(Aviso("aviso", "steam-pw", "Steam tiene PULSE_SINK pero no PROTON_USE_PIPEWIRE=0: con Proton-CachyOS los juegos irán a Sistema."))

    # Juegos de Wine (procesos .exe)
    juegos = grafo.nodo(BUS_JUEGOS)
    for app in grafo.de_tipo(Tipo.APP):
        if not _es_wine(app.proceso):
            continue
        destinos = grafo.sinks_de(app)  # sin los medidores de una consola abierta
        if app.api == "pw":
            avisos.append(Aviso("aviso", f"winepipewire-{app.serial}", f"{app.visible} usa winepipewire: ignora ELENA y sigue al sink por defecto. Ábrelo desde «Steam (ELENA 5.1)»."))
        elif juegos and destinos and juegos not in destinos and any(d.tipo == Tipo.BUS for d in destinos):
            donde = ", ".join(nombre_salida(d) for d in destinos)
            avisos.append(Aviso("aviso", f"fuera-{app.serial}", f"{app.visible} (Wine) está en {donde}, no en Juegos."))
        elif juegos in destinos and (app.canales or 0) <= 2:
            avisos.append(Aviso("info", f"estereo-{app.serial}", f"{app.visible} sale en {app.canales or '?'} canales en Juegos: el juego no está mandando surround."))

    # Chat sin audífonos: lo que entra a Chat no se oye
    chat = next((b for b in grafo.buses() if b.definicion.nodo == BUS_CHAT), None)
    if chat and chat.nodo and chat.apps and not chat.destinos:
        nombres = ", ".join(sorted({a.visible for a in chat.apps}))
        avisos.append(Aviso("aviso", "chat", f"{nombres} está en Chat, pero los audífonos están apagados: no se oye."))

    # Reglas sin vigilante: nadie las aplica
    if reglas and vigilante is False:
        avisos.append(Aviso("aviso", "vigilante", f"Hay {reglas} regla{'s' if reglas != 1 else ''} fija{'s' if reglas != 1 else ''} pero el vigilante no corre: instálalo con «mise run vigilante» (o aplica ELENA)."))

    # Cortes recientes
    for nombre, n in sorted((cortes or {}).items()):
        avisos.append(Aviso("alerta" if n >= 5 else "aviso", f"cortes-{nombre}", f"{n} corte{'s' if n != 1 else ''} en {nombre} en los últimos 30 s."))
    return avisos

