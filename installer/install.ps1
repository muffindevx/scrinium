# Installs Scrinium on Windows from this folder (the release zip).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1        installs
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Yes   does not ask before installing uv
#
# Steps: uv (if missing, after confirming), Scrinium, its tools and the search model (about 0.7 GB, with
# pinned SHA256) and a final check with `scrinium doctor`. No administrator needed.
param([switch]$Yes)

$ErrorActionPreference = "Stop"
$UvVersion = "0.13.7"
$UvInstallerUrl = "https://releases.astral.sh/github/uv/releases/download/$UvVersion/uv-installer.ps1"
$UvInstallerSha256 = "d9e70de578ea2653b631a5933c0296368fca4c91f412b46680661b81bbc399e0"

function Fail([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheel = Get-ChildItem -Path $here -Filter "scrinium-*-py3-none-any.whl" | Select-Object -First 1
if (-not $wheel) { Fail "No encuentro el paquete de Scrinium (scrinium-*.whl) junto a este script." }
$constraints = Join-Path $here "constraints.txt"
if (-not (Test-Path $constraints)) { Fail "Falta constraints.txt junto a este script." }

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
$localUv = Join-Path $HOME ".local\bin\uv.exe"
if (-not $uv -and (Test-Path $localUv)) { $uv = $localUv }
if (-not $uv) {
    Write-Host "Scrinium necesita uv (de Astral) para instalarse. Se instalara uv $UvVersion en tu"
    Write-Host "cuenta, en $HOME\.local\bin, desde: $UvInstallerUrl"
    if (-not $Yes) {
        $answer = Read-Host "Continuar? [s/N]"
        if ($answer -notmatch '^(s|si)$') {
            Fail "Cancelado. Instala uv por tu cuenta (https://docs.astral.sh/uv/) y repite."
        }
    }
    $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("scrinium-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $scratch | Out-Null
    try {
        $installer = Join-Path $scratch "uv-installer.ps1"
        Invoke-WebRequest -Uri $UvInstallerUrl -OutFile $installer -UseBasicParsing
        $actual = (Get-FileHash -Algorithm SHA256 $installer).Hash.ToLower()
        if ($actual -ne $UvInstallerSha256) {
            Fail "El instalador de uv descargado no coincide con el esperado: no se ejecuta."
        }
        # The uv installer in turn checks the SHA256 of the binary it downloads.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { Fail "No se pudo instalar uv." }
    } finally {
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }
    $uv = $localUv
    if (-not (Test-Path $uv)) { Fail "uv no quedo instalado en $uv." }
}

Write-Host "Instalando Scrinium..."
& $uv tool install --reinstall --quiet --python 3.12 $wheel.FullName -c $constraints
if ($LASTEXITCODE -ne 0) { Fail "No se pudo instalar Scrinium." }
$binDir = (& $uv tool dir --bin).Trim()
$scrinium = Join-Path $binDir "scrinium.exe"
if (-not (Test-Path $scrinium)) { Fail "Scrinium no quedo instalado en $scrinium." }

Write-Host "Descargando herramientas y el modelo de busqueda (unos 0,7 GB la primera vez)..."
& $scrinium tools install
if ($LASTEXITCODE -ne 0) { Fail "No se pudo descargar todo. Repite este script para continuar." }

Write-Host ""
& $scrinium doctor
Write-Host ""
if (($env:PATH -split ";") -contains $binDir) {
    Write-Host "Abre Scrinium con: scrinium start"
} else {
    & $uv tool update-shell | Out-Null
    Write-Host "Abre una terminal nueva y arranca Scrinium con: scrinium start"
    Write-Host "(o ahora mismo: & `"$scrinium`" start)"
}
