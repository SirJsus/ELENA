#!/usr/bin/env bash
# Interruptores del mic virtual (ver plantillas/pipewire/20-elena-mic.conf; sin mic en local/equipo.toml no existe).
#   mic.sh estado            muestra los dos interruptores
#   mic.sh ruido [on|off]    supresión de ruido (on: mic limpio; off: mic directo)
#   mic.sh puente [on|off]   puente entre llamadas (navegador → Mic y chat → Mic web)
#   mic.sh voz [on|off]      tu voz en todos los mics (silencia o activa "ELENA · Voz")
#   mic.sh asegurar          lo usa `aplicar`: deja ruido en un estado válido y apaga el puente
# Funciona silenciando streams; WirePlumber recuerda el silencio entre reinicios.
set -euo pipefail

id_de() {  # id de PipeWire de un nodo por su node.name
  pw-dump | jq -r --arg n "$1" '.[] | select(.type == "PipeWire:Interface:Node"
    and .info.props["node.name"] == $n) | .id' | head -1
}
silenciado() { local id; id=$(id_de "$1"); wpctl get-volume "$id" | grep -q MUTED; }
silenciar()  { local id; id=$(id_de "$1"); wpctl set-mute "$id" "$2"; }  # 1 = silenciar, 0 = activar

# Antes de cualquier cosa: los 4 streams de los interruptores deben existir
for n in elena_voz elena_directo elena_limpio_a_voz elena_puente_navegador elena_puente_chat; do
  if [[ -z $(id_de "$n") ]]; then
    echo "ERROR: no existe el nodo $n. El mic virtual se carga con mise run aplicar (con ELENA_MIC_FISICO en local/equipo.toml)" >&2
    exit 1
  fi
done

estado_ruido() {
  local d=0 l=0
  silenciado elena_directo      && d=1
  silenciado elena_limpio_a_voz && l=1
  if   (( d == 1 && l == 0 )); then echo on
  elif (( d == 0 && l == 1 )); then echo off
  else echo inválido; fi       # los dos activos (voz doble) o los dos silenciados (sin voz)
}
estado_puente() {
  local n=0 c=0
  silenciado elena_puente_navegador && n=1
  silenciado elena_puente_chat      && c=1
  if   (( n == 1 && c == 1 )); then echo off
  elif (( n == 0 && c == 0 )); then echo on
  else echo mixto; fi
}

ruido() {
  case ${1:-} in
    on)  silenciar elena_directo 1; silenciar elena_limpio_a_voz 0 ;;
    off) silenciar elena_limpio_a_voz 1; silenciar elena_directo 0 ;;
    "")  ;;
    *) echo "ERROR: usa on u off" >&2; exit 1 ;;
  esac
  echo "Supresión de ruido: $(estado_ruido)"
}
puente() {
  case ${1:-} in
    on)  silenciar elena_puente_navegador 0; silenciar elena_puente_chat 0 ;;
    off) silenciar elena_puente_navegador 1; silenciar elena_puente_chat 1 ;;
    "")  ;;
    *) echo "ERROR: usa on u off" >&2; exit 1 ;;
  esac
  echo "Puente entre llamadas: $(estado_puente)"
  [[ $(estado_puente) == on ]] && echo "  (Discord debe ir a elena_chat y Meet debe usar 'ELENA · Mic web')"
  return 0
}

estado_voz() { silenciado elena_voz && echo "off (silenciada)" || echo on; }
voz() {
  case ${1:-} in
    on)  silenciar elena_voz 0 ;;
    off) silenciar elena_voz 1 ;;
    "")  ;;
    *) echo "ERROR: usa on u off" >&2; exit 1 ;;
  esac
  echo "Tu voz: $(estado_voz)"
}

case ${1:-estado} in
  estado)   echo "Tu voz:                $(estado_voz)"; echo "Supresión de ruido:    $(estado_ruido)"; echo "Puente entre llamadas: $(estado_puente)" ;;
  voz)      voz "${2:-}" ;;
  ruido)    ruido "${2:-}" ;;
  puente)   puente "${2:-}" ;;
  asegurar) # La primera vez, WirePlumber no tiene estado y los dos caminos del mic suenan (voz doble)
            [[ $(estado_ruido) == inválido ]] && ruido on >/dev/null
            puente off >/dev/null
            echo "Mic: supresión de ruido $(estado_ruido), puente $(estado_puente)" ;;
  *) echo "Uso: mic.sh estado | voz [on|off] | ruido [on|off] | puente [on|off] | asegurar" >&2; exit 1 ;;
esac
