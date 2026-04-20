@echo off
title Bot Mercadoi — Painel Web
cd /d "%~dp0"
echo ==========================================
echo   Bot Mercadoi — Painel Web
echo ==========================================
echo.

echo Encerrando processos anteriores...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Verificando dependencias...
py -m pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo [AVISO] Falha ao instalar dependencias. Tentando continuar...
    echo.
)

start "" http://localhost:8000
py panel.py

echo.
pause
