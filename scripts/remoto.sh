#!/usr/bin/env bash
# Servicio de usuario `elena-remoto`: el control remoto de música para el teléfono
# (`elena remoto`). Lo instalan `aplicar todo` y `mise run remoto instalar`, y lo quitan `quitar`
# y `mise run remoto quitar`. La regla de ufw va a mano (ELENA no toca /etc): ver
# docs/guias/control-remoto.md.
#   instalar  genera la unidad en .cache/systemd/, la enlaza con `systemctl --user link`
#             (~/.config/systemd/user/elena-remoto.service), la habilita (default.target.wants)
#             y la (re)arranca.
#   quitar    la detiene, la deshabilita y borra el enlace (el token de .cache/remoto/ se queda)
#   qr        el enlace y el QR para emparejar el teléfono
#   estado    (por defecto) estado del servicio, dirección y sus últimas líneas
set -euo pipefail
: "${ELENA:?Corre esto con: mise run remoto}"
unidad=elena-remoto.service
generada="$ELENA/.cache/systemd/$unidad"
enlace="$HOME/.config/systemd/user/$unidad"

case ${1:-estado} in
  instalar)
    if [[ ! -x $ELENA/.venv/bin/elena ]]; then
      echo "ERROR: falta el entorno de la consola (.venv). Créalo con: mise exec -- uv sync" >&2; exit 1
    fi
    if [[ -e $enlace && ! -L $enlace ]]; then
      echo "ERROR: $enlace existe y no es un enlace de ELENA; no lo toco." >&2; exit 1
    fi
    mkdir -p "$(dirname "$generada")"
    # mise exec da el entorno de mise.toml (ELENA, ELENA_REMOTO_PUERTO, PYTHONPYCACHEPREFIX…)
    cat > "$generada" <<UNIDAD
[Unit]
Description=ELENA · control remoto de música (teléfono, puerto $ELENA_REMOTO_PUERTO)
Documentation=file://$ELENA/docs/guias/control-remoto.md
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service pipewire-pulse.service

[Service]
ExecStart=$(command -v mise) exec -C $ELENA -- $ELENA/.venv/bin/elena remoto
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIDAD
    systemctl --user link "$generada" >/dev/null
    systemctl --user daemon-reload
    systemctl --user enable "$unidad" 2>&1 | grep -v '^Created symlink' || true
    systemctl --user restart "$unidad"
    echo "Control remoto instalado y corriendo ($enlace -> $generada)."
    echo "Empareja el teléfono con: mise run remoto qr"
    ;;
  quitar)
    if [[ -L $enlace ]]; then
      systemctl --user disable --now "$unidad" >/dev/null 2>&1 || true
      rm -f "$enlace"
      systemctl --user daemon-reload
      echo "Control remoto quitado ($enlace)."
    fi
    ;;
  qr)
    "$ELENA/.venv/bin/elena" remoto qr
    ;;
  estado)
    "$ELENA/.venv/bin/elena" remoto estado || true
    echo
    systemctl --user --no-pager -n 8 status "$unidad" || true
    ;;
  *) echo "Uso: remoto.sh instalar | quitar | qr | estado" >&2; exit 1 ;;
esac
