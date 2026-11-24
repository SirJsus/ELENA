# ELENA: prueba el puerto serie COM1, que QEMU conecta a
# .cache/vm/musica.sock en Linux. Clic derecho → «Ejecutar con PowerShell»; no necesita
# administrador ni cambia nada. Pasos:
#   1. Sin nadie del otro lado, escribe 20 KB: QEMU debería descartarlos sin trabar a Windows.
#   2. Pide correr en Linux `mise run vm:prueba-serie` y manda «hola» cada 2 s hasta que conteste.
#   3. Windows → Linux: 100 KB (base64 en una línea JSON, como iría la carátula); Linux mide.
#   4. Linux → Windows: 10 KB; aquí se mide y se comprueba.
# El reporte queda en el Escritorio y en la memoria ELENA-INT (Linux: `mise run vm:intercambio`).
$r = New-Object System.Collections.Generic.List[string]
function L($t, $color = 'Gray') { Write-Host $t -ForegroundColor $color; $r.Add($t) }
function Sha1($s) {
  -join ([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::ASCII.GetBytes($s)) | ForEach-Object { $_.ToString('x2') })
}
function Num($x) { $x.ToString('0.###', [Globalization.CultureInfo]::InvariantCulture) }
# La siguiente línea JSON de tipo $tipo (las demás se saltan), o $null si pasan $segundos
function Esperar($tipo, $segundos) {
  $limite = [DateTime]::Now.AddSeconds($segundos)
  while ([DateTime]::Now -lt $limite) {
    try { $t = $port.ReadLine() } catch [TimeoutException] { continue }
    try { $m = $t | ConvertFrom-Json } catch { L "   (línea que no es JSON: $($t.Length) caracteres)"; continue }
    if ($m.t -eq $tipo) { return $m }
  }
  $null
}
function Terminar($codigo) {
  if ($port -and $port.IsOpen) { $port.Close() }
  $nombre = "elena-prueba-serie-$env:COMPUTERNAME.txt"
  $destinos = @(Join-Path ([Environment]::GetFolderPath('Desktop')) $nombre)
  $letra = (Get-Volume -FileSystemLabel 'ELENA-INT' -ErrorAction SilentlyContinue | Select-Object -First 1).DriveLetter
  if ($letra) { $destinos += "${letra}:\$nombre" }
  foreach ($f in $destinos) { $r | Set-Content -Path $f -Encoding UTF8; Write-Host "Reporte guardado en $f" -ForegroundColor Green }
  Read-Host 'Enter para cerrar'
  exit $codigo
}

L "ELENA, prueba del puerto serie (lado Windows): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
try {
  $port = New-Object System.IO.Ports.SerialPort 'COM1', 115200, 'None', 8, 'One'
  $port.Encoding = [Text.Encoding]::UTF8
  $port.NewLine = "`n"
  $port.ReadBufferSize = 65536
  $port.ReadTimeout = 2000
  $port.WriteTimeout = 30000
  $port.Open()
  L 'COM1 abierto: 115200, 8N1, sin control de flujo'
} catch { L "COM1 no abre: $($_.Exception.Message)" Red; Terminar 1 }

L '1. Sin nadie en Linux: 20 líneas de ~1 KB…'
$relleno = 'x' * 1000
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
  for ($i = 1; $i -le 20; $i++) { $port.WriteLine('{"t":"relleno","i":' + $i + ',"x":"' + $relleno + '"}') }
  while ($port.BytesToWrite -gt 0 -and $sw.Elapsed.TotalSeconds -lt 30) { Start-Sleep -Milliseconds 20 }
  $s = $sw.Elapsed.TotalSeconds
  L ('   {0:N2} s ({1:N1} KB/s); quedaron {2} bytes por escribir' -f $s, (20 * 1035 / 1KB / [math]::Max($s, 0.001)), $port.BytesToWrite)
} catch { L "   Falló o se trabó a los $($sw.Elapsed.TotalSeconds.ToString('N1')) s: $($_.Exception.Message)" Red }

L '2. Ahora corre en Linux:  mise run vm:prueba-serie' Yellow
$enviados = @{}
$hola = $null
$n = 0
$limite = [DateTime]::Now.AddMinutes(10)
while (-not $hola -and [DateTime]::Now -lt $limite) {
  $n++
  $enviados[$n] = [Diagnostics.Stopwatch]::StartNew()
  $port.WriteLine('{"t":"hola","de":"windows","v":1,"n":' + $n + '}')
  try { $t = $port.ReadLine() } catch [TimeoutException] { continue }
  try { $m = $t | ConvertFrom-Json; if ($m.t -eq 'hola') { $hola = $m } } catch {}
}
if (-not $hola) { L '   Linux no contestó en 10 minutos.' Red; Terminar 1 }
$eco = [int]$hola.eco
$ida = if ($enviados.ContainsKey($eco)) { '{0:N0} ms' -f $enviados[$eco].Elapsed.TotalMilliseconds } else { '?' }
L "   Linux contestó al «hola» n=${eco}: ida y vuelta $ida"

L '3. Windows → Linux: 100 KB…'
$datos = New-Object byte[] 76800
(New-Object Random 4).NextBytes($datos)
$b64 = [Convert]::ToBase64String($datos)
$linea = '{"t":"bloque","sha1":"' + (Sha1 $b64) + '","datos":"' + $b64 + '"}'
$port.WriteTimeout = 120000
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
  $port.WriteLine('{"t":"inicio"}')
  $port.WriteLine($linea)
  while ($port.BytesToWrite -gt 0) { Start-Sleep -Milliseconds 10 }
  L ('   Windows lo escribió en {0:N2} s ({1} bytes)' -f $sw.Elapsed.TotalSeconds, ($linea.Length + 1))
} catch { L "   Falló al escribir: $($_.Exception.Message)" Red; Terminar 1 }
$res = Esperar 'resultado' 180
if (-not $res) { L '   Linux no mandó el resultado.' Red; Terminar 1 }
L ('   Linux lo recibió: {0} bytes en {1} s ({2:N1} KB/s), sha1 {3}' -f $res.bytes, $res.segundos,
   ($res.bytes / 1KB / [math]::Max([double]$res.segundos, 0.001)), $(if ($res.sha1_ok) { 'bien' } else { 'MAL' })) $(if ($res.sha1_ok) { 'Green' } else { 'Red' })

L '4. Linux → Windows: 10 KB…'
if (-not (Esperar 'inicio' 30)) { L '   Linux no empezó a mandar.' Red; Terminar 1 }
$sw = [Diagnostics.Stopwatch]::StartNew()
$blq = Esperar 'bloque' 120
$s = $sw.Elapsed.TotalSeconds
if (-not $blq) { L '   No llegó el bloque de Linux.' Red; Terminar 1 }
$ok = (Sha1 $blq.datos) -eq $blq.sha1
L ('   Llegaron {0} bytes de base64 en {1:N2} s ({2:N1} KB/s), sha1 {3}' -f $blq.datos.Length, $s,
   ($blq.datos.Length / 1KB / [math]::Max($s, 0.001)), $(if ($ok) { 'bien' } else { 'MAL' })) $(if ($ok) { 'Green' } else { 'Red' })
$port.WriteLine('{"t":"recibido","bytes":' + $blq.datos.Length + ',"segundos":' + (Num $s) + ',"sha1_ok":' + "$ok".ToLower() + '}')
Start-Sleep -Milliseconds 500
L 'Prueba completa.' Green
Terminar 0
