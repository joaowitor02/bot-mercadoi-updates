param(
    [string]$ExePath = ".\dist\BotMercadoi.exe",
    [string]$CertPath = "",
    [string]$CertPassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (!(Test-Path -LiteralPath $ExePath)) {
    throw "Executavel nao encontrado: $ExePath"
}

if (!$CertPath -or !(Test-Path -LiteralPath $CertPath)) {
    throw "Informe um certificado .pfx valido em -CertPath. Assinatura real exige certificado de code signing."
}

$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if (!$signtool) {
    throw "signtool.exe nao encontrado. Instale o Windows SDK."
}

& $signtool.FullName sign /fd SHA256 /f $CertPath /p $CertPassword /tr $TimestampUrl /td SHA256 $ExePath
& $signtool.FullName verify /pa /v $ExePath

Write-Host "Executavel assinado e verificado: $ExePath" -ForegroundColor Green
