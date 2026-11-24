#!/usr/bin/env bash
# Muestra sinks, fuentes y streams de audio: canales, estado, silencio y a qué se conectan.
# Sirve para ver si un juego, Cider o la VM llevan 6 canales.
set -euo pipefail

tarjeta=${ELENA_TARJETA:-}
perfil=$(pactl list cards | awk -v c="$tarjeta" '
  $1 == "Name:" { n = ($2 == c) }
  n && !h && /Active Profile:/ { sub(/.*Active Profile: /, ""); print; h = 1 }')
# El sink de la tarjeta que exista (5.1 con ELENA, estéreo sin ELENA). Nada aquí debe
# hacer fallar al script: con pipefail, un pactl que falla dentro de $(...) lo mataría.
salida=""
[[ -n $tarjeta ]] && salida=$(pactl list sinks short | awk -v b="alsa_output.${tarjeta#alsa_card.}." '!h && index($2, b) == 1 { print $2; h = 1 }')
volumen=""
[[ -n $salida ]] && volumen=$(pactl get-sink-volume "$salida" | grep -oE '[0-9]+%' | sort -u | paste -sd' ' || true)

[[ -z $tarjeta ]] && echo "Falta local/equipo.toml: corre mise run iniciar."
echo "Perfil de la tarjeta: ${perfil:-?}"
echo "Salida de la tarjeta: ${salida:-(ninguna)}${volumen:+ · $volumen}"
echo "Sink por defecto:     $(pactl get-default-sink)"
echo "Fuente por defecto:   $(pactl get-default-source)"
if pactl list sources short | awk '$2 == "elena_mic" { ok = 1 } END { exit !ok }'; then
  "$(dirname "$0")/mic.sh" estado | sed 's/^/  /'
fi
echo

# CONEXIÓN: para sinks y streams de salida, a dónde va; para fuentes y streams de
# grabación, de dónde viene (←).
# API (solo apps): pulse si entra por pipewire-pulse (winepulse, navegadores) y pw si es
# PipeWire nativo (winepipewire); sirve para saber qué driver usó Proton.
# Wine (winepulse) deja application.name y node.name en blanco (" "): para esos streams se
# muestra el nombre del proceso (argv[0] de /proc/<pid>/cmdline, ej. Control_DX12.exe) y el media.name.
dump=$(pw-dump)
procesos=$(jq -r '.[] | .info.props?["application.process.id"]? // empty' <<<"$dump" | sort -u |
  while read -r pid; do
    # argv[0] sin ruta (/ o \); comm se corta a 15 caracteres
    comm=$({ tr '\0' '\n' < "/proc/$pid/cmdline"; } 2>/dev/null | head -1 | sed 's|.*[/\\]||') || continue
    [[ -n $comm ]] || comm=$(cat "/proc/$pid/comm" 2>/dev/null) || continue
    jq -n --arg p "$pid" --arg c "$comm" '{($p): $c}'
  done | jq -s 'add // {}')
jq -r --argjson proceso "$procesos" '
  def lleno: if type == "string" and test("\\S") then . else null end;
  (map(select(.type == "PipeWire:Interface:Node"))) as $nodos
  | ($nodos | map({key: (.id | tostring), value: (.info.props["node.name"] // "?")}) | from_entries) as $nombre
  | (map(select(.type == "PipeWire:Interface:Link"))) as $links
  | ($links | reduce .[] as $l ({}; .[$l.info["output-node-id"] | tostring] += [$nombre[$l.info["input-node-id"] | tostring]])) as $va_a
  | ($links | reduce .[] as $l ({}; .[$l.info["input-node-id"] | tostring] += [$nombre[$l.info["output-node-id"] | tostring]])) as $viene_de
  | {"Audio/Sink": "sink", "Stream/Output/Audio": "stream", "Audio/Source": "fuente", "Stream/Input/Audio": "graba"} as $tipo
  | ["TIPO", "NODO", "APP", "API", "CANALES", "ESTADO", "SILENCIO", "CONEXIÓN"],
    ($nodos[]
     | (.info.props["media.class"] // "") as $c
     | select($tipo[$c] != null)
     | (if $c == "Audio/Source" or $c == "Stream/Input/Audio"
        then (($viene_de[.id | tostring] // []) | unique | join(", ") | if . == "" then "-" else "← " + . end)
        else (($va_a[.id | tostring] // []) | unique | join(", ") | if . == "" then "-" else . end) end) as $conexion
     | [ $tipo[$c],
         ((.info.props["node.name"] | lleno) // (.info.props["media.name"] | lleno) // "-"),
         ((.info.props["application.name"] | lleno)
          // $proceso[(.info.props["application.process.id"] // "") | tostring] // "-"),
         (if .info.props["client.api"] == "pipewire-pulse" then "pulse"
          elif .info.props["application.name"] != null then "pw" else "-" end),
         # Format está vacío mientras el nodo está suspendido; entonces se usa EnumFormat
         ((.info.params.Format // [])[0].channels // (.info.params.EnumFormat // [])[0].channels
          // .info.props["audio.channels"] // "?" | if type == "object" then (.default // "?") else . end | tostring),
         (.info.state // "-"),
         (if ((.info.params.Props // [])[0].mute // false) then "sí" else "-" end),
         $conexion ])
  | @tsv' <<<"$dump" | column -t -s $'\t'
