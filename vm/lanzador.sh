#!/usr/bin/env bash
# La VM como una app más. Instala dos cosas fuera del repo (enlaces a .cache/):
#   ~/.config/systemd/user/elena-vm.service          corre vm/arrancar.sh; al detenerse (también
#       al cerrar sesión o apagar Linux) apaga Windows por ACPI y espera hasta 2 min. Sin
#       [Install]: no arranca solo, lo arranca el lanzador. PartOf/After graphical-session.target:
#       con UWSM, Hyprland es una unidad de usuario y systemd detiene la VM antes que a él (sin
#       esto, QEMU moría con la ventana y Windows se cortaba en seco).
#   ~/.local/share/applications/elena-apple-music.desktop    «Apple Music (ELENA)»: ejecuta
#       scripts/apple-music.sh (enfoca la VM o arranca el servicio).
# Y una tercera,
#   ~/.config/systemd/user/elena-vm-musica.service   el puente de música (`elena vm musica`):
#       Apple Music de la VM como reproductor MPRIS (Noctalia, teclas multimedia). elena-vm lo
#       pide con Wants=; él va con BindsTo=/After=elena-vm, así que arranca después de la VM y se
#       detiene con ella (antes que ella). Con `vm:arrancar` en primer plano, a mano:
#       mise run elena -- vm musica
# Lo llaman `aplicar todo` (si ya existe la VM) y `quitar`; a mano: mise run vm:lanzador.
#   instalar  genera y enlaza los tres; no arranca la VM
#   quitar    apaga Windows en orden si la VM corre con el servicio, y borra los enlaces
#   estado    (por defecto) estado de los dos servicios y sus últimas líneas
set -euo pipefail
: "${ELENA:?Corre esto con: mise run vm:lanzador}"
unidad=elena-vm.service
generada="$ELENA/.cache/systemd/$unidad"
enlace="$HOME/.config/systemd/user/$unidad"
musica=elena-vm-musica.service
generada_musica="$ELENA/.cache/systemd/$musica"
enlace_musica="$HOME/.config/systemd/user/$musica"
lanzador="$HOME/.local/share/applications/elena-apple-music.desktop"
generado="$ELENA/.cache/lanzadores/elena-apple-music.desktop"

case ${1:-estado} in
  instalar)
    if [[ ! -x $ELENA/.venv/bin/elena ]]; then
      echo "ERROR: falta el entorno de la consola (.venv), que apaga la VM. Créalo con: mise exec -- uv sync" >&2; exit 1
    fi
    for e in "$enlace" "$enlace_musica" "$lanzador"; do
      if [[ -e $e && ! -L $e ]]; then echo "ERROR: $e existe y no es un enlace de ELENA; no lo toco." >&2; exit 1; fi
    done
    mise=$(command -v mise)
    mkdir -p "$(dirname "$generada")" "$(dirname "$generado")"
    cat > "$generada" <<EOF
[Unit]
Description=ELENA · VM de Windows con Apple Music (Atmos en 5.1 hacia elena_musica)
Documentation=file://$ELENA/docs/guias/vm-apple-music-atmos.md
After=graphical-session.target pipewire-pulse.service wireplumber.service
PartOf=graphical-session.target
# El puente de música arranca con la VM (si falla, la VM sigue igual)
Wants=$musica

[Service]
Type=simple
ExecStart=$mise exec -C $ELENA -- $ELENA/vm/arrancar.sh
# Apagado ordenado: el botón de encendido por ACPI (pulir.cmd lo deja en «Apagar»). Corre
# también si Windows ya se apagó solo; entonces no hace nada.
ExecStop=$mise exec -C $ELENA -- $ELENA/.venv/bin/elena vm apagar --esperar 120
# Después, dejar que distrobox y podman cierren la sesión del contenedor (hasta 5 s y 1 s más
# para la limpieza de conmon); si no, systemd los mata con SIGKILL a medio cerrar.
ExecStop=/bin/sh -c 'p=\$\$MAINPID; [ -n "\$\$p" ] && for i in \$\$(seq 50); do kill -0 "\$\$p" 2>/dev/null || break; sleep 0.1; done; sleep 1'
TimeoutStopSec=150
KillMode=mixed
EOF
    cat > "$generada_musica" <<EOF
[Unit]
Description=ELENA · Puente de música: Apple Music de la VM como reproductor MPRIS (Noctalia, teclas multimedia)
Documentation=file://$ELENA/docs/guias/vm-apple-music-atmos.md
# Con la VM: arranca después de ella y se detiene con ella (antes que ella)
BindsTo=$unidad
After=$unidad

[Service]
Type=simple
ExecStart=$mise exec -C $ELENA -- $ELENA/.venv/bin/elena vm musica
Restart=on-failure
RestartSec=5
# 75: ya hay otro puente corriendo (p. ej. a mano); reintentar no sirve
RestartPreventExitStatus=75
EOF
    cat > "$generado" <<EOF
[Desktop Entry]
Type=Application
Name=Apple Music (ELENA)
Comment=Apple Music con Dolby Atmos en 5.1 (VM de Windows) · bus ELENA · Música
Exec="$ELENA/scripts/apple-music.sh"
Icon=audio-x-generic
Terminal=false
Categories=AudioVideo;Audio;Player;
StartupWMClass=qemu
EOF
    systemctl --user link "$generada" "$generada_musica" >/dev/null
    systemctl --user daemon-reload
    if [[ ! -L $lanzador ]]; then
      mkdir -p "$(dirname "$lanzador")"
      ln -s "$generado" "$lanzador"
    fi
    echo "Lanzador «Apple Music (ELENA)» y servicios $unidad y $musica instalados."
    if systemctl --user is-active --quiet "$unidad"; then
      echo "La VM ya corre con el servicio: el puente de música arranca con la siguiente (o: systemctl --user start $musica)."
    fi
    ;;
  quitar)
    if systemctl --user is-active --quiet "$unidad" 2>/dev/null; then
      echo "Apagando Windows (la VM corre con el servicio; puede tardar un minuto)…"
      systemctl --user stop "$unidad" || true
    fi
    systemctl --user stop "$musica" 2>/dev/null || true   # por si corría sin la VM
    [[ -L $lanzador && $(readlink -f "$lanzador") == "$ELENA"/* ]] && { rm "$lanzador"; echo "Quitado $lanzador"; }
    recargar=0
    for e in "$enlace" "$enlace_musica"; do
      if [[ -L $e ]]; then rm -f "$e"; echo "Quitado $e"; recargar=1; fi
    done
    if (( recargar )); then systemctl --user daemon-reload; fi
    ;;
  estado)
    systemctl --user --no-pager -n 8 status "$unidad" || true
    echo
    systemctl --user --no-pager -n 8 status "$musica" || true
    ;;
  *) echo "Uso: lanzador.sh instalar | quitar | estado" >&2; exit 1 ;;
esac
