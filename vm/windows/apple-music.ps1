# ELENA: abre Apple Music al iniciar sesión y, cuando se cierra, apaga Windows: así la
# VM se abre y se cierra como una app más (en Linux, la ventana de QEMU desaparece sola).
# pulir.ps1 lo copia a C:\Apps\ELENA\ y crea su acceso directo en la carpeta de Inicio.
#   -SinApagar  solo abre Apple Music
# Para cancelar el apagado: shutdown /a (antes de 15 s).
param([switch]$SinApagar)
$ErrorActionPreference = 'Stop'

$app = Get-AppxPackage -Name 'AppleInc.AppleMusicWin'
if (-not $app) { throw 'Apple Music no está registrada (corre instalar.cmd desde el CD ELENA).' }
$aplicacion = @((Get-AppxPackageManifest $app).Package.Applications.Application)[0]
$proceso = [IO.Path]::GetFileNameWithoutExtension($aplicacion.Executable)

# Error 7502 / «An unknown error has occurred» en todo el catálogo (la biblioteca descargada sí
# suena): Apple Music guarda en su caché de WinINet el certificado con el que firma las peticiones
# a la tienda (setupCert.plist); si queda la versión gzip, no la sabe leer, y el servidor responde
# 304 siempre, así que la entrada mala no se renueva. Se borra antes de abrirla (con la app cerrada).
# Investigación: https://github.com/mikepink/music-on-windows11-unknown-error-fix (pasó en la VM
# el 2026-11-22 y 23). Cada borrado queda en apple-music.log, con «gzip» si era la entrada mala.
try {
  if (-not (Get-Process -Name $proceso -ErrorAction SilentlyContinue)) {
    $cache = Join-Path $env:LOCALAPPDATA "Packages\$($app.PackageFamilyName)\AC\INetCache"
    Get-ChildItem $cache -Recurse -Force -Filter 'setupCert*' -ErrorAction SilentlyContinue | ForEach-Object {
      $b = [IO.File]::ReadAllBytes($_.FullName)
      $tipo = if ($b.Length -ge 2 -and $b[0] -eq 0x1f -and $b[1] -eq 0x8b) { 'gzip' } else { 'texto' }
      Remove-Item -LiteralPath $_.FullName -Force
      Add-Content -Path (Join-Path $PSScriptRoot 'apple-music.log') `
        -Value "$(Get-Date -Format s) borrado $($_.FullName) ($tipo, $($b.Length) bytes)"
    }
  }
} catch { }   # nunca impide abrir Apple Music

Start-Process "shell:AppsFolder\$($app.PackageFamilyName)!$($aplicacion.Id)"
if ($SinApagar) { exit 0 }

# Esperar a que abra (hasta 2 min: el primer arranque tras iniciar sesión es lento)
$limite = (Get-Date).AddMinutes(2)
while (-not (Get-Process -Name $proceso -ErrorAction SilentlyContinue)) {
  if ((Get-Date) -gt $limite) { exit 1 }   # no abrió: no se apaga nada
  Start-Sleep -Seconds 2
}
# Cerrada = 5 s seguidos sin su proceso (al iniciar sesión o actualizarse puede reiniciarse sola)
$sin = 0
while ($sin -lt 5) {
  Start-Sleep -Seconds 1
  if (Get-Process -Name $proceso -ErrorAction SilentlyContinue) { $sin = 0 } else { $sin++ }
}
shutdown.exe /s /t 15 /c 'Apple Music se cerró: Windows se apaga en 15 segundos. Para cancelar: shutdown /a'
