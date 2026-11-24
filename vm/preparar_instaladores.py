"""Prepara la carpeta de instaladores para la VM, desde Linux.

Uso: python3 -I vm/preparar_instaladores.py <origen> <destino> <plantillas> [<descargas>]

Origen (ELENA_VM_INSTALADORES):
  AppleMusic.zip                 el .msixbundle de Apple Music (renombrado a zip)
  Media_Foundation_Codecs.zip    opcional: de aquí solo se toman las VCLibs
  *.appx / *.msix sueltos        las dependencias (VCLibs, UI.Xaml, la Tienda…; ver la guía de la VM)
  gUIA.txt                       opcional: notas propias, se copian al CD tal cual
Destino (se borra y se rehace):
  AppleMusic/      el .msix x64 extraído, sin AppxMetadata, AppxSignature.p7x ni
                   [Content_Types].xml, con MinVersion cambiado a 10.0.19044.0
  dependencias/    los paquetes a instalar antes de Apple Music
  extras/          lo que haya en <descargas>: WebView2 Runtime (Apple Music lo necesita y
                   Windows 10 LTSC no lo trae) y prueba-eac3-51.mp4
  instalar.cmd, instalar.ps1 (y gUIA.txt si existe)

Antes de terminar comprueba que cada PackageDependency del manifiesto de Apple Music tenga
un paquete en dependencias/ con el mismo nombre y versión igual o mayor; si falta alguno,
sale con código 2 y dice cuál. Cambiar MinVersion solo quita el límite de versión de
Windows: Apple Music sigue reproduciendo con su DRM normal.
"""
import io
import re
import shutil
import sys
import zipfile
from pathlib import Path

WINDOWS_LTSC = "10.0.19044.0"  # Windows 10 IoT Enterprise LTSC 2021
CODECS = (  # del zip de códecs: solo las VCLibs que pide Apple Music
    "Microsoft.VCLibs.140.00_",
)
# El códec Dolby Digital Plus NO va en el CD: instalado desde el archivo queda sin licencia
# (Media Foundation lo encuentra pero falla al crearlo con 0x87E10BC6) y, además, la Tienda lo
# da por «Ya instalado» y no lo licencia. Se obtiene desde la Microsoft Store (9NVJQJBDKN97).
OMITIR = ("DolbyLaboratories.",)
QUITAR = ("AppxMetadata", "AppxSignature.p7x", "[Content_Types].xml")


def version(v):
    return tuple(int(x) for x in v.split("."))


def identidad(manifiesto):
    m = re.search(r"<Identity\b[^>]*>", manifiesto)
    if not m:
        return None
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
    return attrs.get("Name"), attrs.get("Version"), attrs.get("ProcessorArchitecture", "neutral")


def dependencias_de(manifiesto):
    """(Name, MinVersion) de cada PackageDependency de un AppxManifest."""
    out = []
    for tag in re.findall(r"<PackageDependency\b[^>]*>", manifiesto):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        out.append((a["Name"], a.get("MinVersion", "0.0.0.0")))
    return out


def manifiesto_app(p):
    """AppxManifest.xml de un paquete; en un bundle, el del paquete x64 o neutral de dentro."""
    z = zipfile.ZipFile(p)
    if p.suffix.lower() not in (".appxbundle", ".msixbundle"):
        return z.read("AppxManifest.xml").decode("utf-8", "replace")
    bm = z.read("AppxMetadata/AppxBundleManifest.xml").decode("utf-8", "replace")
    for tag in re.findall(r"<Package\b[^>]*>", bm):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if a.get("Type", "application") == "application" and a.get("Architecture") in ("x64", "neutral"):
            with z.open(a["FileName"]) as f:
                return zipfile.ZipFile(io.BytesIO(f.read())).read("AppxManifest.xml").decode("utf-8", "replace")
    return None


def main(origen, destino, plantillas, descargas=None):
    origen, destino, plantillas = Path(origen), Path(destino), Path(plantillas)
    if destino.exists():
        shutil.rmtree(destino)
    (destino / "dependencias").mkdir(parents=True)
    (destino / "extras").mkdir()

    # 0. Extras de vm/instaladores.sh (WebView2 Runtime y el MP4 de prueba en E-AC-3 5.1):
    #    van tal cual a extras/
    if descargas and Path(descargas).is_dir():
        for p in sorted(Path(descargas).iterdir()):
            if p.is_file() and not p.name.endswith(".parcial"):
                print(f"Extra: {p.name}")
                shutil.copy2(p, destino / "extras" / p.name)

    # 1. Apple Music: el .msix x64 de dentro del bundle
    bundle = zipfile.ZipFile(origen / "AppleMusic.zip")
    msix = [n for n in bundle.namelist() if re.fullmatch(r"MusicPackage_[\d.]+_x64\.msix", n)]
    if len(msix) != 1:
        sys.exit(f"ERROR: esperaba un MusicPackage_*_x64.msix en AppleMusic.zip, hay {msix}")
    print(f"Apple Music: {msix[0]}")
    app = destino / "AppleMusic"
    with bundle.open(msix[0]) as f:
        tmp = destino / "_x64.msix"
        with open(tmp, "wb") as out:
            shutil.copyfileobj(f, out)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(app)  # zipfile neutraliza rutas absolutas y «..»
    tmp.unlink()
    for nombre in QUITAR:
        p = app / nombre
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    ruta = app / "AppxManifest.xml"
    texto = ruta.read_bytes().decode("utf-8-sig")
    antes = re.search(r"<TargetDeviceFamily\b[^>]*>", texto).group(0)
    despues = re.sub(r'MinVersion="[^"]*"', f'MinVersion="{WINDOWS_LTSC}"', antes)
    texto = texto.replace(antes, despues, 1)
    ruta.write_bytes(texto.encode("utf-8"))
    print(f"  {antes.strip()}\n  → {despues.strip()}")
    requeridas = [("Apple Music", n, v) for n, v in dependencias_de(texto)]

    # 2. Dependencias: las VCLibs del zip de códecs (si está) y los paquetes sueltos del origen
    if (zip_codecs := origen / "Media_Foundation_Codecs.zip").exists():
        codecs = zipfile.ZipFile(zip_codecs)
        for n in codecs.namelist():
            base = n.rsplit("/", 1)[-1]
            if base.startswith(CODECS) and ("_x64_" in base or "_neutral_" in base):
                with codecs.open(n) as f, open(destino / "dependencias" / base, "wb") as out:
                    shutil.copyfileobj(f, out)
    for p in sorted(origen.iterdir()):
        if p.is_file() and p.suffix.lower() in (".appx", ".msix", ".appxbundle", ".msixbundle"):
            if p.name.startswith(OMITIR):
                print(f"Se omite {p.name}: se obtiene de la Microsoft Store, con licencia")
                continue
            shutil.copy2(p, destino / "dependencias" / p.name)

    # 2b. Si hay dos versiones del mismo paquete (p. ej. el Dolby 1.1.285 del zip de códecs y
    #     uno más nuevo bajado aparte), se queda solo la mayor de cada nombre y arquitectura.
    #     Un Windows que sí decodifica E-AC-3 tenía el Dolby 1.2.591.0; el 1.1.285 no lo hace.
    def ident_de(p):
        try:
            z = zipfile.ZipFile(p)
            if p.suffix.lower() in (".appxbundle", ".msixbundle"):
                return identidad(z.read("AppxMetadata/AppxBundleManifest.xml").decode("utf-8", "replace"))
            return identidad(z.read("AppxManifest.xml").decode("utf-8", "replace"))
        except (zipfile.BadZipFile, KeyError):
            return None

    mejor = {}
    for p in sorted((destino / "dependencias").iterdir()):
        ident = ident_de(p)
        if not ident:
            continue
        clave = (ident[0], ident[2])
        if clave not in mejor:
            mejor[clave] = (p, ident[1])
            continue
        otro, ver_otro = mejor[clave]
        if version(ident[1]) > version(ver_otro):
            print(f"Se descarta {otro.name}: hay una versión mayor ({ident[1]})")
            otro.unlink()
            mejor[clave] = (p, ident[1])
        else:
            print(f"Se descarta {p.name}: hay una versión mayor o igual ({ver_otro})")
            p.unlink()

    # 3. ¿Están las dependencias de Apple Music y las de cada paquete de dependencias/
    #    (p. ej. la Microsoft Store pide UI.Xaml y .NET Native)?
    disponibles = {}
    invalidos = []     # archivos vacíos o dañados: detienen el proceso (no se meten en la ISO)
    incompatibles = []  # piden una versión de Windows mayor que la del LTSC
    for p in sorted((destino / "dependencias").iterdir()):
        print(f"Dependencia: {p.name}")
        try:
            ident, man = ident_de(p), manifiesto_app(p)
        except (zipfile.BadZipFile, KeyError, OSError):
            ident, man = None, None
        if not ident:
            print(f"  ERROR: no es un paquete válido ({p.stat().st_size} bytes): ¿descarga incompleta?")
            invalidos.append(p.name)
            continue
        m = re.search(r'<TargetDeviceFamily\b[^>]*MinVersion="([^"]+)"', man or "")
        if m and version(m.group(1)) > version(WINDOWS_LTSC):
            print(f"  ERROR: pide Windows {m.group(1)} y el LTSC es {WINDOWS_LTSC}")
            incompatibles.append(p.name)
        nombre, ver, arq = ident
        if arq in ("x64", "neutral") and (nombre not in disponibles or version(ver) > version(disponibles[nombre])):
            disponibles[nombre] = ver
        if man:
            requeridas += [(nombre, n, v) for n, v in dependencias_de(man)]
    # Una línea por dependencia: la versión mínima más alta que pida alguien y todos los que la piden
    pedidas, quienes = {}, {}
    for quien, nombre, minima in requeridas:
        quienes.setdefault(nombre, []).append(quien)
        if nombre not in pedidas or version(minima) > version(pedidas[nombre]):
            pedidas[nombre] = minima
    faltan = []
    for nombre, minima in sorted(pedidas.items()):
        quien = ", ".join(dict.fromkeys(quienes[nombre]))
        tiene = disponibles.get(nombre)
        ok = tiene is not None and version(tiene) >= version(minima)
        print(f"  {'OK ' if ok else 'FALTA'} {nombre} ≥ {minima} (lo pide: {quien})" + (f"; hay {tiene}" if tiene else ""))
        if not ok:
            faltan.append((nombre, minima, quien))

    # 4. Scripts de instalación y la guía
    # Todos los .cmd (ASCII) y .ps1 de vm/windows/, con saltos CRLF. PowerShell 5.1 lee los
    # .ps1 sin BOM como ANSI: se guardan en UTF-8 con BOM.
    for p in sorted(plantillas.glob("*.cmd")):
        (destino / p.name).write_text(p.read_text(encoding="ascii"), encoding="ascii", newline="\r\n")
    for p in sorted(plantillas.glob("*.ps1")):
        (destino / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8-sig", newline="\r\n")
    if (origen / "gUIA.txt").exists():
        shutil.copy2(origen / "gUIA.txt", destino / "gUIA.txt")

    if invalidos:
        print("\nERROR: estos archivos están vacíos o dañados; vuelve a descargarlos (clic derecho →")
        print("«Guardar enlace como») y revisa que pesen más de 0 bytes:")
        for n in invalidos:
            print(f"  {n}")
    if incompatibles:
        print("\nERROR: estos paquetes piden un Windows más nuevo que el LTSC; busca una versión anterior:")
        for n in incompatibles:
            print(f"  {n}")
    if invalidos or incompatibles:
        sys.exit(2)
    if faltan:
        print("\nERROR: faltan dependencias. Descárgalas (x64) de store.rg-adguard.net, en la lista de")
        print("la app que las pide (Apple Music: 9pfhdd62mxs1; Microsoft Store: 9WZDNCRFJBMP), y ponlas en")
        print(f"  {origen}")
        for nombre, minima, quien in faltan:
            print(f"  {nombre} ≥ {minima}  (lo pide {quien})")
        sys.exit(2)


if __name__ == "__main__":
    main(*sys.argv[1:5])
