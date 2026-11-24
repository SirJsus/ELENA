#!/usr/bin/env bash
# Servicio de usuario `elena-vigilante`: aplica las reglas fijas de
# local/reglas.toml a cada app nueva (`elena vigilar`). Lo instalan `aplicar todo` y
# `mise run vigilante instalar`, y lo quitan `quitar` y `mise run vigilante quitar`.
#   instalar  genera la unidad en .cache/systemd/, la enlaza con `systemctl --user link`
#             (~/.config/systemd/user/elena-vigilante.service), la habilita (default.target.wants)
#             y la (re)arranca. No reinicia PipeWire.
#   quitar    la detiene, la deshabilita y borra el enlace
#   estado    (por defecto) estado del servicio y sus últimas líneas
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vigilante}"
unidad=elena-vigilante.service
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
    # mise exec da el entorno de mise.toml (ELENA, PYTHONPYCACHEPREFIX…): nada fuera del repo
    cat > "$generada" <<EOF
[Unit]
Description=ELENA · vigilante de reglas fijas (cada app nueva a su bus)
Documentation=file://$ELENA/docs/guias/consola.md
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service pipewire-pulse.service

[Service]
ExecStart=$(command -v mise) exec -C $ELENA -- $ELENA/.venv/bin/elena vigilar
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user link "$generada" >/dev/null
    systemctl --user daemon-reload
    systemctl --user enable "$unidad" 2>&1 | grep -v '^Created symlink' || true
    systemctl --user restart "$unidad"
    echo "Vigilante instalado y corriendo ($enlace -> $generada)."
    ;;
  quitar)
    if [[ -L $enlace ]]; then
      systemctl --user disable --now "$unidad" >/dev/null 2>&1 || true
      rm -f "$enlace"
      systemctl --user daemon-reload
      echo "Vigilante quitado ($enlace)."
    fi
    ;;
  estado)
    systemctl --user --no-pager -n 8 status "$unidad" || true
    ;;
  *) echo "Uso: vigilante.sh instalar | quitar | estado" >&2; exit 1 ;;
esac
