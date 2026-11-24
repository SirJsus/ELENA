# Guía: la consola ELENA

La consola es el «Voicemeeter» de ELENA: en una sola ventana muestra qué suena, en qué bus, con cuántos canales y con qué nivel por bocina, y deja mover apps, cambiar volúmenes, prender el mic y guardar escenas y reglas. Todo lo que hace la ventana también se puede hacer desde la línea de comandos (`elena …`).

## Abrirla

| Cómo | Qué hace |
|---|---|
| **Un atajo de teclado** (por ejemplo `SUPER + M` en Hyprland, ver abajo) | Abre la consola en una ventana flotante de kitty, o la enfoca si ya está abierta |
| **«Consola ELENA»** en el menú de apps | Lo mismo que el atajo |
| `mise run consola` | La abre dentro de la terminal en la que estás |

**Hay una sola consola a la vez:** el atajo y el lanzador reconocen una consola ya abierta (también si la abriste con `mise run consola` en otra terminal) y enfocan su ventana en lugar de abrir otra, porque dos consolas duplicarían los medidores. Fuera de Hyprland, solo avisan que ya está abierta.

Requisitos:
- **ELENA aplicado** (`mise run aplicar`): sin los buses, la consola abre pero lo avisa en rojo.
- **kitty** para el lanzador y el atajo (`mise run consola` sirve en cualquier terminal).
- **El entorno de Python** (`.venv`): se crea con `mise exec -- uv sync`. Si falta, el lanzador lo avisa.

### El lanzador

`mise run aplicar` (alcance `todo`) crea `~/.local/share/applications/elena-consola.desktop` como enlace a `.cache/lanzadores/`, y `mise run quitar` lo borra. Ejecuta `scripts/consola.sh`, que abre `kitty --class elena-consola`.

### El atajo de Hyprland

ELENA no toca la config de Hyprland: copia esto a mano, con la ruta de tu repo. Con la config en Lua (Hyprland 0.57):

```lua
-- tus atajos (binds.lua o donde los tengas)
hl.bind(mainMod .. " + M", hl.dsp.exec_cmd("~/ruta/a/ELENA/scripts/consola.sh"))

-- tus reglas de ventana (windowrules.lua)
hl.window_rule({
    match  = { class = "^(elena-consola)$" },
    float  = true,
    center = true,
    size   = { "monitor_w*0.5", "monitor_h*0.85" },
})
```

- **El tamaño es un punto de partida.** El ancho necesario es de unas 82 columnas (con los cuatro buses y el mic sonando a la vez). El alto, de unas 45 líneas: cabecera, lista, puente de 14 líneas y pie. Con una ventana más angosta, el puente pasa a barras de 2 celdas sin números.
- **Después de guardar, corre `hyprctl reload`.** La recarga automática no siempre ve los cambios: puede cargar el atajo pero no la regla de ventana, y la consola abre en mosaico. Las reglas solo se aplican al abrir una ventana, así que hay que cerrar la consola y volver a abrirla.
- **En otro escritorio** (Sway, GNOME, KDE): el atajo ejecuta `scripts/consola.sh` y la regla deja flotante la ventana de clase `elena-consola`. Fuera de Hyprland, el script no enfoca una consola ya abierta: solo lo avisa.

## La pantalla

```
╭─ ELENA ──────────────────────────────────────────────────────────────╮   ← cabecera: una línea por tema
│ Tarjeta   5.1 ✓                                                      │
│ Defecto   salida ELENA · Sistema · entrada ELENA · Mic               │
│ Mic       voz off [v] · ruido on [n] · puente off [b]                │
│ Visual    música on · juegos on · sistema on  ← Noctalia Spectrum [w]│
│ Audífonos solo off [h] · directo [H]  → Audífonos USB                │
│ Bocinas   calibración activa ✓ · cruce 80 Hz · EQ 0 común + 0 sub · tope 136 %  [a]
│ Estéreo   -15 dB al sonar estéreo: Música 5.1 · Sistema ♪ estéreo -15 dB
╰──────────────────────────────────────────────────────────────────────╯
Buses
Música   6 ch → ELENA · Calibración → Bocinas 5.1   ████████│·   ▮▮▮▮▮▮▮▮▮▮ 100%   ← bus
  ▸ elena-windows · snd0 · 6 ch · pulse               █████████│   ▮▮▮▮▮▮▮▮▮▮ 100%   ← app
Juegos   6 ch → ELENA · Calibración → Bocinas 5.1                 ▮▮▮▮▮▮▮▮▮▮ 100%
  (sin apps)
Sistema  2 ch → ELENA · Calibración → Bocinas 5.1  ♪ estéreo -15 dB   ▮▮▮▮▮▮▮▮▮▮ 100%
…
Entradas · Grabaciones
─────────────────────────────────────────────────────────────────────
    Música                                                  Mic       ← puente
  0  ·   ·   ·   ·   ·   ·                                 │  ·
 -6 ───  ·   ·   ·   ·   ·                                 │  ·
  …
     FL  FR  FC LFE  RL  RR                                    M
     -8  -8  -∞ -25 -13 -13                                  -21
─────────────────────────────────────────────────────────────────────
⚠ avisos (solo si hay)
← Vol −  → Vol +  s Silenciar  m Mover  d Detalle  e Escenas  a Calibración  : Acciones  ? Ayuda  q Salir
```

- **Cabecera** (una línea por tema, con su tecla al final):
  - **Tarjeta:** el perfil (`5.1 ✓`, o en rojo si no está en 5.1);
  - **Defecto:** la salida y la entrada por defecto;
  - **Mic:** los tres interruptores, con su tecla;
  - **Visual:** qué buses entran a la mezcla para visualizadores (`on`/`off`) y quién la escucha. `w` la configura ([guía](visualizadores.md));
  - **Bocinas:** la calibración: si está activa, el cruce, las bandas del EQ y el tope. `a` abre la pantalla de calibración ([Calibración](#calibración));
  - **Estéreo:** la compensación estéreo en vivo de cada bus (solo si está configurada).
- **Pie:** solo lo esencial. Las teclas del mic, Visual e internos están en la cabecera, y todas en la ayuda (`?`).
- **Lista:** «Buses», cada uno con sus apps (sin su nombre interno, que está en el detalle `d`; si va a la calibración, se ve el camino hasta la tarjeta), luego «Directo a una salida» (apps que no van a un bus), «Sin conectar», «Entradas» y «Grabaciones». Los nodos internos de ELENA se ocultan; `i` los muestra.
- **Cada fila** tiene a la derecha:
  - **el nivel:** el canal más alto, con `│` en el pico sostenido 1.5 s. Los colores son: acento hasta −18 dBFS, amarillo hasta −6 y rojo arriba;
  - **el volumen:** `M` si está silenciado, `≠` si los canales tienen volúmenes distintos (balance), la barra de 10 celdas y el %. La barra se pone en rojo arriba de 100 %.
- **Un ▸ o ◂** marca una app que suena o que graba. `◆` marca una app elegida con espacio para moverla, y `⚑ Juegos`, una app con regla fija (en una grabación, `⚑ Visual`). El `pw` en amarillo indica una app que entra por la API nativa de PipeWire.
- **Puente de medidores** (abajo, fijo):
  - una columna por bocina de cada bus con apps, **con el volumen del bus ya aplicado** (lo que sale a las bocinas);
  - el mic, pegado a la derecha;
  - escala de 0 a −60 dBFS, en filas de 6 dB. `─` es el pico sostenido, y el número de abajo es su valor;
  - una etiqueta en rojo indica que ese canal satura (llega a −0.1 dBFS);
  - un bus sin apps no tiene columnas;
  - el título del bloque del bus elegido se resalta, y un clic en un bloque elige ese bus en la lista.
- **Avisos:** solo aparecen si hay algo que corregir (ver [Avisos](#avisos)).

El volumen se muestra en **% como pavucontrol**, que es cúbico: **50 % = −18 dB**, 70 % = −9.3 dB y 100 % = 0 dB. Se puede subir hasta 150 % (+10.6 dB).

## Teclas

### En la lista

| Tecla / mouse | Qué hace | Para qué |
|---|---|---|
| `↑` `↓` · `k` `j` · `Inicio` `Fin` · clic | Elegir un bus, app o entrada | Todo lo demás actúa sobre la fila elegida |
| `←` `→` | Volumen −5 % / +5 % | Bajar o subir un bus, una app o el mic. **Conserva el balance**: todos los canales se mueven igual en dB |
| `shift + ←` `→` | Volumen ±1 % | Ajuste fino |
| Rueda sobre la barra de volumen | Volumen ±2 % | Con el mouse. En el resto de la fila, la rueda desplaza la lista |
| `s` | Silenciar o activar | Callar un bus o una app sin perder su volumen |
| `m` | Mover la app (o las marcadas) | Mandarla a otro bus o directo a una salida. WirePlumber lo recuerda la próxima vez que abra |
| `espacio` | Marcar o desmarcar una app (`◆`) | Mover varias apps juntas con `m` |
| `c` | Balance por canal del bus | Subir o bajar una bocina de un bus en dB; por ejemplo, el centro de Juegos para los diálogos |
| `p` | Probar las bocinas del bus o de la salida | Oír cada bocina en su lugar: todas con voz, o una con ruido rosa (50 Hz en el LFE) |
| `r` | Regla fija sobre la app | «Esta app siempre a este bus»; además la mueve en ese momento. En una **grabación**, la única opción es Visual: «este visualizador siempre a `elena_visual`» |
| `m` en una grabación | Qué graba | Mandar un visualizador a la mezcla `elena_visual` o al monitor de un bus, una vez (sin regla) |
| `d` · `Enter` · doble clic | Detalle del nodo | Ver formato, canales, API, nivel en vivo por canal (pico y RMS) y las props crudas de `pw-dump` |

### En cualquier momento

| Tecla | Qué hace | Para qué |
|---|---|---|
| `v` | Mic: tu voz on/off | Silenciar tu voz en los mics virtuales (`mic:voz`) |
| `n` | Mic: supresión de ruido on/off | Activar o quitar la supresión de ruido (`mic:ruido`) |
| `b` | Mic: puente on/off | Que una llamada del navegador y una del chat se oigan entre sí (`mic:puente`) |
| `w` | Visualizadores | Qué buses entran a la mezcla `elena_visual` y quién la escucha (`mise run visual`) |
| `h` | Solo audífonos on/off | Música, Juegos y Sistema a los audífonos (a donde va Chat), para cuando alguien más está contigo (ver [Solo audífonos](#solo-audífonos)) |
| `H` | Binaural / directo | Cómo suenan los audífonos: surround virtual (HRTF) o estéreo de siempre |
| `e` | Escenas | Cambiar varios volúmenes de golpe (ver [Escenas](#escenas)) |
| `a` | Calibración | El EQ en vivo con su curva, la compensación estéreo y el resumen (ver [Calibración](#calibración)) |
| `:` | Acciones | Arrancar la VM, abrir Steam (ELENA 5.1), `aplicar` o `quitar` |
| `i` | Mostrar u ocultar los nodos internos | Depurar: loopbacks, el mic por dentro y los medidores |
| `?` | Ayuda | La lista de teclas |
| `q` | Salir | — |

### En los diálogos

| Diálogo | Teclas |
|---|---|
| Todos | `Esc` o `q` cierran |
| Mover (`m`) | `↑` `↓` y `Enter` eligen el destino: un bus o una salida directa |
| Balance (`c`) | `↑` `↓` eligen el canal · `←` `→` ±0.5 dB (`shift`: ±0.2 dB) · `r` iguala todos a 0 dB. Rango: −60 dB al tope de 150 % |
| Probar (`p`) | `Enter` suena la opción (todas o una bocina); elegir otra corta la anterior · `Esc` corta y, si no sonaba nada, cierra |
| Escenas (`e`) | `Enter` aplica · `g` guarda los volúmenes actuales con un nombre · `x` o `Supr` borra (pide confirmación) |
| Regla (`r`) | `Enter` sobre un bus fija la regla; sobre «Quitar» la borra |
| Visualizadores (`w`) | `Enter` alterna el bus resaltado; `1` `2` `3` alternan Música, Juegos y Sistema · se pone al día solo · `w` o `Esc` cierra |
| Acciones (`:`) | `Enter` lanza. «Arrancar la VM» se desactiva si ya corre |
| Confirmar | `s`, `y` o `Enter`: sí · `n`, `Esc` o `q`: no |

**Las acciones del menú `:`:**
- **La VM y Steam** se lanzan **separados de la consola**: siguen vivos si la cierras, y su salida va a `.cache/tmp/consola-vm.log` y `consola-steam.log`.
- **`aplicar` y `quitar`** piden confirmación porque **reinician PipeWire**: el audio se corta un momento y la VM puede quedarse sin sonido hasta reiniciarla. Al terminar muestran su salida.

## Línea de comandos

Todo lo de la consola también se hace desde una terminal:

```
mise run elena -- <orden> [argumentos]       # desde cualquier lugar del repo
```

Dentro del entorno de mise (por ejemplo, en una tarea), es `uv run elena <orden>`. Con `--help` después de cualquier orden se ven sus opciones.

| Orden | Para qué | Ejemplo |
|---|---|---|
| `elena` | Abre la consola (lo mismo que `mise run consola`) | `mise run elena` |
| `elena estado [--json] [--todo]` | Qué suena y dónde: perfil, sink físico, valores por defecto, mic, cada bus con sus apps, salidas directas, entradas, grabaciones y avisos. `--todo` agrega los nodos internos; `--json` da lo mismo en JSON, para scripts o un módulo de Waybar | `mise run elena -- estado` |
| `elena mover <app> <destino>` | Mueve una app a un bus o a una salida (como `m`) | `mise run elena -- mover zen musica` |
| `elena volumen <objetivo> <n\|+n\|-n>` | Fija (`80`) o cambia (`+5`, `-10`) el volumen en %, conservando el balance. Tope: 150 | `mise run elena -- volumen juegos -10` |
| `elena silenciar <objetivo> [on\|off]` | Silencia (`on`), activa (`off`) o, sin nada, alterna | `mise run elena -- silenciar musica` |
| `elena escena` | Lista las escenas | `mise run elena -- escena` |
| `elena escena <nombre>` | Aplica una escena | `mise run elena -- escena juego` |
| `elena escena <nombre> --guardar` | Guarda los volúmenes y silencios actuales de los buses con ese nombre | `mise run elena -- escena noche --guardar` |
| `elena escena <nombre> --borrar` | Borra una escena | `mise run elena -- escena noche --borrar` |
| `elena reglas` | Lista las reglas fijas, en orden | `mise run elena -- reglas` |
| `elena regla <app> <bus>` | «Esta app siempre a este bus»: guarda la regla y la mueve ya (como `r`) | `mise run elena -- regla ToyShire juegos` |
| `elena regla <app> --quitar` | Quita las reglas de esa app | `mise run elena -- regla ToyShire --quitar` |
| `elena regla <app> visual` | «Este visualizador siempre a la mezcla `elena_visual`»: para apps que **graban** el sonido del sistema ([guía](visualizadores.md)) | `mise run elena -- regla waywallen visual` |
| `elena visual [bus [on\|off]]` | Qué buses entran a la mezcla de los visualizadores (`musica`, `juegos`, `sistema`). Sin nada, el estado y quién la escucha; con un bus y sin `on`/`off`, alterna ([guía](visualizadores.md)) | `mise run visual sistema off` |
| `elena audifonos [on\|off\|alternar\|binaural\|directo]` | «Solo audífonos» (como `h` y `H`): sin nada, el estado ([Solo audífonos](#solo-audífonos)) | `mise run audifonos on` |
| `elena audifonos graves <dB> [--hz F]` · `lfe <dB>` | Los graves de los audífonos: el estante (−6 a 12 dB, 40 a 250 Hz) y el LFE (−10 a 20 dB), en vivo y guardados | `mise run audifonos graves 8` |
| `elena vigilar [--una-vez]` | Aplica las reglas a cada app nueva. Normalmente lo corre el servicio (ver [El vigilante](#el-vigilante)); `--una-vez` las aplica a lo que suena y termina | `mise run elena -- vigilar --una-vez` |
| `elena vm [estado\|apagar\|red on\|off]` | La VM de Windows por su monitor QMP: estado, apagado ordenado o el cable de red ([guía de la VM](vm-apple-music-atmos.md)) | `mise run elena -- vm red off` |
| `elena remoto [qr\|token nuevo\|estado]` | El control remoto de música para el teléfono. Sin nada, corre el servidor (normalmente lo corre el servicio `elena-remoto`); `qr` da el enlace para emparejar; `token nuevo` desempareja todos los teléfonos ([guía](control-remoto.md)) | `mise run elena -- remoto qr` |
| `elena equipo [detectar\|generar\|iniciar]` | Tu equipo (`local/equipo.toml`): sin nada, lo muestra; `detectar` propone uno con lo que encuentra; `generar`, los `.conf` de `local/` (lo hace `aplicar`); `iniciar`, la primera vez | `mise run equipo detectar` |
| `elena vm musica [estado]` | El puente de música: Apple Music de la VM como reproductor MPRIS. Normalmente lo corre el servicio `elena-vm-musica`; `estado` muestra qué suena y si Windows está conectado ([guía de la VM](vm-apple-music-atmos.md#apple-music-en-los-widgets-de-linux)) | `mise run elena -- vm musica estado` |

Tareas de mise relacionadas:

| Tarea | Para qué |
|---|---|
| `mise run consola` | Abre la consola en la terminal actual |
| `mise run vigilante [instalar\|quitar\|estado]` | Instala, quita o muestra el servicio del vigilante (sin argumento: estado) |
| `mise run remoto [instalar\|quitar\|qr\|estado]` | El servicio del control remoto para el teléfono ([guía](control-remoto.md)) |
| `mise run estado` | El estado en bash (`scripts/estado.sh`): la referencia original de `elena estado` |
| `mise run medir:canales [sink]` | Graba un bus y muestra el nivel por canal, los streams y los cortes, para medir con más detalle que el puente |
| `mise run mic:voz \| mic:ruido \| mic:puente [on\|off]` | Los interruptores del mic (lo mismo que `v`, `n` y `b`) |
| `mise run visual [bus [on\|off]]` | Lo mismo que `elena visual`: qué buses ven los visualizadores |
| `mise run audifonos [on\|off\|alternar\|binaural\|directo]` | Lo mismo que `elena audifonos` |

**Cómo se nombran las cosas:**
- **`<app>`:** el nombre de la app, el del proceso (en Wine, `algo.exe`) o el `media.name`, completo o una parte, sin distinguir mayúsculas. También vale su **serial**, que sale en `elena estado`. Si varias apps distintas coinciden, la orden se niega y las lista. Varios streams de la misma app (Zen abre uno por pestaña) se mueven juntos.
- **`<destino>` y `<bus>`:** `musica`, `juegos`, `sistema`, `chat` o `audifonos` (también la etiqueta o el `node.name`). También vale una salida por su `node.name` o una parte única de su nombre, como `hdmi` o `usb`. `visual` es aparte: solo sirve para apps que **graban** (visualizadores) y las lleva a la mezcla `elena_visual`.
- **`<objetivo>` (volumen y silencio):** un bus, una entrada o una salida por su `node.name` (`elena_mic`, …), o una app. Los nodos internos se rechazan: para el mic se usan `v`, `n` y `b`, o `mise run mic:*`; para la mezcla de los visualizadores, `elena visual`.

## Escenas

Una escena guarda **el volumen y el silencio de los cuatro buses**, no a dónde va cada app. Sirve para cambiar de «modo» de golpe: por ejemplo, jugar con la música baja y el chat alto.

- **Desde la consola:** `e`. Luego `g` guarda la mezcla actual con un nombre y `Enter` aplica la elegida.
- **Desde la CLI:** `elena escena <nombre> --guardar` y `elena escena <nombre>`.
- **A mano:** en `local/escenas.toml`:

```toml
[escenas.juego]
volumen = { musica = 40, juegos = 100, sistema = 50, chat = 80 }
silencio = { musica = false }
```

Al aplicar una escena, **cada bus conserva su balance**: si FL de Juegos estaba a −6 dB del resto, sigue igual.

## Reglas fijas y el vigilante

Una regla manda una app a un bus **cada vez que abre**, aunque la app pida otro sink por nombre. Así se resuelven los juegos con FMOD Ex (por ejemplo Toy Shire), a los que WirePlumber no les recuerda el destino.

- **Desde la consola:** elige la app y presiona `r`. Las apps con regla muestran `⚑ <bus>`.
- **Desde la CLI:** `elena regla <app> <bus>`.
- **A mano:** en `local/reglas.toml`. Las reglas se prueban de arriba abajo y gana la primera que coincide:

```toml
[[regla]]
app = "Discord"          # application.name
bus = "chat"

[[regla]]
proceso = "ToyShire*"    # argv[0]; en Wine, el .exe
bus = "juegos"
```

`app`, `proceso` y `media` aceptan comodines (`*` y `?`) y no distinguen mayúsculas. Si una regla trae varios, deben coincidir todos.

**`bus = "visual"`** es para las apps que **graban** el sonido del sistema (visualizadores como Waywallen o el espectro de Noctalia): las manda a la mezcla `elena_visual` en lugar de dejarlas en `elena_sistema` ([guía](visualizadores.md)). Una regla `visual` solo se aplica a grabaciones y las demás solo a apps que suenan, así que la misma app puede tener una de cada tipo. En la consola, `r` sobre una fila de «Grabaciones» la pone; con la CLI, `elena regla <app> visual`.

**Las reglas las cumple el vigilante** (ver abajo). Sin él, guardar una regla solo mueve la app esa vez: la próxima vez que abra irá a donde WirePlumber decida.

**¿Regla o mover con `m`?**
- Para la mayoría de las apps basta con `m`: WirePlumber recuerda el destino y la app vuelve ahí cada vez que abre.
- La regla es para las apps a las que WirePlumber no les recuerda el destino: las que piden un sink por nombre (FMOD Ex), las que cambian de nombre en cada arranque o las que quieras amarrar a un bus aunque las muevas por error.

## El vigilante

`elena-vigilante` es un **servicio de usuario de systemd** que corre `elena vigilar` en segundo plano: sigue el grafo de PipeWire y, cada vez que aparece una app nueva, le aplica la primera regla de `local/reglas.toml` que coincide (`pactl move-sink-input`; para los visualizadores, `pactl move-source-output` al monitor de `elena_visual`). No usa la consola: funciona aunque esté cerrada, desde que inicias sesión.

### Instalarlo

```
mise run vigilante instalar
```

- **También lo instala `mise run aplicar`** (alcance `todo`). Con `mise run vigilante instalar` se instala solo, **sin reiniciar PipeWire** ni cortar el audio.
- Hace falta el `.venv` (`mise exec -- uv sync`); si falta, se niega y lo dice.
- **Qué deja fuera del repo** (una excepción deliberada, como los lanzadores):
  - genera la unidad en `.cache/systemd/elena-vigilante.service`;
  - la enlaza en `~/.config/systemd/user/` con `systemctl --user link`;
  - la habilita (enlace en `default.target.wants/`), para que arranque sola en cada inicio de sesión;
  - y la arranca.
- La unidad ejecuta `mise exec -C <repo> -- <repo>/.venv/bin/elena vigilar`, con el entorno de `mise.toml` y `local/equipo.toml` (así nada de Python se escribe fuera del repo), y con `Restart=on-failure` (si se cae, se reinicia a los 3 s).

### Comprobar que funciona

| Comando | Qué muestra |
|---|---|
| `mise run vigilante` | El estado del servicio (`active (running)`) y sus últimas 8 líneas |
| `journalctl --user -u elena-vigilante -f` | Su registro en vivo: cada app que mueve y por qué regla |
| `mise run elena -- reglas` | Las reglas que está aplicando, en orden |

Al arrancar (y cada vez que relee el archivo) escribe cuántas reglas leyó, y por cada app que mueve, una línea así:

```
Vigilante de ELENA: aplicando reglas fijas a cada app nueva.
Reglas: 1 (/ruta/a/ELENA/local/reglas.toml)
elena-prueba-regla (serial 7797) → ELENA · Música  [app = elena-prueba-regla → musica]
```

**Prueba rápida:**
1. Elige en la consola una app que suene y presiona `r` → un bus distinto del actual (por ejemplo, Toy Shire → Juegos).
2. Cierra la app y vuelve a abrirla.
3. Debe aparecer directo en ese bus, con `⚑`, y el journal muestra la línea de arriba.

### Cómo se comporta

- **Solo mueve las apps nuevas**, y al arrancar, una vez, las que ya sonaban. **Si después mueves una app a mano, no la regresa:** la regla vuelve a aplicar la próxima vez que la app abra.
- **Relee `local/reglas.toml` cuando cambia.** Las reglas guardadas desde la consola, la CLI o a mano aplican sin reiniciar nada.
- **Si pactl todavía no conoce la app** (acaba de aparecer), lo reintenta en los siguientes cambios del grafo, hasta 5 veces.
- **Puede oírse un instante en el bus equivocado** antes de moverla, porque WirePlumber la conecta primero. Si llega a molestar, el plan B es un script Lua de WirePlumber, que rutea antes del primer link pero obliga a reiniciar WirePlumber con cada regla.
- **Si PipeWire se reinicia** (`aplicar` o `quitar`), el vigilante se reconecta solo.
- **Deja los audífonos al 100 %:** si el sink al que sale Chat baja (la perilla, pavucontrol o un movimiento fantasma), lo regresa al 100 % (`journalctl --user -u elena-vigilante` dice cuándo).
- **Mantiene «Solo audífonos»:** con el modo prendido, si las salidas de los buses vuelven a las bocinas (un reinicio de PipeWire), las regresa a los audífonos; si Chat cambia de destino, `elena_audifonos` lo sigue.

### Quitarlo

```
mise run vigilante quitar
```

Lo detiene, lo deshabilita y borra el enlace de `~/.config/systemd/user/`. `mise run quitar` también lo hace. Las reglas se quedan en `local/reglas.toml`.

### Si algo falla

- **La consola avisa «el vigilante no corre»:** hay reglas pero ningún `elena vigilar` vivo. Con `mise run vigilante` ves si no está instalado (`not-found`) o si falló (`failed`, con el error en las últimas líneas). Para instalarlo o reiniciarlo: `mise run vigilante instalar`.
- **Una app no se mueve:**
  - revisa con `mise run elena -- reglas` que la regla coincida con lo que muestra `elena estado`. En Wine, el nombre de la app suele venir en blanco: usa `proceso = "Juego.exe"`;
  - una regla anterior que también coincide gana, porque se prueban de arriba abajo;
  - en el journal debe aparecer la app. Si dice «no la pude mover a …», pactl falló 5 veces seguidas (el error va entre paréntesis).
- **Editaste `reglas.toml` a mano y no hace caso:** si el archivo quedó inválido, el journal muestra `ERROR: … Sigo con las reglas anteriores.` y la consola lo avisa al abrir. Corrige el archivo: se relee solo.
- **Moviste el repo de carpeta:** la unidad guarda la ruta absoluta. Corre `mise run vigilante instalar` otra vez desde la ruta nueva.
- **Para probar sin el servicio:** `mise run elena -- vigilar` lo corre en la terminal (`Ctrl+C` lo detiene), y `--una-vez` aplica las reglas a lo que suena y termina.

## Avisos

Aparecen en un panel abajo de la consola, y también en `elena estado`, **solo mientras la causa sigue ahí**.

| Aviso | Qué hacer |
|---|---|
| ELENA no está aplicado o falta un bus | `mise run aplicar` |
| La tarjeta no está en 5.1 | `mise run aplicar` (fuerza el perfil) |
| El sink físico no está al 100 % | Ponerlo en 100 % (pavucontrol): el volumen se maneja por bus |
| Steam está abierto sin ELENA | Salir de Steam (Steam → Salir) y abrir «Steam (ELENA 5.1)» (o `:` → Steam) |
| Steam sin winepulse | Igual: Steam se abrió con `PULSE_SINK` pero sin `PROTON_USE_PIPEWIRE=0` |
| Un juego usa winepipewire | El juego no sale en 5.1. Abrir Steam con el lanzador de ELENA |
| Un juego fuera de Juegos | Moverlo con `m`, o con una regla fija (`r`) para que no se repita |
| Un juego en estéreo (info) | El juego entrega 2 canales; revisar sus opciones de audio |
| App en Chat sin audífonos | Los audífonos están apagados o desconectados: no se oye nada de Chat |
| Cortes en un nodo | Xruns en los últimos 30 s (en rojo desde 5). Si pasa con la VM, ver la [guía de la VM](vm-apple-music-atmos.md) |
| El vigilante no corre | `mise run vigilante instalar` |
| Las bocinas esperan a ELENA · Calibración | `elena_salida` no existe: revisar `40-elena-calibracion.conf`, o `mise run calibracion -- desactivar` y `mise run aplicar` |
| ELENA · Calibración no está al 100 % | Ponerla en 100 %: el volumen se maneja por bus |
| Un bus calibrado con volumen por canal | El balance se aplicaría dos veces: ponerlo en un solo volumen (o `mise run aplicar`) |
| Música o Juegos arriba de tu máximo | Bájalo cuando quieras: no sube más del tope (`[maximo] tope` de la calibración) |

## Solo audífonos

Para cuando alguien más trabaja contigo: con **`h`** (o `mise run audifonos on`), **Música, Juegos y Sistema dejan las bocinas y van a los audífonos**, a donde vaya Chat. Otra `h` los regresa. Necesita audífonos en `local/equipo.toml` (`ELENA_AUDIFONOS`).

- **Cómo funciona:** el bus **Audífonos** (`elena_audifonos`, 5.1) convierte a estéreo y sale a donde va Chat. El modo mueve las salidas de los tres buses de bocinas a ese bus; al apagarlo, regresan a la calibración (o al físico si está apagada).
- **Lo demás no cambia:** el volumen de cada bus, el tope de Música y Juegos, la compensación estéreo, los visualizadores, la VM, las teclas multimedia y el control remoto. Chat sigue directo a los audífonos, con su propio volumen.
- **El volumen de los audífonos** es el del bus **Audífonos** (en la lista, como cualquier bus; WirePlumber lo recuerda y entra en las escenas). Sube los tres buses juntos. Chat tiene el suyo. **El sink de los audífonos va fijo al 100 %**, como el físico: así tu volumen ideal siempre se mide desde la potencia completa, y si algo lo baja (la perilla del audífono, pavucontrol o un cambio fantasma), **el vigilante lo regresa al 100 %** y lo anota en su registro. Sin el vigilante, la consola lo avisa. WirePlumber recuerda ese 100 % cada vez que los prendes.
- **Binaural o directo (`H`, directo por omisión):** muchos audífonos «7.1» no hacen surround virtual por su cuenta (en Linux son una tarjeta USB estéreo; el 7.1 es software de Windows). ELENA lo hace con la HRTF genérica MIT KEMAR: cada canal suena como una bocina virtual en su lugar (frontales a ±30°, centro al frente, traseras a ±110°). Si no te convence, **directo** es la mezcla estéreo de siempre. Los dos suenan igual de fuerte.
- **Graves:** aquí no hay calibración de bocinas (ni el nivel, ni el balance, ni el cruce al sub), así que los audífonos llevan lo suyo:
  - un **estante de graves** de +6 dB en 105 Hz (la forma de la curva Harman para audífonos): `mise run audifonos graves 8`, o con otra frecuencia, `mise run audifonos graves 8 --hz 90`;
  - el **LFE** a +10 dB sobre las frontales (como un receptor y como `[lfe] nivel` de la calibración): `mise run audifonos lfe 12`. El estante también lo sube: a 40 Hz queda +15.8 dB sobre FL.
  - Suenan al instante y se guardan. Para valores negativos, `mise run elena -- audifonos lfe -3` (si no, mise toma el `-3` como opción). En la cabecera se ven como «graves +6 · LFE +10».
  - **No hay limitador:** subir graves y LFE come margen. Con el bus Audífonos a volumen normal sobra; si lo pones cerca del 100 % y los subes mucho, una explosión de un juego en 5.1 puede saturar (se oye como un chasquido).
- **Se mantiene:** el estado va en `.cache/audifonos/estado.json` y el vigilante lo vuelve a aplicar si PipeWire se reinicia o al prender la PC. Sin el vigilante, un reinicio regresa todo a las bocinas (la consola lo avisa).
- **Si los audífonos están apagados,** Música, Juegos y Sistema quedan en silencio (no caen a las bocinas) y un aviso lo dice. Al prenderlos, suenan solos.
- **En la cabecera,** la línea **Audífonos** dice si está prendido, el camino y a dónde sale. Si falta mover algún bus (o quedó alguno en audífonos con el modo apagado), lo marca en rojo. En la lista, la fila de **Audífonos** dice qué buses le llegan, y cada bus dice «→ ELENA · Audífonos → …».
- Las pruebas de la calibración (`elena calibracion …`) suenan en las bocinas aunque el modo esté prendido.

## Calibración

La calibración de las bocinas ([guía](calibracion.md)) se ve en la cabecera, en la línea **Calibración**: si está activa, el cruce, cuántas bandas tiene el EQ (común + sub), el tope de Música y Juegos y la **compensación estéreo en vivo** de cada bus. Mientras un bus está compensado, su fila dice **«♪ estéreo −15 dB»**. Cada bus puede bajar distinto (`sistema = -10.0` en `[estereo]`, o `elena calibracion estereo -10 --bus sistema`). La compensación la hace el vigilante; la consola la lee del volumen de la salida del bus, así que muestra lo que pasa de verdad. Si el vigilante no corre, lo dice.

Con `a` se abre la pantalla de calibración: el resumen (tu 100 %, el LFE, el cruce y cada bocina), la compensación estéreo y el **EQ, con su curva** (de 20 Hz a 20 kHz; la del sub, de 20 a 250 Hz). Todo cambio suena al instante (solo cambia coeficientes, sin reiniciar) y se guarda en `local/calibracion.toml` un segundo después, o al cerrar.

| Tecla | Qué hace |
|---|---|
| `↑` `↓` | Elegir banda |
| `←` `→` | Ganancia ±0.5 dB |
| `shift+←` `shift+→` | Frecuencia, una sexta de octava |
| `[` `]` | Q (más ancha / más angosta) |
| `t` | Tipo: pico → estante grave → estante agudo |
| `a` | Agregar una banda (pico en 1 kHz, o en 60 Hz en el sub, en 0 dB) |
| `x` | Quitar la banda |
| `s` | Cambiar entre el EQ común (las 5 satélites) y el del sub |
| `u` | Deshacer: regresa a como estaba al abrir |
| `Esc` | Cerrar (guarda) |

Lo mismo desde la terminal: `mise run calibracion -- eq …` (ver `mise run calibracion -- eq --help`). Las ganancias de las bocinas, el sub, el LFE y el cruce se afinan con `mise run calibracion -- afinar`.

## Problemas comunes

- **El lanzador o el atajo no hacen nada:** corre `scripts/consola.sh` en una terminal para ver el error. Lo normal es que falte el `.venv` (`mise exec -- uv sync`).
- **El atajo abre la ventana en mosaico, no flotante:**
  1. `hyprctl reload` y volver a abrir la consola. Es lo más común: la regla está en el archivo pero Hyprland no la cargó;
  2. `hyprctl configerrors` no debe mostrar nada;
  3. `hyprctl clients -j | jq '.[] | .class'` debe mostrar `elena-consola`.

  Mientras tanto, tu atajo de «flotante» de Hyprland la suelta.
- **La consola se ve con fondo opaco o colores raros:** usa los 16 colores de la terminal y no pinta fondo propio, así que toma la paleta y la transparencia de tu terminal.
- **El escritorio muestra «mic en uso» con la consola abierta:** es normal. Es el medidor del mic, que siempre está activo.
- **Después de `aplicar` o `quitar`, los medidores tardan un momento:** PipeWire se reinició y la consola reabre sus medidores pasados 2 s.
- **pavucontrol o `pactl` muestran una app 5.1 (la VM) con el centro, el LFE y las traseras en 0 %:** WirePlumber le restauró un volumen guardado en estéreo. La señal sí pasa, y la consola lo muestra bien (6 volúmenes al nivel de FL y FR). Con el primer cambio de volumen desde la consola se guardan los 6 canales y deja de pasar.
- **El volumen salta al usar las teclas multimedia o `wpctl`:** esos controles ponen un solo valor para todos los canales y **borran el balance** (`≠`). En la consola y en `elena volumen`, el balance se conserva.
