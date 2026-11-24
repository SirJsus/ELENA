# ELENA: deja la VM lista para el uso diario. Guía: docs/guias/vm-apple-music-atmos.md
# Se corre como administrador con la cuenta de todos los días (pulir.cmd lo eleva). Se puede
# repetir: lo que ya está hecho se salta. Hace, en orden:
#   1. Disco de música: el disco vacío (musica.qcow2) → GPT, NTFS, «Musica», letra M:, sin
#      indexado y excluido de Defender (~100 GB cifrados: revisarlos solo gasta CPU).
#   2. Energía: sin hibernación ni inicio rápido (apagar es apagar; borra hiberfil.sys), sin
#      suspensión, botón de encendido en «Apagar» (lo que manda `elena vm apagar` desde Linux)
#      y las apps se cierran sin preguntar al apagar.
#   3. Windows Update: sin reinicios automáticos con la sesión iniciada.
#   4. Sonidos de Windows: «Sin sonidos» (salían por el bus de música y abrían la tarjeta en estéreo).
#   5. Limpieza: componentes viejos de Windows Update (DISM /ResetBase), Liberador de espacio
#      con todas sus categorías, temporales, y TRIM de C: para que Linux recupere el espacio.
#   6. Inicio de sesión automático de esta cuenta.
#   7. Apple Music al iniciar sesión (C:\Apps\ELENA\apple-music.ps1): al cerrarla, Windows se apaga.
#   8. Puente de música (C, \Apps\ELENA\puente-musica.ps1): Apple Music en los widgets de
#      Linux, por COM1. Arranca oculto al iniciar sesión, y también al terminar este paso.
# Opciones:
#   -SinApagar            al cerrar Apple Music, Windows sigue encendido
#   -SinLimpieza          se salta el paso 5 (DISM tarda de 5 a 15 minutos)
#   -SinInicioAutomatico  se salta los pasos 6 y 7
#   -SinPuente            quita el puente de música
# Solo el puente, rápido y sin pedir la contraseña: pulir.cmd -SinLimpieza -SinInicioAutomatico
param([switch]$SinApagar, [switch]$SinLimpieza, [switch]$SinInicioAutomatico, [switch]$SinPuente)
$ErrorActionPreference = 'Stop'
$origen = $PSScriptRoot

function Paso($texto) { Write-Host ''; Write-Host "== $texto" -ForegroundColor Cyan }
function Aviso($texto) { Write-Host "  AVISO: $texto" -ForegroundColor Yellow }
function Ok($texto) { Write-Host "  $texto" -ForegroundColor Green }
function LibreC { [math]::Round((Get-PSDrive C).Free / 1GB, 1) }
function Registro($ruta, $nombre, $valor, $tipo = 'DWord') {
  if (-not (Test-Path $ruta)) { New-Item -Path $ruta -Force | Out-Null }
  New-ItemProperty -Path $ruta -Name $nombre -Value $valor -PropertyType $tipo -Force | Out-Null
}

$antes = LibreC
Write-Host "ELENA · pulir la VM · cuenta $env:USERNAME · C: con $antes GB libres"

Paso '1. Disco de música (M:)'
$m = Get-Volume -DriveLetter M -ErrorAction SilentlyContinue
if ($m) {
  Write-Host "  ya existe: M: «$($m.FileSystemLabel)», $([math]::Round($m.Size / 1GB)) GB, $([math]::Round($m.SizeRemaining / 1GB)) GB libres"
} else {
  $vacios = @(Get-Disk | Where-Object PartitionStyle -eq 'RAW')
  if ($vacios.Count -eq 1) {
    $d = $vacios[0]
    Write-Host "  disco $($d.Number): $($d.FriendlyName), $([math]::Round($d.Size / 1GB)) GB, vacío"
    Initialize-Disk -Number $d.Number -PartitionStyle GPT
    $p = New-Partition -DiskNumber $d.Number -UseMaximumSize -DriveLetter M
    Format-Volume -Partition $p -FileSystem NTFS -NewFileSystemLabel 'Musica' -Confirm:$false | Out-Null
    Ok 'M: «Musica» lista'
  } elseif ($vacios.Count -eq 0) {
    Aviso 'no hay un disco vacío. ¿Arrancaste la VM con mise run vm:arrancar? (crea .cache/vm/musica.qcow2)'
  } else {
    Aviso "hay $($vacios.Count) discos vacíos; no sé cuál es el de música. Formatéalo a mano como M: en Administración de discos."
  }
}
if (Get-Volume -DriveLetter M -ErrorAction SilentlyContinue) {
  $v = Get-CimInstance Win32_Volume -Filter "DriveLetter='M:'"
  try {
    if ($v.IndexingEnabled) { Set-CimInstance -InputObject $v -Property @{ IndexingEnabled = $false }; Write-Host '  indexado de M: apagado' }
  } catch { Aviso "indexado de M:: $($_.Exception.Message)" }
  try {
    if ((Get-MpPreference).ExclusionPath -notcontains 'M:\') { Add-MpPreference -ExclusionPath 'M:\'; Write-Host '  M:\ excluido de Defender' }
  } catch { Aviso "Defender: $($_.Exception.Message)" }
  New-Item -ItemType Directory -Path 'M:\Apple Music' -Force | Out-Null
  Write-Host '  carpeta para Apple Music: M:\Apple Music'
}

Paso '2. Energía'
powercfg /h off
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 3   # 3 = Apagar
powercfg /setactive SCHEME_CURRENT
# Al apagar, cerrar las apps sin el «Esta app impide el apagado» (que dejaría a Linux esperando)
Registro 'HKCU:\Control Panel\Desktop' 'AutoEndTasks' '1' 'String'
Ok 'sin hibernación ni suspensión; el botón de encendido apaga'

Paso '3. Windows Update sin reinicios automáticos con la sesión iniciada'
Registro 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoRebootWithLoggedOnUsers' 1
Ok 'listo (las actualizaciones se siguen instalando; el reinicio espera a que apagues)'

Paso '4. Sonidos de Windows: «Sin sonidos»'
Set-ItemProperty -Path 'HKCU:\AppEvents\Schemes' -Name '(Default)' -Value '.None'
Get-ChildItem 'HKCU:\AppEvents\Schemes\Apps' -Recurse |
  Where-Object PSChildName -eq '.Current' |
  ForEach-Object { Set-ItemProperty -Path $_.PSPath -Name '(Default)' -Value '' }
Ok 'listo'

if ($SinLimpieza) {
  Paso '5. Limpieza: omitida (-SinLimpieza)'
} else {
  Paso '5. Limpieza de C:'
  Write-Host '  DISM: componentes viejos de Windows Update (tarda de 5 a 15 minutos)…'
  Dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase /Quiet /NoRestart
  if ($LASTEXITCODE -ne 0) { Aviso "DISM terminó con código $LASTEXITCODE" }
  Write-Host '  Liberador de espacio, con todas las categorías…'
  $categorias = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches'
  Get-ChildItem $categorias | ForEach-Object { New-ItemProperty -Path $_.PSPath -Name StateFlags0042 -Value 2 -PropertyType DWord -Force | Out-Null }
  Start-Process cleanmgr.exe -ArgumentList '/sagerun:42' -Wait
  Write-Host '  temporales y descargas de Windows Update…'
  Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
  foreach ($c in "$env:windir\SoftwareDistribution\Download\*", "$env:windir\Temp\*", "$env:TEMP\*") {
    Remove-Item -Path $c -Recurse -Force -ErrorAction SilentlyContinue
  }
  Start-Service wuauserv -ErrorAction SilentlyContinue
  $descargas = Join-Path $env:USERPROFILE 'Downloads'
  $peso = (Get-ChildItem $descargas -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  if ($peso -gt 100MB) { Aviso "Descargas ocupa $([math]::Round($peso / 1GB, 1)) GB; revísala y borra lo que ya no uses (no se borra sola)." }
  # TRIM: avisa al disco virtual qué bloques quedaron libres, y el qcow2 se encoge en Linux
  $trim = (fsutil behavior query DisableDeleteNotify) -join ' '
  if ($trim -match 'NTFS DisableDeleteNotify = 1') { Aviso "TRIM desactivado en Windows ($trim); se activa:"; fsutil behavior set DisableDeleteNotify 0 }
  try { Optimize-Volume -DriveLetter C -ReTrim -Verbose }
  catch { Aviso "TRIM de C: falló ($($_.Exception.Message)). En Linux: compactar el qcow2 (ver la guía)." }
  Ok "C: pasó de $antes a $(LibreC) GB libres. En Linux: du -h .cache/vm/windows.qcow2"
}

if ($SinInicioAutomatico) {
  Paso '6 y 7. Inicio automático: omitido (-SinInicioAutomatico)'
} else {
  Paso "6. Inicio de sesión automático de $env:USERNAME"
  $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
  $clave = Read-Host "  Contraseña de $env:USERNAME (Enter si no tiene; queda guardada en el registro de esta VM)" -AsSecureString
  $texto = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave))
  $valida = $true
  if ($texto) {
    try {
      Add-Type -AssemblyName System.DirectoryServices.AccountManagement
      $ctx = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Machine')
      $valida = $ctx.ValidateCredentials($env:USERNAME, $texto)
    } catch { Aviso "no pude comprobar la contraseña ($($_.Exception.Message)); se guarda tal cual." }
  }
  if (-not $valida) {
    Aviso 'la contraseña no es correcta; no se activa el inicio automático (vuelve a correr pulir.cmd).'
  } else {
    Registro $winlogon 'AutoAdminLogon' '1' 'String'
    Registro $winlogon 'DefaultUserName' $env:USERNAME 'String'
    Registro $winlogon 'DefaultDomainName' $env:COMPUTERNAME 'String'
    Registro $winlogon 'DefaultPassword' $texto 'String'
    Ok 'listo'
  }

  Paso '7. Apple Music al iniciar sesión'
  $carpeta = 'C:\Apps\ELENA'
  New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
  Copy-Item -Path (Join-Path $origen 'apple-music.ps1') -Destination $carpeta -Force
  $acceso = Join-Path ([Environment]::GetFolderPath('Startup')) 'Apple Music (ELENA).lnk'
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($acceso)
  $lnk.TargetPath = "$env:windir\System32\WindowsPowerShell\v1.0\powershell.exe"
  $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$carpeta\apple-music.ps1`"" + ($(if ($SinApagar) { ' -SinApagar' } else { '' }))
  $lnk.WindowStyle = 7   # minimizada
  $lnk.Save()
  if ($SinApagar) { Ok "listo: $acceso (al cerrar Apple Music, Windows sigue encendido)" }
  else { Ok "listo: $acceso (al cerrar Apple Music, Windows se apaga en 15 s; para cancelar: shutdown /a)" }
}

# Los puentes que estén corriendo (se detienen para reemplazarlo o quitarlo: COM1 es de uno solo)
function Puentes {
  @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*puente-musica.ps1*' })
}
$carpeta = 'C:\Apps\ELENA'
$accesoPuente = Join-Path ([Environment]::GetFolderPath('Startup')) 'Puente de música (ELENA).lnk'
if ($SinPuente) {
  Paso '8. Puente de música: se quita (-SinPuente)'
  Puentes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "  se detuvo (PID $($_.ProcessId))" }
  Remove-Item -Path $accesoPuente, (Join-Path $carpeta 'puente-musica.ps1') -Force -ErrorAction SilentlyContinue
  Ok 'listo: ya no arranca al iniciar sesión'
} else {
  Paso '8. Puente de música (Apple Music en los widgets de Linux, por COM1)'
  New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
  Puentes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "  se detuvo el que corría (PID $($_.ProcessId))" }
  Copy-Item -Path (Join-Path $origen 'puente-musica.ps1') -Destination $carpeta -Force
  $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($accesoPuente)
  $lnk.TargetPath = "$env:windir\System32\WindowsPowerShell\v1.0\powershell.exe"
  $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$carpeta\puente-musica.ps1`""
  $lnk.WindowStyle = 7   # minimizada
  $lnk.Save()
  # Arrancarlo ya. Este script corre como administrador; explorer abre el acceso directo con la
  # sesión normal, como al iniciar sesión.
  Start-Process explorer.exe -ArgumentList "`"$accesoPuente`""
  Start-Sleep -Seconds 3
  $corriendo = Puentes
  if ($corriendo) { Ok "listo: $accesoPuente; corriendo (PID $($corriendo[0].ProcessId)). Registro: $carpeta\puente-musica.log" }
  else { Aviso "quedó instalado ($accesoPuente) pero no arrancó; arrancará al reiniciar Windows. Ver $carpeta\puente-musica.log" }
}

Paso 'Siguiente'
Write-Host '  1. Reinicia Windows: debe entrar solo y abrir Apple Music.'
Write-Host '  2. Apple Music → Configuración: la carpeta de medios / descargas en M:\Apple Music,'
Write-Host '     y, si existe la opción, descargar en Dolby Atmos.'
Write-Host '  3. Descarga UN álbum Atmos y haz la prueba sin red (docs/guias/vm-apple-music-atmos.md):'
Write-Host '       mise run elena -- vm red off'
Write-Host '       mise run medir:canales elena_musica --segundos 30'
