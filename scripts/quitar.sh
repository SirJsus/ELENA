#!/usr/bin/env bash
# Revierte ELENA: borra los enlaces (los lanzadores de Steam, la consola y Apple Music, el vigilante, el control remoto, el servicio elena-vm y el del puente de música) y restaura el perfil, el sink y la fuente por defecto,
# el volumen de la salida y el volumen y silencio del mic físico.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run quitar}"
: "${ELENA_TARJETA:?Falta local/equipo.toml (ELENA_TARJETA)}"

previo="$ELENA/.cache/estado-previo"
SINK_DEFECTO="" PERFIL="" CREO_DIR_PIPEWIRE="" CREO_DIR_WIREPLUMBER=""
FUENTE_DEFECTO="" VOLUMEN_SALIDA="" MIC_VOLUMEN="" MIC_SILENCIO=""
# shellcheck source=/dev/null
[[ -f $previo ]] && source "$previo"

desenlazar() {
  local destino=$1
  if [[ -L $destino && $(readlink -f "$destino") == "$ELENA"/* ]]; then
    rm "$destino"; echo "Quitado $destino"
  fi
}
desenlazar "$HOME/.config/pipewire/pipewire.conf.d"
desenlazar "$HOME/.config/wireplumber/wireplumber.conf.d"
desenlazar "$HOME/.local/share/applications/elena-steam.desktop"
desenlazar "$HOME/.local/share/applications/elena-consola.desktop"
"$ELENA/scripts/vigilante.sh" quitar
"$ELENA/scripts/remoto.sh" quitar
# Si la VM corre con el servicio, apaga Windows en orden antes de quitar los buses
"$ELENA/vm/lanzador.sh" quitar
# Borrar las carpetas solo si las creó ELENA y quedaron vacías
[[ -n $CREO_DIR_PIPEWIRE ]]    && rmdir "$HOME/.config/pipewire"    2>/dev/null || true
[[ -n $CREO_DIR_WIREPLUMBER ]] && rmdir "$HOME/.config/wireplumber" 2>/dev/null || true

systemctl --user restart pipewire pipewire-pulse wireplumber
for _ in {1..30}; do pactl list cards short | grep -q "$ELENA_TARJETA" && break; sleep 0.5; done

if [[ -n $PERFIL ]]; then
  pactl set-card-profile "$ELENA_TARJETA" "$PERFIL" && echo "Perfil restaurado: $PERFIL"
fi
if [[ -n $SINK_DEFECTO ]]; then
  for _ in {1..30}; do pactl list sinks short | grep -q "$SINK_DEFECTO" && break; sleep 0.5; done
  pactl set-default-sink "$SINK_DEFECTO" && echo "Sink por defecto restaurado: $SINK_DEFECTO"
fi
if [[ -n $VOLUMEN_SALIDA ]]; then
  # El sink de la tarjeta que exista tras restaurar el perfil
  sink=$(pactl list sinks short | awk -v b="alsa_output.${ELENA_TARJETA#alsa_card.}." '!h && index($2, b) == 1 { print $2; h = 1 }')
  [[ -n $sink ]] && pactl set-sink-volume "$sink" "$VOLUMEN_SALIDA" && echo "Volumen restaurado: $sink en $VOLUMEN_SALIDA"
fi
if [[ -n $FUENTE_DEFECTO ]]; then
  pactl set-default-source "$FUENTE_DEFECTO" && echo "Fuente por defecto restaurada: $FUENTE_DEFECTO"
fi
if [[ -n $MIC_VOLUMEN && -n ${ELENA_MIC_FISICO:-} ]] && pactl get-source-volume "$ELENA_MIC_FISICO" >/dev/null 2>&1; then
  pactl set-source-volume "$ELENA_MIC_FISICO" "$MIC_VOLUMEN"
  pactl set-source-mute "$ELENA_MIC_FISICO" "${MIC_SILENCIO:-0}"
  echo "Mic físico restaurado: $MIC_VOLUMEN, silenciado=${MIC_SILENCIO:-0}"
fi
rm -f "$previo"
