#!/usr/bin/env bash
# Mide qué canales llevan señal en un sink (por defecto elena_juegos): graba su monitor
# unos segundos y muestra el nivel RMS y el pico de cada canal. Sirve para las fases 3 y 4
# (¿el juego, Cider o la VM mandan surround de verdad?).
#   mise run medir:canales                          # elena_juegos, 15 s
#   mise run medir:canales elena_musica --segundos 30
# Antes de medir muestra los streams que entran al sink, con sus canales: es la prueba
# directa. Un stream de 2 canales puede llenar las traseras por el upmix de PipeWire, y
# eso no es surround.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run medir:canales}"
nodo=${1:-elena_juegos} segundos=${2:-15}
[[ $segundos =~ ^[0-9]+$ && $segundos -gt 0 ]] || { echo "ERROR: --segundos debe ser un entero positivo" >&2; exit 1; }

dump=$(pw-dump)
# Posiciones de canal del sink (ej. FL FR FC LFE RL RR)
posiciones=$(jq -r --arg n "$nodo" '
  [.[] | select(.type == "PipeWire:Interface:Node" and .info.props["node.name"] == $n
               and .info.props["media.class"] == "Audio/Sink")][0]
  | if . == null then empty
    else ((.info.params.Format // [])[0].position // (.info.params.EnumFormat // [])[0].position // [])
         | join(" ") end' <<<"$dump")
if [[ -z $posiciones ]]; then
  echo "ERROR: no existe el sink '$nodo' o no tiene posiciones de canal. Revisa con: mise run estado" >&2; exit 1
fi
read -ra canal <<<"$posiciones"
n=${#canal[@]}

echo "Sink:     $nodo ($n canales: ${canal[*]})"
echo "Entran:"
# Wine deja application.name y node.name en blanco: se usa argv[0] del proceso y media.name
procesos=$(jq -r '.[] | .info.props?["application.process.id"]? // empty' <<<"$dump" | sort -u |
  while read -r pid; do
    comm=$({ tr '\0' '\n' < "/proc/$pid/cmdline"; } 2>/dev/null | head -1 | sed 's|.*[/\\]||') || continue
    [[ -n $comm ]] && jq -n --arg p "$pid" --arg c "$comm" '{($p): $c}'
  done | jq -s 'add // {}')
jq -r --arg n "$nodo" --argjson proceso "$procesos" '
  def lleno: if type == "string" and test("\\S") then . else null end;
  (map(select(.type == "PipeWire:Interface:Node"))) as $nodos
  | ($nodos | map(select(.info.props["node.name"] == $n)) | map(.id)) as $ids
  | [.[] | select(.type == "PipeWire:Interface:Link" and (.info["input-node-id"] as $i | $ids | index($i)))
         | .info["output-node-id"]] | unique as $origen
  | $nodos[] | select(.id as $i | $origen | index($i))
  | "  \((.info.props["node.name"] | lleno) // (.info.props["media.name"] | lleno) // "?") · "
    + "\((.info.props["application.name"] | lleno) // $proceso[(.info.props["application.process.id"] // "") | tostring] // "-") · "
    + "\((.info.params.Format // [])[0].channels // "?") canales · "
    + "\(if .info.props["client.api"] == "pipewire-pulse" then "pulse" else "pw" end)"' <<<"$dump" \
  | grep . || echo "  (nada: el sink no recibe audio ahora; la medida saldrá en silencio)"

wav="$ELENA/.cache/tmp/medida-$nodo.wav"
mkdir -p "$(dirname "$wav")"
echo
echo "Grabando $segundos s del monitor de $nodo → $wav"
# pw-record no tiene opción de duración: timeout le manda SIGINT (cierra el WAV bien) y
# SIGKILL 2 s después si no terminó. 124 y 137 son fin normal.
# Mientras graba, cada ~1 s se anotan los canales de cada stream que entra: un stream puede
# cambiar de formato a media medición (la VM de QEMU lo hace cuando Windows cambia el modo
# de la tarjeta USB), y entonces la tabla mezcla los dos formatos.
formatos="$ELENA/.cache/tmp/medida-$nodo.formatos"
: > "$formatos"
muestrear() {
  while :; do
    pw-dump 2>/dev/null | jq -r --arg n "$nodo" '
      (map(select(.type == "PipeWire:Interface:Node"))) as $nodos
      | ($nodos | map(select(.info.props["node.name"] == $n)) | map(.id)) as $ids
      | [.[] | select(.type == "PipeWire:Interface:Link" and (.info["input-node-id"] as $i | $ids | index($i)))
             | .info["output-node-id"]] | unique as $origen
      | $nodos[] | select(.id as $i | $origen | index($i))
      | "\(.id)\t\(.info.props["node.name"] // "?")\t\((.info.params.Format // [])[0].channels // "?")"' \
      >> "$formatos" 2>/dev/null || true
    sleep 1
  done
}
muestrear & muestreo=$!
rc=0
timeout -s INT -k 2 "$segundos" pw-record --target "$nodo" -P '{ stream.capture.sink = true }' \
  --rate 48000 --channels "$n" --channel-map "$(IFS=,; echo "${canal[*]}")" --format f32 "$wav" || rc=$?
kill "$muestreo" 2>/dev/null || true
wait "$muestreo" 2>/dev/null || true
if (( rc != 0 && rc != 124 && rc != 137 )); then echo "ERROR: pw-record terminó con código $rc" >&2; exit 1; fi

echo "Canales de lo que entró durante la grabación (secuencia, sin repetir):"
resumen=$(awk -F'\t' '
  { k = $1 "\t" $2
    if (!(k in seq)) { seq[k] = $3; orden[++n] = k }
    else if (ult[k] != $3) seq[k] = seq[k] " → " $3
    ult[k] = $3 }
  END { for (i = 1; i <= n; i++) { split(orden[i], a, "\t"); print "  " a[2] " (id " a[1] "): " seq[orden[i]] } }' "$formatos")
echo "${resumen:-  (nada entró al sink)}"
if [[ $resumen == *"→"* ]]; then
  echo "  ⚠️  Un stream cambió de canales a media grabación: la tabla de abajo mezcla los dos formatos."
fi

# Nivel por canal con astats. Sin señal, astats da "-inf".
niveles() {  # niveles [filtro previo] → líneas "RMS PICO" por canal
  local filtro=${1:+$1,}
  ffmpeg -hide_banner -nostats -i "$wav" \
    -af "${filtro}astats=measure_perchannel=RMS_level+Peak_level:measure_overall=none" -f null - 2>&1 |
    awk '  # astats imprime "Channel: N" y después el pico antes que el RMS
      function dec(v) { return v ~ /inf/ ? v : sprintf("%.1f", v) }
      /Channel:/        { if (c) print dec(r), dec(p); c = 1; r = p = "?" }
      /RMS level dB:/   { r = $NF }
      /Peak level dB:/  { p = $NF }
      END               { if (c) print dec(r), dec(p) }'
}
# Cortes: huecos de silencio TOTAL (ceros exactos) de 0.5 a 100 ms con sonido a los dos
# lados. Es la huella de un búfer que se vació (underrun): la música real casi nunca trae
# ceros exactos a media nota. Se buscan como bytes de float32 en cero (+0.0 o -0.0).
cortes() {
  ffmpeg -hide_banner -loglevel error -i "$wav" -f f32le -acodec pcm_f32le - |
    python3 -I -X pycache_prefix="$ELENA/.cache/pycache" -c '
import sys, array, re
ch = int(sys.argv[1])
d = array.array("f"); d.frombytes(sys.stdin.buffer.read())
cero = re.compile(rb"(?:\x00\x00\x00[\x00\x80]){24,4800}")
for c in range(ch):
    x = d[c::ch]; n = len(x); k = 0
    for m in cero.finditer(x.tobytes()):
        i = -(-m.start() // 4); j = m.end() // 4
        if j - i < 24 or i < 240 or j + 240 > n:
            continue
        if max(map(abs, x[i - 240:i])) > 0.01 and max(map(abs, x[j:j + 240])) > 0.01:
            k += 1
    print(k)' "$n"
}
mapfile -t corte < <(cortes)

echo
printf '%-6s %10s %10s %8s\n' CANAL "RMS (dB)" "PICO (dB)" CORTES
i=0 total_cortes=0
while read -r rms pico; do
  printf '%-6s %10s %10s %8s\n' "${canal[$i]}" "$rms" "$pico" "${corte[$i]:-?}"
  total_cortes=$((total_cortes + ${corte[$i]:-0}))
  i=$((i + 1))
done < <(niveles)
if (( total_cortes > 0 )); then
  echo "⚠️  $total_cortes cortes: huecos de silencio total a media nota (búfer vacío; se oyen como chasquidos)."
fi

# Traseras menos frontales: si son copia del frente, la diferencia cae muy por debajo
# del nivel de la trasera; con contenido distinto queda cerca de él.
indice() { local k; for k in "${!canal[@]}"; do [[ ${canal[$k]} == "$1" ]] && { echo "$k"; return; }; done; }
diferencia() {  # diferencia TRASERA FRONTAL
  local t f; t=$(indice "$1") f=$(indice "$2")
  [[ -n $t && -n $f ]] || return 0
  read -r rms pico < <(niveles "pan=mono|c0=c$t-c$f")
  printf '%-6s %10s %10s\n' "$1−$2" "$rms" "$pico"
}
if [[ " ${canal[*]} " == *" RL "* ]]; then
  echo
  diferencia RL FL
  diferencia RR FR
  echo
  echo "Lectura: trasera en -inf o muy baja → el juego mandó estéreo sin upmix."
  echo "         RL−FL muy por debajo de RL (más de 10 dB) → las traseras copian el frente."
  echo "         RL−FL cerca de RL → contenido distinto (surround real, o el upmix de PipeWire"
  echo "         si el stream que entra es de 2 canales: revisa la lista «Entran»)."
fi
