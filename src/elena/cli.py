"""Comando `elena`: sin argumentos abre la consola (TUI); con un subcomando funciona como CLI.

  elena estado [--json] [--todo]        buses, apps, salidas, grabaciones y mic
  elena equipo [detectar|generar|iniciar]   tu equipo (local/equipo.toml) y los .conf que salen de él
  elena mover <app> <destino>           destino: musica, juegos, sistema, chat o una salida
                                        (o visual, para un visualizador que graba el sistema)
  elena volumen <objetivo> <n|+n|-n>    % como pavucontrol; conserva el balance entre canales
  elena silenciar <objetivo> [on|off]   sin on/off, alterna
  elena escena [nombre] [--guardar|--borrar]   sin nombre, lista las escenas
  elena reglas                          lista las reglas fijas
  elena regla <app> <bus> | --quitar    «esta app siempre a este bus» (visual: visualizadores)
  elena visual [bus [on|off]]           qué buses entran a la mezcla de los visualizadores
  elena audifonos [on|off|alternar]     «solo audífonos»: Música, Juegos y Sistema a donde va Chat
  elena audifonos binaural|directo      cómo suenan ahí: surround binaural (HRTF) o estéreo directo
  elena audifonos graves <dB> [--hz F] · lfe <dB>   el estante de graves y el LFE, en vivo y guardados
  elena vigilar [--una-vez]             aplica las reglas a cada app nueva (el servicio)
  elena vm [estado|apagar|red on|off]   la VM de Windows por su monitor QMP
  elena vm musica [estado]              Apple Music de la VM como reproductor MPRIS
  elena remoto [qr|token nuevo|estado]  control remoto de música desde el teléfono
  elena calibracion generar             local/calibracion.toml → 40-elena-calibracion.conf
  elena calibracion activar|desactivar  la calibración en las bocinas (se aplica con mise run aplicar)
  elena calibracion estado              si está activa, sus valores y si elena_salida está bien
  elena calibracion afinar (o ab)       en vivo con música: elige bocina, + / −, A/B contra neutro, g guarda
  elena calibracion <bocina> ganancia|retardo|distancia <valor>   en vivo y guardado (fl fr fc rl rr sub)
  elena calibracion nivel|lfe <dB> · cruce <Hz>                   en vivo y guardado
  elena calibracion estereo <dB> [--bus B]  cuánto baja un bus mientras suena estéreo (lo aplica el vigilante)
  elena calibracion eq [--sub]          las bandas del EQ y su respuesta; y en vivo y guardado:
      eq agregar <pico|grave|agudo> <Hz> <dB> [--q Q] · eq <n> tipo|frecuencia|ganancia|q <v> · eq quitar <n> · eq limpiar
  elena calibracion volumen [--canal C] el volumen más alto cómodo por bocina (va primero)
  elena calibracion satelites [--mic]   hasta qué tan grave llega cada satélite, a ese volumen
  elena calibracion nivelar [--mic]     el balance entre las satélites, desde neutro y a ciegas (A/B contra FL)
  elena calibracion musica [--restaurar] ¿el sub aguanta tu volumen ideal? Con música real
  elena calibracion comparar             tu 100 %, tu sub y tu máximo con música, desde neutro y a ciegas
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from elena import __version__, audifonos, avisos, escenas, reglas, tope, vm
from elena.acciones import cmd_mover, cmd_mover_grabacion, cmd_silencio, cmd_volumen, ejecutar
from elena.formato import nombre_salida, texto_app, texto_canales, texto_volumen
from elena.modelo import (
    BUSES, PERFIL_51, SINK_FISICO, VISUAL, VISUAL_BUSES, Bus, ErrorElena, Grafo, Nodo, Tipo,
    alimentador_visual, buscar_apps, buscar_objetivo, buscar_sink, buscar_visual, escalar, lineal_a_db, lineal_a_pct,
    normalizar,
)
from elena.monitor import leer_estado


def cargar_grafo() -> Grafo:
    try:
        return Grafo.desde_dump(leer_estado())
    except (OSError, subprocess.CalledProcessError) as e:
        raise ErrorElena(f"No pude leer el estado de PipeWire con pw-dump: {e}") from e


# ---------- estado ----------

def texto_estado(grafo: Grafo, todo: bool = False) -> str:
    lineas = []
    perfil = grafo.perfil()
    aviso = "" if perfil == PERFIL_51 else "   ⚠ no es el perfil 5.1 de ELENA"
    lineas.append(f"Perfil:   {perfil or '(tarjeta no encontrada)'}{aviso}")
    if fisico := grafo.nodo(SINK_FISICO):
        lineas.append(f"Física:   {nombre_salida(fisico)} ({fisico.nombre}) · {texto_volumen(fisico)} · {fisico.estado or '-'}")
    else:
        lineas.append(f"Física:   {SINK_FISICO} no existe (¿perfil estéreo?)")
    lineas.append(f"Defecto:  salida {grafo.sink_por_defecto or '-'} · entrada {grafo.fuente_por_defecto or '-'}")
    if mic := grafo.mic():
        lineas.append(f"Mic:      voz {mic.voz} · ruido {mic.ruido} · puente {mic.puente}")
    else:
        lineas.append("Mic:      (sin mic virtual)")
    lineas.append(f"Visual:   {texto_visual(grafo)}")
    lineas.append(f"Audífonos: {audifonos.texto(grafo, audifonos.leer())}")

    for bus in grafo.buses():
        lineas.append("")
        lineas.extend(_texto_bus(bus))

    if otras := grafo.otras_salidas():
        lineas += ["", "Directo a una salida"]
        for salida, apps in otras:
            lineas.append(f"  {nombre_salida(salida)} ({salida.nombre})")
            lineas += [f"    ▸ {texto_app(a)}" for a in apps]
    if sueltas := grafo.apps_sueltas():
        lineas += ["", "Sin conectar"]
        lineas += [f"  ▸ {texto_app(a)}" for a in sueltas]
    if fuentes := grafo.de_tipo(Tipo.FUENTE):
        lineas += ["", "Entradas"]
        for f in sorted(fuentes, key=lambda f: (not f.nombre.startswith("elena_"), f.nombre)):
            lineas.append(f"  {f.visible} ({f.nombre}) · {texto_canales(f)} · {texto_volumen(f)} · {f.estado or '-'}")
    if grabaciones := grafo.de_tipo(Tipo.GRABACION):
        lineas += ["", "Grabaciones"]
        for g in grabaciones:
            origen = ", ".join(o.nombre for o in grafo.origenes(g)) or "-"
            lineas.append(f"  ◂ {texto_app(g)} ← {origen}")
    if lista := avisos.calcular(grafo, avisos.entornos_steam()):
        lineas += ["", "Avisos"]
        iconos = {"alerta": "⚠", "aviso": "⚠", "info": "ℹ"}
        lineas += [f"  {iconos.get(a.nivel, '·')} {a.texto}" for a in lista]
    if todo:
        lineas += ["", "Internos de ELENA"]
        for n in sorted(grafo.de_tipo(Tipo.INTERNO), key=lambda n: n.nombre):
            destino = ", ".join(d.nombre for d in grafo.destinos(n)) or "-"
            lineas.append(f"  {n.nombre} · {n.clase} · {texto_canales(n)} · {n.estado or '-'} · {texto_volumen(n)} → {destino}")
    return "\n".join(lineas)


ETIQUETA_BUS = {b.clave: b.etiqueta for b in BUSES}


def texto_visual(grafo: Grafo) -> str:
    """La mezcla para visualizadores en una línea: qué buses entran y quién la escucha."""
    visual = grafo.visual()
    if visual is None:
        return f"(sin {VISUAL}: falta `mise run aplicar`)"
    estado = {True: "on", False: "off", None: "?"}
    partes = [f"{ETIQUETA_BUS[c].lower()} {estado[v]}" for c, v in visual.buses.items()]
    oyentes = ", ".join(sorted({o.visible for o in visual.oyentes})) or "nadie escucha"
    return " · ".join(partes) + f"  ← {oyentes}"


def _texto_bus(bus: Bus) -> list[str]:
    d = bus.definicion
    if bus.nodo is None:
        return [f"{d.etiqueta} ({d.nodo}): no existe. ¿Falta `mise run aplicar`?"]
    n = bus.nodo
    if bus.destinos:
        destino = ", ".join(nombre_salida(x) for x in bus.destinos)
    elif bus.esperando:
        destino = f"esperando a {bus.esperando} (apagado o desconectado)"
    else:
        destino = "sin destino"
    lineas = [f"{d.etiqueta} · {n.nombre} · {texto_canales(n)} → {destino} · {texto_volumen(n)} · {n.estado or '-'}"]
    lineas += [f"  ▸ {texto_app(a)}" for a in bus.apps] or ["  (sin apps)"]
    return lineas


def _dict_nodo(n: Nodo) -> dict:
    return {
        "id": n.id, "serial": n.serial, "nombre": n.nombre, "visible": n.visible, "tipo": n.tipo.value,
        "app": n.app, "proceso": n.proceso, "media": n.media, "api": n.api,
        "canales": n.canales, "posiciones": list(n.posiciones), "formato": n.formato, "tasa": n.tasa,
        "estado": n.estado, "volumen_pct": None if n.volumen_pct is None else round(n.volumen_pct, 1),
        "volumenes_db": [None if math.isinf(db := lineal_a_db(v)) else round(db, 2) for v in n.volumenes],
        "silencio": n.silencio,
    }


def dict_estado(grafo: Grafo, todo: bool = False) -> dict:
    mic = grafo.mic()
    visual = grafo.visual()
    resultado = {
        "perfil": grafo.perfil(),
        "perfil_51": grafo.perfil() == PERFIL_51,
        "sink_fisico": _dict_nodo(f) if (f := grafo.nodo(SINK_FISICO)) else None,
        "sink_por_defecto": grafo.sink_por_defecto,
        "fuente_por_defecto": grafo.fuente_por_defecto,
        "mic": None if mic is None else vars(mic),
        "visual": None if visual is None else {
            "buses": visual.buses, "oyentes": [_dict_nodo(o) for o in visual.oyentes],
        },
        "audifonos": None if (au := grafo.audifonos()) is None else {
            "activo": audifonos.leer().activo, "camino": au.camino, "enviados": au.enviados,
            "graves": audifonos.leer().graves, "frecuencia": audifonos.leer().frecuencia,
            "lfe": audifonos.leer().lfe,
            "destinos": [d.nombre for d in au.destinos], "chat": [d.nombre for d in au.chat],
            "esperando": au.esperando,
        },
        "buses": [
            {
                "clave": b.definicion.clave, "etiqueta": b.definicion.etiqueta, "nodo": b.definicion.nodo,
                "existe": b.nodo is not None,
                **({"bus": _dict_nodo(b.nodo)} if b.nodo else {}),
                "destinos": [x.nombre for x in b.destinos], "esperando": b.esperando,
                "apps": [_dict_nodo(a) for a in b.apps],
            }
            for b in grafo.buses()
        ],
        "otras_salidas": [{"salida": _dict_nodo(s), "apps": [_dict_nodo(a) for a in apps]}
                          for s, apps in grafo.otras_salidas()],
        "sin_conectar": [_dict_nodo(a) for a in grafo.apps_sueltas()],
        "entradas": [_dict_nodo(f) for f in grafo.de_tipo(Tipo.FUENTE)],
        "grabaciones": [_dict_nodo(g) | {"origenes": [o.nombre for o in grafo.origenes(g)]}
                        for g in grafo.de_tipo(Tipo.GRABACION)],
    }
    resultado["avisos"] = [vars(a) for a in avisos.calcular(grafo, avisos.entornos_steam())]
    if todo:
        resultado["internos"] = [_dict_nodo(n) for n in grafo.de_tipo(Tipo.INTERNO)]
    return resultado


# ---------- cambios ----------

def nuevo_volumen(n: Nodo, valor: str) -> tuple[float, ...]:
    """`valor`: 80 u 80% (absoluto), +5 o -5 (relativo), en % como pavucontrol. Tope: 150 %."""
    texto = valor.strip().removesuffix("%")
    try:
        numero = float(texto)
    except ValueError as e:
        raise ErrorElena(f"Volumen inválido: «{valor}». Usa 80, +5 o -5.") from e
    if not n.volumenes:
        raise ErrorElena(f"{n.visible} no tiene control de volumen.")
    actual = n.volumen_pct or 0.0
    objetivo = actual + numero if texto[0] in "+-" else numero
    return escalar(n.volumenes, min(max(objetivo, 0.0), 150.0))


def _activar(valor: str | None) -> bool | None:
    if valor is None:
        return None
    if valor in ("on", "si", "sí", "1"):
        return True
    if valor in ("off", "no", "0"):
        return False
    raise ErrorElena(f"Usa on u off (o nada para alternar), no «{valor}».")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="elena", description="Consola de ELENA (buses de PipeWire).")
    parser.add_argument("--version", action="version", version=f"elena {__version__}")
    sub = parser.add_subparsers(dest="orden", metavar="orden")

    p = sub.add_parser("equipo", help="tu equipo (local/equipo.toml): sin nada, lo muestra; detectar, generar o iniciar")
    p.add_argument("accion", nargs="?", choices=["detectar", "generar", "iniciar"],
                   help="detectar: propone un equipo.toml · generar: los .conf de local/ (lo corre aplicar) · "
                        "iniciar: crea local/ desde los ejemplos y lo detectado")
    p.add_argument("--escribir", action="store_true", help="con detectar: lo guarda en local/equipo.toml si no existe")

    p = sub.add_parser("estado", help="buses, apps, salidas, grabaciones y mic")
    p.add_argument("--json", action="store_true", help="salida en JSON (para scripts o Waybar)")
    p.add_argument("--todo", action="store_true", help="incluye los nodos internos de ELENA")

    p = sub.add_parser("mover", help="mueve una app a un bus o a una salida")
    p.add_argument("app", help="nombre de la app o del proceso, o su serial (ver `elena estado`)")
    p.add_argument("destino", help="musica, juegos, sistema, chat, o una salida (node.name o parte de su nombre); "
                                   "visual mueve un visualizador (una app que graba) a elena_visual")

    p = sub.add_parser("volumen", help="volumen de un bus, mic, salida o app")
    p.add_argument("objetivo", help="bus (musica, juegos…), node.name, o app")
    p.add_argument("valor", help="80 (absoluto) o +5 / -5 (relativo), en %% como pavucontrol")

    p = sub.add_parser("silenciar", help="silencia o activa un bus, mic, salida o app")
    p.add_argument("objetivo")
    p.add_argument("modo", nargs="?", help="on (silenciar), off (activar); sin nada, alterna")

    p = sub.add_parser("escena", help="aplica, guarda o borra una escena (volumen y silencio de los buses)")
    p.add_argument("nombre", nargs="?", help="sin nombre, lista las escenas")
    accion = p.add_mutually_exclusive_group()
    accion.add_argument("--guardar", action="store_true", help="guarda los volúmenes actuales con ese nombre")
    accion.add_argument("--borrar", action="store_true", help="borra la escena")

    sub.add_parser("reglas", help="lista las reglas fijas (local/reglas.toml)")

    p = sub.add_parser("regla", help="«esta app siempre a este bus» (o --quitar)")
    p.add_argument("app", help="una app que esté sonando: nombre, proceso o serial")
    p.add_argument("bus", nargs="?", help="musica, juegos, sistema, chat o una salida; "
                                          "visual para un visualizador (una app que graba)")
    p.add_argument("--quitar", action="store_true", help="quita las reglas de esa app")

    p = sub.add_parser("visual", help="mezcla para visualizadores (elena_visual): qué buses entran")
    p.add_argument("bus", nargs="?", help=", ".join(VISUAL_BUSES) + "; sin él, muestra el estado")
    p.add_argument("modo", nargs="?", help="on (entra) u off (no entra); sin nada, alterna")

    p = sub.add_parser("audifonos", help="«solo audífonos»: Música, Juegos y Sistema a donde va Chat")
    p.add_argument("modo", nargs="?", choices=(*audifonos.ORDENES, "graves", "lfe"),
                   help="on, off, alternar (el modo); binaural, directo (cómo suena); graves o lfe con su valor; "
                        "sin nada, el estado")
    p.add_argument("valor", nargs="?", type=float, help="con graves o lfe: dB (graves −6 a 12, lfe −10 a 20)")
    p.add_argument("--hz", type=float, help="con graves: la frecuencia del estante (40 a 250 Hz)")

    p = sub.add_parser("vigilar", help="aplica las reglas a cada app nueva (lo corre el servicio elena-vigilante)")
    p.add_argument("--una-vez", action="store_true", help="aplica las reglas a lo que suena ahora y termina")

    p = sub.add_parser("vm", help="la VM de Windows (Apple Music): estado, apagar, red o musica")
    p.add_argument("accion", nargs="?", choices=["estado", "apagar", "red", "musica"], default="estado")
    p.add_argument("modo", nargs="?",
                   help="con «red»: on u off (el cable); con «musica»: estado (sin él, corre el puente)")
    p.add_argument("--esperar", type=float, default=120, metavar="S",
                   help="con «apagar»: segundos que espera a que Windows termine (120)")

    p = sub.add_parser("remoto", help="control remoto de música desde el teléfono")
    p.add_argument("accion", nargs="?", choices=["servir", "qr", "token", "estado"], default="servir",
                   help="servir (por defecto; lo corre el servicio elena-remoto), qr, token nuevo o estado")
    p.add_argument("modo", nargs="?", help="con «token»: nuevo (los teléfonos emparejados dejan de entrar)")

    p = sub.add_parser("calibracion", help="calibración de las bocinas")
    cal = p.add_subparsers(dest="accion", metavar="accion", required=True)
    p = cal.add_parser("satelites", help="hasta qué tan grave llega cada satélite: tonos cada 10 Hz por bocina")
    p.add_argument("--canal", action="append", type=str.upper, metavar="C",
                   help="solo esta bocina: FL, FR, FC, RL o RR (se puede repetir; por defecto, las 5)")
    p.add_argument("--desde", type=int, default=200, metavar="HZ", help="primer tono, el más agudo (200)")
    p.add_argument("--hasta", type=int, default=40, metavar="HZ", help="último tono, el más grave (40, el mínimo)")
    p.add_argument("--paso", type=int, default=10, metavar="HZ", help="entre tonos (10)")
    p.add_argument("--relativo", type=float, default=0.0, metavar="DB",
                   help="respecto a tu volumen cómodo de la prueba de volumen, de -20 a 0 dB (0)")
    p.add_argument("--en-bus", action="store_true",
                   help="sin la prueba de volumen: tonos al bus de Música, a --nivel")
    p.add_argument("--nivel", type=float, default=-20.0, metavar="DB",
                   help="con --en-bus: pico del tono en el bus, de -40 a -10 dBFS (-20)")
    p.add_argument("--mic", action="store_true", help="además graba cada tono con el mic físico y mide "
                                                      "nivel y distorsión")
    p.add_argument("--destino", default="elena_musica", help=argparse.SUPPRESS)  # pruebas: un sink nulo
    p = cal.add_parser("volumen", help="el volumen más alto con el que estás cómodo, por bocina (va antes de "
                                       "satelites): ruido rosa desde tu nivel de música, de 2 en 2 dB")
    p.add_argument("--canal", action="append", type=str.upper, metavar="C",
                   help="solo esta bocina: FL, FR, FC, RL, RR o LFE (se puede repetir; por defecto, las 6)")
    p.add_argument("--destino", default=None, help=argparse.SUPPRESS)  # pruebas: un sink nulo de 6 canales
    p = cal.add_parser("generar", help="lee local/calibracion.toml, escribe 40-elena-calibracion.conf y lo valida "
                                       "con pw-config (no reinicia nada)")
    p.add_argument("--toml", type=Path, default=None, help="otro TOML (por defecto, local/calibracion.toml)")
    p.add_argument("--salida", type=Path, default=None,
                   help="otro .conf (por defecto, local/pipewire/pipewire.conf.d/40-elena-calibracion.conf)")
    p.add_argument("--prueba", metavar="SINK", default=None,
                   help="versión aparte para `pipewire -c`, con elena_salida_prueba y la salida a SINK (pide --salida)")
    cal.add_parser("activar", help="escribe 40-elena-calibracion.conf y manda Música, Juegos y Sistema a "
                                   "elena_salida (no reinicia: luego mise run aplicar, con la VM apagada)")
    cal.add_parser("desactivar", help="los buses regresan al físico y se borra el .conf (luego mise run aplicar)")
    cal.add_parser("estado", help="si la calibración está activa, sus valores y si elena_salida está bien")
    cal.add_parser("buses", help=argparse.SUPPRESS)   # lo corre aplicar después de reiniciar PipeWire
    cal.add_parser("afinar", help="en vivo con música: elige bocina, + / − 0.5 dB, a alterna con neutro, g guarda")
    cal.add_parser("ab", help="lo mismo que afinar (A/B: calibrado ↔ neutro con la tecla a)")
    for bocina in ("fl", "fr", "fc", "rl", "rr", "sub"):
        p = cal.add_parser(bocina, help=f"{bocina}: ganancia (dB), retardo (ms) o distancia (m), en vivo y guardado")
        p.add_argument("campo", choices=["ganancia", "retardo", "distancia"])
        p.add_argument("valor", type=float)
    p = cal.add_parser("nivel", help="tu 100 %%: la ganancia de FL hasta el DAC (dB), en vivo y guardado")
    p.add_argument("valor", type=float)
    p = cal.add_parser("lfe", help="el nivel del canal LFE (dB, +10 como un receptor), en vivo y guardado")
    p.add_argument("valor", type=float)
    p = cal.add_parser("cruce", help="la frecuencia del cruce de graves (Hz), en vivo y guardado")
    p.add_argument("valor", type=float)
    p = cal.add_parser("eq", help="el EQ (común a las 5 satélites, o el del sub con --sub), en vivo y guardado: "
                                  "sin más, la lista; agregar <pico|grave|agudo> <Hz> <dB> [--q Q], <n> <campo> <valor>, "
                                  "quitar <n> o limpiar")
    p.add_argument("partes", nargs="*", help="agregar … · <n> tipo|frecuencia|ganancia|q <valor> · quitar <n> · limpiar")
    p.add_argument("--sub", action="store_true", help="el EQ del sub (después de la suma de graves)")
    p.add_argument("--q", type=float, default=None, help="con agregar: la Q (1 en pico, 0.707 en los estantes)")
    p = cal.add_parser("estereo", help="cuánto baja Música mientras suena estéreo, y Sistema siempre (dB, de -24 a 0); "
                                       "lo aplica el vigilante en vivo")
    p.add_argument("valor", type=float)
    p.add_argument("--bus", choices=["musica", "juegos", "sistema"],
                   help="solo este bus (sin --bus: la de todos los que no tienen una propia)")
    p = cal.add_parser("nivelar", help="el balance entre las satélites, desde neutro y a ciegas: ruido rosa "
                                       "de 500 Hz a 2 kHz, A/B contra FL, ajustas hasta que suenen igual")
    p.add_argument("--canal", action="append", type=str.upper, metavar="C",
                   help="solo esta bocina: FR, FC, RL o RR (se puede repetir; por defecto, las 4)")
    p.add_argument("--mic", action="store_true", help="antes, mide cada bocina con el mic físico "
                                                      "(lo muestra al final, junto a lo que oíste)")
    p.add_argument("--destino", default=None, help=argparse.SUPPRESS)  # pruebas: un sink nulo de 6 canales
    p = cal.add_parser("musica", help="con música real: sube el bus de Música hacia tu volumen ideal y ajusta el "
                                      "sub, midiendo lo que le llega; al salir regresa el volumen")
    p.add_argument("--restaurar", action="store_true",
                   help="solo regresa el volumen del bus si una prueba anterior no terminó bien")
    p = cal.add_parser("comparar", help="con música, desde neutro y a ciegas: tu 100 %%, tu sub y tu máximo "
                                        "(con el balance de nivelar); al salir regresa el volumen del bus")
    p.add_argument("--restaurar", action="store_true",
                   help="solo regresa el volumen del bus si una prueba anterior no terminó bien")

    args = parser.parse_args(argv)
    try:
        return _orden(args)
    except ErrorElena as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _orden_visual(grafo: Grafo, args: argparse.Namespace) -> int:
    """`elena visual [bus [on|off]]`: el interruptor es el silencio del alimentador de ese bus
    (WirePlumber lo recuerda, como los del mic)."""
    if args.bus is None:
        print(f"Visual: {texto_visual(grafo)}")
        return 0
    clave = normalizar(args.bus)
    if clave not in VISUAL_BUSES:
        raise ErrorElena(f"«{args.bus}» no entra a la mezcla de visualizadores. Usa: {', '.join(VISUAL_BUSES)}.")
    buscar_visual(grafo)
    if (alimentador := grafo.nodo(alimentador_visual(clave))) is None:
        raise ErrorElena(f"No existe {alimentador_visual(clave)}. ¿Falta `mise run aplicar`?")
    entrar = _activar(args.modo)
    ejecutar(cmd_silencio(alimentador, None if entrar is None else not entrar))
    entra = alimentador.silencio if entrar is None else entrar
    print(f"{ETIQUETA_BUS[clave]} en la mezcla de visualizadores: {'on' if entra else 'off'}")
    return 0


def _orden_audifonos(grafo: Grafo, args: argparse.Namespace) -> int:
    """`elena audifonos [on|off|alternar|binaural|directo]`: guarda el estado y mueve lo que falta (el
    vigilante lo mantiene después, también si PipeWire se reinicia)."""
    actual = audifonos.leer()
    if args.modo in ("graves", "lfe"):
        if args.valor is None and args.hz is None:
            raise ErrorElena(f"Falta el valor: elena audifonos {args.modo} <dB>.")
        if args.modo == "lfe" and args.hz is not None:
            raise ErrorElena("--hz solo va con graves.")
        nuevo = audifonos.ajustar(actual, **({"lfe": args.valor} if args.modo == "lfe"
                                             else {"graves": args.valor, "frecuencia": args.hz}))
        for linea in audifonos.cambiar(grafo, nuevo, ejecutar):
            print(linea)
        grafo, actual = cargar_grafo(), nuevo
    elif args.modo is not None:
        nuevo = audifonos.siguiente(actual, args.modo)
        for linea in audifonos.cambiar(grafo, nuevo, ejecutar):
            print(linea)
        grafo, actual = cargar_grafo(), nuevo
    print(f"Audífonos: {audifonos.texto(grafo, actual)}")
    from elena import vigilante as vg
    if actual.activo and not vg.corriendo():
        print("⚠ El vigilante no corre: si PipeWire se reinicia, todo regresa a las bocinas "
              "(mise run vigilante instalar).")
    return 0


def _orden_vm(args: argparse.Namespace) -> int:
    if args.accion == "estado":
        print(f"VM: {vm.estado()}")
        return 0
    if args.accion == "musica":
        from elena import musica
        if args.modo == "estado":
            print(musica.texto_estado())
            return 0
        if args.modo is not None:
            raise ErrorElena(f"No conozco «{args.modo}»: elena vm musica [estado]")
        try:
            musica.correr()
        except KeyboardInterrupt:
            pass
        except musica.PuenteOcupado as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return musica.OCUPADO
        return 0
    if args.modo is not None and args.accion != "red":
        raise ErrorElena(f"«{args.modo}» solo va con red o musica: elena vm red off")
    if args.accion == "red":
        if args.modo not in ("on", "off"):
            raise ErrorElena("Falta on u off: elena vm red off")
        vm.red(args.modo == "on")
        print(f"Red de la VM {'conectada' if args.modo == 'on' else 'desconectada'}.")
        return 0
    if vm.pid() is None:
        print("La VM ya está apagada.")
        return 0
    print(f"Apagando Windows (botón de encendido por ACPI; espero hasta {args.esperar:.0f} s)…", flush=True)
    if vm.apagar(args.esperar):
        print("VM apagada.")
        return 0
    print("ERROR: Windows no terminó de apagarse. Revisa la ventana de la VM.", file=sys.stderr)
    return 1


def _orden_remoto(args: argparse.Namespace) -> int:
    from elena.remoto import acceso
    if args.modo is not None and args.accion != "token":
        raise ErrorElena(f"«{args.modo}» solo va con token: elena remoto token nuevo")
    if args.accion == "servir":
        from elena.remoto import servidor
        servidor.correr()
        return 0
    token = acceso.Token()
    if args.accion == "token":
        if args.modo != "nuevo":
            raise ErrorElena("Usa: elena remoto token nuevo")
        token.nuevo()
        print("Token nuevo: los teléfonos emparejados tienen que volver a escanear el QR.")
        args.accion = "qr"
    if args.accion == "qr":
        import segno
        enlace = acceso.url(token.valor())
        print(f"Abre en el teléfono (misma red Wi-Fi): {enlace}\n")
        segno.make(enlace, error="m").terminal(compact=True)
        print("\nEl enlace lleva el token: no lo compartas. Revocarlo: elena remoto token nuevo")
        return 0
    print(texto_remoto(acceso))
    return 0


def _red(direccion: str) -> str:
    """http://192.168.1.20:8765/ → 192.168.1.0/24 (para la regla de ufw)."""
    import ipaddress
    from urllib.parse import urlparse
    try:
        return str(ipaddress.ip_network(f"{urlparse(direccion).hostname}/24", strict=False))
    except ValueError:
        return "<tu red>/24"


def texto_remoto(acceso) -> str:
    """`elena remoto estado`: servicio, puerto, dirección y emparejamiento."""
    import socket as _socket
    activo = subprocess.run(["systemctl", "--user", "is-active", "elena-remoto.service"],
                            capture_output=True, text=True).stdout.strip() or "?"
    with _socket.socket() as s:
        s.settimeout(0.5)
        escucha = s.connect_ex(("127.0.0.1", acceso.PUERTO)) == 0
    try:
        direccion = acceso.url()
    except ErrorElena as e:
        direccion = str(e)
    lineas = [
        f"Servicio elena-remoto: {activo}",
        f"Puerto {acceso.PUERTO}: {'escuchando' if escucha else 'nadie escucha'}",
        f"Dirección: {direccion}",
        f"Token: {'sí' if acceso.Token().valor(crear=False) else 'todavía no (se crea con elena remoto qr)'}",
        f"Firewall (a mano): sudo ufw allow from {_red(direccion)} to any port {acceso.PUERTO} proto tcp",
    ]
    return "\n".join(lineas)


def _orden_equipo(args) -> int:
    from elena import equipo, rutas
    if args.accion == "iniciar":
        for h in equipo.iniciar():
            print(h)
        print("Luego: revisa local/equipo.toml y corre mise run aplicar.")
        return 0
    if args.accion == "detectar":
        texto = equipo.texto_toml(equipo.detectar())
        if not args.escribir:
            print(texto, end="")
        elif rutas.EQUIPO.exists():
            raise ErrorElena(f"Ya existe {rutas.EQUIPO}: bórralo o edítalo a mano (sin --escribir solo lo muestra).")
        else:
            rutas.EQUIPO.parent.mkdir(parents=True, exist_ok=True)
            rutas.EQUIPO.write_text(texto)
            print(f"Escrito {rutas.EQUIPO}: revisa el mic y los audífonos, y corre mise run aplicar.")
        return 0
    eq = equipo.leer()
    if args.accion == "generar":
        hechos = equipo.generar(eq)
        print("\n".join(hechos) if hechos else "Los .conf de local/ ya estaban al día.")
        return 0
    print("\n".join(equipo.resumen(eq)))
    faltan = [a.destino.name for a in equipo.archivos(eq) if not a.destino.exists()]
    print(f"Faltan .conf en local/: {', '.join(faltan)} (mise run aplicar)" if faltan else ".conf de local/: al día con mise run aplicar")
    return 0


def _generar_calibracion(args) -> int:
    from elena import calibracion as cb
    if args.prueba and not args.salida:
        raise ErrorElena("--prueba pide --salida (no se escribe en pipewire.conf.d).")
    salida = args.salida or cb.CONF
    extra = {"destino": args.prueba, "nombre": "elena_salida_prueba", "cliente": True} if args.prueba else {}
    cal, avisos = cb.generar(args.toml or cb.TOML, salida, **extra)
    print("\n".join(cb.resumen(cal)))
    for a in avisos:
        print(f"⚠ {a}")
    print(f"Escrito y validado con pw-config: {salida}")
    if not args.prueba:
        print("Se activa al reiniciar PipeWire (mise run aplicar, con la VM apagada).")
    return 0


def _cambiar_calibracion(args) -> int:
    from dataclasses import replace as cambiar

    from elena import afinar
    from elena import calibracion as cb
    cal = cb.leer()
    if args.accion in cb.BOCINAS:
        nueva = afinar.con_bocina(cal, args.accion, args.campo, args.valor)
    elif args.accion == "cruce":
        if cal.cruce is None:
            raise ErrorElena("No hay cruce: agrégalo en local/calibracion.toml ([cruce] frecuencia), generar y aplicar.")
        nueva = afinar.validar(cambiar(cal, cruce=args.valor))
    else:
        nueva = afinar.validar(cambiar(cal, **{args.accion: args.valor}))
    for h in afinar.cambiar(nueva):
        print(h)
    print("\n".join(cb.resumen(nueva)))
    return 0


def _compensacion_estereo(valor: float, bus: str | None = None) -> int:
    from dataclasses import replace as cambiar

    from elena import afinar
    from elena import calibracion as cb
    from elena import vigilante as vg
    cal = cb.leer()
    if cal.estereo is None:
        raise ErrorElena("No hay [estereo] en local/calibracion.toml: agrégalo (compensacion y buses).")
    if bus is not None and bus not in cal.estereo_buses:
        raise ErrorElena(f"{bus} no está en estereo.buses de local/calibracion.toml: agrégalo ahí primero.")
    nueva = afinar.validar(cambiar(cal, estereo=valor) if bus is None else
                           cambiar(cal, estereo_por_bus={**cal.estereo_por_bus, bus: valor}))
    cb.TOML.write_text(afinar.editar_toml(cb.TOML.read_text(), nueva))
    print(f"Estéreo: {cb.texto_estereo(nueva)} (guardado en {cb.TOML.name}).")
    if bus is None and (propias := [b for b in nueva.estereo_por_bus if nueva.estereo_de(b) != valor]):
        print(f"  {', '.join(propias)} tiene una propia en [estereo]: cámbiala con --bus.")
    print("El vigilante lo aplica en vivo." if vg.corriendo() else
          "⚠ El vigilante no corre: nadie lo aplica (mise run vigilante instalar).")
    return 0


def _integracion_calibracion(accion: str) -> int:
    from elena import calibracion as cb
    if accion == "buses":
        for h in cb.ajustar_tras_aplicar(cargar_grafo()):
            print(h)
        return 0
    if accion == "estado":
        for linea in _estado_calibracion(cb):
            print(linea)
        return 0
    if accion == "activar":
        cal, avisos = cb.activar()
        print("\n".join(cb.resumen(cal)))
        for a in avisos:
            print(f"⚠ {a}")
        print(f"Escrito {cb.CONF.name} y Música, Juegos y Sistema → {cb.NODO} en {cb.BUSES_CONF.name}.")
    else:
        cb.desactivar()
        print(f"Borrado {cb.CONF.name} y Música, Juegos y Sistema → el físico en {cb.BUSES_CONF.name}.")
    print("Nada cambia hasta reiniciar PipeWire: mise run aplicar.")
    if cb.vm_corriendo():
        print("⚠ La VM está abierta: apágala antes de mise run aplicar (reiniciar PipeWire puede dejarla sin audio).")
    return 0


def _estado_calibracion(cb) -> list[str]:
    destinos = cb.destino_de_buses(cb.BUSES_CONF.read_text()) if cb.BUSES_CONF.exists() else {"(sin generar: mise run aplicar)"}
    lineas = [f"Calibración: {'activa' if cb.activa() else 'inactiva'} · buses de bocinas → {', '.join(sorted(destinos))}"]
    if cb.activa() != (destinos == {cb.NODO}):
        lineas.append("⚠ El .conf y el destino de los buses no coinciden: corre activar o desactivar otra vez.")
    try:
        cal = cb.leer()
        lineas += cb.resumen(cal) + [f"⚠ {a}" for a in cb.avisos(cal)]
        if cal.tope is not None:
            lineas.append(f"Tope de los buses: {cal.tope:+.1f} dB sobre tu 100 % ({lineal_a_pct(10 ** (cal.tope / 20)):.0f} %)"
                          + (f" · perilla: {cal.perilla}" if cal.perilla else " · perilla: sin anotar"))
    except ErrorElena as e:
        lineas.append(f"⚠ {e}")
    grafo = cargar_grafo()
    try:
        cal = cb.leer()
        if cal.estereo is not None:
            from elena import vigilante as vg
            from elena.modelo import BUSES as DEFS
            partes = []
            for d in DEFS:
                if d.clave in cal.estereo_buses and (s := grafo.nodo(d.salida)) and s.volumenes:
                    partes.append(f"{d.etiqueta} {lineal_a_db(max(s.volumenes)):+.1f} dB ahora")
            lineas.append(f"Estéreo: {cb.texto_estereo(cal)} en {', '.join(cal.estereo_buses)} · "
                          + (" · ".join(partes) or "-")
                          + ("" if vg.corriendo() else " · ⚠ el vigilante no corre: nadie la aplica"))
    except ErrorElena:
        pass
    if salida := grafo.nodo(cb.NODO):
        lineas.append(f"{cb.NODO}: {texto_volumen(salida)} · {salida.estado or '-'}")
    elif cb.activa():
        lineas.append(f"⚠ No existe {cb.NODO}: ¿falta mise run aplicar?")
    for n in cb.BUSES_BOCINAS:
        if (nodo := grafo.nodo(n)) and not nodo.balanceado and cb.activa():
            lineas.append(f"⚠ {n} tiene volumen por canal: el balance se aplicaría dos veces ({texto_volumen(nodo)}).")
    return lineas


def _orden(args: argparse.Namespace) -> int:
    if args.orden is None:
        # Las animaciones las pone la terminal (kitty: cursor_trail); Textual no anima nada.
        # Va antes de importar Textual: lee la variable al cargarse.
        os.environ.setdefault("TEXTUAL_ANIMATIONS", "none")
        from elena.tui.app import ElenaApp  # Textual solo se carga para la TUI
        ElenaApp().run()
        return 0

    if args.orden == "equipo":
        return _orden_equipo(args)
    if args.orden == "vm":
        return _orden_vm(args)
    if args.orden == "remoto":
        return _orden_remoto(args)
    if args.orden == "calibracion" and args.accion == "comparar":
        from elena import comparar
        return comparar.correr(cargar_grafo(), solo_restaurar=args.restaurar)
    if args.orden == "calibracion" and args.accion == "musica":
        from elena import escucha
        return escucha.correr(cargar_grafo(), solo_restaurar=args.restaurar)
    if args.orden == "calibracion" and args.accion == "generar":
        return _generar_calibracion(args)
    if args.orden == "calibracion" and args.accion in ("afinar", "ab"):
        from elena import afinar
        return afinar.correr()
    if args.orden == "calibracion" and args.accion == "eq":
        from elena import afinar
        from elena import calibracion as cb
        cal = cb.leer()
        if args.partes:
            cal = afinar.eq_nueva(cal, args.partes, args.sub, args.q)
            for h in afinar.cambiar(cal):
                print(h)
        print("\n".join(afinar.texto_eq(cal)))
        return 0
    if args.orden == "calibracion" and args.accion == "estereo":
        return _compensacion_estereo(args.valor, args.bus)
    if args.orden == "calibracion" and args.accion in ("fl", "fr", "fc", "rl", "rr", "sub", "nivel", "lfe", "cruce"):
        return _cambiar_calibracion(args)
    if args.orden == "calibracion" and args.accion in ("activar", "desactivar", "estado", "buses"):
        return _integracion_calibracion(args.accion)
    if args.orden == "calibracion" and args.accion == "nivelar":
        from elena import nivelar
        return nivelar.correr(cargar_grafo(), tuple(args.canal or nivelar.BOCINAS), args.mic,
                              *([args.destino] if args.destino else []))
    if args.orden == "calibracion" and args.accion == "volumen":
        from elena import maximo
        return maximo.correr(cargar_grafo(), tuple(args.canal or maximo.BOCINAS),
                             *([args.destino] if args.destino else []))
    if args.orden == "calibracion":
        from elena import satelites
        op = satelites.Opciones(destino=args.destino, canales=tuple(args.canal or satelites.SATELITES),
                                desde=args.desde, hasta=args.hasta, paso=args.paso, nivel_db=args.nivel,
                                mic=args.mic, en_bus=args.en_bus, relativo=args.relativo)
        return satelites.correr(cargar_grafo(), op)
    if args.orden == "vigilar":
        import asyncio

        from elena.vigilante import Vigilante
        try:
            asyncio.run(Vigilante().correr(una_vez=args.una_vez))
        except KeyboardInterrupt:
            pass
        return 0
    if args.orden == "reglas":
        lista = reglas.cargar()
        print("\n".join(f"{i}. {r.descripcion}" for i, r in enumerate(lista, 1)) or "(sin reglas)")
        return 0
    if args.orden == "escena" and not args.nombre:
        lista = escenas.cargar()
        print("\n".join(f"{n}: {e.resumen}" for n, e in lista.items()) or "(sin escenas)")
        return 0

    grafo = cargar_grafo()
    if args.orden == "escena":
        todas = escenas.cargar()
        if args.guardar:
            todas[args.nombre] = escenas.capturar(grafo, args.nombre)
            escenas.guardar(todas)
            print(f"Guardada «{args.nombre}»: {todas[args.nombre].resumen}")
        elif args.borrar:
            if todas.pop(args.nombre, None) is None:
                raise ErrorElena(f"No existe la escena «{args.nombre}».")
            escenas.guardar(todas)
            print(f"Borrada «{args.nombre}».")
        else:
            if args.nombre not in todas:
                raise ErrorElena(f"No existe la escena «{args.nombre}». Hay: {', '.join(todas) or '(ninguna)'}.")
            topados: list[str] = []
            for cmd in escenas.comandos(grafo, todas[args.nombre], topados):
                ejecutar(cmd)
            print(f"Escena «{args.nombre}»: {todas[args.nombre].resumen}")
            for aviso in topados:
                print(f"⚠ {aviso}")
        return 0
    if args.orden == "visual":
        return _orden_visual(grafo, args)
    if args.orden == "audifonos":
        return _orden_audifonos(grafo, args)
    if args.orden == "regla" and args.bus and normalizar(args.bus) == reglas.VISUAL:
        grabaciones = buscar_apps(grafo, args.app, (Tipo.GRABACION,))
        mezcla = buscar_visual(grafo)
        lista = reglas.fijar(reglas.cargar(), grabaciones[0], reglas.VISUAL)
        reglas.guardar(lista)
        print(f"Regla: {lista[0].descripcion}")
        for g in grabaciones:
            if mezcla not in grafo.origenes(g):
                ejecutar(cmd_mover_grabacion(g, mezcla))
                print(f"{g.visible} (serial {g.serial}) → {nombre_salida(mezcla)}")
        return 0
    if args.orden == "regla":
        apps = buscar_apps(grafo, args.app, (Tipo.APP, Tipo.GRABACION) if args.quitar else (Tipo.APP,))
        lista = reglas.cargar()
        if args.quitar:
            antes = len(lista)
            for app in apps:
                lista = reglas.quitar(lista, app)
            reglas.guardar(lista)
            print(f"Quitadas {antes - len(lista)} regla(s) de {apps[0].visible}.")
            return 0
        if not args.bus:
            raise ErrorElena("Falta el bus: elena regla <app> <bus> (o --quitar).")
        destino = buscar_sink(grafo, args.bus)
        bus = next((b.clave for b in escenas.CLAVES.values() if b.nodo == destino.nombre), destino.nombre)
        lista = reglas.fijar(lista, apps[0], bus)
        reglas.guardar(lista)
        print(f"Regla: {lista[0].descripcion}")
        for app in apps:
            if destino not in grafo.sinks_de(app):
                ejecutar(cmd_mover(app, destino))
                print(f"{app.visible} (serial {app.serial}) → {nombre_salida(destino)}")
        return 0
    if args.orden == "estado":
        if args.json:
            print(json.dumps(dict_estado(grafo, args.todo), ensure_ascii=False, indent=2))
        else:
            print(texto_estado(grafo, args.todo))
    elif args.orden == "mover" and normalizar(args.destino) == reglas.VISUAL:
        mezcla = buscar_visual(grafo)
        for g in buscar_apps(grafo, args.app, (Tipo.GRABACION,)):
            ejecutar(cmd_mover_grabacion(g, mezcla))
            print(f"{g.visible} (serial {g.serial}) → {nombre_salida(mezcla)} ({mezcla.nombre})")
    elif args.orden == "mover":
        destino = buscar_sink(grafo, args.destino)
        for app in buscar_apps(grafo, args.app):
            ejecutar(cmd_mover(app, destino))
            print(f"{app.visible} (serial {app.serial}) → {nombre_salida(destino)} ({destino.nombre})")
    elif args.orden == "volumen":
        for n in buscar_objetivo(grafo, args.objetivo):
            volumenes, aviso = tope.acotar_volumenes(grafo, n, nuevo_volumen(n, args.valor))
            if aviso:
                print(f"⚠ {aviso}")
            ejecutar(cmd_volumen(n, volumenes))
            print(f"{n.visible}: {texto_volumen(replace(n, volumenes=volumenes))}")
    elif args.orden == "silenciar":
        modo = _activar(args.modo)
        for n in buscar_objetivo(grafo, args.objetivo):
            ejecutar(cmd_silencio(n, modo))
            nuevo = (not n.silencio) if modo is None else modo
            print(f"{n.visible}: {'silenciado' if nuevo else 'activo'}")
    return 0
