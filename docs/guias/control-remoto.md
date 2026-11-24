# Guía: el control remoto de música (teléfono)

Una página web que sirve la PC en la red de casa para usar el teléfono como control remoto de música:
- **Música:** la canción, el artista, el álbum, la carátula y el avance, con play/pausa, siguiente y anterior.
- **Volumen:** el del bus «ELENA · Música» y su silencio.
- **Temporizador para dormir:** baja el volumen poco a poco, pausa y deja el volumen como estaba; según el nivel, también apaga la VM y suspende o apaga la PC.
- **Equipo:** encender y apagar la VM, y suspender o apagar la PC.

**Solo funciona en la red de casa:** no hay acceso desde fuera, ni HTTPS, ni puertos abiertos en el router.

## Cómo funciona

```
Teléfono (navegador) ── HTTP + WebSocket, con cookie ──▶ elena remoto (servicio elena-remoto, puerto 8765)
                                                           ├─ música: MPRIS por D-Bus (Apple Music (ELENA), Cider…)
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
- **No se puede adelantar:** la barra solo muestra el avance, porque Apple Music para Windows no permite cambiar la posición.
- **Varios reproductores:** si hay más de uno abierto (Cider, el navegador), arriba aparece un selector. Si no eliges ninguno, el actual es:
  1. el que suena, con Apple Music (ELENA) primero;
  2. Apple Music (ELENA);
  3. el último que sonó.
- **El punto de arriba a la derecha** es la conexión: verde, conectado; rojo, reconectando. Al volver a la página después de bloquear el teléfono, se reconecta sola.

### Volumen

- **El control y el botón** son el volumen y el silencio del bus **«ELENA · Música»**, en % como pavucontrol y la consola. Apple Music va al 100 % dentro de Windows; el volumen real es el del bus.
- **El balance entre canales se conserva** (`modelo.escalar`), igual que en la consola.

### Temporizador

- **Para ponerlo:** toca 15, 30, 45, 60 o 90 minutos, «Fin de canción», o escribe los minutos (de 1 a 720) y toca «Poner».
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
- **«Fin de canción»:** si pausas, espera a que la canción termine de verdad. Si la canción cambia antes de tiempo (la posición de Apple Music es estimada), pausa en ese momento; como el volumen ya va en 0, no se oye el inicio de la siguiente.
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

El registro anota los emparejamientos, el temporizador, la VM y la PC; no anota el volumen ni play/pausa. Ejemplo con un temporizador de 1 minuto:

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
- **El volumen sale con «—»:** no existe el bus `elena_musica`. ¿Falta `mise run aplicar`?
- **«Encender» está apagado:** falta el servicio de la VM (`mise run vm:lanzador instalar`). Sin él, QEMU quedaría dentro del servicio del control remoto.
- **Cambiaste el código del servidor o de la página:** `systemctl --user restart elena-remoto`. La página nueva llega al recargarla (se sirve con `no-cache`).
- **Moviste el repo de carpeta:** la unidad guarda la ruta absoluta. Corre otra vez `mise run remoto instalar` desde la ruta nueva.
- **Para probar sin el servicio:** `mise run remoto quitar` y luego `mise run elena -- remoto`, que lo corre en la terminal (`Ctrl+C` lo detiene).

## Sin probar todavía

Probados: emparejar, la página, el temporizador en el nivel «Pausar» y cancelar. Falta probar en un equipo real los niveles **+ apagar VM**, **+ suspender PC** y **+ apagar PC**, **«Fin de canción»**, encender y apagar la VM desde el teléfono, y suspender o apagar con la VM encendida.

## Límites

- Solo en la red de casa.
- No se puede navegar ni buscar en la biblioteca de Apple Music.
- No se puede adelantar, ni hay aleatorio o repetir: Apple Music no los publica.
- No hay controles en la pantalla de bloqueo del teléfono.
- Solo controla el bus de música: los otros buses y las escenas se manejan en la consola.
