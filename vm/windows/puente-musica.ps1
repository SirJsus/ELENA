# ELENA: puente de música. Publica por COM1 lo que suena en Apple Music y ejecuta las
# órdenes que llegan de vuelta (play/pausa, siguiente, anterior). QEMU conecta COM1 a
# .cache/vm/musica.sock en Linux, donde `elena vm musica` lo convierte en un reproductor MPRIS
# (Noctalia, teclas multimedia). Guía: docs/guias/vm-apple-music-atmos.md
# pulir.ps1 lo copia a C:\Apps\ELENA\ y lo arranca oculto al iniciar sesión.
#
# Todo va en un solo hilo (los eventos de WinRT y de SerialPort en PowerShell 5.1 corren en otros
# hilos y son frágiles):
#   - cada 100 ms lee las órdenes de Linux;
#   - cada 500 ms consulta los controles multimedia de Windows (SMTC) y manda solo lo que cambió
#     (cada 2 s mientras Apple Music no tenga sesión);
#   - un latido cada 5 s, para que Linux sepa que sigue vivo.
# Protocolo: JSON por líneas, versión 1 (ver el plan). Si Linux manda {"t":"hola"} (al
# conectarse), se le manda todo otra vez: lo que Windows escribe sin nadie conectado, QEMU lo tira.
# Es un proceso aparte: si falla, Apple Music y el apagado siguen igual. Nunca se cierra solo; si
# COM1 no abre, lo reintenta cada 10 s. Registro: puente-musica.log, junto al script (tope 512 KB).
#   -Consola  también escribe el registro en pantalla (para probarlo a mano desde el CD)
param([string]$Puerto = 'COM1', [switch]$Consola)
$ErrorActionPreference = 'Stop'

$registro = Join-Path $PSScriptRoot 'puente-musica.log'
function Log($texto) {
  $linea = '{0:yyyy-MM-dd HH:mm:ss.fff} {1}' -f (Get-Date), $texto
  if ($Consola) { Write-Host $linea }
  try {
    if ((Test-Path $registro) -and (Get-Item $registro).Length -gt 512KB) { Move-Item $registro "$registro.1" -Force }
    Add-Content -Path $registro -Value $linea -Encoding UTF8
  } catch {}
}
function Falla($e) { $b = $e.Exception.GetBaseException(); '{0}: {1}' -f $b.GetType().Name, $b.Message }

# Una sola copia: COM1 solo lo puede abrir un proceso
$nueva = $false
$mutex = [System.Threading.Mutex]::new($true, 'Local\ELENA-puente-musica', [ref]$nueva)
if (-not $nueva) { Log 'Ya hay otro puente corriendo; este se cierra.'; exit 0 }

# WinRT desde PowerShell 5.1: sin await, las operaciones asíncronas se esperan con AsTask
Add-Type -AssemblyName System.Runtime.WindowsRuntime, System.Drawing
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Esperar($op, [Type]$tipo, $ms = 3000) {
  $t = $asTask.MakeGenericMethod($tipo).Invoke($null, @($op))
  if (-not $t.Wait($ms)) { throw "sin respuesta en $ms ms ($($tipo.Name))" }
  $t.Result
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IInputStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$tGestor = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
$tDatos = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$tFlujo = [Windows.Storage.Streams.IRandomAccessStreamWithContentType]
$comoFlujo = [System.IO.WindowsRuntimeStreamExtensions].GetMethod('AsStreamForRead', [Type[]]@([Windows.Storage.Streams.IInputStream]))
# Carátula: como máximo 300×300 en JPEG (Apple Music la da en 1200×1200, 263 a 483 KB)
$LADO = 300
$jpeg = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg'
$calidad = New-Object System.Drawing.Imaging.EncoderParameters 1
$calidad.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, [long]85)

# ---------- Puerto serie ----------
$port = $null
function Abrir {
  try {
    $p = New-Object System.IO.Ports.SerialPort $Puerto, 115200, 'None', 8, 'One'
    $p.Encoding = New-Object System.Text.UTF8Encoding $false
    $p.NewLine = "`n"
    $p.ReadBufferSize = 65536
    $p.WriteTimeout = 5000
    $p.Open()
    $script:port = $p
    Log "$Puerto abierto"
    return $true
  } catch { Log "$Puerto no abre: $(Falla $_); se reintenta en 10 s"; return $false }
}
function Cerrar {
  try { $port.Close() } catch {}
  $script:port = $null
  $script:proxPuerto = $reloj.ElapsedMilliseconds + 10000
}
function Mandar($mensaje) {
  if (-not $port) { return }
  try { $port.WriteLine(($mensaje | ConvertTo-Json -Compress -Depth 4)) }
  catch { Log "No se pudo escribir en ${Puerto}: $(Falla $_)"; Cerrar }
}

# ---------- Apple Music por SMTC ----------
$gestor = $null
function Sesion {
  if (-not $gestor) { $script:gestor = Esperar ($tGestor::RequestAsync()) $tGestor }
  foreach ($s in $gestor.GetSessions()) {
    # Apple Music publica como «AppleMusic.exe», aunque se abra por su paquete
    $id = $s.SourceAppUserModelId
    if ($id -eq 'AppleMusic.exe' -or $id -like 'AppleInc.AppleMusicWin*') { return $s }
  }
  $null
}
function Saludar {
  $app = $null
  try { $s = Sesion; if ($s) { $app = $s.SourceAppUserModelId } } catch {}
  Mandar ([ordered]@{ t = 'hola'; v = 1; app = $app; agente = 'puente-musica.ps1' })
}
# Lee el estado de la sesión como lo pide el protocolo; deja la miniatura en $script:miniatura
function Leer($s) {
  $p = Esperar ($s.TryGetMediaPropertiesAsync()) $tDatos
  $script:miniatura = $p.Thumbnail
  $i = $s.GetPlaybackInfo()
  $c = $i.Controls
  $t = $s.GetTimelineProperties()
  $estado = switch ([string]$i.PlaybackStatus) { 'Playing' { 'reproduciendo' } 'Paused' { 'pausa' } default { 'detenido' } }
  $duracion = [long]($t.EndTime - $t.StartTime).TotalMilliseconds
  # Apple Music actualiza la posición cada ~1 s, en segundos enteros: se le suma lo que pasó desde
  # entonces si está sonando. Justo al reanudar, la última actualización todavía es de antes de la
  # pausa (probado: daba 31 s en lugar de 27): entonces se cuenta desde que empezó a sonar.
  $posicion = ($t.Position - $t.StartTime).TotalMilliseconds
  if ($estado -eq 'reproduciendo') {
    $ahora = [DateTimeOffset]::Now
    if (-not $sonandoDesde) { $script:sonandoDesde = $ahora }
    $base = $t.LastUpdatedTime
    if ($base.Year -lt 2000 -or $base -lt $sonandoDesde.AddMilliseconds(-1200)) { $base = $sonandoDesde }
    $velocidad = if ($i.PlaybackRate) { [double]$i.PlaybackRate } else { 1.0 }
    $posicion += ($ahora - $base).TotalMilliseconds * $velocidad
  } else { $script:sonandoDesde = $null }
  if ($duracion -gt 0) { $posicion = [math]::Min($posicion, $duracion) }
  [ordered]@{
    t = 'estado'; estado = $estado
    titulo = [string]$p.Title; artista = [string]$p.Artist; album = [string]$p.AlbumTitle
    artista_album = [string]$p.AlbumArtist; pista = [int]$p.TrackNumber
    duracion_ms = $duracion; posicion_ms = [long][math]::Max(0, $posicion)
    caratula = $null
    puede = [ordered]@{
      play = [bool]$c.IsPlayEnabled; pausa = [bool]$c.IsPauseEnabled; alternar = [bool]$c.IsPlayPauseToggleEnabled
      detener = [bool]$c.IsStopEnabled; siguiente = [bool]$c.IsNextEnabled; anterior = [bool]$c.IsPreviousEnabled
      posicion = [bool]$c.IsPlaybackPositionEnabled }
  }
}
# La miniatura, reducida a $LADO en JPEG: @{ id = sha1; datos = base64 }
function Caratula($mini) {
  $flujo = Esperar ($mini.OpenReadAsync()) $tFlujo
  $lector = $comoFlujo.Invoke($null, @($flujo))
  $ms = New-Object System.IO.MemoryStream
  try { $lector.CopyTo($ms) } finally { $lector.Dispose() }
  $ms.Position = 0
  $img = [System.Drawing.Image]::FromStream($ms)
  try {
    $escala = [math]::Min(1.0, $LADO / [double][math]::Max($img.Width, $img.Height))
    $ancho = [math]::Max(1, [int][math]::Round($img.Width * $escala))
    $alto = [math]::Max(1, [int][math]::Round($img.Height * $escala))
    $bmp = New-Object System.Drawing.Bitmap $ancho, $alto
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.DrawImage($img, 0, 0, $ancho, $alto)
    $g.Dispose()
    $salida = New-Object System.IO.MemoryStream
    $bmp.Save($salida, $jpeg, $calidad)
    $bmp.Dispose()
    $bytes = $salida.ToArray()
  } finally { $img.Dispose() }
  $sha = -join ([System.Security.Cryptography.SHA1]::Create().ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })
  @{ id = $sha; datos = [Convert]::ToBase64String($bytes) }
}

# Lo último que se mandó, para mandar solo lo que cambia
$haySesion = $null      # $true, $false o $null (todavía no se dice)
$ultimo = $null         # último «estado» mandado
$ultimaFirma = $null    # el mismo estado sin la posición
$desde = $null          # cronómetro desde que se mandó $ultimo
$cancion = $null        # título, artista, álbum y duración de la canción actual
$caratula = $null       # carátula de la canción actual
$caratulaMandada = $null
$intentos = 0           # lecturas fallidas de la carátula de esta canción
$miniatura = $null
$sonandoDesde = $null   # cuándo vio el puente que empezó a sonar (para la posición al reanudar)

function Consultar {
  try { $s = Sesion } catch { Log "SMTC no responde: $(Falla $_)"; $script:gestor = $null; $s = $null }
  if (-not $s) {
    if ($haySesion -ne $false) {
      Mandar ([ordered]@{ t = 'sin_sesion' })
      Log 'Apple Music sin sesión'
      $script:haySesion = $false; $script:ultimo = $null; $script:ultimaFirma = $null
      $script:cancion = $null; $script:caratulaMandada = $null
    }
    return
  }
  if ($haySesion -ne $true) { Log "Sesión de Apple Music: $($s.SourceAppUserModelId)"; $script:haySesion = $true }
  try { $e = Leer $s } catch { Log "No se pudo leer el estado: $(Falla $_)"; return }

  $clave = "$($e.titulo)`n$($e.artista)`n$($e.album)`n$($e.duracion_ms)"
  if ($clave -ne $cancion) {
    $script:cancion = $clave; $script:caratula = $null; $script:intentos = 0
    Log "Canción: $($e.titulo) · $($e.artista)"
  }
  if (-not $caratula -and $miniatura -and $intentos -lt 10) {
    try { $script:caratula = Caratula $miniatura }
    catch { $script:intentos++; Log "Carátula (intento $intentos): $(Falla $_)" }
  }
  if ($caratula) {
    $e.caratula = $caratula.id
    if ($caratula.id -ne $caratulaMandada) {
      Mandar ([ordered]@{ t = 'caratula'; id = $caratula.id; tipo = 'image/jpeg'; datos = $caratula.datos })
      $script:caratulaMandada = $caratula.id
    }
  }

  $firma = (@($e.estado, $e.titulo, $e.artista, $e.album, $e.artista_album, $e.pista, $e.duracion_ms, $e.caratula) +
            @($e.puede.Values)) -join "`n"
  $mandar = $firma -ne $ultimaFirma
  if (-not $mandar -and $ultimo) {
    # La posición cuenta como cambio solo si se aleja más de 1 s de la que Linux calcula
    $esperada = $ultimo.posicion_ms
    if ($ultimo.estado -eq 'reproduciendo') { $esperada += $desde.ElapsedMilliseconds }
    if ($e.duracion_ms -gt 0) { $esperada = [math]::Min($esperada, $e.duracion_ms) }
    $mandar = [math]::Abs($e.posicion_ms - $esperada) -gt 1000
  }
  if ($mandar) {
    Mandar $e
    $script:ultimo = $e; $script:ultimaFirma = $firma
    $script:desde = [System.Diagnostics.Stopwatch]::StartNew()
  }
}

function Ordenar($m) {
  $s = $null
  try { $s = Sesion } catch {}
  if (-not $s) { Log "Orden «$($m.orden)» sin sesión de Apple Music"; return }
  $op = switch ([string]$m.orden) {
    'alternar'  { $s.TryTogglePlayPauseAsync() }
    'play'      { $s.TryPlayAsync() }
    'pausa'     { $s.TryPauseAsync() }
    'detener'   { $s.TryStopAsync() }
    'siguiente' { $s.TrySkipNextAsync() }
    'anterior'  { $s.TrySkipPreviousAsync() }
    'posicion'  { $s.TryChangePlaybackPositionAsync([long]$m.ms * 10000) }  # en unidades de 100 ns
    default     { $null }
  }
  if (-not $op) { Log "Orden desconocida: $($m.orden)"; return }
  try {
    if (Esperar $op ([bool]) 2000) { Log "Orden: $($m.orden)" } else { Log "Apple Music no aceptó la orden «$($m.orden)»" }
  } catch { Log "Orden «$($m.orden)» falló: $(Falla $_)" }
}

function Atender($linea) {
  try { $m = $linea | ConvertFrom-Json } catch {
    Log "Línea que no se entiende: $($linea.Substring(0, [math]::Min(200, $linea.Length)))"; return
  }
  switch ([string]$m.t) {
    'hola' {
      Log "Linux saludó (v$($m.v)): se manda todo otra vez"
      Saludar
      $script:haySesion = $null; $script:ultimo = $null; $script:ultimaFirma = $null; $script:caratulaMandada = $null
    }
    'orden' { Ordenar $m }
    default { Log "Mensaje que no se espera: $($m.t)" }
  }
}

# ---------- Ciclo principal ----------
$reloj = [System.Diagnostics.Stopwatch]::StartNew()
$proxPuerto = 0; $proxConsulta = 0; $proxLatido = 0
$pendiente = ''
Log "Puente de música iniciado (PowerShell $($PSVersionTable.PSVersion), $Puerto, PID $PID)"
while ($true) {
  try {
    $ahora = $reloj.ElapsedMilliseconds
    if (-not $port -and $ahora -ge $proxPuerto) {
      if (Abrir) { Saludar; $haySesion = $null; $ultimo = $null; $ultimaFirma = $null; $caratulaMandada = $null; $proxConsulta = 0 }
      else { $proxPuerto = $ahora + 10000 }
    }
    if ($port) {
      try { if ($port.BytesToRead -gt 0) { $pendiente += $port.ReadExisting() } }
      catch { Log "No se pudo leer de ${Puerto}: $(Falla $_)"; Cerrar }
      while (($n = $pendiente.IndexOf("`n")) -ge 0) {
        $linea = $pendiente.Substring(0, $n).Trim()
        $pendiente = $pendiente.Substring($n + 1)
        if ($linea) { Atender $linea; $proxConsulta = [math]::Min($proxConsulta, $ahora + 200) }
      }
      if ($pendiente.Length -gt 65536) { Log 'Línea de Linux demasiado larga: se descarta'; $pendiente = '' }
    }
    if ($port -and $ahora -ge $proxConsulta) {
      Consultar
      $proxConsulta = $ahora + $(if ($haySesion) { 500 } else { 2000 })
    }
    if ($port -and $ahora -ge $proxLatido) { Mandar ([ordered]@{ t = 'latido' }); $proxLatido = $ahora + 5000 }
  } catch { Log "Error inesperado: $(Falla $_)" }
  Start-Sleep -Milliseconds 100
}
