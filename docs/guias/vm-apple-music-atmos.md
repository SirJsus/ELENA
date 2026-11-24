# Guía: Apple Music Atmos en 5.1 con la VM de Windows

Apple Music para Linux no existe, y la de Windows es la única de escritorio que reproduce Dolby Atmos en bocinas 5.1 sin receptor. Esta guía la monta en una VM de Windows 10 IoT LTSC 2021, desde cero: decodifica el Atmos con el códec de Dolby y entrega 5.1 a `elena_musica`, que sale por las bocinas. Después la deja como una app más: un lanzador, el apagado ordenado, la biblioteca descargada en su propio disco y Apple Music en las teclas multimedia y los widgets del escritorio.

**Probado de punta a punta:** una pista Dolby Atmos sale por `elena_musica` con 6 canales, traseras con contenido propio y 0 cortes, también descargada y sin red.

**Lo que necesitas tú:** una ISO de Windows 10 IoT Enterprise LTSC 2021 y su licencia, y una cuenta de Apple Music. ELENA no descarga Windows ni Apple Music, ni elude su DRM.

## Cómo funciona

```
Apple Music (VM) ─ Atmos E-AC-3 JOC ─► códec Dolby (Media Foundation) ─► PCM 5.1
  ─► tarjeta USB virtual de QEMU (usb-audio, multi=on) ─► backend pa (100 ms de colchón)
  ─► elena_musica (5.1) ─► tarjeta 5.1 ─► bocinas
```

Igual que en un Windows instalado en el disco: el audio espacial de Windows está desactivado, las bocinas están en **5.1 Surround** y Apple Music tiene Dolby Atmos en **Siempre**. No hacen falta Dolby Access, HDMI ni receptor.

## Requisitos del host

- **ELENA aplicado** (`mise run aplicar`): el bus `elena_musica` tiene que existir.
- **`/dev/kvm`** accesible (CPU con virtualización: `svm` en AMD o `vmx` en Intel), unos **4 GB de RAM** libres (`ELENA_VM_RAM`) y espacio en `/home` para los dos discos: hasta 64 GB de Windows más lo que mida la biblioteca (el disco de música es de 150 GB virtuales, `ELENA_VM_MUSICA_TAMANO`). Los qcow2 solo crecen con lo que Windows escribe; si lo que pueden crecer no cabe, `qemu.sh` avisa al arrancar.
- **`distrobox`** y **`podman`** (capa 3; QEMU va dentro de la caja, no en el sistema).
- **`python3`, `ffmpeg` y `curl`** en el host: los usa `vm:instaladores` (preparar Apple Music, generar el MP4 de prueba y bajar WebView2).
- **Internet en la VM** (NAT de QEMU): para la Tienda y la sesión de Apple Music.

## Lo que tiene que estar en `.cache/` (ignorado en git)

| Archivo | Dónde va | De dónde sale |
|---|---|---|
| ISO de Windows 10 IoT Enterprise LTSC 2021 (x64) | `.cache/windows.iso` (`ELENA_VM_ISO`) | La tuya |
| `AppleMusic.zip`: el `.msixbundle` de Apple Music **renombrado con ese nombre exacto**; dentro debe traer `MusicPackage_<versión>_x64.msix` | `.cache/instaladores/` (`ELENA_VM_INSTALADORES`) | store.rg-adguard.net, ProductId `9pfhdd62mxs1` |
| `Microsoft.VCLibs.140.00_14.0.33519.0_x64__8wekyb3d8bbwe.Appx` (o un `Media_Foundation_Codecs.zip` que la traiga) | ídem | rg-adguard, lista de `9pfhdd62mxs1` |
| `Microsoft.VCLibs.140.00.UWPDesktop_14.0.33728.0_x64__8wekyb3d8bbwe.Appx` | ídem | rg-adguard, lista de `9pfhdd62mxs1` |
| `Microsoft.WindowsStore_…_neutral_~_8wekyb3d8bbwe.Msixbundle` | ídem | rg-adguard, `9WZDNCRFJBMP` |
| `Microsoft.StorePurchaseApp_…_neutral_~_8wekyb3d8bbwe.Msixbundle` | ídem | rg-adguard, `9NBLGGH4LS1F` |
| `Microsoft.UI.Xaml.2.8_…_x64__8wekyb3d8bbwe.Appx` | ídem | rg-adguard, lista de `9WZDNCRFJBMP` |
| `Microsoft.NET.Native.Framework.2.2_…_x64…` y `…Runtime.2.2_…_x64…` (opcionales) | ídem | ídem |
| `gUIA.txt` (opcional) | ídem | Tus notas: se copian al CD tal cual |

Si tus archivos están en otro lugar o con otro nombre, apunta a ellos desde `mise.local.toml`:

```toml
[env]
ELENA_VM_ISO = "{{config_root}}/.cache/Win10_IoT_LTSC_2021.iso"
ELENA_VM_INSTALADORES = "{{config_root}}/.cache/mis-instaladores"
```

**Cómo descargar de rg-adguard** (store.rg-adguard.net, anillo Retail):
- Cloudflare bloquea las descargas automáticas, así que se baja a mano desde el navegador.
- Usa clic derecho → «Guardar enlace como». Algunas descargas llegan **vacías (0 bytes)**; revisa el tamaño o bájalas con `curl -L -o "<nombre>" "<URL>"`.
- Toma la variante **x64** o **neutral**, nunca x86 ni arm.
- `mise run vm:instaladores` revisa todo esto y se detiene si algo falta, está vacío o pide un Windows mayor que 19044.

**Versión nueva de Apple Music:** basta con reemplazar `AppleMusic.zip` y correr `vm:instaladores`. El cambio de `MinVersion` es automático. Si la versión nueva pide otra dependencia o una versión mayor de VCLibs, el preparador lo dice con un «FALTA».

**El códec Dolby no se baja:** se obtiene desde la Microsoft Store dentro de la VM (paso 9). Si hay un `DolbyLaboratories…` en la carpeta, `vm:instaladores` lo omite.

`vm:instaladores` baja por su cuenta, a `.cache/vm/descargas/`, el WebView2 Runtime (instalador oficial completo de Microsoft) y genera `prueba-eac3-51.mp4`.

## En Linux

1. **ELENA aplicado** (los buses): `mise run aplicar`. `elena_musica` tiene que existir.
2. **Distrobox y podman**, con el gestor de paquetes de tu distribución (en Arch: `sudo pacman -S distrobox podman`). Es lo único que va en el sistema: QEMU va dentro de la caja.
3. **Caja con QEMU:** `mise run vm:caja`. Crea `elena-vm` (Arch con `qemu-desktop`, `edk2-ovmf`, `swtpm`, `libisoburn`, `pipewire`, `dosfstools` y `mtools`). Después comprueba que QEMU tenga `usb-audio` y que los backends `pipewire` y `pa` **conecten de verdad** al audio del host.
4. **CD de instaladores:** `mise run vm:instaladores`. Crea `.cache/vm/instaladores.iso` (~870 MB, etiqueta `ELENA`) con:
   - `AppleMusic/`: el `.msix` x64 extraído, sin `AppxMetadata`, `AppxSignature.p7x` ni `[Content_Types].xml`, con `MinVersion` cambiado a `10.0.19044.0` (la versión 1.1538 pide `10.0.26100.0`, Windows 11 24H2).
   - `dependencias/`: VCLibs, UI.Xaml, .NET Native, Store Purchase App y Microsoft Store.
   - `extras/`: el instalador de WebView2 y `prueba-eac3-51.mp4`.
   - `instalar.cmd`, `instalar.ps1`, `pulir.cmd`, `diagnostico.ps1` y los demás scripts de `vm/windows/`.
5. **Arrancar la VM:** `mise run vm:arrancar`. No hace falta poner variables. La línea `Audio:` debe empezar con `pa,id=snd0,server=unix:…` y terminar con `out.latency=100000`.
   - La primera vez (sin disco de Windows) se conectan la ISO de Windows, el CD de instaladores y la memoria de intercambio. Si apagas la VM antes de terminar el paso 11, vuelve a arrancarla con `ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar`: con el disco ya creado, el CD y la memoria solo se conectan si se piden.

## En Windows (la VM)

6. **Instalar Windows:**
   - En «Press any key to boot from CD», presiona una tecla.
   - Crea una cuenta local y elige «No tengo clave de producto».
   - **Región: elige un país** (México, Estados Unidos…), no «Latinoamérica y el Caribe»: con esa región, la Tienda no encuentra el códec de Dolby. Si se te pasa, `instalar.cmd` la cambia a México (paso 8).
7. **Bocinas en 5.1:** Panel de sonido → el dispositivo USB («Speakers (Audio Device)») → Configurar → **5.1 Surround** → Probar. En Linux, mientras suena:
   ```
   mise run medir:canales elena_musica
   ```
   Debe mostrar `elena-windows` con **6 canales** todo el tiempo y `CORTES` en 0 o cerca.
8. **`instalar.cmd`** (doble clic en el CD «ELENA»; pide administrador). Hace en orden:
   - **la región:** si es «Latinoamérica y el Caribe», la cambia a México;
   - **el Modo de desarrollador;**
   - **las dependencias y la Tienda;**
   - **WebView2;**
   - **Apple Music:** la copia a `C:\Apps\AppleMusic` y la registra;
   - **la Tienda:** la abre en el códec Dolby.
9. **Códec Dolby desde la Tienda:** en la página «Dolby Digital Plus decoder for PC OEMs» presiona **Obtener** (gratis). Si no se abrió sola:
   ```powershell
   start ms-windows-store://pdp/?ProductId=9NVJQJBDKN97
   ```
   - En Windows: Configuración → Sistema → Sonido → el dispositivo → **Audio espacial: Desactivado** (es lo que trae por defecto).
10. **Prueba del códec, sin Apple Music:** abre `extras\prueba-eac3-51.mp4` en el Reproductor de Windows Media. Debe sonar un tono distinto en cada bocina, y `medir:canales elena_musica` debe ver los 6 canales.
11. **Apple Music:** inicia sesión → Configuración → Reproducción → **Dolby Atmos: Siempre**, y **Sound Check y el ecualizador desactivados**. Con los dos activos, la señal sube unos 10 dB y los picos de FL y FR se aplastan contra un techo de −0.13 dBFS; sin ellos, la pista llega sin limitar y con unos 9 dB de margen. El volumen se controla en ELENA, no en Windows. Reproduce una pista con el distintivo Dolby Atmos y corre:
    ```
    mise run medir:canales elena_musica --segundos 30
    ```
    - **Éxito:** `elena-windows` con 6 canales, traseras con nivel propio, RL−FL cerca de RL y `CORTES` en 0 o cerca.
    - **Comparación:** la misma pista con Dolby Atmos «Desactivado» debe dejar las traseras en `-inf`.

## Dejarla lista para el uso diario

12. **El lanzador y el servicio** (en Linux): `mise run aplicar` los crea si ya existe la VM; si no, `mise run vm:lanzador instalar`. Quedan «Apple Music (ELENA)» en el menú de apps y el servicio de usuario `elena-vm`.
13. **Arranca la VM con el CD:** `ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar`. Crea `.cache/vm/musica.qcow2`, el disco de la biblioteca, que Windows ve como un disco vacío. El CD «ELENA» tiene que ser el de ahora (`mise run vm:instaladores` con la VM apagada): trae `pulir.cmd`.
14. **`pulir.cmd`** (doble clic en el CD; pide administrador). Se corre con la cuenta de todos los días y se puede repetir. Hace, en orden:
    - **disco M: «Musica»** (GPT y NTFS), sin indexado y excluido de Defender, con la carpeta `M:\Apple Music`;
    - **energía:** sin hibernación ni inicio rápido, sin suspensión, el botón de encendido en «Apagar» y las apps se cierran sin preguntar al apagar;
    - **Windows Update** sin reinicios automáticos con la sesión iniciada;
    - **sonidos de Windows** en «Sin sonidos»;
    - **limpieza:** DISM `/ResetBase`, el Liberador de espacio, temporales y TRIM de C: (DISM tarda de 5 a 15 minutos);
    - **inicio de sesión automático** (pide la contraseña; queda en el registro de la VM);
    - **Apple Music al iniciar sesión**, y al cerrarla Windows se apaga en 15 s (`shutdown /a` lo cancela). Antes de abrirla, `apple-music.ps1` borra el certificado de la tienda de su caché (el error 7502; ver «Problemas conocidos»);
    - **el puente de música:** publica por COM1 lo que suena, para los widgets de Linux. Arranca oculto al iniciar sesión y también al terminar este paso.

    Opciones (desde una terminal en el CD o con Win+R, por ejemplo `D:\pulir.cmd -SinApagar`): `-SinApagar` (cerrar Apple Music no apaga), `-SinLimpieza`, `-SinInicioAutomatico` y `-SinPuente` (quita el puente de música). **Solo el puente**, en segundos y sin pedir la contraseña: `D:\pulir.cmd -SinLimpieza -SinInicioAutomatico`.
15. **Espacio recuperado:** en Linux, `du -h .cache/vm/windows.qcow2` (en una prueba, de 26 a 17 GB). Mide con `du`, no con `ls`: btrfs perfora el archivo y el tamaño aparente no baja. Si no bajó, ver «Compactar el disco de Windows» abajo.
16. **Biblioteca en M:** en Apple Music → Configuración, la carpeta de medios o de descargas en `M:\Apple Music` y, si existe la opción, descargar en Dolby Atmos. **Antes de bajar todo, la prueba sin red:** descarga un álbum Atmos y, en Linux:
    ```
    mise run elena -- vm red off
    mise run medir:canales elena_musica --segundos 30
    mise run elena -- vm red on
    ```
    Debe dar 6 canales, traseras propias y 0 cortes, sin internet. `diagnostico.ps1` muestra en qué carpetas quedaron las descargas.
17. **La biblioteca completa:** mientras se descarga, no cierres Apple Music (las descargas las hace su proceso; al cerrarla, Windows se apaga). Si vas a dejarla descargando, usa `shutdown /a` si se te cierra.

## Uso diario

1. **Abrir:** «Apple Music (ELENA)» en el menú de apps. Windows arranca, inicia sesión solo y abre Apple Music. Si la VM ya estaba abierta, el lanzador enfoca su ventana. También desde la consola ELENA (menú `:`).
2. **Volumen:** deja **Windows y Apple Music al 100%** y controla el volumen con el bus **«ELENA · Música»** (consola o pavucontrol). La tarjeta virtual trabaja en 16 bits (`S16LE`): bajar el volumen en Windows reduce la resolución. En PipeWire el volumen se aplica en float, sin esa pérdida.
3. **Cerrar:** cierra Apple Music y Windows se apaga solo; la ventana desaparece. También sirven el menú de Windows, «Apagar la VM» en la consola, `mise run elena -- vm apagar` o «Machine → Power Down» en el menú de QEMU (escondido: `Ctrl+Alt+M` lo muestra). **Cerrar la ventana no hace nada** (`window-close=off`): antes cortaba la corriente.
4. **Ventana:** la VM abre en **pantalla completa** y sin el menú de QEMU, a la resolución de `ELENA_VM_RESOLUCION` (pon la de tu monitor), así que se ve como una app nativa. El atajo de pantalla completa de tu escritorio (o `Ctrl+Alt+F`, de QEMU) sale y vuelve a entrar; maximizada, la imagen se escala para caber (un poco menos nítida). Para ir a otra app, cambia de escritorio o de ventana como siempre.
5. **Al cerrar sesión o apagar Linux** con la VM abierta, systemd apaga Windows en orden (espera hasta 2 min) antes de cerrar la sesión gráfica (con UWSM, antes de cerrar el compositor).
6. **Diagnosticar:** `mise run vm:arrancar` corre la VM en primer plano, con su salida en la terminal (no arranca si ya hay una). El servicio deja su salida en `journalctl --user -u elena-vm`.

### Recursos y discos

- **RAM y núcleos:** `ELENA_VM_RAM` (4G) y `ELENA_VM_NUCLEOS` (6); para cambiarlos siempre, ponlos en `mise.local.toml`, y para probar otro valor, desde la terminal: `ELENA_VM_RAM=8G mise run vm:arrancar`. Windows ocupa toda la RAM que recibe: con 8G, QEMU usaba 8.4 GB del host; con 4G, 4.3 GB, y Windows tenía 2.1 GB en uso con Apple Music sonando en Atmos, sin cortes.
- **Resolución:** `ELENA_VM_RESOLUCION` (1920x1080 por omisión; sin ella, OVMF usaría 1280x800). Va en el EDID de la VGA y OVMF arranca en ese modo; Windows usa el mismo, así que no se cambia desde Windows. Cuenta al **encender** la VM: reiniciar Windows no basta, hay que apagarla y volver a abrirla. Máximo: lo que quepa en los 16 MiB de la VGA (hasta 2560x1600).
- **Agrandar M:** con la VM apagada, `distrobox enter elena-vm -- qemu-img resize .cache/vm/musica.qcow2 +50G`, y en Windows, Administración de discos → M: → «Extender volumen». M: está solo en su disco, así que no hay particiones que estorben.
- **Compactar el disco de Windows** (solo si TRIM no lo encogió): en Windows, `sdelete -z C:` (Sysinternals); apagar; en Linux, `distrobox enter elena-vm -- qemu-img convert -O qcow2 .cache/vm/windows.qcow2 .cache/vm/windows-nuevo.qcow2`, revisar que arranque con el nuevo y reemplazar el viejo.
- **Reinstalar o reparar Windows:** `ELENA_VM_CON_ISO=1 mise run vm:arrancar` conecta la ISO de Windows (a diario ya no se conecta).
- **Lo que ve Windows a diario** (comprobado con QEMU en pausa y `query-block`):
  - **C:** (`windows.qcow2`) y **M:** (`musica.qcow2`): los únicos discos con algo montado;
  - **una unidad de DVD vacía**, que QEMU agrega por defecto (`ide2-cd0`, sin medio), y un puerto **LPT1** que nadie usa. Se dejaron así a propósito: quitarlos pide `-nodefaults`;
  - **COM1**, el canal del puente de música.

  El firmware (`OVMF_CODE` y `OVMF_VARS`) también aparece como «unidad» en QEMU, pero Windows no lo ve como disco.
- **CD de instaladores y memoria de intercambio:** a diario tampoco se conectan. Para `pulir.cmd`, `instalar.cmd` o `diagnostico.ps1`: `ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar`. Las dos variables se pueden juntar. La tarjeta de audio va siempre en el puerto USB 3, con o sin la memoria: si cambiara de puerto, Windows la vería como nueva y en estéreo.

## Apple Music en los widgets de Linux

Apple Music de la VM aparece en Linux como un reproductor más (MPRIS), llamado «Apple Music (ELENA)». Los widgets del escritorio (Noctalia, por ejemplo) muestran el título, el artista, el álbum, la carátula y el avance, y sus botones, las teclas multimedia y `playerctl` hacen play/pausa, siguiente y anterior, sin configurar nada más.

```
Apple Music ─► controles multimedia de Windows (SMTC) ─► puente-musica.ps1 ─► COM1
  ─► puerto serie de QEMU ─► .cache/vm/musica.sock ─► elena vm musica (servicio elena-vm-musica)
  ─► org.mpris.MediaPlayer2.elena_applemusic ─► widgets, teclas multimedia, playerctl
```

**Instalarlo** (una vez):
1. **En Linux:** `mise run aplicar`, o `mise run vm:lanzador instalar`, deja el servicio `elena-vm-musica` junto al de la VM. Arranca y se detiene con ella, sin hacer nada más.
2. **En Windows:** `ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar` y, con Win+R, `D:\pulir.cmd -SinLimpieza -SinInicioAutomatico`. El paso 8 instala el puente y debe decir «corriendo (PID …)».

**A diario** no hay que hacer nada: al abrir «Apple Music (ELENA)», el reproductor aparece en cuanto suena algo, y al cerrar Apple Music desaparece. El canal es el puerto serie, que no depende de la red; debería seguir funcionando con `elena vm red off` (sin probar todavía).

**Lo que no hace** (Apple Music no lo publica, o se decidió así):
- **No adelanta ni atrasa** dentro de la canción: Apple Music no lo permite en SMTC. Siguiente y anterior sí funcionan, y «anterior» vuelve al inicio de la canción.
- **Ni aleatorio ni repetir:** Apple Music no los publica.
- **El volumen** del reproductor queda fijo en 100 %: el que manda es el del bus «ELENA · Música».
- **Cerrar** desde el widget no hace nada, porque cerrar Apple Music apaga Windows. «Abrir» (Raise) enfoca la ventana de la VM.

**Revisarlo:**
- `mise run elena -- vm musica estado`: si el puente corre, si Windows está conectado, qué suena, en qué segundo, qué controles permite y la carátula.
- `mise run vm:lanzador`: el estado y las últimas líneas de los dos servicios; el registro completo, en `journalctl --user -u elena-vm-musica`.
- En Windows: `C:\Apps\ELENA\puente-musica.log`.
- Con `mise run vm:arrancar` (en primer plano) no hay servicio: el puente se corre a mano en otra terminal, con `mise run elena -- vm musica`.

Las carátulas se guardan en `.cache/vm/caratulas/` (las últimas 20) y el último estado en `.cache/vm/musica-estado.json`. El agente de Windows usa de 0.2 a 0.3 % de CPU.

## Problemas conocidos y su solución

| Síntoma | Causa | Solución |
|---|---|---|
| El stream de la VM llega con 2 canales | Windows no está en 5.1 | Paso 7. Una notificación de Windows puede abrir la tarjeta en estéreo un momento (`medir:canales` muestra `6 → 2 → 6`) |
| El sonido se rompe (chasquidos) | Backend `pipewire` de QEMU: entrega 480 muestras cada 10 ms sin colchón, contra ciclos de 256 en PipeWire. Huecos de 16 a 32 muestras | Backend `pa` con 100 ms (ya es el valor por defecto). Más colchón: `ELENA_VM_LATENCIA=200000 mise run vm:arrancar` |
| QEMU: «Could not create PipeWire context» | La caja no tiene `/usr/share/pipewire/client.conf` | Paquete `pipewire` en la caja (ya lo instala `vm:caja`) |
| QEMU con `pa`: «could not stat pidfile» | libpulse no encuentra el servidor desde la caja | `server=unix:$XDG_RUNTIME_DIR/pulse/native` (ya lo pone `qemu.sh`) |
| «Apple Music requires Microsoft Edge WebView2» | LTSC no trae WebView2 | Lo instala `instalar.cmd` desde `extras\` |
| Las pistas Atmos se saltan; el MP4 de prueba no abre («cannot play… codec») | El códec Dolby **no tiene licencia**: Media Foundation lo encuentra pero falla al crearlo con **`0x87E10BC6`** (sección «Prueba» de `diagnostico.ps1`) | Obtenerlo desde la Tienda (paso 9). Instalado desde el archivo **no** funciona, sea la versión que sea |
| La Tienda dice «Ya instalado» y no hace nada | Hay un Dolby instalado desde el archivo, sin licencia | `Get-AppxPackage *DolbyDigitalPlusDecoderOEM* \| Remove-AppxPackage` y volver al paso 9 |
| La Tienda: «Try that again / Something happened on our end» | **Región «Latinoamérica y el Caribe»**: la Tienda pide el producto con `market=419` y el catálogo responde 404. En `MX` y `US` sí existe | `Set-WinHomeLocation -GeoId 166` (México), cerrar la Tienda y repetir. El código real está en el registro `Microsoft-Windows-Store/Operational`, que incluye `diagnostico.ps1`; el texto de la ventana (ej. `XdggggIY/…`) es solo un identificador de sesión |
| Cerrar la ventana de QEMU no cierra la VM | Es a propósito (`window-close=off`): cerrarla cortaba la corriente | Cerrar Apple Music, o cualquier forma de «Cerrar» en [Uso diario](#uso-diario) |
| «An unknown error has occurred» en Home, New, Radio y la búsqueda, o «error 7502» al descargar; la biblioteca descargada sí suena y la red de Windows se ve bien | Apple Music guarda en su caché de WinINet la versión **gzip** del certificado con el que firma las peticiones a la tienda (`setupCert.plist`) y no la sabe leer; como el servidor siempre responde 304, la entrada mala no se renueva. Volver a iniciar sesión lo quita por un tiempo y regresa. Una captura de la red (`object-add filter-dump` por QMP) muestra DNS y TLS bien y respuestas cortas del catálogo ([investigación](https://github.com/mikepink/music-on-windows11-unknown-error-fix)) | `apple-music.ps1` la borra en cada arranque y lo anota en `C:\Apps\ELENA\apple-music.log` (`gzip` = era la mala). A mano, con Apple Music cerrada (y `shutdown /a`): `Get-ChildItem "$env:LOCALAPPDATA\Packages\AppleInc.AppleMusicWin_nzyj5cx40ttqa\AC\INetCache" -Recurse -Force -Filter "setupCert*" \| Remove-Item -Force -Verbose` y volver a abrirla |
| «error 7608» al descargar | La PC no está autorizada con tu cuenta | Tu nombre (abajo a la izquierda) → Authorize Machine. La VM cuenta como una computadora (máximo 5); si cambia de identidad, «Deauthorize All» en la cuenta y autorizar de nuevo |
| Windows se apagó al cerrar Apple Music sin querer | `apple-music.ps1` apaga al cerrarla | `shutdown /a` en los primeros 15 s; para que no apague, `pulir.cmd -SinApagar` |
| `elena vm apagar` no apaga Windows | El botón de encendido no está en «Apagar», o una app lo detiene | `pulir.cmd` (paso de energía); se espera hasta 120 s y luego avisa |
| «AVISO: si los discos de la VM se llenan…» al arrancar | Lo que pueden crecer los qcow2 no cabe en `/home` | Liberar espacio en el host o no agrandar M:; con el host lleno, Windows recibe errores de escritura |
| Windows sigue en la resolución anterior | La VM no se apagó (un reinicio de Windows no vuelve a leer el EDID), o alguien eligió una resolución en el menú de OVMF, que gana sobre el EDID | Apagar la VM y abrirla de nuevo. Si sigue igual: Esc al arrancar → Device Manager → OVMF Platform Configuration → la resolución deseada → Commit Changes and Exit |
| `vm:arrancar`: «la VM ya está corriendo» | Ya hay una (dos no comparten el disco) | El lanzador la enfoca; `mise run elena -- vm apagar` la apaga |
| `wsreset -i` no instala la Tienda | No funciona en todos los LTSC 2021 | Se instala a mano desde el CD (ya lo hace `instalar.cmd`) |
| El widget no muestra «Apple Music (ELENA)» | Apple Music todavía no tiene una canción elegida (no se publica un reproductor vacío), el puente de Windows no corre o el servicio no está instalado | Poner algo a sonar. Luego `mise run elena -- vm musica estado`: «Windows: sin conexión» es el servicio (`mise run vm:lanzador`); «conectado» sin reproductor es Windows (`C:\Apps\ELENA\puente-musica.log`; reinstalarlo con `pulir.cmd -SinLimpieza -SinInicioAutomatico`) |
| `elena vm musica`: «Ya hay un puente de música corriendo» | El servicio `elena-vm-musica` ya lo corre (QEMU atiende a un solo cliente por socket) | Nada; a mano solo hace falta con `vm:arrancar` |
| La barra de avance del widget no deja adelantar | Apple Music no permite cambiar la posición en SMTC (`CanSeek = false`) | Ninguna; siguiente y anterior sí funcionan |
| `vm:instaladores`: «FALTA …» o «no es un paquete válido» | Falta una dependencia, o una descarga llegó vacía o en x86 | Bajar lo que indica, en x64 y con más de 0 bytes |

## Herramientas

- **`mise run medir:canales <sink> [--segundos N]`:**
  - qué streams entran y con cuántos canales (los vuelve a revisar cada segundo);
  - el nivel RMS y el pico por canal, y los **CORTES** (huecos de silencio total a mitad del sonido);
  - las restas RL−FL y RR−FR.
- **`diagnostico.ps1`** (en el CD de la VM, que se conecta con `ELENA_VM_CON_INSTALADORES=1 mise run vm:arrancar`; en un Windows instalado en el disco, copia el contenido de [`vm/windows/diagnostico.ps1`](../../vm/windows/diagnostico.ps1) y pégalo en PowerShell, para comparar; no hace falta ser administrador):
  - Windows, equipo y BIOS, activación;
  - paquetes con su firma;
  - **una prueba de Media Foundation** que busca el decodificador E-AC-3 e intenta crearlo, y muestra el HRESULT si falla;
  - los MFT registrados, los controladores y los eventos (incluida la Tienda);
  - **los apagados** de los últimos 7 días (1074 pedido; 41 y 6008, corte en seco) y **en qué carpetas** quedaron las descargas de Apple Music;
  - **los controles multimedia (SMTC)**: qué publica Apple Music, qué controles permite, si la posición avanza y su carátula, que guarda junto al reporte. También **los puertos serie** y si COM1 abre. Para esto, córrelo con una pista sonando.

  Guarda el reporte en el Escritorio, en la memoria **ELENA-INT** (VM) y, con `-Disco <etiqueta>`, en ese disco (por ejemplo, un NTFS que Linux también vea).
- **`mise run vm:intercambio`** (con Windows apagado): copia lo que Windows dejó en la memoria USB virtual `ELENA-INT` (`.cache/vm/intercambio.img`, FAT32) a `.cache/vm/intercambio/`, sin montar nada.
- **`mise run elena -- vm [estado|apagar|red on|off]`:** la VM por su monitor QMP (`.cache/vm/qmp.sock`): estado, apagado ordenado por ACPI y el cable de red virtual.
- **`mise run vm:lanzador [instalar|quitar|estado]`:** el lanzador «Apple Music (ELENA)» y los servicios `elena-vm` y `elena-vm-musica`.
- **`mise run elena -- vm musica [estado]`:** el puente de música en primer plano (lo que corre el servicio), o con `estado`, lo último que llegó de Windows.
- **`prueba-serie.ps1`** (en el CD) y **`mise run vm:prueba-serie`:** prueban el canal del puerto serie en los dos sentidos y miden su velocidad. Primero el de Windows, y el de Linux cuando lo pida.
- **`mise run vm:borrar`:** borra la caja y `.cache/vm/` (los discos de Windows y de música incluidos); pide escribir «borrar» y no corre con la VM encendida. No toca la ISO ni la carpeta de instaladores.

## Notas

- **Activación:** la licencia de Windows es tuya. En las pruebas, un Windows sin activar (`LicenseStatus 5`) no impidió nada de lo anterior, ni siquiera la Tienda.
- **DRM:** cambiar `MinVersion` solo quita el límite de versión de Windows, y el códec se obtiene gratis por la vía oficial (la Tienda). No se elude el DRM de Apple ni el de Dolby.
- **Windows 10 LTSC no trae decodificador de E-AC-3,** aunque algunos paquetes de códecs digan lo contrario: el E-AC-3 solo lo decodifica el paquete de Dolby, y solo con licencia.
- **Pendiente de verificar:** el GeoId `161832257` de «Latinoamérica y el Caribe» que usa `instalar.ps1` viene de la tabla de regiones de Microsoft. Aún no se ha probado en una instalación nueva; si no lo detecta, cambia la región a mano (paso 6).
