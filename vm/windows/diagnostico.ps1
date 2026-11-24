# ELENA: diagnóstico de Windows: Dolby / E-AC-3, discos y descargas de Apple Music, controles
# multimedia y puerto serie. Se corre igual en una instalación de Windows en el disco (dual boot) y
# en la VM, para comparar. No necesita administrador ni cambia nada: solo lee y guarda un reporte en
# el Escritorio, en la memoria USB virtual «ELENA-INT» si existe (VM; Linux la lee con
# `mise run vm:intercambio`) y, con `-Disco <etiqueta>`, en ese disco (en el dual boot, un disco NTFS
# que Linux también vea). Para los controles multimedia, córrelo con una pista sonando en Apple Music.
param([string]$Disco = '')
$r = New-Object System.Collections.Generic.List[string]
$archivos = @{}  # nombre → bytes que se guardan junto al reporte (la carátula de Apple Music)
function S($t) { $r.Add(''); $r.Add("=== $t") }
function A($o) { (($o | Out-String -Width 300).TrimEnd() -split "`r?`n") | ForEach-Object { $r.Add($_) } }
function Falla($e) { $b = $e.Exception.GetBaseException(); '{0}: {1}' -f $b.GetType().Name, $b.Message }

$v = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
S 'Windows'
A ('{0} | {1} | {2} | compilación {3}.{4} | equipo {5}' -f $v.ProductName, $v.EditionID, $v.DisplayVersion, $v.CurrentBuild, $v.UBR, $env:COMPUTERNAME)

S 'Equipo y BIOS (lo que ve Windows; en la VM dice QEMU)'
$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
$bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
A ('Fabricante: {0} | Modelo: {1} | Hipervisor: {2}' -f $cs.Manufacturer, $cs.Model, $cs.HypervisorPresent)
A ('BIOS: {0} | {1}' -f $bios.Manufacturer, $bios.SMBIOSBIOSVersion)

# ¿Apple Music publica título, estado y controles en los controles
# multimedia de Windows (SMTC, Windows.Media.Control)? Va antes que lo demás para leerlo
# mientras la pista sigue sonando. PowerShell 5.1 no tiene await: las operaciones asíncronas
# de WinRT se esperan con AsTask (System.WindowsRuntimeSystemExtensions), por reflexión.
S 'Controles multimedia (SMTC): sesiones, datos, controles y avance (con una pista sonando)'
A (Get-StartApps -ErrorAction SilentlyContinue | Where-Object Name -match 'Apple' | Format-Table Name, AppID -AutoSize)
A (Get-Process -ErrorAction SilentlyContinue |
   Where-Object { $_.Path -like '*\WindowsApps\AppleInc.*' -or $_.ProcessName -match '^(AppleMusic|AMP)' } |
   Format-Table Id, ProcessName, @{ n = 'MB'; e = { [math]::Round($_.WorkingSet64 / 1MB) } }, Path -AutoSize)
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
  function Esperar($op, [Type]$tipo) {
    $t = $asTask.MakeGenericMethod($tipo).Invoke($null, @($op))
    if (-not $t.Wait(5000)) { throw "sin respuesta en 5 s ($($tipo.Name))" }
    $t.Result
  }
  function Linea($t) {
    'Posición {0:hh\:mm\:ss\.f} de {1:hh\:mm\:ss\.f} (inicio {2:hh\:mm\:ss}; se puede ir de {3:hh\:mm\:ss} a {4:hh\:mm\:ss}) | LastUpdatedTime {5:HH:mm:ss.fff zzz}, hace {6:N1} s' -f
      $t.Position, $t.EndTime, $t.StartTime, $t.MinSeekTime, $t.MaxSeekTime, $t.LastUpdatedTime,
      ([DateTimeOffset]::Now - $t.LastUpdatedTime).TotalSeconds
  }
  $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.IInputStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
  $tMgr = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
  $mgr = Esperar ($tMgr::RequestAsync()) $tMgr
  $sesiones = @($mgr.GetSessions())
  $actual = $mgr.GetCurrentSession()
  A ('Sesiones: {0} | la actual (la del recuadro multimedia de Windows): {1}' -f $sesiones.Count,
     $(if ($actual) { $actual.SourceAppUserModelId } else { 'ninguna' }))
  if ($sesiones.Count -eq 0) { A 'Ninguna sesión: ¿Apple Music estaba abierta y sonando?' }
  $controles = 'IsPlayEnabled', 'IsPauseEnabled', 'IsPlayPauseToggleEnabled', 'IsStopEnabled', 'IsNextEnabled',
               'IsPreviousEnabled', 'IsPlaybackPositionEnabled', 'IsShuffleEnabled', 'IsRepeatEnabled',
               'IsFastForwardEnabled', 'IsRewindEnabled', 'IsPlaybackRateEnabled'
  $primera = @{}  # sesión → (Timeline, hora en que se leyó)
  $n = 0
  foreach ($s in $sesiones) {
    $n++
    A "--- Sesión ${n}: $($s.SourceAppUserModelId)"
    try {
      $i = $s.GetPlaybackInfo()
      A ('Estado: {0} | Tipo: {1} | Aleatorio: {2} | Repetir: {3} | Velocidad: {4}' -f
         $i.PlaybackStatus, $i.PlaybackType, $i.IsShuffleActive, $i.AutoRepeatMode, $i.PlaybackRate)
      $c = $i.Controls
      A ('Controles: ' + (($controles | ForEach-Object { '{0}={1}' -f ($_ -replace '^Is|Enabled$'), $c.$_ }) -join ' '))
      $t = $s.GetTimelineProperties()
      $primera[$n] = @($t, [DateTimeOffset]::Now)
      A (Linea $t)
    } catch { A "No se pudo leer el estado: $(Falla $_)" }
    try {
      $p = Esperar ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
      A ('Título: {0} | Subtítulo: {1}' -f $p.Title, $p.Subtitle)
      A ('Artista: {0} | Álbum: {1} | Artista del álbum: {2}' -f $p.Artist, $p.AlbumTitle, $p.AlbumArtist)
      A ('Pista {0} de {1} | Géneros: {2} | Tipo: {3} | Carátula: {4}' -f
         $p.TrackNumber, $p.AlbumTrackCount, (@($p.Genres) -join ', '), $p.PlaybackType, [bool]$p.Thumbnail)
    } catch { A "No se pudieron leer los datos: $(Falla $_)"; $p = $null }
    if ($p -and $p.Thumbnail) {
      # Se guarda junto al reporte para ver si es la del álbum. El tiempo en el puerto serie es
      # en base64 a 115200 baudios (8N1 ≈ 11 520 bytes/s), el peor caso de la prueba del puerto serie.
      # Dos formas de leerla (AsStreamForRead y DataReader); el reporte dice cuál sirvió.
      try {
        $st = Esperar ($p.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        $bytes = $null
        try {
          $lector = [System.IO.WindowsRuntimeStreamExtensions].GetMethod('AsStreamForRead',
                      [Type[]]@([Windows.Storage.Streams.IInputStream])).Invoke($null, @($st))
          $ms = New-Object System.IO.MemoryStream
          $lector.CopyTo($ms)
          $bytes = $ms.ToArray(); $via = 'AsStreamForRead'
        } catch {
          A "AsStreamForRead falló ($(Falla $_)); se intenta con DataReader"
          $dr = New-Object Windows.Storage.Streams.DataReader ($st.GetInputStreamAt(0))
          $leidos = Esperar ($dr.LoadAsync([uint32]$st.Size)) ([uint32])
          $bytes = New-Object byte[] $leidos
          $dr.ReadBytes($bytes); $via = 'DataReader'
        }
        $dim = '?'
        try {
          Add-Type -AssemblyName System.Drawing
          $img = [System.Drawing.Image]::FromStream((New-Object System.IO.MemoryStream (, $bytes)))
          $dim = '{0}x{1}' -f $img.Width, $img.Height
          $img.Dispose()
        } catch { $dim = "? ($(Falla $_))" }
        $ext = switch -Regex ($st.ContentType) { 'jpe?g' { 'jpg' } 'png' { 'png' } 'bmp' { 'bmp' } default { 'bin' } }
        $archivos["elena-caratula-$env:COMPUTERNAME-$n.$ext"] = $bytes
        A ('Carátula: {0} | {1} bytes ({2:N1} KB) | {3} | leída con {4} | por el puerto serie a 115200: ~{5:N1} s' -f
           $st.ContentType, $bytes.Length, ($bytes.Length / 1KB), $dim, $via, ($bytes.Length * 4 / 3 / 11520))
      } catch { A "No se pudo leer la carátula: $(Falla $_)" }
    }
  }
  if ($primera.Count) {
    # ¿La posición avanza sola o Apple Music la actualiza de vez en cuando? (si no
    # avanza, la calcula Linux)
    Start-Sleep -Seconds 3
    A '--- Línea de tiempo, otra vez'
    foreach ($k in $primera.Keys | Sort-Object) {
      try {
        $s = $sesiones[$k - 1]
        $t1, $h1 = $primera[$k]
        $t2 = $s.GetTimelineProperties()
        $real = ([DateTimeOffset]::Now - $h1).TotalSeconds
        A ('Sesión {0} ({1}): {2:N1} s después' -f $k, $s.GetPlaybackInfo().PlaybackStatus, $real)
        A (Linea $t2)
        A ('La posición avanzó {0:N1} s en {1:N1} s; LastUpdatedTime {2}' -f ($t2.Position - $t1.Position).TotalSeconds, $real,
           $(if ($t2.LastUpdatedTime -ne $t1.LastUpdatedTime) { 'cambió' } else { 'no cambió' }))
      } catch { A "Sesión ${k}: no se pudo leer otra vez: $(Falla $_)" }
    }
  }
} catch { A "No se pudo usar SMTC: $(Falla $_)" }

S 'Puertos serie: COM1 es el canal del puente de música'
A ('GetPortNames: {0}' -f (@([System.IO.Ports.SerialPort]::GetPortNames()) -join ', '))
A (Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | Format-Table Status, FriendlyName, InstanceId -AutoSize)
try {
  $com = New-Object System.IO.Ports.SerialPort 'COM1', 115200
  $com.Open()
  A 'COM1 abre (se cerró sin escribir nada)'
  $com.Close()
  $com.Dispose()
} catch { A "COM1 no abre: $(Falla $_)" }

S 'Paquetes Appx (Dolby, códecs, Tienda y sus dependencias, Apple, medios)'
# SignatureKind: Store = firmado por la Tienda; Developer = registrado a mano (-Register)
A (Get-AppxPackage | Where-Object { $_.Name -match 'Dolby|Codec|Extension|VCLibs|Apple|Media|WebView|Store|Purchase|Xaml|NET\.Native|Xbox' } |
   Sort-Object Name | Format-Table Name, Version, Architecture, Status, SignatureKind -AutoSize)

S 'Activación de Windows y servicios de licencias'
# LicenseStatus: 0 sin licencia, 1 con licencia, 2-6 periodos de gracia o notificación
A (Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" -ErrorAction SilentlyContinue |
   Where-Object { $_.Name -like 'Windows*' } | Format-Table Name, LicenseStatus, Description -AutoSize)
A (Get-Service ClipSVC, LicenseManager, InstallService, wlidsvc, AppXSvc -ErrorAction SilentlyContinue |
   Format-Table Name, Status, StartType -AutoSize)

S 'Manifiestos de los paquetes Dolby (cómo registran el códec)'
Get-AppxPackage *Dolby* | ForEach-Object {
  A "--- $($_.PackageFullName)  (instalado en $($_.InstallLocation))"
  A (Get-AppxPackageManifest $_).OuterXml
}

S 'Prueba: decodificadores de E-AC-3 que encuentra Media Foundation (MFTEnumEx) y si se pueden crear'
# Lo mismo que hace un reproductor: pedir a Media Foundation un decodificador de audio para
# E-AC-3 (MFAudioFormat_Dolby_DDPlus) y crearlo. Si el de Dolby aparece pero no se crea, el
# código de error dice por qué. Flags: 0x3F = todos los MFT locales; +0x40 = ordenados y
# filtrados como los usa un reproductor; +0x400 = incluir MFT de la Tienda no confiables.
try {
  if (-not ('ElenaMF' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("7FEE9E9A-4A89-47a6-899C-B6A53A70FB67"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMFActivate {
  // IMFAttributes: 30 métodos; solo se usa GetAllocatedString (posición 11)
  void a0(); void a1(); void a2(); void a3(); void a4(); void a5(); void a6(); void a7(); void a8(); void a9();
  [PreserveSig] int GetAllocatedString([In] ref Guid key, [MarshalAs(UnmanagedType.LPWStr)] out string value, out int length);
  void a11(); void a12(); void a13(); void a14(); void a15(); void a16(); void a17(); void a18(); void a19();
  void a20(); void a21(); void a22(); void a23(); void a24(); void a25(); void a26(); void a27(); void a28(); void a29();
  // IMFActivate
  [PreserveSig] int ActivateObject([In] ref Guid riid, out IntPtr ppv);
  [PreserveSig] int ShutdownObject();
  [PreserveSig] int DetachObject();
}
[StructLayout(LayoutKind.Sequential)]
public struct MFT_REGISTER_TYPE_INFO { public Guid guidMajorType; public Guid guidSubtype; }
public static class ElenaMF {
  [DllImport("mfplat.dll")] static extern int MFStartup(int version, int flags);
  [DllImport("mfplat.dll")] static extern int MFTEnumEx(Guid category, uint flags, ref MFT_REGISTER_TYPE_INFO input, IntPtr output, out IntPtr activates, out uint count);
  [DllImport("ole32.dll")] static extern void CoTaskMemFree(IntPtr p);
  public static string[] Probar(uint flags) {
    MFStartup(0x20070, 0);
    var info = new MFT_REGISTER_TYPE_INFO {
      guidMajorType = new Guid("73647561-0000-0010-8000-00AA00389B71"),  // MFMediaType_Audio
      guidSubtype = new Guid("a7fb87af-2d02-42fb-a4d4-05cd93843bdd") };  // E-AC-3 (DD+)
    IntPtr arr; uint n;
    int hr = MFTEnumEx(new Guid("9ea73fb4-ef7a-4559-8d5d-719d8f0426c7"), flags, ref info, IntPtr.Zero, out arr, out n);
    if (hr != 0) return new[] { String.Format("MFTEnumEx falló: 0x{0:X8}", hr) };
    if (n == 0) return new[] { "ninguno" };
    var res = new string[n];
    Guid nombre = new Guid("314ffbae-5b41-4c95-9c19-4e7d586face3");     // MFT_FRIENDLY_NAME_Attribute
    Guid iidTransform = new Guid("bf94c121-5b05-4e6f-8000-ba598961414d"); // IMFTransform
    for (int i = 0; i < n; i++) {
      IntPtr p = Marshal.ReadIntPtr(arr, i * IntPtr.Size);
      var act = (IMFActivate)Marshal.GetObjectForIUnknown(p);
      string name; int len;
      if (act.GetAllocatedString(ref nombre, out name, out len) != 0) name = "(sin nombre)";
      IntPtr obj;
      int ahr = act.ActivateObject(ref iidTransform, out obj);
      res[i] = String.Format("{0} -> crear: {1}", name, ahr == 0 ? "OK" : String.Format("FALLA 0x{0:X8}", ahr));
      if (obj != IntPtr.Zero) Marshal.Release(obj);
      act.ShutdownObject();
      Marshal.ReleaseComObject(act);
      Marshal.Release(p);
    }
    CoTaskMemFree(arr);
    return res;
  }
}
'@
  }
  foreach ($f in 0x3F, 0x7F, 0x47F) {
    A ('flags 0x{0:X}:' -f $f)
    A ([ElenaMF]::Probar([uint32]$f) | ForEach-Object { "  $_" })
  }
} catch { A "No se pudo correr la prueba: $($_.Exception.Message)" }

S 'Archivos Dolby / AC3 en System32 y SysWOW64'
A (Get-ChildItem "$env:windir\System32", "$env:windir\SysWOW64" -File -ErrorAction SilentlyContinue |
   Where-Object { $_.Name -match 'dolby|ac3|ddp|eac3|msauddec|msac3' } |
   Format-Table FullName, Length, @{ n = 'Versión'; e = { $_.VersionInfo.FileVersion } } -AutoSize)

S 'Media Foundation: transformaciones registradas (nombre)'
A (Get-ChildItem 'HKLM:\SOFTWARE\Classes\MediaFoundation\Transforms' -ErrorAction SilentlyContinue |
   Where-Object { $_.PSChildName -notin 'Categories', 'Preferred' } |
   ForEach-Object { '{0}  {1}' -f $_.PSChildName, (Get-ItemProperty $_.PSPath).'(default)' } | Sort-Object)

S 'Programas instalados (Dolby, Realtek, códecs, Apple, WebView2)'
A (Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
                    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
   Where-Object { $_.DisplayName -match 'Dolby|Realtek|Codec|K-Lite|LAV|Apple|iTunes|WebView2|Nahimic|DTS|Sonic' } |
   Sort-Object DisplayName | Format-Table DisplayName, DisplayVersion, Publisher -AutoSize)

S 'Dispositivos y controladores de audio'
A (Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue | Where-Object { $_.DeviceClass -eq 'MEDIA' } |
   Format-Table DeviceName, DriverVersion, DriverProviderName -AutoSize)
A (Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue | Format-Table Status, FriendlyName -AutoSize)

S 'Eventos de error y aviso de las últimas 3 horas'
$desde = (Get-Date).AddHours(-3)
foreach ($log in 'Application', 'Microsoft-Windows-AppModel-Runtime/Admin', 'Microsoft-Windows-Store/Operational',
                 'Microsoft-Windows-AppXDeploymentServer/Operational', 'Microsoft-Windows-AppxPackaging/Operational') {
  $ev = @(Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $desde; Level = 1, 2, 3 } -ErrorAction SilentlyContinue |
          Select-Object -First 40)
  A "--- $log ($($ev.Count))"
  foreach ($e in $ev) { A ('{0:HH:mm:ss} {1} {2}: {3}' -f $e.TimeCreated, $e.ProviderName, $e.Id, (($e.Message -split "`r?`n")[0])) }
}

S 'Apagados de los últimos 7 días: 1074 = apagado pedido; 41 y 6008 = corte en seco'
A (Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41, 1074, 6008; StartTime = (Get-Date).AddDays(-7) } -ErrorAction SilentlyContinue |
   Select-Object -First 20 | ForEach-Object { '{0:yyyy-MM-dd HH:mm:ss} {1}: {2}' -f $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n")[0]) })

S 'Discos y biblioteca de Apple Music: ¿dónde quedan las descargas?'
A (Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter |
   Format-Table DriveLetter, FileSystemLabel, FileSystem, DriveType,
     @{ n = 'GB'; e = { [math]::Round($_.Size / 1GB, 1) } }, @{ n = 'GB libres'; e = { [math]::Round($_.SizeRemaining / 1GB, 1) } } -AutoSize)
# Las apps empaquetadas (MSIX) escriben AppData dentro de su paquete (LocalCache): se revisan las dos rutas
$candidatas = @("$env:USERPROFILE\Music", 'M:\') +
  @(Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'AppleInc.AppleMusicWin_*' -ErrorAction SilentlyContinue | ForEach-Object FullName) +
  @(Get-ChildItem "$env:LOCALAPPDATA", "$env:APPDATA" -Directory -Filter 'Apple*' -ErrorAction SilentlyContinue | ForEach-Object FullName)
foreach ($c in $candidatas | Where-Object { Test-Path $_ }) {
  A "--- $c"
  A (Get-ChildItem $c -Directory -Recurse -Depth 3 -Force -ErrorAction SilentlyContinue | ForEach-Object {
       $s = (Get-ChildItem $_.FullName -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
       if ($s -gt 50MB) { [pscustomobject]@{ MB = [math]::Round($s / 1MB); Carpeta = $_.FullName } }
     } | Sort-Object MB -Descending | Select-Object -First 15 | Format-Table -AutoSize)
}

$nombre = "elena-diagnostico-$env:COMPUTERNAME.txt"
$destinos = @(Join-Path ([Environment]::GetFolderPath('Desktop')) $nombre)
foreach ($etiqueta in @('ELENA-INT') + @($Disco | Where-Object { $_ })) {
  $letra = (Get-Volume -FileSystemLabel $etiqueta -ErrorAction SilentlyContinue | Select-Object -First 1).DriveLetter
  if ($letra) { $destinos += "${letra}:\$nombre" }
}
foreach ($f in $destinos) {
  $r | Set-Content -Path $f -Encoding UTF8
  Write-Host "Reporte guardado en $f" -ForegroundColor Green
  foreach ($k in $archivos.Keys) {
    $g = Join-Path (Split-Path $f) $k
    [System.IO.File]::WriteAllBytes($g, $archivos[$k])
    Write-Host "  y $g"
  }
}
