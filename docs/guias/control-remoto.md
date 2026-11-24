# Guía: el control remoto de música (teléfono)

Una página web que sirve la PC en la red de casa para usar el teléfono como control remoto de música:
- **Música:** la canción, el artista, el álbum, la carátula y el avance, con play/pausa, siguiente y anterior. Si el reproductor lo permite (el navegador sí, Apple Music no), también se mueve la posición: con la barra o con ±10 s.
- **Ver este video:** pegas un enlace de YouTube y se abre en la PC.
- **Volumen:** el del bus «ELENA · Música» y su silencio.
- **Temporizador para dormir:** baja el volumen poco a poco, pausa y deja el volumen como estaba; según el nivel, también apaga la VM y suspende o apaga la PC.
- **Equipo:** encender y apagar la VM, y suspender o apagar la PC.

**Solo funciona en la red de casa:** no hay acceso desde fuera, ni HTTPS, ni puertos abiertos en el router.

## Cómo funciona

```
Teléfono (navegador) ── HTTP + WebSocket, con cookie ──▶ elena remoto (servicio elena-remoto, puerto 8765)
                                                           ├─ música: MPRIS por D-Bus (Apple Music (ELENA), Cider, el navegador…)
                                                           ├─ ver este video: ELENA_REMOTO_ABRIR con systemd-run --user
                                                           ├─ volumen: bus elena_musica (pw-dump y pactl)
                                                           ├─ temporizador (corre en la PC)
                                                           ├─ VM: systemctl --user start|stop elena-vm
                                                           └─ PC: systemctl suspend|poweroff
```

- **Música:** le habla al reproductor por MPRIS, igual que Noctalia o las teclas multimedia. No toca el puente de música de la VM.
- **Temporizador:** vive en la PC. Sigue aunque el teléfono se bloquee o cierres la página.

## Instalarlo

```
mise run remoto instalar
```

- **También lo instala `mise run aplicar`** (alcance `todo`). `mise run remoto instalar` lo instala solo, sin reiniciar PipeWire.
- Hace falta el `.venv` (`mise exec -- uv sync`). Para encender la VM desde el teléfono también hace falta su servicio (`mise run vm:lanzador instalar`).
- **Qué deja fuera del repo** (una excepción deliberada, como el vigilante):
  - la unidad, en `.cache/systemd/elena-remoto.service`;
  - su enlace en `~/.config/systemd/user/`, más el de `default.target.wants/`, así que arranca sola en cada inicio de sesión.
- La unidad ejecuta `mise exec -C <repo> -- <repo>/.venv/bin/elena remoto`, con `Restart=on-failure`.
- El **token** se crea en `.cache/remoto/token` (permisos 600) la primera vez. El temporizador activo se guarda en `.cache/remoto/temporizador.json`.
- **El puerto** es `ELENA_REMOTO_PUERTO` (8765 por omisión). Para cambiarlo, ponlo en `mise.local.toml` (`[env]`), corre otra vez `mise run remoto instalar` y cambia también la regla del firewall.

## Abrir el puerto en el firewall (a mano)

Si tienes un firewall activo, bloquea el puerto. ELENA no toca `/etc`, así que la regla la pones tú, solo para la red de casa. Con `ufw` (cambia `192.168.1.0/24` por tu red; `mise run remoto` muestra la regla con tu red y tu puerto):

```
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp comment 'ELENA remoto'
```

Para quitarla:

```
sudo ufw delete allow from 192.168.1.0/24 to any port 8765 proto tcp
```

Con `firewalld` o `nftables`, la regla equivalente: TCP al puerto 8765, solo desde tu red local.

El servidor escucha **solo en IPv4**. Si la PC tiene una IPv6 global, escuchar ahí podría exponer el puerto a internet si el router dejara pasar IPv6 hacia dentro.

## Emparejar el teléfono

1. En la PC: `mise run remoto qr`. Muestra el enlace y un QR.
2. Con el teléfono en el mismo Wi-Fi, escanea el QR con la cámara. La página pone una cookie de un año y redirige a `http://<IP de la PC>:8765/`.
3. En el menú del navegador, elige **«Agregar a la pantalla de inicio»**: queda un ícono que abre la página a pantalla completa.

- **El enlace del QR lleva el token:** no lo compartas. Cualquiera que lo abra en tu Wi-Fi puede pausar o apagar la PC.
- **El QR lleva la IP, no `<nombre de la PC>.local`:** muchos teléfonos no resuelven nombres `.local` (mDNS).
- **Reserva la IP de la PC en el router** (DHCP estático). Si la IP cambia, el ícono deja de servir y hay que volver a escanear el QR.
- **Para desemparejar todos los teléfonos** (por ejemplo, si perdiste uno): `mise run elena -- remoto token nuevo`. Las cookies viejas dejan de servir al instante, sin reiniciar el servicio, y la página de esos teléfonos pide emparejar otra vez.

## La página

### Reproductor

- **Qué muestra:** carátula, título, artista · álbum, y el avance con el tiempo transcurrido y la duración.
- **Botones:** anterior, play/pausa y siguiente. Se apagan los que el reproductor no permite.
- **Moverse en la canción:** si el reproductor lo permite, la barra muestra una perilla.
  - **Arrastra o toca** un punto de la barra. La orden sale al soltar, no mientras arrastras: cada salto obliga al reproductor a volver a cargar.
  - **«−10 s» y «+10 s»**, debajo de la barra, saltan desde donde va. También funcionan con un video en directo, que no tiene duración y por eso no se puede arrastrar.
  - Con un teclado, las flechas sobre la barra también saltan 10 s.
- **Qué reproductores lo permiten:** los que publican `CanSeek` en MPRIS. El navegador lo permite en YouTube y en la mayoría de los reproductores web, mpv también. **Apple Music de la VM no:** Apple Music para Windows no deja cambiar la posición, así que su barra solo muestra el avance y no hay botones.
- **Con YouTube en Zen o Firefox,** la duración llega unos segundos después de que arranca el video: mientras, la barra no se puede arrastrar.
- **Varios reproductores:** si hay más de uno abierto (Cider, el navegador), arriba aparece un selector. Si no eliges ninguno, el actual es:
  1. el que suena, con Apple Music (ELENA) primero;
  2. Apple Music (ELENA);
  3. el último que sonó.
- **El punto de arriba a la derecha** es la conexión: verde, conectado; rojo, reconectando. Al volver a la página después de bloquear el teléfono, se reconecta sola.

### Volumen

- **El control y el botón** son el volumen y el silencio del bus **«ELENA · Música»**, en % como pavucontrol y la consola. Apple Music va al 100 % dentro de Windows; el volumen real es el del bus.
- **El balance entre canales se conserva** (`modelo.escalar`), igual que en la consola.

### Ver este video

Para ver YouTube desde el sillón sin mouse ni teclado: copia el enlace en el teléfono (por ejemplo, con «Compartir → Copiar enlace» en la app de YouTube), pégalo en el campo y toca «Abrir». El video se abre en el navegador de la PC, aparece arriba como reproductor y se maneja con la barra y los ±10 s.

- **Solo videos de YouTube:** `youtube.com/watch?v=…`, `youtu.be/…`, `shorts/…`, `live/…`, `embed/…` y `music.youtube.com`, con o sin `https://`. Se conservan el inicio (`t=`) y la lista (`list=`), y se descarta todo lo demás (`si=`, `feature=`…). Una lista sola, sin video, no se abre.
- **Cómo se abre:** con el comando de `ELENA_REMOTO_ABRIR`. Por omisión es `xdg-open {url}`, que abre tu navegador predeterminado.
- **Que el video arranque solo:** los navegadores bloquean por omisión el audio que arranca sin que toques la página. Permítelo para YouTube: en Firefox o Zen, en una pestaña de YouTube, toca el ícono a la izquierda de la dirección y en «Reproducción automática» elige «Permitir audio y video». Sin el permiso, la pestaña abre en pausa y no aparece como reproductor hasta que la toques en la PC.

**Elegir ventana, espacio de trabajo o contenedor.** `xdg-open` abre en la ventana del navegador que usaste al último. Si quieres otra cosa, apunta la variable a un script tuyo en `mise.local.toml` (fuera de git) y reinicia el servicio, que lee la variable al arrancar:

```toml
[env]
ELENA_REMOTO_ABRIR = "{{config_root}}/local/abrir-video.sh {url}"
```

```
systemctl --user restart elena-remoto
```

- **El comando no pasa por una shell:** se separa como en la terminal (las comillas agrupan) y `{url}` se reemplaza dentro de su argumento. Sin `{url}`, la URL va al final.
- **Corre en su propia unidad** de `systemd-run --user`, para que el navegador no quede dentro del servicio del control remoto (se cerraría al reiniciarlo). Su entorno es el de tus servicios de usuario, no el de mise: el script no ve las variables de `mise.toml` y debe usar programas del sistema.
- **Contenedores de Firefox o Zen:** desde la línea de comandos no se puede elegir contenedor; la pestaña cae en el espacio o contenedor activo de la ventana. Con la extensión «Open external links in a container», un enlace `ext+container:name=<contenedor>&url=<URL codificada>` sí lo elige.

Un ejemplo para Hyprland y Zen: enfoca la ventana del navegador que ya tenga YouTube al frente (si ninguna, la última que usaste) y abre ahí una pestaña. Para Firefox, cambia la clase (`firefox`), el final del título y el comando.

```bash
#!/usr/bin/env bash
# local/abrir-video.sh <url>
set -euo pipefail
url=${1:?falta la URL}

ventana=$(hyprctl clients -j | python3 -c '
import json, sys
nav = [c for c in json.load(sys.stdin) if c["class"] == "zen"]
yt = [c for c in nav if c["title"].endswith("- YouTube — Zen Browser")]
elegida = min(yt or nav, key=lambda c: c["focusHistoryID"], default=None)
print(elegida["address"] if elegida else "")
')

if [[ -n $ventana ]]; then
    # Hyprland 0.57 con config en Lua; antes: hyprctl dispatch focuswindow "address:$ventana"
    hyprctl -q dispatch "hl.dsp.focus({ window = \"address:$ventana\" })" || true
fi
exec zen-browser --new-tab "$url"
```

Para probarlo como lo corre el servicio, sin el teléfono:

```
systemd-run --user --wait --collect --quiet -- "$PWD/local/abrir-video.sh" 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
```

### Temporizador

- **Para ponerlo:** toca 15, 30, 45, 60 o 90 minutos, «Fin de canción», o escribe los minutos (de 1 a 720) y toca «Poner».
- **«Fin de canción»** necesita algo sonando con duración conocida. Con YouTube en el navegador, espera unos segundos después de que arranque el video: la duración llega tarde. Un directo no tiene duración.
- **Al terminar,** según el nivel que elijas (la página lo recuerda):

| Nivel | Qué hace |
|---|---|
| **Pausar** | Baja el volumen, pausa y lo regresa |
| **+ apagar VM** | Lo mismo y apaga Windows en orden |
| **+ suspender PC** | Lo mismo, y cuando la VM terminó, suspende la PC |
| **+ apagar PC** | Lo mismo, y cuando la VM terminó, apaga la PC |

- **Mientras corre:** muestra la cuenta regresiva y a qué hora vence, con «+10 min» y «Cancelar».

**Cómo termina:**
1. **Baja el volumen 30 s antes de la hora**, hasta 0, de forma pareja. Con «Fin de canción», son los últimos 8 s.
2. **Pausa**, espera a que Apple Music confirme la pausa y **1.5 s después regresa el volumen**. El margen es para el audio que todavía sale del búfer de QEMU y para lo que tarda la orden en llegar a Windows. Así no se oye un golpe de volumen.
3. Si el nivel lo pide, apaga la VM (Windows tarda unos segundos) y luego suspende o apaga la PC. Si la VM no termina en 150 s, sigue de todos modos.

**Lo que hace si intervienes:**
- **Pausar antes de la hora no lo cancela:** sigue contando. Al vencer, como ya no suena nada, se salta la bajada y la pausa y sigue con su nivel.
- **Si mueves el volumen mientras baja** (desde el teléfono, la consola o pavucontrol), se cancela y se queda el volumen que pusiste.
- **Si pausas mientras baja,** se cancela y el volumen regresa a como estaba.
- **«Cancelar» mientras baja** también regresa el volumen.
- **«Fin de canción»:** si pausas, espera a que la canción termine de verdad. Si la canción cambia antes de tiempo (la posición de Apple Music es estimada), pausa en ese momento; como el volumen ya va en 0, no se oye el inicio de la siguiente. La canción se reconoce por su identificador de MPRIS, título, artista y URL: el navegador usa el mismo identificador para todos los videos.
- **Si mueves la posición,** «Fin de canción» se ajusta a la nueva hora de fin.
- **Si Apple Music no pausa,** lo pide otra vez a los 3 s. Si aun así sigue sonando, silencia el bus para no despertarte.
- **Si el servicio se reinicia a media bajada,** al arrancar regresa el volumen. Un temporizador que todavía no vence se retoma; uno que venció hace más de un minuto se descarta.

### Equipo

- **VM de Windows:** su estado (apagada, encendiendo…, encendida, apagando…) y un botón:
  - **Encender:** necesita el servicio `elena-vm`. Abre la ventana de Apple Music en la PC.
  - **Apagar:** mantén presionado 2 s. Es el mismo apagado en orden de Windows que hace `systemctl --user stop elena-vm`.
- **Suspender PC** y **Apagar PC:** mantén presionado 2 s; un toque corto no hace nada. Antes, pausa la música. La PC no se puede encender desde el teléfono: un navegador no puede mandar Wake-on-LAN.

## Comprobar que funciona

| Comando | Qué muestra |
|---|---|
| `mise run remoto` | Si el servicio está activo, si el puerto escucha, la dirección, si hay token, la regla de `ufw` y las últimas líneas del registro |
| `journalctl --user -u elena-remoto -f` | El registro en vivo |
| `mise run remoto qr` | El enlace y el QR para emparejar |

El registro anota los emparejamientos, los videos abiertos, el temporizador, la VM y la PC; no anota el volumen, play/pausa ni la posición. Ejemplo con un temporizador de 1 minuto:

```
Teléfono emparejado: 192.168.1.34
Temporizador: minutos, nivel pausar, vence a las 00:55:06.
Temporizador: bajando el volumen en 30 s.
Temporizador: listo, música en pausa.
```

**Desde la PC, con curl** (para depurar; todo lo de `/api` exige la cookie, y los POST, el `Origin`):

```
T=$(cat .cache/remoto/token); B=http://192.168.1.20:8765        # la IP de tu PC
curl -s -c .cache/tmp/c.txt -o /dev/null "$B/?t=$T"              # emparejar (guarda la cookie)
curl -s -b .cache/tmp/c.txt $B/api/estado | python3 -m json.tool   # el estado completo
curl -s -b .cache/tmp/c.txt -H "Origin: $B" -H 'Content-Type: application/json' \
     -d '{"orden": "alternar"}' $B/api/musica                      # play/pausa
curl -s -b .cache/tmp/c.txt -H "Origin: $B" -H 'Content-Type: application/json' \
     -d '{"posicion_ms": 90000}' $B/api/musica                     # ir al minuto 1:30 ("saltar_ms": -10000 retrocede)
rm .cache/tmp/c.txt
```

Las rutas y sus cuerpos JSON están en la cabecera de `src/elena/remoto/servidor.py`.

## Seguridad

- **Token:** sin la cookie del token, la página carga pero no puede ver ni hacer nada (401). `remoto token nuevo` la revoca.
- **Host:** solo se aceptan la IP de la PC, `localhost`, `127.0.0.1` y `<nombre de la PC>.local`, con el puerto (contra DNS rebinding). Si el DHCP cambia la IP, el servidor la vuelve a leer sola.
- **Origin:** los POST y el WebSocket deben venir de la propia página. Otra página abierta en el teléfono no puede mandar órdenes (CSRF).
- **Cabeceras:** CSP estricta (solo scripts y estilos propios), `nosniff` y `no-referrer`.
- **Sin cifrar:** es HTTP simple, así que el token viaja sin cifrar por el Wi-Fi de casa. Se aceptó porque no sale de la red; HTTPS con un certificado propio llenaría el teléfono de advertencias.
- **Confirmaciones:** apagar o suspender exige mantener presionado en la página, y el servidor también pide `"confirmar": true`.
- **Ver este video:** el servidor acepta solo videos de YouTube, saca el ID del video y arma la URL de nuevo; nunca pasa al comando el texto que llegó, y el comando no pasa por una shell. Con una cookie robada, lo más que se puede hacer es abrirte un video de YouTube.

## Quitarlo

```
mise run remoto quitar
```

Lo detiene, lo deshabilita y borra el enlace de `~/.config/systemd/user/`. `mise run quitar` también lo hace. El token se queda en `.cache/remoto/`, así que al reinstalar el teléfono sigue emparejado. La regla del firewall también se quita a mano (ver arriba).

## Problemas comunes

- **El teléfono no abre la página:**
  - ¿Está en el mismo Wi-Fi y no en datos móviles?
  - ¿Está la regla del firewall? (con `ufw`: `sudo ufw status`)
  - ¿Cambió la IP de la PC? `mise run remoto` muestra la actual.
  - Si nada de eso, puede ser el aislamiento de clientes del router.
- **«Falta emparejar este teléfono»:** la cookie no está (otro navegador, o borraste los datos del sitio) o el token cambió. Vuelve a escanear el QR (`mise run remoto qr`).
- **«Enlace vencido»:** el QR era de un token anterior. Genera otro con `mise run remoto qr`.
- **«Nada sonando» con Apple Music abierta:** el puente de la VM no publica el reproductor. Revisa `mise run elena -- vm musica estado` y el servicio `elena-vm-musica` ([guía de la VM](vm-apple-music-atmos.md#apple-music-en-los-widgets-de-linux)).
- **La barra no se puede arrastrar ni hay «±10 s»:** el reproductor no publica `CanSeek` (Apple Music de la VM, algunos sitios web). Si hay botones pero la barra no se arrastra, todavía no llega la duración (YouTube, los primeros segundos) o es un directo.
- **«Ver este video» no hace nada:**
  - El error de la página dice qué falló. «Solo se pueden abrir enlaces de YouTube» o «no es de un video»: el enlace no es de un video de YouTube.
  - Sin error, pero no abre: el comando falló dentro de su unidad. Búscalo en `journalctl --user --since -5min` y prueba el comando con `systemd-run` (arriba).
  - Abre, pero en pausa y no aparece como reproductor: falta el permiso de reproducción automática.
  - Cambiaste `ELENA_REMOTO_ABRIR` y sigue usando el comando anterior: `systemctl --user restart elena-remoto`.
- **El volumen sale con «—»:** no existe el bus `elena_musica`. ¿Falta `mise run aplicar`?
- **«Encender» está apagado:** falta el servicio de la VM (`mise run vm:lanzador instalar`). Sin él, QEMU quedaría dentro del servicio del control remoto.
- **Cambiaste el código del servidor o de la página:** `systemctl --user restart elena-remoto`. La página nueva llega al recargarla (se sirve con `no-cache`).
- **Moviste el repo de carpeta:** la unidad guarda la ruta absoluta. Corre otra vez `mise run remoto instalar` desde la ruta nueva.
- **Para probar sin el servicio:** `mise run remoto quitar` y luego `mise run elena -- remoto`, que lo corre en la terminal (`Ctrl+C` lo detiene).

## Sin probar todavía

Probados: emparejar, la página, el temporizador en los niveles «Pausar» y **+ apagar VM**, «Fin de canción» con Apple Music y con YouTube en Zen, cancelar, la barra y los ±10 s con YouTube en Zen, Apple Music sin barra, y «Ver este video». Falta probar en un equipo real los niveles **+ suspender PC** y **+ apagar PC**, encender y apagar la VM desde el teléfono, suspender o apagar con la VM encendida, y la barra con otros reproductores (mpv, sitios sin `CanSeek`).

## Límites

- Solo en la red de casa.
- No se puede navegar ni buscar en la biblioteca de Apple Music.
- Con Apple Music no se puede adelantar, ni hay aleatorio o repetir: no los publica. Ningún reproductor tiene aleatorio o repetir en la página.
- «Ver este video» solo abre YouTube, y el enlace se pega a mano: «Compartir» directo desde la app de YouTube pediría instalar la página como app, y Android solo lo deja con HTTPS.
- No hay mouse ni teclado: no se puede poner el video en pantalla completa ni navegar en la página de YouTube.
- No hay controles en la pantalla de bloqueo del teléfono.
- Solo controla el bus de música: los otros buses y las escenas se manejan en la consola.
