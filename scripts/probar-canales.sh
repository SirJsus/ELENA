#!/usr/bin/env bash
# Prueba de bocinas en el nodo indicado (por defecto ELENA · Calibración si existe; si no, el sink físico 5.1).
#   mise run probar:canales                  # una voz dice el nombre de cada canal
#   mise run probar:canales elena_juegos     # lo mismo, a través de un bus
#   mise run probar:canales 5                # una sola bocina (atajo de --canal 5)
#   mise run probar:canales --canal 5        # 1=FL 2=FR 3=RL 4=RR 5=FC 6=LFE
# Una sola bocina suena con ruido rosa; el LFE (6) suena con una senoidal de 50 Hz,
# porque la voz casi no se distingue en el sub.
set -euo pipefail
nodo=${1:-} canal=${2:-}
# Atajo: un número del 1 al 6 como primer argumento es el canal, no un nodo
if [[ $nodo =~ ^[1-6]$ && -z $canal ]]; then canal=$nodo nodo=""; fi
# Por defecto, ELENA · Calibración (elena_salida) si existe: se oye calibrado. Para comparar sin
# calibrar, pasa el físico: mise run probar:canales $ELENA_SINK_FISICO
if [[ -z $nodo ]] && pactl list sinks short | awk '$2 == "elena_salida" { ok = 1 } END { exit !ok }'; then
  nodo=elena_salida
fi
nodo=${nodo:-${ELENA_SINK_FISICO:?Corre esto con: mise run probar:canales}}

if ! volumen=$(pactl get-sink-volume "$nodo" 2>/dev/null); then
  echo "ERROR: no existe el sink '$nodo'. Revisa los nombres con: mise run estado" >&2; exit 1
fi
echo "Nodo:    $nodo"
echo "Volumen: $(head -1 <<<"$volumen" | grep -oE '[0-9]+%' | sort -u | paste -sd' ')"
dispositivo="pipewire:NODE=$nodo"
# Canales del nodo (2 en elena_sistema y elena_chat; 6 en el físico, elena_juegos y elena_musica)
canales=$(pactl list sinks | awk -v n="$nodo" '
  $1 == "Name:" { m = ($2 == n) }
  m && !h && /Sample Specification:/ { for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+ch$/) { sub(/ch/, "", $i); print $i; h = 1; break } }')
canales=${canales:-6}
echo "Canales: $canales"

# speaker-test a veces no termina al vaciar el búfer con el plugin de PipeWire, y en ese
# estado ignora SIGTERM: timeout manda SIGTERM a los $s segundos y SIGKILL 2 s después.
# Los códigos 124 (TERM) y 137 (KILL) se toman como fin normal.
tocar() {
  local s=$1 r=0; shift
  timeout --foreground -k 2 "$s" speaker-test -D "$dispositivo" -c "$canales" "$@" || r=$?
  [[ $r == 0 || $r == 124 || $r == 137 ]]
}

if [[ -z $canal ]]; then
  if (( canales == 2 )); then
    echo "Estéreo: debe sonar Front Left y luego Front Right (solo en las frontales)."
  else
    echo "speaker-test recorre el cuarto en sentido horario; anota dónde suena cada voz:"
    echo "  Front Left → Front Center → Front Right → Rear Right → Rear Left → LFE"
  fi
  tocar 15 -t wav -l 1
elif (( canales == 2 )) && [[ $canal =~ ^[3-6]$ ]]; then
  echo "ERROR: $nodo es estéreo; solo tiene los canales 1 (FL) y 2 (FR)" >&2; exit 1
elif [[ $canal =~ ^[1-6]$ ]]; then
  nombres=(- "Front Left" "Front Right" "Rear Left" "Rear Right" "Center" "LFE")
  echo "Canal $canal (${nombres[$canal]}); Ctrl+C para cortar"
  if [[ $canal == 6 ]]; then
    tocar 10 -s 6 -t sine -f 50
  else
    tocar 10 -s "$canal" -t pink
  fi
else
  echo "ERROR: el canal va de 1 a 6 (1=FL 2=FR 3=RL 4=RR 5=FC 6=LFE)" >&2; exit 1
fi
