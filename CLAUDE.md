# ELENA — Enrutador Linux Envolvente Nativo de Audio

Reemplazo de Voicemeeter en Linux: buses de PipeWire, una tarjeta en 5.1 analógico, surround en juegos con Proton, calibración por bocina, una consola (TUI y CLI), un control remoto para el teléfono y, opcional, Apple Music Atmos en una VM de Windows. Uso y requisitos: [README.md](README.md).

## Estructura

- `src/elena/`: la consola, la CLI y los servicios (Python 3.14). `rutas.py` dice dónde vive cada cosa; `equipo.py` lee `local/equipo.toml` y genera los `.conf`.
- `plantillas/`: la config de PipeWire y WirePlumber, con marcadores (`@SINK@`, `@CHAT@`…). **No la lee PipeWire:** `elena equipo generar` (lo corre `mise run aplicar`) la llena y la escribe en `local/`.
- `local/` (fuera de git): lo de cada equipo. `equipo.toml` (tarjeta, sink, mic, audífonos; plano, porque mise lo carga con `_.file`), `calibracion.toml`, `reglas.toml`, `escenas.toml` y los `.conf` generados. `mise run iniciar` lo crea desde `config/*.ejemplo.toml` y lo detectado.
- `config/`: solo ejemplos.
- `scripts/` (bash), `vm/` (Distrobox, QEMU y los scripts de Windows), `tests/`, `docs/guias/`.

## Reglas

- **Nada fuera del repo.** Excepciones deliberadas, creadas por `mise run aplicar` y revertidas por `mise run quitar`:
  - `~/.config/pipewire/pipewire.conf.d` → `local/pipewire/pipewire.conf.d`
  - `~/.config/wireplumber/wireplumber.conf.d` → `local/wireplumber/wireplumber.conf.d`
  - `~/.local/share/applications/elena-steam.desktop` y `elena-consola.desktop` → `.cache/lanzadores/` (ejecutan `scripts/steam.sh` y `scripts/consola.sh`).
  - `~/.config/systemd/user/elena-vigilante.service` y `elena-remoto.service` → `.cache/systemd/`, más sus enlaces en `default.target.wants/`. También los crean y borran `mise run vigilante|remoto instalar|quitar`. El token del control remoto queda en `.cache/remoto/`; la regla del firewall la pone el usuario a mano.
  - Con la VM: `elena-apple-music.desktop`, `elena-vm.service` y `elena-vm-musica.service`, sin habilitar (`elena-vm` pide al puente con `Wants=`; el puente va con `BindsTo=`/`After=elena-vm.service`). Los crean `aplicar` (si existe la VM) y `mise run vm:lanzador instalar`; los borran `quitar` (que antes apaga la VM) y `vm:lanzador quitar`.
  - El estado normal de WirePlumber (perfil, sink por defecto, volúmenes) y el almacén de podman (imagen de la caja `elena-vm`).
- **Lo generado no se edita a mano:** los `.conf` de `local/` salen de `plantillas/` y de `local/equipo.toml`; `40-elena-calibracion.conf`, de `local/calibracion.toml`. Que exista `40-elena-calibracion.conf` es lo que significa «calibración activa», y el generador manda ahí los buses de bocinas; `mise run calibracion -- activar|desactivar` escriben o borran ese `.conf` y regeneran los buses, siempre juntos. Se aplica con `mise run aplicar`, **con la VM apagada**.
- **Las pruebas no dependen del equipo:** `tests/conftest.py` fija las variables `ELENA_*` (las de la foto de `pw-dump`) y apunta `ELENA_LOCAL` a una carpeta que no existe, antes de importar ELENA. Nada de las pruebas lee ni escribe `local/`.
- **Nada personal en el repo:** nombres de dispositivos, IPs, rutas de `$HOME`, mediciones de un equipo concreto van en `local/`. Los fixtures van anonimizados.
- ELENA no toca la config del escritorio (`~/.config/hypr/`…): las guías traen los fragmentos para copiarlos a mano.
- `distrobox create` siempre con `--no-entry`: sin él, crea un `.desktop` en `~/.local/share/applications/` y un ícono en `~/.local/share/icons/distrobox/`.
- No tocar `/etc` ni `/usr/lib/firmware`.
- Lo temporal, las AppImages, el estado previo del audio y la VM (disco, TPM, home de la caja) van a `.cache/`.
- Los scripts se corren con `mise run <tarea>` (o `mise exec --`), que define `ELENA`, `ELENA_LOCAL`, `TMPDIR`, carga `local/equipo.toml` y las demás `ELENA_*`. **mise pone su `[env]` por encima del entorno de la terminal:** `X=1 mise run …` no cambia una `X` definida en `mise.toml`. Las que se cambian desde la terminal llevan `{{ get_env(name='X', default='…') }}`; los ajustes fijos de una máquina van en `mise.local.toml` (fuera de git). Un proceso que heredó la variable de antes de cambiarla (VS Code y su terminal) sigue con el valor viejo hasta reabrirse; el servicio de systemd y una terminal nueva leen el actual.
- **Python siempre por mise** (`mise run`, o `mise exec -- uv run python …`), nunca `.venv/bin/python` directo: sin `PYTHONPYCACHEPREFIX`, Python escribe `.pyc` en la instalación de Python de mise y en `src/`. La caché de uv va a `.cache/` y uv no descarga Pythons (`UV_PYTHON_DOWNLOADS=never`). **`python -I` ignora `PYTHONPYCACHEPREFIX`:** con `-I` hay que agregar `-X pycache_prefix="$ELENA/.cache/pycache"`, como hacen `vm/instaladores.sh` y `medir-canales.sh`.
- Pruebas: `mise run test`.
- No hay que eludir el DRM de Apple Music ni de Dolby: queda fuera del alcance.

## Guías

- [consola.md](docs/guias/consola.md): la consola, la CLI, escenas, reglas fijas, el vigilante, los avisos y «solo audífonos».
- [calibracion.md](docs/guias/calibracion.md): el `filter-chain` de las bocinas, las pruebas y el ajuste en vivo.
- [control-remoto.md](docs/guias/control-remoto.md): el control remoto para el teléfono.
- [visualizadores.md](docs/guias/visualizadores.md): la mezcla `elena_visual`.
- [vm-apple-music-atmos.md](docs/guias/vm-apple-music-atmos.md): la VM de Windows con Apple Music Atmos, desde cero.

## Hechos verificados

Probados con PipeWire 1.6.10–1.6.11, WirePlumber 0.5.18 y QEMU 11.1.

### PipeWire y WirePlumber

- Un stream con `node.dont-fallback` cuyo `target.object` no existe se **destruye** (y con él su módulo loopback), salvo que tenga `node.linger = true`. Todos los streams de ELENA llevan los dos: esperan en silencio a que aparezca su destino.
- `stream.rules` **no** rutea streams (su `target.object` solo se usa al restaurar estado). El ruteo se hace con `PULSE_SINK`, moviendo el stream una vez (WirePlumber lo recuerda) o con el vigilante.
- WirePlumber **no restaura** un destino movido si las props del stream traen `target.object` (`state-stream.lua`): tras un reinicio, vuelven a su destino de la config. FMOD Ex se conecta por nombre al sink por defecto por eso.
- WirePlumber restaura el volumen de una app **por `application.name`, con los canales con los que se guardó**: una app 5.1 con un volumen estéreo guardado muestra FC, LFE, RL y RR en 0 % en pactl (la señal sí pasa), y pactl rechaza un volumen con menos valores que canales. La consola acomoda los volúmenes a los canales del stream (`modelo._volumenes`).
- Volumen por canal: `pactl set-sink-volume <sink> v1 … v6` (en el orden del `channelMap`) o `pw-cli set-param <id> Props '{ channelVolumes = [ … ] }'` (lineal). `wpctl` pone un solo valor y borra el balance. El % de pactl es cúbico: 50 % = −18 dB y 70 % = −9.3 dB.
- `pw-dump --monitor` manda objetos completos y avisa las bajas con `{"id": N, "info": null}`.
- `pactl move-sink-input <object.serial> <sink>` mueve streams pulse y nativos (también un loopback con `node.dont-fallback`, `node.linger` y `target.object`, sin cambiar su `target.object`), y WirePlumber lo recuerda al reabrir la app. `pactl move-source-output <serial> <sink>.monitor` mueve grabaciones pulse y nativas.
- El monitor de un bus de ELENA mide **antes** del volumen del bus (`monitor.channel-volumes = false`); un sink nulo de `pactl load-module module-null-sink` lo trae en `true`. `pw-record --target <stream>` captura una app **después** de su volumen; `pw-record --target <sink>` sin `stream.capture.sink = true` graba vacío.
- `node.passive` no deja suspender un bus con un medidor abierto. `pw-record` escribe en cada ciclo, así que los medidores leen por intervalos (50 ms), no por evento.
- `PIPEWIRE_NODE` no sirve para juegos: libpipewire lo aplica también a la captura, y el chat de voz grabaría el bus en lugar del mic.
- `pw-config -n <ruta absoluta> -r list` valida un `.conf` (también uno de WirePlumber) sin reiniciar nada; `-r list context.modules` lista sus módulos.
- Las pruebas con `pw-play` llevan `-P '{ state.restore-props = false }'`: si no, WirePlumber guarda el volumen que se le ponga y lo restaura a **todos** los `pw-play`. `pw-play --latency` solo acepta enteros con unidad (`7500us`).
- `scripts/aplicar.sh` y `quitar.sh` **reinician PipeWire** (el audio se corta un momento). `aplicar` es idempotente.

### `filter-chain` (calibración y audífonos)

- Los controles se llaman `<nodo>:<control>` (`fl_ganancia:Mult`, `fr_retardo:Delay (s)`, `sub_suma:Gain 1`) y `pw-cli set-param <id del sink> Props '{ params = [ "<nombre>" <valor> ] }'` los cambia en vivo, sin cortes.
- `linear` procesa audio (`Mult`). `delay` usa `config = { "max-delay" = … }`; `Feedback` y `Feedforward` van en 0. Una salida puede ir a varias entradas.
- Dos `bq_highpass` y dos `bq_lowpass` con Q 0.707 dan un Linkwitz-Riley de 4.º orden: −6 dB en los dos lados en el corte y la suma plana (±0.01 dB).
- `bq_raw` acepta `b0`…`a2` en vivo y divide entre `a0`, pero **sin `config = { coefficients = [ { rate = 48000 b0 = … } ] }` deja pasar silencio**. El EQ usa ranuras `bq_raw` fijas con coeficientes RBJ propios: todo cambio del EQ es en vivo.
- El builtin no trae limitador (solo `clamp`). El `mixer` tiene 8 entradas (`Gain n`). `bq_lowshelf` tiene `Freq`, `Q` y `Gain` (dB).
- El plugin `sofa` (label `spatializer`: `In`, `Out L`, `Out R`; `Azimuth`, `Elevation`, `Radius`) con la HRTF MIT KEMAR de libmysofa suena unos 4.5 dB más fuerte que la mezcla directa.
- Un `filter-chain` se prueba aparte con `pipewire -c <ruta absoluta>` (va **después** de `protocol-native`, o falla con «can't find protocol»).

### Juegos (Proton)

- Proton-CachyOS usa **winepipewire** por defecto, que ignora `PULSE_SINK` y sigue al sink por defecto (estéreo). Con `PROTON_USE_PIPEWIRE=0` usa winepulse, que respeta `PULSE_SINK` y toma los canales del sink. `scripts/steam.sh` pone las dos. GE-Proton y Proton Experimental solo traen winepulse. `PULSE_SINK` atraviesa pressure-vessel (Steam Linux Runtime 2.0 y 4.0).

### VM de Windows (QEMU en la caja `elena-vm`)

- Cadena de audio: Windows entrega **S16LE, 48 kHz, 6 canales** (FL FR FC LFE RL RR); QEMU no mezcla ni remuestrea (`out.mixing-engine=off,out.fixed-settings=off`; su mezclador es estéreo); los canales los elige Windows (`usb-audio,multi=on`). En Linux, solo el volumen del bus cambia la señal. **Sound Check y el ecualizador de Apple Music deben ir desactivados:** con ellos, la señal sube ~10 dB y los picos se aplastan contra −0.13 dBFS.
- Backend por defecto: `pa` con `out.latency=100000` (con `pipewire`, 8 a 15 cortes cada 20 s; con `pa`, 1). `ELENA_VM_AUDIO=pipewire` es el alterno. Con la VM sonando, el quantum del grafo es de 360 cuadros (lo pide QEMU).
- Windows 10 IoT LTSC 2021 **no trae decodificador E-AC-3**: lo da `DolbyLaboratories.DolbyDigitalPlusDecoderOEM`, que **necesita licencia de la Microsoft Store** (ProductId `9NVJQJBDKN97`, gratis); instalado desde el archivo, Media Foundation falla al crearlo con `0x87E10BC6`. La Tienda pide los productos con el mercado de la región: «Latinoamérica y el Caribe» (`market=419`) da 404; México o EE. UU. sí.
- El monitor QMP (`.cache/vm/qmp.sock`) se usa desde el host (`elena vm`, Python sin dependencias). `set_link` sobre `-nic user,id=red` funciona sin cambiar la MAC.
- `distrobox enter` desde systemd necesita `-T`. `systemctl --user stop elena-vm` apaga Windows por ACPI (`system_powerdown`); QEMU no queda en el cgroup del servicio (lo lanza conmon), así que `ExecStop` espera a que desaparezca el proceso y a `$MAINPID`. Con UWSM, `PartOf`/`After=graphical-session.target` apaga la VM antes que el compositor.
- TRIM: con `discard=unmap,detect-zeroes=unmap`, `Optimize-Volume -ReTrim` en Windows encoge el qcow2 en el host. btrfs perfora el archivo: se mide con `du`, no con `ls`.
- Resolución: OVMF arranca en el modo del EDID de la VGA (`-global VGA.xres/yres`); sin EDID, 1280x800. Una resolución elegida en el menú de OVMF (`PlatformConfig`) gana sobre el EDID.
- Windows reconoce la tarjeta de audio por su puerto USB: tableta en el 1, memoria de intercambio en el 2 y audio en el 3, fijos (`port=`), con o sin los discos de instalación.
- Windows toca toda la RAM que recibe (QEMU ocupa ~RAM + 0.3 GB); con 4G, Apple Music en Atmos va sin cortes.
- Apple Music para Windows publica en SMTC con el identificador **`AppleMusic.exe`**, junta «Artista — Álbum» en `Artist`, **no permite cambiar la posición** ni aleatorio ni repetir, y su `Timeline` se actualiza cada ~1 s en segundos enteros. Al reanudar, la última actualización es de antes de la pausa. Descarga en Atmos.
- El COM1 de QEMU (`-serial unix:…`) no va al ritmo del baud rate (~77 KB/s de Windows a Linux); sin nadie conectado del lado de Linux, QEMU descarta lo que Windows escribe. OVMF escribe su texto de arranque en COM1.
- Error 7502 de Apple Music («An unknown error has occurred» en el catálogo): una entrada `setupCert*` mala en la caché de WinINet del paquete (`AC\INetCache`); `apple-music.ps1` la borra antes de abrir la app. Una captura de red en caliente: `object-add qom-type=filter-dump netdev=red file=…` por QMP.
- PowerShell 5.1 y WinRT funcionan con `AsTask` por reflexión. En el host no hay PowerShell: los `.ps1` solo se prueban en la VM.

### Otros

- MPRIS no avisa `Position` con PropertiesChanged: el cliente la lee al conectar, al cambiar el estado o la canción, con `Seeked` y cada 5 s mientras suena.
- Zen 1.24 (Firefox) con YouTube: `mpris:trackid` es **fijo** (`/org/mpris/MediaPlayer2/firefox`, igual para todo video) y `SetPosition` no lo revisa. **`Seek(offset)` ignora el desplazamiento** y mueve siempre 5 s (los saltos de YouTube): los saltos van con `SetPosition`. Cada salto emite de 2 a 4 `Seeked`, y `Position` (en segundos enteros) puede leerse 0 justo después. Con la reproducción automática bloqueada (por omisión), una pestaña nueva no crea sesión de medios y MPRIS sigue en la anterior; al arrancar, `mpris:length` y `Position` llegan varios segundos después.
- Hyprland 0.57 con config en Lua: `hyprctl dispatch` recibe un despachador de Lua (`hyprctl dispatch 'hl.dsp.focus({ window = "address:0x…" })'`); la sintaxis vieja (`focuswindow address:…`) sale con código 7.
- `dbus-fast` lee las firmas D-Bus de las anotaciones de texto (`-> "s"`). Las pruebas de D-Bus usan un `dbus-daemon` con config propia y socket en `.cache/pytest/` (el `session.conf` del sistema escucha en `/tmp`).
- Muchos teléfonos no resuelven `<host>.local` (mDNS): el QR del control remoto lleva la IP.
- speaker-test a veces ignora SIGTERM: se manda SIGTERM a su grupo y SIGKILL a lo que quede (`acciones.detener`).
- `pkill -f <patrón>` dentro de un comando de shell puede matar a la propia shell si el patrón aparece en el mismo comando: usar `pkill -x <proceso>`.
