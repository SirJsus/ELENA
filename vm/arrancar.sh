#!/usr/bin/env bash
# Entra a la caja elena-vm y arranca QEMU (vm/qemu.sh), en primer plano.
# Por defecto: backend pa con 100 ms de colchón (lo que dio menos cortes; ver vm/qemu.sh).
#   ELENA_VM_AUDIO=pipewire mise run vm:arrancar    # backend alterno
#   ELENA_VM_LATENCIA=200000 mise run vm:arrancar   # más colchón
#   ELENA_VM_RAM=4G mise run vm:arrancar            # otra RAM (por defecto, la de mise.toml)
#   ELENA_VM_CON_ISO=1 mise run vm:arrancar         # con la ISO de Windows (reparar o reinstalar)
#   ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar  # con el CD de instaladores y la memoria
#                                                     # de intercambio (pulir.cmd, diagnostico.ps1)
# A diario la arranca el servicio elena-vm (lanzador «Apple Music (ELENA)»), que corre
# este mismo script. Aquí se rechaza una segunda VM: dos QEMU no comparten el disco.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:arrancar}"

if pgrep -f -- '^[^ ]*qemu-system-x86_64 -name elena-windows' >/dev/null; then
  echo "ERROR: la VM ya está corriendo (apágala con: mise run elena -- vm apagar)." >&2
  exit 1
fi
if ! pactl list sinks short | awk '$2 == "elena_musica" { ok = 1 } END { exit !ok }'; then
  echo "ERROR: no existe el bus elena_musica (corre primero: mise run aplicar)." >&2
  exit 1
fi
if ! distrobox list 2>/dev/null | awk -F'|' '{ gsub(/ /, "", $2) } $2 == "elena-vm" { ok = 1 } END { exit !ok }'; then
  echo "ERROR: no existe la caja elena-vm (créala con: mise run vm:caja)." >&2
  exit 1
fi

# Todas las ELENA_VM_* de mise.toml (y de la línea de comandos) pasan a la caja. La latencia
# lleva 100000 si no se define; ELENA_VM_LATENCIA= vacía la quita (ver vm/qemu.sh).
variables=(ELENA="$ELENA" TMPDIR="$TMPDIR" ELENA_VM_AUDIO="${ELENA_VM_AUDIO:-pa}"
           ELENA_VM_LATENCIA="${ELENA_VM_LATENCIA-100000}")
while IFS='=' read -r nombre _; do
  [[ $nombre == ELENA_VM_AUDIO || $nombre == ELENA_VM_LATENCIA ]] || variables+=("$nombre=${!nombre}")
done < <(env | grep '^ELENA_VM_[A-Z_]*=')
# -T: sin terminal (el servicio elena-vm no tiene una)
tty=(); [[ -t 0 ]] || tty=(-T)
exec distrobox enter "${tty[@]}" elena-vm -- env "${variables[@]}" "$ELENA/vm/qemu.sh"
