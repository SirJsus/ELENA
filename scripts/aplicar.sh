#!/usr/bin/env bash
# Aplica ELENA. Se corre con `mise run aplicar [alcance]` (mise define ELENA y carga local/equipo.toml).
# Primero genera los .conf de local/ desde plantillas/ (elena equipo generar).
#   perfil  solo el perfil 5.1 (WirePlumber); sin buses; el sink físico 5.1 queda por defecto
#   todo    (valor por defecto): perfil 5.1, buses, mic virtual (si hay mic), mezcla para visualizadores,
#           lanzadores «Steam (ELENA 5.1)» y «Consola ELENA», el vigilante de reglas fijas, el control remoto
#           y, si existe la VM, «Apple Music (ELENA)» con el servicio elena-vm. elena_sistema y elena_mic
#           quedan por defecto; el sink físico y el mic físico, en 100%.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run aplicar}"
: "${ELENA_TARJETA:?Falta local/equipo.toml: corre mise run iniciar}"
: "${ELENA_SINK_FISICO:?Falta ELENA_SINK_FISICO en local/equipo.toml}"
local_dir=${ELENA_LOCAL:-$ELENA/local}
conf_pw="$local_dir/pipewire/pipewire.conf.d"
mic=${ELENA_MIC_FISICO:-}

alcance=${1:-todo}
case $alcance in
  perfil|todo) ;;
  *) echo "ERROR: alcance desconocido '$alcance' (usa: perfil o todo)" >&2; exit 1 ;;
esac

previo="$ELENA/.cache/estado-previo"
mkdir -p "$ELENA/.cache/tmp"

# 0. Los .conf de local/ (valida todo con pw-config antes de reemplazar nada)
uv run --quiet elena equipo generar

perfil_activo() {
  pactl list cards | awk -v c="$ELENA_TARJETA" '
    $1 == "Name:" { n = ($2 == c) }
    n && !h && /Active Profile:/ { sub(/.*Active Profile: /, ""); print; h = 1 }'
}
sink_de_la_tarjeta() {  # el sink de la tarjeta que exista ahora (estéreo o 5.1)
  local base=${ELENA_TARJETA#alsa_card.}
  pactl list sinks short | awk -v b="alsa_output.$base." '!h && index($2, b) == 1 { print $2; h = 1 }'
}
porcentaje() { grep -oE '[0-9]+%' | head -1; }

# 1. Guardar cómo estaba todo, para que `quitar` lo restaure. Cada clave se guarda solo
#    si falta: así un estado-previo de una versión anterior gana las claves nuevas
#    sin perder los valores originales.
touch "$previo"
mic_nuevo=0
agregar() {  # agregar CLAVE VALOR, solo si la clave no está
  grep -q "^$1=" "$previo" && return 0
  echo "$1=$2" >> "$previo"
  [[ $1 == MIC_SILENCIO ]] && mic_nuevo=1
  return 0
}
agregar SINK_DEFECTO   "$(pactl get-default-sink)"
agregar PERFIL         "$(perfil_activo)"
[[ -e $HOME/.config/pipewire ]]    || agregar CREO_DIR_PIPEWIRE 1
[[ -e $HOME/.config/wireplumber ]] || agregar CREO_DIR_WIREPLUMBER 1
agregar FUENTE_DEFECTO "$(pactl get-default-source)"
agregar VOLUMEN_SALIDA "$(pactl get-sink-volume "$(sink_de_la_tarjeta)" | porcentaje)"
if [[ -n $mic ]] && pactl get-source-volume "$mic" >/dev/null 2>&1; then
  agregar MIC_VOLUMEN    "$(pactl get-source-volume "$mic" | porcentaje)"
  agregar MIC_SILENCIO   "$(pactl get-source-mute "$mic" | grep -q yes && echo 1 || echo 0)"
fi
echo "Estado previo en $previo"

# 2. Enlaces (la única escritura fuera del repo; ver CLAUDE.md)
enlazar() {
  local destino=$1 origen=$2
  if [[ -L $destino ]]; then
    if [[ $(readlink -f "$destino") == "$(readlink -f "$origen")" ]]; then return 0; fi
    if [[ $(readlink -f "$destino") == "$ELENA"/* ]]; then
      # Un enlace de ELENA a otra ruta del repo (versiones anteriores): se repunta
      ln -sfn "$origen" "$destino"; echo "Repuntado $destino -> $origen"; return 0
    fi
    echo "ERROR: $destino ya es un enlace a otra parte; revísalo a mano." >&2; exit 1
  fi
  if [[ -e $destino ]]; then
    echo "ERROR: $destino ya existe y no es de ELENA; revísalo a mano." >&2; exit 1
  fi
  mkdir -p "$(dirname "$destino")"
  ln -s "$origen" "$destino"
  echo "Enlazado $destino -> $origen"
}
enlazar "$HOME/.config/wireplumber/wireplumber.conf.d" "$local_dir/wireplumber/wireplumber.conf.d"
buses="$HOME/.config/pipewire/pipewire.conf.d"
if [[ $alcance == todo ]]; then
  enlazar "$buses" "$conf_pw"
elif [[ -L $buses && $(readlink -f "$buses") == "$ELENA"/* ]]; then
  # Venías de `aplicar todo`: quitar los buses para probar el hardware solo
  rm "$buses"; echo "Quitado $buses (alcance perfil: sin buses)"
fi

# Lanzador «Steam (ELENA 5.1)». Se genera en .cache/ porque Exec necesita la ruta
# absoluta del repo. Sin MimeType: los enlaces steam:// siguen yendo al steam.desktop normal.
lanzador="$HOME/.local/share/applications/elena-steam.desktop"
if [[ $alcance == todo ]]; then
  generado="$ELENA/.cache/lanzadores/elena-steam.desktop"
  mkdir -p "$(dirname "$generado")"
  cat > "$generado" <<EOF
[Desktop Entry]
Type=Application
Name=Steam (ELENA 5.1)
Comment=Steam con el audio de los juegos en ELENA · Juegos (5.1)
Exec="$ELENA/scripts/steam.sh" %U
Icon=steam
Terminal=false
Categories=Game;
EOF
  enlazar "$lanzador" "$generado"
elif [[ -L $lanzador && $(readlink -f "$lanzador") == "$ELENA"/* ]]; then
  rm "$lanzador"; echo "Quitado $lanzador (alcance perfil: sin buses no hay elena_juegos)"
fi

# Lanzador «Consola ELENA»: abre la consola en kitty o la enfoca si ya está abierta.
# StartupWMClass = la clase de la ventana (kitty --class), para que el menú la asocie.
lanzador="$HOME/.local/share/applications/elena-consola.desktop"
if [[ $alcance == todo ]]; then
  generado="$ELENA/.cache/lanzadores/elena-consola.desktop"
  mkdir -p "$(dirname "$generado")"
  cat > "$generado" <<EOF
[Desktop Entry]
Type=Application
Name=Consola ELENA
Comment=Buses, apps, volúmenes y medidores de ELENA
Exec="$ELENA/scripts/consola.sh"
Icon=audio-volume-high
Terminal=false
Categories=AudioVideo;Audio;Mixer;
StartupWMClass=elena-consola
EOF
  enlazar "$lanzador" "$generado"
elif [[ -L $lanzador && $(readlink -f "$lanzador") == "$ELENA"/* ]]; then
  rm "$lanzador"; echo "Quitado $lanzador (alcance perfil: sin buses no hay consola)"
fi

# 3. Reiniciar el audio y esperar a la tarjeta
systemctl --user restart pipewire pipewire-pulse wireplumber
esperar() { for _ in {1..30}; do eval "$1" >/dev/null 2>&1 && return 0; sleep 0.5; done; echo "ERROR: $2" >&2; exit 1; }
esperar "pactl list cards short | grep -q '$ELENA_TARJETA'" "no apareció la tarjeta $ELENA_TARJETA"
sleep 1  # dar tiempo a WirePlumber de elegir el perfil

# 4. ¿Qué perfil eligió WirePlumber por sí solo? Si no hay perfil guardado, lo decidió la regla
activo=$(perfil_activo)
guardado="$HOME/.local/state/wireplumber/default-profile"
if grep -qs "^$ELENA_TARJETA=" "$guardado"; then origen="perfil guardado por WirePlumber"; else origen="regla 10-elena-perfil.conf"; fi
if [[ $activo == "$ELENA_PERFIL" ]]; then
  echo "OK: WirePlumber eligió $activo sin forzarlo ($origen)"
else
  echo "AVISO: WirePlumber eligió '$activo' ($origen); se fuerza con pactl" >&2
  pactl set-card-profile "$ELENA_TARJETA" "$ELENA_PERFIL"
fi
esperar "pactl list sinks short | grep -q '$ELENA_SINK_FISICO'" "no apareció el sink $ELENA_SINK_FISICO"

# 5. Por defecto y volúmenes
if [[ $alcance == todo ]]; then
  esperar "pactl list sinks short | grep -q elena_sistema" "no aparecieron los buses de ELENA"
  pactl set-default-sink elena_sistema
  # El físico queda fijo en 100%; el volumen se maneja por bus
  pactl set-sink-volume "$ELENA_SINK_FISICO" 100%
  echo "Sink físico en 100%: baja el volumen de los buses en pavucontrol antes de reproducir."
  if [[ -f $conf_pw/20-elena-mic.conf ]]; then
    esperar "pactl list sources short | awk '\$2 == \"elena_mic\" { ok = 1 } END { exit !ok }'" "no apareció el mic virtual elena_mic"
    pactl set-default-source elena_mic
    # El mic físico también en 100%; la ganancia y el silencio pasan a "ELENA · Voz".
    # La primera vez, si el mic físico estaba silenciado, el silencio pasa a elena_voz.
    if pactl get-source-volume "$mic" >/dev/null 2>&1; then
      pactl set-source-volume "$mic" 100%
      pactl set-source-mute "$mic" 0
    else
      echo "AVISO: no está conectado el mic $mic; el mic virtual espera a que aparezca." >&2
    fi
    if (( mic_nuevo )) && grep -q '^MIC_SILENCIO=1' "$previo"; then
      pactl set-sink-mute elena_voz 1
      echo "Tu mic estaba silenciado: 'ELENA · Voz' queda silenciado (actívalo en pavucontrol)."
    fi
    # jq -e lee toda la salida; con `grep -q`, pw-dump moriría por SIGPIPE y pipefail lo tomaría como fallo
    esperar "pw-dump | jq -e 'any(.[]; .info.props[\"node.name\"]? == \"elena_puente_chat\")'" "no apareció el mic virtual completo"
    "$ELENA/scripts/mic.sh" asegurar
  fi
  # La mezcla para visualizadores (30-elena-visual.conf); qué buses entran: mise run visual
  if [[ -f $conf_pw/30-elena-visual.conf ]]; then
    esperar "pw-dump | jq -e 'any(.[]; .info.props[\"node.name\"]? == \"elena_visual_sistema\")'" "no apareció la mezcla para visualizadores (elena_visual)"
  fi
  # «Solo audífonos» (50-elena-audifonos.conf): el bus elena_audifonos; el modo lo vuelve a poner el vigilante
  if [[ -f $conf_pw/50-elena-audifonos.conf ]]; then
    # Sin él, todo lo demás funciona: es un aviso, no un error (el módulo lleva nofail)
    if ! (for _ in {1..30}; do pactl list sinks short | awk '$2 == "elena_audifonos" { ok = 1 } END { exit !ok }' && exit 0; sleep 0.5; done; exit 1); then
      echo "AVISO: no apareció elena_audifonos (ELENA · Audífonos): «solo audífonos» no funciona. Revisa 50-elena-audifonos.conf (journalctl --user -u pipewire)." >&2
    fi
  fi
  # La calibración de las bocinas (40-elena-calibracion.conf; mise run calibracion -- activar).
  # Activa: elena_salida al 100 % y los buses de bocinas en un solo volumen (el balance va en la
  # calibración; con respaldo). Inactiva con respaldo: los buses regresan a su balance de antes.
  if [[ -f $conf_pw/40-elena-calibracion.conf ]]; then
    esperar "pactl list sinks short | awk '\$2 == \"elena_salida\" { ok = 1 } END { exit !ok }'" \
      "no apareció elena_salida (ELENA · Calibración): las bocinas no suenan. Revisa 40-elena-calibracion.conf (journalctl --user -u pipewire) o: mise run calibracion -- desactivar && mise run aplicar"
  fi
  if [[ -x $ELENA/.venv/bin/elena ]]; then
    "$ELENA/.venv/bin/elena" calibracion buses
  else
    echo "AVISO: falta el entorno de Python (mise exec -- uv sync); los buses no se ajustaron a la calibración." >&2
  fi
  # El vigilante de reglas fijas (servicio de usuario elena-vigilante)
  "$ELENA/scripts/vigilante.sh" instalar
  # El control remoto de música (servicio de usuario elena-remoto; la regla del firewall va a mano)
  "$ELENA/scripts/remoto.sh" instalar
  # El lanzador «Apple Music (ELENA)» y el servicio elena-vm, solo si ya existe la VM
  if [[ -f $ELENA/.cache/vm/windows.qcow2 ]]; then
    "$ELENA/vm/lanzador.sh" instalar
  fi
else
  pactl set-default-sink "$ELENA_SINK_FISICO"
fi

echo
"$ELENA/scripts/estado.sh"
