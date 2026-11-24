# ELENA: deja lista la VM de Windows 10 IoT LTSC 2021 para Apple Music en Atmos.
# Guía completa: docs/guias/vm-apple-music-atmos.md. Se corre como administrador; instalar.cmd
# lo eleva. Hace, en orden:
#   1. Región de Windows: si quedó en «Latinoamérica y el Caribe» (no es un país; la Tienda
#      pide el códec con market=419 y Microsoft responde 404), la cambia a México.
#   2. Modo de desarrollador (para registrar Apple Music sin firma).
#   3. Dependencias del CD: VCLibs, .NET Native, UI.Xaml, Store Purchase App y Microsoft Store.
#   4. WebView2 Runtime (Apple Music lo necesita; LTSC no lo trae).
#   5. Apple Music, ya preparada desde Linux (vm/preparar_instaladores.py): .msix x64 extraído,
#      sin firma y con MinVersion 10.0.19044.0.
#   6. Abre la Tienda en el códec Dolby Digital Plus: hay que obtenerlo de ahí (gratis) para que
#      tenga licencia. Instalado desde el archivo falla con 0x87E10BC6.
$ErrorActionPreference = 'Stop'
$origen  = $PSScriptRoot
$destino = 'C:\Apps\AppleMusic'
$codecDolby = '9NVJQJBDKN97'   # «Dolby Digital Plus decoder for PC OEMs» en la Tienda

function Paso($texto) { Write-Host ''; Write-Host "== $texto" -ForegroundColor Cyan }

$build = [Environment]::OSVersion.Version.Build
Paso "Windows: compilación $build"
if ($build -lt 19044) { throw 'Hace falta la compilación 19044 o mayor (Windows 10 LTSC 2021).' }

Paso 'Región de Windows (la Tienda la usa como «mercado»)'
# 161832257 = «Latinoamérica y el Caribe» (market=419): la Tienda no encuentra el códec ahí.
# 166 = México (market=MX). Las demás regiones se dejan como están.
$geo = (Get-WinHomeLocation).GeoId
if ($geo -eq 161832257) {
  Set-WinHomeLocation -GeoId 166
  Write-Host '  era «Latinoamérica y el Caribe»; ahora México' -ForegroundColor Yellow
} else {
  Write-Host "  $((Get-WinHomeLocation).HomeLocation) (GeoId $geo)"
}

Paso 'Modo de desarrollador (para registrar Apple Music sin firma)'
$clave = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
if (-not (Test-Path $clave)) { New-Item -Path $clave -Force | Out-Null }
New-ItemProperty -Path $clave -Name AllowDevelopmentWithoutDevLicense -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $clave -Name AllowAllTrustedApps -Value 1 -PropertyType DWord -Force | Out-Null
Write-Host '  activado'

Paso 'Dependencias y Microsoft Store'
# Primero las bibliotecas base (VCLibs, .NET Native, UI.Xaml), de las que dependen Apple Music
# y la Tienda; después las apps (Store Purchase App, Microsoft Store)
$paquetes = Get-ChildItem -Path (Join-Path $origen 'dependencias') -File |
  Sort-Object @{ Expression = { if ($_.Name -match '^Microsoft\.(VCLibs|NET\.Native|UI\.Xaml)') { 0 } else { 1 } } }, Name
foreach ($p in $paquetes) {
  Write-Host "  $($p.Name)"
  try {
    Add-AppxPackage -Path $p.FullName -ErrorAction Stop
    Write-Host '    instalado' -ForegroundColor Green
  } catch {
    # Ya instalado (igual o mayor) o no aplica a esta versión de Windows: se sigue
    $linea = ($_.Exception.Message -split "`r?`n")[0]
    Write-Host "    AVISO: $linea" -ForegroundColor Yellow
  }
}

Paso 'WebView2 Runtime (Apple Music lo necesita; Windows 10 LTSC no lo trae)'
# Instalado a nivel de máquina si EdgeUpdate tiene su versión (pv) registrada
$wv2 = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$pv = (Get-ItemProperty -Path $wv2 -Name pv -ErrorAction SilentlyContinue).pv
if ($pv -and $pv -ne '0.0.0.0') {
  Write-Host "  ya instalado ($pv)"
} else {
  $instalador = Get-ChildItem -Path (Join-Path $origen 'extras') -Filter 'MicrosoftEdgeWebView2RuntimeInstaller*.exe' -File |
    Select-Object -First 1
  if (-not $instalador) { throw 'Falta el instalador de WebView2 en extras\ (vuelve a correr mise run vm:instaladores).' }
  Write-Host "  instalando $($instalador.Name)…"
  $proc = Start-Process -FilePath $instalador.FullName -ArgumentList '/silent', '/install' -Wait -PassThru
  $pv = (Get-ItemProperty -Path $wv2 -Name pv -ErrorAction SilentlyContinue).pv
  if (-not $pv) { throw "WebView2 no quedó instalado (código $($proc.ExitCode))." }
  Write-Host "  instalado ($pv)" -ForegroundColor Green
}

Paso "Apple Music → $destino"
Get-AppxPackage -Name 'AppleInc.AppleMusicWin' | Remove-AppxPackage
if (Test-Path $destino) { Remove-Item -Path $destino -Recurse -Force }
# robocopy: copia recursiva fiable (nombres raros) y /A-:R quita el solo-lectura del CD
& robocopy (Join-Path $origen 'AppleMusic') $destino /E /A-:R /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy falló con código $LASTEXITCODE" }
Write-Host '  copiado'

Paso 'Registrar Apple Music'
Add-AppxPackage -Path (Join-Path $destino 'AppxManifest.xml') -Register
$app = Get-AppxPackage -Name 'AppleInc.AppleMusicWin'
if (-not $app) { throw 'Apple Music no quedó registrado.' }
Write-Host "  OK: $($app.Name) $($app.Version)" -ForegroundColor Green

Paso 'Códec Dolby Digital Plus (desde la Tienda, con licencia)'
$dolby = Get-AppxPackage -Name 'DolbyLaboratories.DolbyDigitalPlusDecoderOEM'
if ($dolby) {
  Write-Host "  ya instalado ($($dolby.Version)). Si extras\prueba-eac3-51.mp4 no suena, quítalo y obténlo de nuevo:"
  Write-Host '    Get-AppxPackage *DolbyDigitalPlusDecoderOEM* | Remove-AppxPackage'
  Write-Host "    start ms-windows-store://pdp/?ProductId=$codecDolby"
} else {
  Write-Host '  Se abre la Tienda en el códec: presiona «Obtener» (es gratis).'
  Start-Process "ms-windows-store://pdp/?ProductId=$codecDolby"
}

Paso 'Siguiente'
Write-Host '  1. Con el códec ya obtenido, abre extras\prueba-eac3-51.mp4 en el Reproductor de Windows'
Write-Host '     Media: deben sonar 6 tonos, uno por bocina.'
Write-Host '  2. Abre Apple Music, inicia sesión y en Configuración → Reproducción pon Dolby Atmos: Siempre.'
Write-Host '  3. Reproduce una pista con el distintivo Dolby Atmos y, en Linux:'
Write-Host '       mise run medir:canales elena_musica --segundos 30'
