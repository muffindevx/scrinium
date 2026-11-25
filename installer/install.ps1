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
if (-not $wheel) { Fail "The Scrinium package (scrinium-*.whl) was not found next to this script." }
$constraints = Join-Path $here "constraints.txt"
if (-not (Test-Path $constraints)) { Fail "constraints.txt is missing next to this script." }

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
$localUv = Join-Path $HOME ".local\bin\uv.exe"
if (-not $uv -and (Test-Path $localUv)) { $uv = $localUv }
if (-not $uv) {
    Write-Host "Scrinium needs uv (by Astral) to install. uv $UvVersion will be installed in your"
    Write-Host "account, in $HOME\.local\bin, from: $UvInstallerUrl"
    if (-not $Yes) {
        $answer = Read-Host "Continue? [y/N]"
        if ($answer -notmatch '^(y|yes)$') {
            Fail "Cancelled. Install uv yourself (https://docs.astral.sh/uv/) and try again."
        }
    }
    $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("scrinium-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $scratch | Out-Null
    try {
        $installer = Join-Path $scratch "uv-installer.ps1"
        Invoke-WebRequest -Uri $UvInstallerUrl -OutFile $installer -UseBasicParsing
        $actual = (Get-FileHash -Algorithm SHA256 $installer).Hash.ToLower()
        if ($actual -ne $UvInstallerSha256) {
            Fail "The downloaded uv installer does not match the expected one: it is not run."
        }
        # The uv installer in turn checks the SHA256 of the binary it downloads.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { Fail "uv could not be installed." }
    } finally {
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }
    $uv = $localUv
    if (-not (Test-Path $uv)) { Fail "uv was not installed in $uv." }
}

Write-Host "Installing Scrinium..."
& $uv tool install --reinstall --quiet --python 3.12 $wheel.FullName -c $constraints
if ($LASTEXITCODE -ne 0) { Fail "Scrinium could not be installed." }
$binDir = (& $uv tool dir --bin).Trim()
$scrinium = Join-Path $binDir "scrinium.exe"
if (-not (Test-Path $scrinium)) { Fail "Scrinium was not installed in $scrinium." }

Write-Host "Downloading the tools and the search model (about 0.7 GB the first time)..."
& $scrinium tools install
if ($LASTEXITCODE -ne 0) { Fail "Not everything could be downloaded. Run this script again to continue." }

Write-Host ""
& $scrinium doctor
Write-Host ""
if (($env:PATH -split ";") -contains $binDir) {
    Write-Host "Open Scrinium with: scrinium start"
} else {
    & $uv tool update-shell | Out-Null
    Write-Host "Open a new terminal and start Scrinium with: scrinium start"
    Write-Host "(or right now: & `"$scrinium`" start)"
}
