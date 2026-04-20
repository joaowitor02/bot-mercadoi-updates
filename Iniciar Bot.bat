@echo off
title Bot Mercadoi
cd /d "%~dp0"
echo ==========================================
echo         BOT MERCADOI v3.1
echo ==========================================
echo.
echo  [1] Processar pendentes agora (uma vez)
echo  [2] Modo Watch - verificar automaticamente
echo.
set /p OPCAO="Escolha uma opcao (1 ou 2): "

if "%OPCAO%"=="2" goto watch

:normal
echo.
echo Iniciando processamento unico...
echo.
py main.py
echo.
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] O bot encerrou com erro. Verifique as mensagens acima.
) else (
    echo [OK] Processamento concluido com sucesso.
)
goto fim

:watch
echo.
set /p MINUTOS="Intervalo em minutos entre verificacoes (Enter = 5): "
if "%MINUTOS%"=="" set MINUTOS=5
echo.
echo Iniciando modo watch a cada %MINUTOS% minuto(s)...
echo Pressione CTRL+C para parar.
echo.
py main.py --watch %MINUTOS%

:fim
echo.
pause
