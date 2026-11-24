# Guía: calibración de las bocinas

La calibración pone, entre los buses y la tarjeta, un `filter-chain` que da a cada bocina su **ganancia**, su **retardo**, un **EQ** y el **cruce de graves al subwoofer**, como un receptor. Los valores se miden con pruebas a ciegas y se afinan en vivo, sin cortes.

## Cómo funciona

```
elena_musica  ─┐
elena_juegos  ─┼─► elena_salida (filter-chain, 5.1) ──────────────► tarjeta 5.1
elena_sistema ─┘     5 satélites: EQ común → pasa altos ─► ganancia → retardo ─► FL FR FC RL RR
                                          └► pasa bajos ─┐
                     LFE: nivel LFE ─────────────────────┴► suma → EQ del sub → ganancia → retardo ─► LFE
elena_chat ─────────────────────────────────────────────────────────► audífonos (sin calibrar)
```

- **La fuente de verdad es `local/calibracion.toml`**, en dB, ms o metros, y Hz (empieza como una copia neutra de `config/calibracion.ejemplo.toml`). `elena calibracion generar` escribe con él `40-elena-calibracion.conf`, que no se edita a mano.
- **Activarla** es escribir ese `.conf` y mandar Música, Juegos y Sistema a `elena_salida`: `mise run calibracion -- activar` y luego `mise run aplicar`. **Desactivarla:** `mise run calibracion -- desactivar` y `mise run aplicar`. Hazlo con la VM apagada: reiniciar PipeWire puede dejar a QEMU sin audio.
- **El cruce** es un Linkwitz-Riley de 4.º orden (dos biquads de Q 0.707 por lado): −6 dB de cada lado en la frecuencia de corte y la suma plana. Va antes de las ganancias, así que los graves que se le quitan a una satélite llegan al sub con la ganancia del sub.
- **`nivel`** es tu 100 %: la ganancia de FL hasta el DAC con el bus al 100 %. Ganancia final de cada bocina = `nivel` + su ganancia (el LFE, además, `[lfe] nivel`). Con eso, **el 100 % de un bus es tu volumen de todos los días** en la consola, el teléfono, las teclas y pavucontrol.
- **`evitar_saturacion`:** si algún camino sube de 0 dB (`nivel` + ganancia + las bandas positivas del EQ), todo baja lo mismo (un *preamp*) para que ninguno pase de 0 dB, y se avisa. No es un limitador: si un bus ya llega saturado, sigue saturado.
- **Cada valor es un control con nombre** (`fl_ganancia:Mult`, `fr_retardo:Delay (s)`, `sub_suma:Gain 1`…), así que casi todo se cambia en vivo con `pw-cli set-param`, sin reiniciar.
- **Lo que no se calibra:** Chat (va a los audífonos), la mezcla de los visualizadores (graba los buses antes de la calibración) y «solo audífonos».

## Antes de empezar

1. **ELENA aplicado** y los jacks en su lugar: `mise run probar:canales` (una voz en cada bocina).
2. **La perilla del amplificador** donde la uses siempre, y anótala en `[maximo] perilla`: el volumen máximo y el tope valen para esa posición. Si la mueves, repite las pruebas de volumen.
3. **Un mic en el punto de escucha** (opcional, `--mic`): el de `ELENA_MIC_FISICO`. No hace falta que sea de medición: es el mismo y en el mismo lugar para todas las bocinas, así que la comparación entre ellas vale.
4. **Música en 5.1** para `comparar` y `musica` (en el bus Música).

Las pruebas van **directo a la tarjeta** cuando hace falta pasar del volumen del bus, con topes: nunca más de −1 dBFS ni de 30 dB sobre tu nivel de música de hoy. Cada una guarda su resultado en `.cache/calibracion/`.

## Las pruebas, en orden

| # | Prueba | Qué da | Dónde va |
|---|---|---|---|
| 1 | `mise run calibracion -- volumen` | El volumen más alto con el que estás cómodo, por bocina | Lo usa la prueba 2 |
| 2 | `mise run calibracion -- satelites [--mic]` | Hasta qué tan grave llega cada satélite | `[cruce] frecuencia` |
| 3 | `mise run calibracion -- nivelar [--mic]` | El balance entre las satélites, desde neutro | `[bocinas]` (fr, fc, rl, rr) |
| 4 | `mise run calibracion -- comparar` | Tu 100 %, el sub y tu máximo, con música | `nivel`, `[bocinas] sub` y `[maximo] tope` |
| 5 | `mise run calibracion -- musica` (opcional) | Si el sub aguanta tu volumen con música real | — |

### 1. Volumen máximo

Ráfagas de 2 s de ruido rosa por cada bocina (las satélites de 200 Hz para arriba; el sub, de 30 a 120 Hz). Empieza en tu nivel de música de hoy y solo sube cuando presionas `+`, de 2 en 2 dB, después de oír al menos 1 s el nivel anterior. **Enter:** «es lo más fuerte con lo que estoy cómodo». **Espacio:** «ya vibra o distorsiona» (vale el paso anterior). Es tu volumen ideal «a todo volumen»: los picos de una canción pueden pasarlo.

### 2. Las satélites

Por cada satélite, tonos de 3 s de 200 Hz hacia abajo, cada 10 Hz, al volumen de la prueba 1. Presiona espacio en el primer paso donde el tono **deja de oírse**, cae de golpe, vibra, zumba o suena más agudo de lo que debería (los armónicos de la bocina). Bajando, todo suena un poco más bajo porque el oído es menos sensible en graves: lo que cuenta es la caída brusca.

- **Al final:** el límite de cada bocina. Si coinciden, un solo cruce un paso arriba del límite más alto. Si una bocina difiere mucho de su gemela, puede estar dañada, mal conectada o tapada.
- **Cruce por bocina:** `cruce = <Hz>` en `[bocinas]` gana sobre `[cruce] frecuencia`.
- **Con `--mic`:** mide además el nivel y la distorsión (armónicos 2f y 3f) de cada tono, y si todavía se distingue del ruido de fondo. `--canal LFE` (solo con `--mic`) usa el sub como referencia del mic en graves.

### 3. Nivelar

Ruido rosa de 500 Hz a 2 kHz (la banda que usan los receptores: sin los modos de la sala), 1.5 s en FL y 1.5 s en la otra bocina, en bucle. Con `+` y `−` ajustas la otra hasta que suenen igual; Enter la anota.

- **A ciegas:** cada bocina empieza con un desfase al azar de ±6 dB y no ves números hasta el final. Dos rondas y una tercera para las que difieran más de 2 dB; vale la mediana.
- **Con `--mic`:** el mic mide cada bocina con la misma señal y lo compara con lo que oíste; avisa si difieren más de 3 dB.
- **El sub no entra:** con ruido no se nivela bien. Su ganancia sale de `comparar`.
- El resultado (la ganancia de cada satélite sobre FL) va en `[bocinas]`.

### 4. Comparar: tu 100 %, el sub y tu máximo

Con música 5.1 en el bus Música, todo absoluto: lo que tenga hoy el bus no cuenta (al salir regresa exactamente a como estaba; si el proceso muere, `--restaurar`). Tres fases por ronda, a ciegas:

1. **Tu 100 %**, tu volumen de todos los días: empieza en un nivel al azar; `+` y `−` hasta que sea ese.
2. **El sub**, a tu 100 %: empieza con un desfase al azar; hasta que los graves estén bien.
3. **Tu máximo**, lo más fuerte con lo que estás cómodo.

Dos rondas y una tercera si algo difiere más de 3 dB. El resultado da `nivel` (tu 100 %), la ganancia del sub (sobre el LFE +10) y el tope (`[maximo] tope`, en dB sobre tu 100 %). Avisa si la canción es estéreo: tu 100 % se define con 5.1.

### 5. El sub con música (opcional)

`mise run calibracion -- musica`: pones una canción de muchos graves y subes el bus de 2 en 2 dB, viendo el pico que le llega al sub. Sirve para saber si el sub limita tu volumen: con ruido rosa continuo suele distorsionar mucho antes que con el LFE de una canción.

## Escribir y activar

Pasa los resultados a `local/calibracion.toml` (cada prueba los muestra al final), y luego:

```sh
mise run calibracion -- generar      # valida y escribe el .conf (no cambia el sonido todavía)
mise run calibracion -- activar      # los buses de bocinas → elena_salida
mise run aplicar                     # con la VM apagada
mise run calibracion -- estado       # si está activa, sus valores y si elena_salida está bien
```

`aplicar` deja `elena_salida` al 100 % y pasa los buses de bocinas a un solo volumen (el balance va en la calibración), con respaldo del balance anterior en `.cache/calibracion/buses-antes.json`, que regresa al desactivar.

**Retardos:** por bocina, `retardo` en ms o `distancia` en metros al punto de escucha (una u otra). Con distancias, cada bocina se retrasa (la más lejana − la suya) / 343 m/s.

## Afinar en vivo

| Para… | Corre |
|---|---|
| Afinar al oído con música, con A/B contra neutro | `mise run calibracion -- afinar` (o `ab`): elige la bocina, `+`/`−` 0.5 dB, `a` alterna calibrado ↔ neutro, `g` guarda |
| Cambiar un valor | `mise run calibracion -- fl ganancia 1.5` · `rr distancia 2.4` · `nivel -20` · `lfe 10` · `cruce 90` |
| El EQ | `mise run calibracion -- eq` (lo muestra) · `eq agregar pico 120 -3 --q 2` · `eq 1 ganancia -2` · `eq quitar 1` · `eq limpiar` · con `--sub`, el del sub |
| El EQ con su curva | En la consola, `a` |

Todo suena al instante y se guarda en el TOML (conserva tus comentarios). Para valores negativos: `mise run elena -- calibracion nivel -20` (si no, mise toma el `-20` como una opción).

## El tope de volumen

Con la calibración activa, **Música y Juegos no suben más allá de `[maximo] tope`** (en dB sobre tu 100 %; +8 dB son 136 %). Lo respetan la CLI, las escenas, la consola y el control remoto, y el vigilante regresa al tope lo que suba desde fuera (pavucontrol, las teclas de volumen). Si un bus ya estaba arriba, no lo baja de golpe: solo no lo deja subir más. Sistema no lleva tope. Sin `tope` en el TOML, no hay tope.

## Compensación estéreo

Las mezclas multicanal (Atmos) suelen masterizarse mucho más bajo que las estéreo: una canción estéreo puede llegar ~12 dB más fuerte. Con `[estereo]`, el vigilante detecta por la señal cuándo un bus suena en estéreo (FC, LFE y traseras en silencio digital) y baja su salida, en rampa:

```toml
[estereo]
compensacion = -12.0           # dB mientras suena estéreo
buses = ["musica", "sistema"]  # Sistema es estéreo siempre: va compensado siempre
sistema = -10.0                # opcional: uno propio por bus
```

En vivo: `mise run elena -- calibracion estereo -12` (o `--bus sistema`). Tu volumen, la consola y los medidores no cambian: baja la salida del bus, no el bus. Con `compensacion = 0.0` no hace nada.

## Problemas

- **Las bocinas no suenan después de `aplicar`:** no apareció `elena_salida`. `mise run calibracion -- estado` lo dice; revisa `journalctl --user -u pipewire` o desactívala (`desactivar` y `aplicar`).
- **«Un bus calibrado con volumen por canal»:** el balance se aplicaría dos veces. Pon el bus en un solo volumen, o `mise run aplicar`.
- **Satura en el máximo:** baja `nivel` o el tope, o deja `evitar_saturacion = true`. No hay limitador: el `filter-chain` no trae uno.
