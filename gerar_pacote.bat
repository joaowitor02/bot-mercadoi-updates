@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo  Bot Mercadoi — Gerador de Pacote para Cliente
echo  ================================================
echo.

REM Pasta e nome do ZIP
set "TEMP_PKG=%TEMP%\bot_mercadoi_pkg"
set "ZIP_SAIDA=%~dp0bot_mercadoi_cliente.zip"

REM Limpa pasta temporaria se existir
if exist "%TEMP_PKG%" rd /s /q "%TEMP_PKG%"
mkdir "%TEMP_PKG%"
mkdir "%TEMP_PKG%\modules"
mkdir "%TEMP_PKG%\panel_static"
mkdir "%TEMP_PKG%\logs"

echo Copiando arquivos...

REM Codigo
copy /Y "panel.py"        "%TEMP_PKG%\" > nul
copy /Y "main.py"         "%TEMP_PKG%\" > nul
copy /Y "requirements.txt" "%TEMP_PKG%\" > nul
copy /Y "version.py"      "%TEMP_PKG%\" > nul
copy /Y "CHANGELOG.md"    "%TEMP_PKG%\" > nul
copy /Y "LEIA-ME.md"      "%TEMP_PKG%\" > nul

REM Launcher
copy /Y "Abrir Painel.vbs" "%TEMP_PKG%\" > nul

REM Config de exemplo (cliente renomeia para config.json)
copy /Y "config.example.json" "%TEMP_PKG%\config.json" > nul

REM Modulos
xcopy /E /I /Q "modules\*" "%TEMP_PKG%\modules\" > nul

REM Frontend
xcopy /E /I /Q "panel_static\*" "%TEMP_PKG%\panel_static\" > nul

REM Suporte remoto (inclui cloudflared se existir)
if exist "cloudflared.exe" (
    copy /Y "cloudflared.exe" "%TEMP_PKG%\" > nul
    echo Suporte remoto incluido ^(cloudflared.exe^)
) else (
    echo AVISO: cloudflared.exe nao encontrado — suporte remoto nao incluido
)

echo Compactando...
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP_PKG%\*' -DestinationPath '%ZIP_SAIDA%' -Force"

if errorlevel 1 (
    echo ERRO ao criar o ZIP. Verifique se o PowerShell esta disponivel.
    rd /s /q "%TEMP_PKG%"
    pause
    exit /b 1
)

rd /s /q "%TEMP_PKG%"

echo.
echo  Pacote gerado com sucesso!
echo  Arquivo: %ZIP_SAIDA%
echo.
echo  Instrucoes para o cliente:
echo   1. Instalar Python 3.10+ em python.org (marcar "Add to PATH")
echo   2. Abrir config.json e preencher mercadoi_url e downloads_path
echo   3. Dar duplo clique em "Abrir Painel.vbs"
echo.
pause
