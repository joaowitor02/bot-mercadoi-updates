param(
    [switch]$RecreateVenv,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".build_venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Set-Location $Root

if ($RecreateVenv -and (Test-Path -LiteralPath $Venv)) {
    Remove-Item -LiteralPath $Venv -Recurse -Force
}

if (!(Test-Path -LiteralPath $Python)) {
    py -3 -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r "$Root\requirements.txt"
& $Python -m pip install pyinstaller

if ($Clean) {
    Remove-Item -LiteralPath "$Root\build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "$Root\dist" -Recurse -Force -ErrorAction SilentlyContinue
}

& $Python -m PyInstaller --clean --noconfirm "$Root\botmercadoi.spec"

$DistExe = Join-Path $Root "dist\BotMercadoi.exe"
if (!(Test-Path -LiteralPath $DistExe)) {
    throw "Executavel nao encontrado em $DistExe"
}

$DistConfig = Join-Path $Root "dist\config.json"
if (!(Test-Path -LiteralPath $DistConfig)) {
    Copy-Item -LiteralPath "$Root\config.example.json" -Destination $DistConfig
}

$SizeMb = [math]::Round((Get-Item -LiteralPath $DistExe).Length / 1MB, 1)
Write-Host ""
Write-Host "Build limpo concluido:" -ForegroundColor Green
Write-Host "  $DistExe"
Write-Host "  Tamanho: $SizeMb MB"
Write-Host ""
Write-Host "Configure dist\config.json antes de entregar ao cliente."
