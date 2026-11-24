#!/usr/bin/env bash
# Prepara los instaladores (desde Linux) y los empaqueta en una
# ISO que la VM monta como segunda unidad de CD. En Windows solo hay que abrir el CD y
# ejecutar instalar.cmd (doble clic), que corre instalar.ps1 como administrador.
#   - vm/preparar_instaladores.py arma .cache/vm/instaladores/ (Apple Music listo para
#     registrar, dependencias y los scripts de vm/windows/) y comprueba que estén todas las
#     dependencias que pide el manifiesto de Apple Music.
#   - xorriso (de la caja elena-vm; no se instala nada en el sistema) crea la ISO.
# La VM lee la ISO al arrancar: si ya estaba encendida, apágala y vuelve a arrancarla.
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:instaladores}"
origen=${ELENA_VM_INSTALADORES:?falta ELENA_VM_INSTALADORES en mise.toml}
preparada="$ELENA/.cache/vm/instaladores"
destino="$ELENA/.cache/vm/instaladores.iso"

[[ -d $origen ]] || { echo "ERROR: no existe la carpeta $origen" >&2; exit 1; }
mkdir -p "$(dirname "$destino")"
# WebView2 Runtime: Apple Music lo necesita y Windows 10 LTSC no lo trae. Instalador completo
# (sin conexión) x64, del enlace oficial de Microsoft; se baja una sola vez.
descargas="$ELENA/.cache/vm/descargas"
webview2="$descargas/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
if [[ ! -s $webview2 ]]; then
  mkdir -p "$descargas"
  echo "Descargando WebView2 Runtime (x64, ~200 MB) de Microsoft…"
  curl -sSL --fail -o "$webview2.parcial" "https://go.microsoft.com/fwlink/?linkid=2124701"
  mv "$webview2.parcial" "$webview2"
fi

# Prueba del decodificador E-AC-3 de Windows, sin Apple Music ni DRM: 60 s con un tono
# distinto por canal (FL 440, FR 550, FC 660, LFE 50, traseras 770 y 880 Hz) a -20 dBFS.
prueba="$descargas/prueba-eac3-51.mp4"
if [[ ! -s $prueba ]]; then
  mkdir -p "$descargas"
  a=0.1
  ffmpeg -hide_banner -loglevel error -y -f lavfi \
    -i "aevalsrc=$a*sin(2*PI*440*t)|$a*sin(2*PI*550*t)|$a*sin(2*PI*660*t)|$a*sin(2*PI*50*t)|$a*sin(2*PI*770*t)|$a*sin(2*PI*880*t):s=48000:d=60:c=5.1" \
    -c:a eac3 -b:a 640k "$prueba"
fi

# La ISO vieja se borra antes: si la preparación falla, no queda una ISO incompleta o rota.
rm -f "$destino"
echo "Preparando $preparada (extrae ~500 MB)…"
# -I ignora PYTHONPYCACHEPREFIX: el bytecode se manda a .cache/ con -X (sin él, va al Python de mise)
python3 -I -X pycache_prefix="$ELENA/.cache/pycache" "$ELENA/vm/preparar_instaladores.py" "$origen" "$preparada" "$ELENA/vm/windows" "$descargas"

# Joliet (nombres largos en Windows) y Rock Ridge; etiqueta ELENA
distrobox enter elena-vm -- xorriso -as mkisofs -quiet -J -joliet-long -R -V ELENA \
  -o "$destino" "$preparada"
echo "Listo: $destino ($(du -h "$destino" | cut -f1))"
