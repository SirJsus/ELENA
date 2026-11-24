# ELENA

**Enrutador Linux Envolvente Nativo de Audio.** Un reemplazo de Voicemeeter para Linux, hecho con PipeWire: buses virtuales para música, juegos, sistema y chat; bocinas 5.1 analógicas; surround en los juegos de Steam con Proton; calibración por bocina; una consola en la terminal; y un control remoto de música para el teléfono. Opcionalmente, una VM de Windows para escuchar Apple Music en Dolby Atmos por las bocinas 5.1.

Todo vive dentro del repositorio: no instala paquetes del sistema, no toca `/etc` y lo que pone fuera del repo son enlaces que `mise run quitar` borra.

## Qué hace

- **Buses tipo Voicemeeter.** Cuatro sinks virtuales con volumen propio: **Música** y **Juegos** (5.1), **Sistema** (estéreo, por las frontales) y **Chat** (a los audífonos). Cada app va al bus que elijas y WirePlumber lo recuerda.
- **5.1 analógico.** Pone la tarjeta en su perfil 5.1 (los tres jacks traseros) y deja el sink físico al 100 %: el volumen se maneja por bus.
- **Juegos en 5.1 con Proton.** Un lanzador «Steam (ELENA 5.1)» abre Steam con el audio de los juegos en el bus Juegos y con winepulse, que sí entrega 6 canales.
- **Mic virtual.** Supresión de ruido y cancelación de eco (WebRTC), un interruptor de voz y un puente entre una llamada del navegador y una del chat.
- **Calibración de las bocinas.** Un `filter-chain` con la ganancia, el retardo, el EQ y el cruce al subwoofer de cada bocina, y pruebas a ciegas para medirlos: el volumen máximo, hasta dónde llega cada satélite en graves, el balance entre bocinas y tu volumen de todos los días. Se afina en vivo, sin cortes.
- **La consola.** Una TUI (Textual) con todos los buses, las apps, los volúmenes y medidores por canal, escenas, reglas fijas por app y la calibración. Todo lo que hace también se puede hacer desde la línea de comandos (`elena …`).
- **El vigilante.** Un servicio de usuario que aplica las reglas fijas («esta app siempre a este bus»), cuida el tope de volumen y baja la música estéreo para que suene parejo con la multicanal.
- **Solo audífonos.** Con una tecla, Música, Juegos y Sistema pasan de las bocinas a los audífonos, en estéreo o con surround binaural (HRTF).
- **Mezcla para visualizadores.** Los fondos animados y espectros que graban «el sonido del sistema» ven también la música y los juegos.
- **Control remoto.** Una página web para el teléfono, en la red de casa: lo que suena, play/pausa, volumen, un temporizador para dormir y apagar la VM o la PC.
- **Apple Music Atmos en 5.1 (opcional).** Una VM de Windows 10 IoT LTSC 2021 en una caja de Distrobox, con audio USB multicanal hacia el bus Música, un lanzador que la hace parecer una app más y un puente que publica Apple Music como reproductor MPRIS (teclas multimedia y widgets del escritorio).

```
apps ─► ELENA · Música  (5.1) ─┐
apps ─► ELENA · Juegos  (5.1) ─┼─► ELENA · Calibración (opcional) ─► tarjeta 5.1 ─► bocinas
apps ─► ELENA · Sistema (2.0) ─┘     ganancia, retardo, EQ y cruce por bocina
apps ─► ELENA · Chat    (2.0) ───► audífonos (o las bocinas)
```

## Requisitos

- **Linux con PipeWire 1.6 y WirePlumber 0.5** (con `pipewire-pulse`). Probado en Arch (CachyOS).
- **Una tarjeta con salida 5.1 analógica:** que `pactl list cards` muestre un perfil `output:analog-surround-51`. Es lo habitual en las tarjetas madre con tres jacks traseros (verde, negro y naranja).
- **[mise](https://mise.jdx.dev/)**, que instala Python 3.14 y uv dentro del proyecto.
- En el sistema: `pactl`, `pw-dump`, `pw-cli`, `pw-record` y `pw-config` (vienen con PipeWire), `wpctl` (WirePlumber), `jq` y `speaker-test` (alsa-utils).
- Opcional:
  - **kitty**, para el lanzador y el atajo de la consola (`mise run consola` funciona en cualquier terminal);
  - **Steam**, para el lanzador de juegos;
  - **distrobox** y **podman**, `/dev/kvm`, una ISO de Windows 10 IoT Enterprise LTSC 2021 y una cuenta de Apple Music, para la VM;
  - **Hyprland** y **Noctalia**: la consola trae el atajo para Hyprland y el puente de música se ve en los widgets de Noctalia, pero ninguno es necesario.

## Instalación

```sh
git clone <este repositorio> ELENA
cd ELENA
mise trust
mise install                 # Python 3.14 y uv, dentro del proyecto
mise run iniciar             # crea local/ y detecta tu tarjeta, tu mic y tus salidas
```

`mise run iniciar` escribe `local/equipo.toml` con lo que encontró. **Revísalo antes de seguir:**

- `ELENA_TARJETA`, `ELENA_PERFIL` y `ELENA_SINK_FISICO`: tu tarjeta 5.1. Si hay varias, la lista viene en comentarios.
- `ELENA_MIC_FISICO`: el mic que alimenta al mic virtual. Vacío, no hay mic virtual.
- `ELENA_AUDIFONOS`: a dónde va Chat. Vacío, Chat va a las bocinas frontales y no hay «solo audífonos». Si son inalámbricos, préndelos y corre `mise run equipo detectar` para ver su nombre.

Luego:

```sh
mise run aplicar             # genera la config, enlaza y reinicia PipeWire (el audio se corta un momento)
mise run probar:canales      # una voz en cada bocina: comprueba que cada jack esté donde va
mise run consola             # la consola
```

Después de cambiar `local/equipo.toml`, vuelve a correr `mise run aplicar`.

## Uso

| Para… | Corre |
|---|---|
| Ver y mover todo | `mise run consola` (o «Consola ELENA» en el menú de apps) |
| El estado desde la terminal | `mise run elena -- estado` |
| Mandar una app a un bus | En la consola, `m`; o `mise run elena -- mover <app> juegos` |
| Que una app siempre vaya a un bus | En la consola, `r`; o `mise run elena -- regla <app> juegos` |
| Juegos en 5.1 | Abre Steam con «Steam (ELENA 5.1)» |
| Calibrar las bocinas | [docs/guias/calibracion.md](docs/guias/calibracion.md) |
| Ver todas las tareas | `mise tasks` |
| Dejar el audio como estaba | `mise run quitar` |

## Guías

- [La consola](docs/guias/consola.md): la pantalla, las teclas, la línea de comandos, escenas, reglas fijas, el vigilante, los avisos y «solo audífonos».
- [Calibración de las bocinas](docs/guias/calibracion.md): las pruebas, en orden, y cómo afinar en vivo.
- [Control remoto](docs/guias/control-remoto.md): instalarlo, el firewall, emparejar el teléfono y la página.
- [Mezcla para visualizadores](docs/guias/visualizadores.md): fondos animados y espectros que reaccionan a la música.
- [Apple Music Atmos con la VM de Windows](docs/guias/vm-apple-music-atmos.md): desde cero, con la solución de cada problema conocido.

## Lo tuyo va en `local/`

`local/` no está en git: guarda tu equipo y todo lo que ELENA aprende de él.

| Archivo | Qué es |
|---|---|
| `local/equipo.toml` | Tu tarjeta, tu sink 5.1, tu mic y tus audífonos. mise lo carga como variables de entorno |
| `local/calibracion.toml` | La calibración de tus bocinas (empieza neutra) |
| `local/reglas.toml`, `local/escenas.toml` | Tus reglas fijas y tus escenas (las escriben la consola y la CLI) |
| `local/pipewire/`, `local/wireplumber/` | La config de PipeWire y WirePlumber, **generada** desde `plantillas/` con tu equipo. No se edita a mano |

Los ejemplos de `config/` son el punto de partida de `mise run iniciar`. Si quieres cambiar una variable de `mise.toml` solo en tu máquina (los recursos de la VM, el puerto del control remoto), ponla en un `mise.local.toml` en la raíz, que tampoco va en git:

```toml
[env]
ELENA_VM_RAM = "8G"
```

## Qué toca fuera del repositorio

Solo enlaces y servicios de usuario, todos hacia archivos del repo. `mise run aplicar` los crea y `mise run quitar` los borra y restaura el perfil, el sink y la fuente por defecto, y los volúmenes que había:

- `~/.config/pipewire/pipewire.conf.d` → `local/pipewire/pipewire.conf.d`
- `~/.config/wireplumber/wireplumber.conf.d` → `local/wireplumber/wireplumber.conf.d`
- `~/.local/share/applications/`: los lanzadores «Steam (ELENA 5.1)» y «Consola ELENA» (y «Apple Music (ELENA)» si existe la VM), generados en `.cache/lanzadores/`.
- `~/.config/systemd/user/`: los servicios `elena-vigilante` y `elena-remoto` (habilitados) y, con la VM, `elena-vm` y `elena-vm-musica` (sin habilitar), generados en `.cache/systemd/`.

Si ya tenías algo en `~/.config/pipewire/pipewire.conf.d` o `~/.config/wireplumber/wireplumber.conf.d`, `aplicar` se detiene y te avisa en lugar de pisarlo.

Además, WirePlumber recuerda como siempre el perfil de la tarjeta, el sink por defecto y los volúmenes, y la VM usa el almacén de podman para la imagen de su caja. La regla del firewall para el control remoto la pones tú, a mano.

## Límites

- **Solo 5.1 analógico.** Las salidas 7.1, estéreo o HDMI no están contempladas.
- **Sin DRM de por medio.** ELENA no elude el DRM de Apple Music ni de Dolby: la VM corre Apple Music para Windows con su DRM normal y el códec de Dolby se obtiene gratis desde la Microsoft Store. La licencia de Windows, la ISO y la cuenta de Apple Music son tuyas.
- **Probado en un equipo.** Las mediciones y verificaciones de las guías salen de una tarjeta Realtek ALC1220 y unos audífonos USB. Si algo se comporta distinto en tu hardware, abre un issue.

## Desarrollo

```sh
mise run test                # pytest; no depende de tu equipo ni toca local/
```

- `src/elena/`: la consola, la CLI y los servicios (Python 3.14, Textual, aiohttp, dbus-fast).
- `plantillas/`: la config de PipeWire y WirePlumber, con marcadores que llena `elena equipo generar`.
- `scripts/`: aplicar, quitar, el lanzador de Steam y los servicios (bash).
- `vm/`: la caja de Distrobox, QEMU y los scripts de Windows.
- `tests/`: las pruebas, con una foto anonimizada de `pw-dump`.

Python siempre por mise (`mise run …` o `mise exec -- uv run …`): sin sus variables, Python escribe bytecode y cachés fuera del repo.

## Licencia

[GPL-3.0-or-later](LICENSE).
