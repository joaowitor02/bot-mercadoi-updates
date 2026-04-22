param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Clean) {
    Remove-Item -LiteralPath "$Root\build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "$Root\dist" -Recurse -Force -ErrorAction SilentlyContinue
}

python -m pip install --upgrade pyinstaller
python -m PyInstaller --clean --noconfirm "$Root\botmercadoi.spec"

$DistExe = Join-Path $Root "dist\BotMercadoi.exe"
if (!(Test-Path -LiteralPath $DistExe)) {
    throw "Executavel nao encontrado em $DistExe"
}

$DistConfig = Join-Path $Root "dist\config.json"
if (!(Test-Path -LiteralPath $DistConfig)) {
    Copy-Item -LiteralPath "$Root\config.example.json" -Destination $DistConfig
}

Write-Host ""
Write-Host "Build concluido:" -ForegroundColor Green
Write-Host "  $DistExe"
Write-Host ""
Write-Host "Antes de entregar ao cliente, configure dist\config.json com licenciamento_habilitado=true,"
Write-Host "licenca_chave e licenca_servidor_url."
