#!/usr/bin/env bash
# Abre Steam con el audio de los juegos en ELENA · Juegos (5.1). Lo usan el lanzador
# «Steam (ELENA 5.1)» (que crea `aplicar todo`) y `mise run juegos:steam`.
#   PULSE_SINK=elena_juegos  libpulse manda ahí los streams que no eligen destino: Steam,
#                            Wine con winepulse y los juegos nativos que usan pulse.
#   PROTON_USE_PIPEWIRE=0    Proton-CachyOS usa winepipewire por defecto, que ignora
#                            PULSE_SINK y sigue al sink por defecto (elena_sistema, estéreo).
#                            Con 0 usa winepulse. GE-Proton y Proton Experimental no lo
#                            necesitan (solo traen winepulse) y no les afecta.
# Si Steam ya está abierto, `steam` solo le pasa la orden a esa instancia y las variables
# no aplican; en ese caso se avisa y no se cierra Steam.
set -euo pipefail
bus=elena_juegos

avisar() {
  echo "ELENA: $1" >&2
  if command -v notify-send >/dev/null; then
    notify-send -a ELENA -i steam "Steam (ELENA 5.1)" "$1" || true
  fi
}

# ELENA no está aplicado: Steam se abre normal
if ! pactl list sinks short 2>/dev/null | awk -v b="$bus" '$2 == b { ok = 1 } END { exit !ok }'; then
  avisar "No existe $bus (¿ELENA no está aplicado?). Steam se abre sin ELENA."
  exec steam "$@"
fi

# ¿Steam ya corre? ¿Lo abrió ELENA? (cualquiera de sus procesos `steam` con PULSE_SINK)
corriendo=0 con_elena=0
for pid in $(pgrep -x steam || true); do
  corriendo=1
  if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qx "PULSE_SINK=$bus"; then
    con_elena=1
  fi
done

if (( corriendo )); then
  if (( ! con_elena )); then
    avisar "Steam ya estaba abierto sin ELENA: los juegos no saldrán en 5.1. Sal de Steam (Steam → Salir) y vuelve a abrir Steam (ELENA 5.1)."
  fi
  exec steam "$@"
fi

export PULSE_SINK=$bus PROTON_USE_PIPEWIRE=0
exec steam "$@"
