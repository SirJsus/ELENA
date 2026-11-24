# Guía: la mezcla para visualizadores (`elena_visual`)

## El problema

Las apps que reaccionan al sonido del sistema (fondos animados como **Waywallen**, el espectro de **Noctalia**, barras de audio, etc.) graban el **monitor del sink por defecto**. En un sistema normal es la única salida y ahí está todo. Con ELENA, el sink por defecto es `elena_sistema`: esas apps no ven la música (`elena_musica`) ni los juegos (`elena_juegos`).

## Cómo funciona

```
elena_musica.monitor  ─[elena_visual_musica]──┐
elena_juegos.monitor  ─[elena_visual_juegos]──┼─► elena_visual (estéreo) ─► monitor ─► Waywallen, Noctalia…
elena_sistema.monitor ─[elena_visual_sistema]─┘
```

- **`elena_visual`** es un sink nulo (`30-elena-visual.conf`, plantilla en `plantillas/pipewire/`): no suena por ninguna bocina, solo se graba su monitor. Es interno: no aparece como bus en la consola y WirePlumber nunca lo elige como salida por defecto.
- **Tres alimentadores** (loopbacks) copian el monitor de cada bus a `elena_visual`. Los monitores miden **antes del volumen del bus**: el visualizador se mueve igual aunque bajes o silencies un bus en ELENA. El 5.1 se baja a estéreo al entrar.
- **El chat no entra**: va a los audífonos y no es lo que se quiere ver.
- **Las apps se mandan a `elena_visual` con una regla fija** `bus = "visual"` en `local/reglas.toml`. El vigilante la cumple con `pactl move-source-output <serial> elena_visual.monitor` en cuanto la app abre su grabación.

## Qué buses entran

Por defecto entran los tres. El interruptor silencia el alimentador de ese bus, y WirePlumber lo recuerda entre reinicios, como los del mic.

```sh
mise run visual                 # estado: qué entra y quién escucha
mise run visual sistema off     # que los sonidos del escritorio no muevan el fondo
mise run visual juegos          # sin on/off, alterna
```

`mise run elena -- estado` muestra lo mismo en la línea `Visual:`.

**En la consola** (`mise run consola`): la línea **Visual** de la cabecera muestra qué entra y quién escucha, y **`w`** abre el diálogo. Ahí, `Enter` alterna el bus resaltado y `1` `2` `3` alternan Música, Juegos y Sistema. El diálogo se pone al día solo y también lista las grabaciones que todavía graban un bus directo.

## Mandar una app a la mezcla

Las reglas de Waywallen y Noctalia, para copiarlas a `local/reglas.toml`:

```toml
regla = [
    { bus = "visual", app = "Waywallen Daemon" },
    { bus = "visual", app = "Noctalia Spectrum" },
]
```

Para otra app:

1. Con la app abierta, busca su grabación en `mise run elena -- estado`, sección «Grabaciones». Aparece conectada a `elena_sistema`: `◂ <nombre> … ← elena_sistema`.
2. Fija la regla. La guarda y la mueve en ese momento:
   ```sh
   mise run elena -- regla "<nombre>" visual
   ```
3. Revisa que quedó conectada: `mise run visual` la lista después de `←`.

En la consola: en «Grabaciones», elige la fila de la app y presiona `r` → Visual (guarda la regla y la mueve), o `m` para moverla una sola vez, sin regla (a Visual o de vuelta a un bus).

Para probar sin guardar regla desde la terminal: `mise run elena -- mover "<nombre>" visual`. Una regla `visual` solo se aplica a **grabaciones**; si la misma app también suena, sus reglas de buses no se pisan con esta.

No hace falta tocar la configuración de la app ni de PipeWire, ni reiniciar nada.

### Waywallen

- Graba por **pipewire-pulse**, con `application.name = "Waywallen Daemon"` y `media.name = "waywallen.daemon.audio-response.capture"`. Abre **un solo stream** para todos los fondos: los renderers de Wallpaper Engine reciben el audio del daemon.
- No tiene opción para elegir la fuente (revisado en la versión 0.5.0).

### Noctalia

Graba de forma nativa (`application.name = "Noctalia Spectrum"`) y tampoco deja elegir la fuente. Con la regla `visual`, el vigilante la mueve al abrir. Si antes la mandabas a otro sink con una regla de cliente de libpipewire (`~/.config/pipewire/client.conf.d/`), ya no hace falta: con las dos, abre donde diga la regla de cliente y el vigilante la pasa a Visual al instante.

## Cómo se verificó

Con los mismos nodos creados a mano (`pw-cli create-node` y `pw-loopback`), sin reiniciar PipeWire:

| Prueba | Resultado |
|---|---|
| Tono de 440 Hz a −20 dBFS (solo izquierda) a `elena_juegos` **silenciado**, medido en el monitor de `elena_visual` (`mise run medir:canales elena_visual`) | FL −23.0 dB RMS / −20.0 pico; FR −inf. Ganancia 1, canales en su lugar, el silencio del bus no afecta |
| Alimentador de juegos silenciado (`pactl set-sink-input-mute`) | FL −inf; al activarlo, vuelve a −23.0 |
| `pactl move-source-output <serial> elena_visual.monitor` con Waywallen (pulse) | Enlazado a `elena_visual:monitor_FL/FR` |
| Lo mismo con Noctalia (nativa, aun con una regla de cliente) | Enlazada a `elena_visual:monitor_FL/FR` |
| Al destruir `elena_visual` | Las dos volvieron solas a `elena_sistema` (el sink por defecto) |

## Problemas

- **`mise run visual` dice «sin elena_visual»**: falta `mise run aplicar`. Hazlo con la VM apagada: reiniciar PipeWire puede dejar a QEMU sin audio.
- **El visualizador sigue en `elena_sistema`**: revisa que el vigilante corra (`mise run vigilante`) y que el `application.name` de la regla sea exactamente el de la grabación (`elena estado`). Un visualizador que abrió **antes** de que existiera `elena_visual` no se vuelve a mover; reinícialo o muévelo con `elena mover "<nombre>" visual`.
- **No se mueve aunque todo esté bien**: si la app cambia de nombre en una versión nueva, la regla deja de coincidir. Revisa el nombre con `elena estado` y corrige la regla.
- **`journalctl --user -u elena-vigilante -n 20`** muestra cada movimiento: `Waywallen Daemon (serial …) → ELENA · Visual (interno)`.

## Quitarlo

`mise run quitar` quita `elena_visual` junto con los buses. Para dejar ELENA sin mandar nada a la mezcla, quita las reglas `visual` de `local/reglas.toml`: los visualizadores vuelven al sink por defecto.
