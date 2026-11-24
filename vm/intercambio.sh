#!/usr/bin/env bash
# Saca lo que Windows dejó en la memoria USB virtual ELENA-INT (.cache/vm/intercambio.img)
# y lo copia a .cache/vm/intercambio/. Usa mtools de la caja: lee la imagen FAT32 sin montarla.
# Hazlo con Windows apagado: si la VM está encendida, lo último escrito puede no estar en la imagen.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:intercambio}"
img="$ELENA/.cache/vm/intercambio.img"
dest="$ELENA/.cache/vm/intercambio"
[[ -f $img ]] || { echo "ERROR: no existe $img (se crea al arrancar la VM)" >&2; exit 1; }
if pgrep -f 'qemu-system-x86_64 -name elena-windows' >/dev/null; then
  echo "AVISO: la VM sigue encendida; apaga Windows para tener todo lo escrito." >&2
fi
mkdir -p "$dest"
distrobox enter elena-vm -- bash -c "MTOOLS_SKIP_CHECK=1 mdir -i '$img' -b :: && MTOOLS_SKIP_CHECK=1 mcopy -i '$img' -s -o -n ::* '$dest/'" 2>/dev/null || true
echo "Copiado a $dest:"
ls -la "$dest"
