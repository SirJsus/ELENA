#!/usr/bin/env bash
# VM de Windows 10 IoT LTSC 2021 para Apple Music en Atmos.
# Corre DENTRO de la caja elena-vm; se lanza con `mise run vm:arrancar`.
#
# Audio: tarjeta USB virtual multicanal (usb-audio,multi=on: estéreo, 5.1 o 7.1), conectada
# directo al bus elena_musica. El mezclador de QEMU solo maneja estéreo, así que va apagado
# (mixing-engine=off) y, como pide el manual, también fixed-settings=off: el número de
# canales lo decide Windows al configurar el dispositivo (Panel de sonido → Configurar → 5.1).
#   ELENA_VM_AUDIO=pa (por defecto) o pipewire.
#   Medido el 2026-11-05 con la prueba de bocinas de Windows (`medir:canales`, columna CORTES):
#     pipewire: 8 a 15 cortes de 16 o 32 muestras en 20 s, con o sin out.latency o buffer=.
#               QEMU entrega 480 muestras cada 10 ms sin colchón y el grafo consume de 256 en
#               256; cuando caen dos ciclos entre dos entregas, faltan 32.
#     pa + out.latency=100000: 1 corte en 20 s. pipewire-pulse junta un colchón antes de
#               empezar (prebuf) y lo mantiene (tlength), así que absorbe el desfase.
# Video: VGA estándar, sin GPU dedicada (Apple Music no la necesita).
#   Resolución: ELENA_VM_RESOLUCION (mise.toml; conviene la de tu monitor) va en el EDID de la
#   VGA (xres/yres).
#   OVMF arranca en ese modo y Windows (Adaptador de pantalla básico de Microsoft) usa el modo
#   del firmware, así que se cambia aquí y no en Windows. Sin EDID, OVMF usa 1280x800 (lo que
#   tendría Windows). Verificado el 2026-11-08 (edk2-ovmf 202608) con un QEMU sin discos: con
#   xres=1600,yres=900, OVMF arranca en 1600x900, y Windows también (screendump). Si alguien
#   elige otra resolución en el menú de OVMF (Device Manager → OVMF Platform Configuration),
#   esa gana y queda guardada en OVMF_VARS.4m.fd.
#   Ventana (2026-11-13), para que parezca una app nativa: show-menubar=off quita la barra
#   «Machine / View» de QEMU, full-screen=on abre en pantalla completa (con la resolución del
#   monitor, pixel a pixel) y zoom-to-fit=on escala la imagen si la ventana sale de pantalla
#   completa (por ejemplo, maximizada con la barra del escritorio), en vez de recortarla.
#   Atajos de QEMU que siguen sirviendo: Ctrl+Alt+F (pantalla completa) y Ctrl+Alt+M (menú).
#
# Uso diario:
#   - disco de música aparte (musica.qcow2, M: en Windows), creado la primera vez con
#     ELENA_VM_MUSICA_TAMANO; los dos discos con TRIM (discard=unmap), así el host recupera lo
#     que Windows borra;
#   - ELENA_VM_RAM y ELENA_VM_NUCLEOS (en mise.toml);
#   - monitor QMP en .cache/vm/qmp.sock: lo usa `elena vm` (apagar por ACPI, red on/off);
#   - window-close=off: cerrar la ventana ya no le corta la corriente a Windows (en pantalla
#     completa no hay X, pero el compositor puede pedir el cierre: tampoco hace nada);
#   - la ISO de Windows solo se conecta si falta el disco o con ELENA_VM_CON_ISO=1;
#   - el CD de instaladores y la memoria USB de intercambio, igual: si falta el disco o con
#     ELENA_VM_CON_INSTALADORES=1 (pulir.cmd, diagnostico.ps1). A diario, ninguno (2026-11-08).
#
# Apple Music en los widgets de Linux: el COM1 de Windows va a .cache/vm/musica.sock,
# que lee el puente de música en el host. Es el mismo puerto serie que QEMU ya creaba con
# -display gtk (isa-serial en 0x3f8, IRQ 4), antes conectado a la pestaña escondida «serial0»
# de la ventana; Windows no ve hardware nuevo. wait=off: QEMU arranca sin esperar al puente.
#
# Éxito: Apple Music muestra Dolby Atmos y `mise run medir:canales elena_musica` (en el host)
# enseña el stream de la VM con 6 canales y las traseras con contenido propio.
set -euo pipefail
: "${ELENA:?Se lanza con: mise run vm:arrancar}"

VM="$ELENA/.cache/vm"
DISCO="$VM/windows.qcow2"
MUSICA="$VM/musica.qcow2"
QMP="$VM/qmp.sock"
SERIE="$VM/musica.sock"
RAM=${ELENA_VM_RAM:-4G}
NUCLEOS=${ELENA_VM_NUCLEOS:-6}
[[ $RAM =~ ^[0-9]+[MG]$ ]] || { echo "ERROR: ELENA_VM_RAM debe ser como 4G u 8192M" >&2; exit 1; }
[[ $NUCLEOS =~ ^[0-9]+$ ]] || { echo "ERROR: ELENA_VM_NUCLEOS debe ser un entero" >&2; exit 1; }
RESOLUCION=${ELENA_VM_RESOLUCION:-1920x1080}
[[ $RESOLUCION =~ ^([0-9]+)x([0-9]+)$ ]] || { echo "ERROR: ELENA_VM_RESOLUCION debe ser como 1920x1080" >&2; exit 1; }
ANCHO=${BASH_REMATCH[1]} ALTO=${BASH_REMATCH[2]}
# La VGA estándar trae 16 MiB de memoria de video (vgamem_mb): 32 bits por pixel deben caber.
(( ANCHO * ALTO * 4 <= 16 << 20 )) || { echo "ERROR: $RESOLUCION no cabe en los 16 MiB de la VGA estándar" >&2; exit 1; }
ISO="${ELENA_VM_ISO:-}"
INSTALADORES="$VM/instaladores.iso"
OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd
OVMF_VARS_PLANTILLA=/usr/share/edk2/x64/OVMF_VARS.4m.fd

case ${ELENA_VM_AUDIO:-pa} in
  pipewire) audiodev="pipewire,id=snd0,out.name=elena_musica,out.stream-name=ELENA-VM" ;;
  # pa necesita el socket explícito: sin server=, libpulse no conecta desde la caja
  # (intenta arrancar su propio servidor), aunque pactl sí lo haga.
  pa)       audiodev="pa,id=snd0,server=unix:${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pulse/native,out.name=elena_musica" ;;
  *) echo "ERROR: ELENA_VM_AUDIO debe ser pipewire o pa" >&2; exit 1 ;;
esac
audiodev+=",out.mixing-engine=off,out.fixed-settings=off"

# Ajustes contra cortes. Vacíos = valor de QEMU. vm/arrancar.sh pone ELENA_VM_LATENCIA=100000
# si no se define (ELENA_VM_LATENCIA= vacío la quita). Con el backend pipewire, el stream pide
# node.latency=360/48000 (7.5 ms, el 75% de timer-period) sin importar out.latency.
#   ELENA_VM_LATENCIA  out.latency del backend, en µs (con pa: el colchón del servidor)
#   ELENA_VM_TIMER     timer-period de QEMU, en µs (ej. 20000)
#   ELENA_VM_BUFFER    buffer= del usb-audio, en bytes (ej. 65536)
for v in ELENA_VM_LATENCIA ELENA_VM_TIMER ELENA_VM_BUFFER; do
  [[ -z ${!v:-} || ${!v} =~ ^[0-9]+$ ]] || { echo "ERROR: $v debe ser un entero" >&2; exit 1; }
done
[[ -n ${ELENA_VM_LATENCIA:-} ]] && audiodev+=",out.latency=$ELENA_VM_LATENCIA"
[[ -n ${ELENA_VM_TIMER:-} ]] && audiodev+=",timer-period=$ELENA_VM_TIMER"
# Puertos USB fijos (tableta 1, memoria de intercambio 2, audio 3): Windows reconoce la tarjeta
# de audio (sin número de serie) por su puerto. Era el 3 cuando se configuró en 5.1; sin la
# memoria, QEMU la pasaría al 2 y Windows la vería como nueva, en estéreo (2026-11-08).
usbaudio="usb-audio,audiodev=snd0,bus=xhci.0,port=3,multi=on"
[[ -n ${ELENA_VM_BUFFER:-} ]] && usbaudio+=",buffer=$ELENA_VM_BUFFER"

# Unidades de CD: la ISO de Windows (arranque) y la de instaladores (Apple Music y códecs).
# El disco arranca primero (bootindex=0); mientras esté vacío, OVMF pasa a la ISO de Windows
# y ahí hay que presionar una tecla en «Press any key to boot from CD».
# Al día ya no hace falta la ISO de Windows: solo para instalar (sin disco) o con ELENA_VM_CON_ISO=1.
cds=()
if [[ -f $DISCO && ${ELENA_VM_CON_ISO:-0} != 1 ]]; then
  :
elif [[ -n $ISO && -f $ISO ]]; then
  cds+=(-drive "file=$ISO,media=cdrom,if=none,id=cd0,readonly=on" -device ide-cd,drive=cd0,bus=ide.0,bootindex=1)
elif [[ ! -f $DISCO ]]; then
  echo "Primera vez: no encuentro la ISO de Windows (ELENA_VM_ISO='$ISO')" >&2
  exit 1
fi
if [[ $ISO == *,* || $INSTALADORES == *,* ]]; then
  echo "ERROR: QEMU no admite comas en las rutas de las ISO" >&2; exit 1
fi

# CD de instaladores (instalar.cmd, pulir.cmd, diagnostico.ps1) y memoria USB virtual de
# intercambio (FAT32, 64 MB, etiqueta ELENA-INT: Windows guarda ahí los reportes de
# diagnostico.ps1 y Linux los saca con `mise run vm:intercambio`, sin montar nada).
# Solo para instalar (sin disco) o con ELENA_VM_CON_INSTALADORES=1. Sin ellos, Windows deja de
# ver la unidad de CD y la memoria; al volver a conectarlos, recupera las mismas letras.
usb=()
INTERCAMBIO="$VM/intercambio.img"
if [[ ! -f $DISCO || ${ELENA_VM_CON_INSTALADORES:-0} == 1 ]]; then
  if [[ -f $INSTALADORES ]]; then
    cds+=(-drive "file=$INSTALADORES,media=cdrom,if=none,id=cd1,readonly=on" -device ide-cd,drive=cd1,bus=ide.1)
  else
    echo "AVISO: no existe $INSTALADORES (créala con: mise run vm:instaladores)" >&2
  fi
  [[ -f $INTERCAMBIO ]] || mkfs.fat -C -F 32 -n ELENA-INT "$INTERCAMBIO" 65536 >/dev/null
  usb+=(-drive "file=$INTERCAMBIO,format=raw,if=none,id=intercambio"
        -device usb-storage,drive=intercambio,bus=xhci.0,port=2,removable=on)
  echo "Con el CD de instaladores y la memoria de intercambio (ELENA-INT)"
fi

mkdir -p "$VM/tpm"
[[ -f $DISCO ]] || qemu-img create -f qcow2 "$DISCO" 64G
if [[ ! -f $MUSICA ]]; then
  # Disco de la biblioteca: crece solo con lo descargado. pulir.cmd lo formatea como M:
  qemu-img create -f qcow2 "$MUSICA" "${ELENA_VM_MUSICA_TAMANO:-150G}" >/dev/null
  echo "Creado $MUSICA (${ELENA_VM_MUSICA_TAMANO:-150G} virtuales; en Windows: pulir.cmd lo deja como M:)"
fi

# ¿Caben en el host los discos si se llenan? Si /home se llena, Windows recibe errores de escritura.
falta=0
for d in "$DISCO" "$MUSICA"; do
  virtual=$(qemu-img info "$d" | sed -n 's/^virtual size: .*(\([0-9]*\) bytes)$/\1/p')
  ocupa=$(( $(stat -c '%b * %B' "$d") ))
  if (( virtual > ocupa )); then falta=$(( falta + virtual - ocupa )); fi
done
libre=$(( $(df --output=avail -B1 "$VM" | tail -n1) ))
if (( falta > libre )); then
  echo "AVISO: si los discos de la VM se llenan, crecerían $(( falta >> 30 )) GiB y en el host quedan $(( libre >> 30 )) GiB libres." >&2
fi
[[ -f $VM/OVMF_VARS.4m.fd ]] || cp "$OVMF_VARS_PLANTILLA" "$VM/OVMF_VARS.4m.fd"

# TPM 2.0 emulado (Windows 10 no lo pide; no estorba). Termina solo cuando QEMU se desconecta.
swtpm socket --tpm2 --daemon --terminate \
  --tpmstate dir="$VM/tpm" \
  --ctrl type=unixio,path="$VM/tpm/sock"

echo "VM:    $NUCLEOS núcleos, $RAM de RAM, pantalla de $RESOLUCION; monitor QMP en $QMP"
echo "COM1:  $SERIE (puente de música)"
echo "Audio: -audiodev $audiodev"
echo "       -device $usbaudio"
# hv_*: enlightenments de Hyper-V; dan a Windows un reloj estable (menos cortes de audio).
exec qemu-system-x86_64 \
  -name elena-windows \
  -enable-kvm -machine q35,smm=on \
  -cpu host,hv_relaxed,hv_vapic,hv_spinlocks=0x1fff,hv_time \
  -smp "$NUCLEOS" -m "$RAM" -rtc base=localtime \
  -global driver=cfi.pflash01,property=secure,value=on \
  -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
  -drive if=pflash,format=raw,file="$VM/OVMF_VARS.4m.fd" \
  -chardev socket,id=chrtpm,path="$VM/tpm/sock" \
  -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
  -drive file="$DISCO",if=none,id=disco,format=qcow2,discard=unmap,detect-zeroes=unmap \
  -device nvme,drive=disco,serial=elena01,bootindex=0 \
  -drive file="$MUSICA",if=none,id=musica,format=qcow2,discard=unmap,detect-zeroes=unmap \
  -device nvme,drive=musica,serial=elena02 \
  "${cds[@]}" \
  -nic user,id=red,model=e1000e \
  -vga std -global VGA.xres="$ANCHO" -global VGA.yres="$ALTO" \
  -display gtk,window-close=off,show-menubar=off,full-screen=on,zoom-to-fit=on \
  -qmp unix:"$QMP",server=on,wait=off \
  -serial unix:"$SERIE",server=on,wait=off \
  -device qemu-xhci,id=xhci -device usb-tablet,bus=xhci.0,port=1 \
  "${usb[@]}" \
  -audiodev "$audiodev" \
  -device "$usbaudio"
