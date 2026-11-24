#!/usr/bin/env bash
# Abre la consola ELENA en una ventana de kitty, o la enfoca si ya está abierta (una sola
# instancia: dos consolas duplicarían los medidores). Lo usan el lanzador «Consola ELENA» (que
# crea `aplicar todo`) y el atajo de Hyprland de docs/guias/consola.md.
#   consola.sh           enfoca la consola abierta o abre kitty --class elena-consola
#   consola.sh --dentro  lo que corre kitty: la TUI con el entorno de mise.toml
# Calcula ELENA desde su propia ruta (como steam.sh): el lanzador no pasa por mise.
# La consola abierta se reconoce por su proceso (`.venv/bin/elena` sin argumentos), así que
# también se enfoca una abierta con `mise run consola` en otra terminal.
set -euo pipefail
ELENA=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
clase=elena-consola

if [[ ${1:-} == --dentro ]]; then
  # mise exec da ELENA, TMPDIR, PYTHONPYCACHEPREFIX… (sin ellas, Python escribe fuera del repo)
  exec mise exec -C "$ELENA" -- "$ELENA/.venv/bin/elena"
fi

avisar() {
  echo "ELENA: $1" >&2
  command -v notify-send >/dev/null && notify-send -a ELENA -i audio-volume-high "Consola ELENA" "$1" || true
}

if [[ ! -x $ELENA/.venv/bin/elena ]]; then
  avisar "Falta el entorno de la consola (.venv). Créalo con: mise exec -- uv sync"
  exit 1
fi

# ¿Ya hay una consola? (la TUI es `python …/.venv/bin/elena` sin argumentos; el vigilante lleva «vigilar»)
tui=$( { pgrep -f -- "/\.venv/bin/elena\$" || true; } | while read -r pid; do
  if [[ $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null) == *"$ELENA/.venv/bin/elena " ]]; then echo "$pid"; fi
done | head -n1)

if [[ -n $tui ]]; then
  if command -v hyprctl >/dev/null && [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
    # La ventana es la de algún ancestro del proceso (kitty): se sube por los padres
    ventanas=$(hyprctl clients -j | jq -r '.[].pid')
    pid=$tui
    while [[ -n $pid && $pid -gt 1 ]]; do
      if grep -qx "$pid" <<< "$ventanas"; then
        hyprctl dispatch "hl.dsp.focus({ window = 'pid:$pid' })" >/dev/null
        exit 0
      fi
      pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
    done
  fi
  avisar "La consola ya está abierta (proceso $tui)."
  exit 0
fi

exec kitty --class "$clase" --title "Consola ELENA" -e "$ELENA/scripts/consola.sh" --dentro
