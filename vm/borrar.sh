#!/usr/bin/env bash
# Deshace la VM: borra la caja elena-vm y .cache/vm/ (disco de Windows, disco de música,
# TPM, OVMF, home de la caja e ISO de instaladores). NO toca la ISO de Windows ni la carpeta de
# instaladores originales (ELENA_VM_ISO, ELENA_VM_INSTALADORES).
#   mise run vm:borrar          # pide escribir «borrar» para confirmar
#   mise run vm:borrar --si     # sin preguntar
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:borrar}"
vm="$ELENA/.cache/vm"
if pgrep -f -- '^[^ ]*qemu-system-x86_64 -name elena-windows' >/dev/null; then
  echo "ERROR: la VM está corriendo. Apágala primero: mise run elena -- vm apagar" >&2; exit 1
fi

echo "Se borrará:"
distrobox list 2>/dev/null | awk -F'|' '{ gsub(/ /, "", $2) } $2 == "elena-vm" { print "  la caja elena-vm" }'
[[ -d $vm ]] && echo "  $vm ($(du -sh "$vm" 2>/dev/null | cut -f1))"
if [[ ${1:-} != --si ]]; then
  read -rp "Escribe «borrar» para confirmar: " respuesta
  [[ $respuesta == borrar ]] || { echo "Cancelado."; exit 1; }
fi

if distrobox list 2>/dev/null | awk -F'|' '{ gsub(/ /, "", $2) } $2 == "elena-vm" { ok = 1 } END { exit !ok }'; then
  distrobox rm --force elena-vm
fi
rm -rf "$vm"
echo "Listo. La imagen base (docker.io/library/archlinux) sigue en podman; si nadie más la usa:"
echo "  podman rmi docker.io/library/archlinux:latest"
