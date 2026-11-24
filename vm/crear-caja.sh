#!/usr/bin/env bash
# Caja Distrobox con QEMU, OVMF (UEFI), swtpm (TPM) y xorriso.
# Nada de QEMU se instala en el sistema; la caja usa un HOME propio en .cache/vm/home.
# La imagen del contenedor vive en el almacén de podman (~/.local/share/containers),
# como cualquier otro contenedor de la capa 2/3 (excepción anotada en CLAUDE.md).
# --no-entry: sin él, distrobox crea ~/.local/share/applications/elena-vm.desktop y un ícono
# en ~/.local/share/icons/distrobox/ (fuera del repo).
#   qemu-desktop  QEMU x86 con los backends de audio pipewire y pa, pantalla GTK y usb-audio
#   pipewire      solo por /usr/share/pipewire/client.conf: sin él, el backend pipewire de
#                 QEMU falla con «Could not create PipeWire context» (qemu-audio-pipewire solo
#                 trae libpipewire). En la caja no corre ningún daemon: usa el del host.
#   libisoburn    xorriso, para la ISO de instaladores (vm:instaladores)
#   dosfstools, mtools  la memoria USB virtual de intercambio (FAT32): Windows escribe y
#                 Linux la lee sin montarla (vm:intercambio)
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:caja}"

if ! command -v distrobox >/dev/null; then
  echo "Falta distrobox (la herramienta de la capa 3). Instálalo con:" >&2
  echo "  sudo pacman -S distrobox" >&2
  exit 1
fi
if distrobox list 2>/dev/null | awk -F'|' '{ gsub(/ /, "", $2) } $2 == "elena-vm" { ok = 1 } END { exit !ok }'; then
  echo "La caja elena-vm ya existe."
else
  mkdir -p "$ELENA/.cache/vm/home"
  distrobox create \
    --name elena-vm \
    --image docker.io/library/archlinux:latest \
    --home "$ELENA/.cache/vm/home" \
    --volume "$ELENA:$ELENA" \
    --additional-flags "--device /dev/kvm" \
    --additional-packages "qemu-desktop edk2-ovmf swtpm libisoburn pipewire dosfstools mtools" \
    --no-entry \
    --yes
fi

# distrobox instala los paquetes la primera vez que se entra: se hace aquí y se comprueba
# que QEMU tenga lo que la fase necesita.
echo "Inicializando la caja (la primera vez descarga los paquetes; tarda unos minutos)…"
distrobox enter elena-vm -- bash -c '
  set -e
  qemu-system-x86_64 --version | head -1
  qemu-system-x86_64 -audiodev help | grep -qx pipewire || { echo "ERROR: QEMU sin backend pipewire" >&2; exit 1; }
  qemu-system-x86_64 -audiodev help | grep -qx pa       || { echo "ERROR: QEMU sin backend pa" >&2; exit 1; }
  qemu-system-x86_64 -device help | grep -q "\"usb-audio\"" || { echo "ERROR: QEMU sin usb-audio" >&2; exit 1; }
  command -v swtpm xorriso mkfs.fat mcopy >/dev/null
  ls /usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd /usr/share/pipewire/client.conf >/dev/null
  # Conexión real al audio del host con cada backend: QEMU sin máquina y detenido; si inicia
  # el backend, sigue corriendo y timeout lo corta (124). Si no conecta, sale con 1.
  for b in "pipewire,id=a" "pa,id=a,server=unix:$XDG_RUNTIME_DIR/pulse/native"; do
    rc=0
    timeout 3 qemu-system-x86_64 -machine none -nodefaults -display none -S \
      -audiodev "$b,out.mixing-engine=off,out.fixed-settings=off" >/dev/null 2>&1 || rc=$?
    [[ $rc == 124 ]] || { echo "ERROR: QEMU no conecta al audio del host con ${b%%,*} (código $rc)" >&2; exit 1; }
  done
  echo "OK: backends pipewire y pa (conectan al host), usb-audio, swtpm, xorriso y OVMF"'

echo "Listo. Siguiente: mise run vm:instaladores y luego mise run vm:arrancar"
