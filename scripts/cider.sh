#!/usr/bin/env bash
# Lanza Cider 4 con todo su estado dentro de .cache/ y su audio en elena_musica.
# Criterio: reproducir una pista Atmos y correr `mise run estado`.
#   6 u 8 canales -> éxito.  2 canales -> es binaural (solo audífonos); pasar a la VM.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run cider}"

app=$(find "$ELENA/.cache/apps" -maxdepth 1 -iname 'cider*.appimage' 2>/dev/null | sort | tail -1)
if [[ -z $app ]]; then
  echo "No encontré la AppImage de Cider. Descárgala desde https://cider.sh y ponla en:" >&2
  echo "  $ELENA/.cache/apps/" >&2
  exit 1
fi

base="$ELENA/.cache/cider"
mkdir -p "$base"/{config,data,cache} "$TMPDIR"
export XDG_CONFIG_HOME="$base/config" XDG_DATA_HOME="$base/data" XDG_CACHE_HOME="$base/cache"
# No hay fuse2 instalado: la AppImage se extrae en TMPDIR (.cache/tmp) y corre desde ahí
export APPIMAGE_EXTRACT_AND_RUN=1
export PULSE_SINK=elena_musica

chmod +x "$app"
exec "$app"
