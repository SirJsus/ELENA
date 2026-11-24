#!/usr/bin/env bash
# Lo que ejecuta el lanzador «Apple Music (ELENA)» (vm/lanzador.sh lo crea).
#   - Si la VM ya corre, enfoca su ventana (Hyprland).
#   - Si no, arranca el servicio elena-vm (systemd apaga Windows en orden al cerrar sesión).
# Windows inicia sesión solo y abre Apple Music; al cerrar Apple Music, Windows se apaga y la
# ventana desaparece (vm/windows/pulir.ps1). Calcula ELENA desde su ruta: el lanzador no pasa por mise.
set -euo pipefail
ELENA=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
unidad=elena-vm.service

avisar() {
  echo "ELENA: $1" >&2
  command -v notify-send >/dev/null && notify-send -a ELENA -i audio-x-generic "Apple Music (ELENA)" "$1" || true
}

pid=$(pgrep -f -- '^[^ ]*qemu-system-x86_64 -name elena-windows' | head -n1 || true)
if [[ -n $pid ]]; then
  if command -v hyprctl >/dev/null && [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] &&
     hyprctl clients -j | jq -e --argjson p "$pid" 'any(.[]; .pid == $p)' >/dev/null; then
    hyprctl dispatch "hl.dsp.focus({ window = 'pid:$pid' })" >/dev/null
  else
    avisar "La VM ya está abierta."
  fi
  exit 0
fi

if ! pactl list sinks short | awk '$2 == "elena_musica" { ok = 1 } END { exit !ok }'; then
  avisar "No existe el bus elena_musica. Aplica ELENA primero (mise run aplicar)."
  exit 1
fi
if [[ ! -f $ELENA/.cache/vm/windows.qcow2 ]]; then
  avisar "No existe la VM (.cache/vm/windows.qcow2). Ver docs/guias/vm-apple-music-atmos.md."
  exit 1
fi

systemctl --user start "$unidad"
# Type=simple: start no espera a QEMU. Si arrancar.sh falla al principio, se ve aquí.
sleep 3
if systemctl --user is-failed --quiet "$unidad"; then
  avisar "La VM no arrancó: $(journalctl --user -u "$unidad" -n 1 -o cat --no-pager 2>/dev/null)"
  exit 1
fi
