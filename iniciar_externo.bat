@echo off
REM gasolina-gt — Iniciar dashboard + ngrok para acceso externo
REM Uso: iniciar_externo.bat

title GasolinaGT Dashboard (ngrok)

REM ── Paso 1: Actualizar datos y exportar JSON ──────────────────────
python collector/main.py --all --export
if errorlevel 1 (
    echo [!] Error al actualizar datos. Continuando con datos existentes...
)

REM ── Paso 2: Iniciar servidor web en puerto 9090 ──────────────────
echo.
echo [=] Iniciando servidor local en puerto 9090...
start "GasolinaGT Server" cmd /c "python serve.py 9090"
timeout /t 3 /nobreak >nul

REM ── Paso 3: Iniciar ngrok tunnel ────────────────────────────────
echo [=] Iniciando ngrok tunnel...
set NGROK_FOUND=0

where ngrok >nul 2>&1
if %errorlevel% equ 0 (
    set NGROK_FOUND=1
) else (
    REM Buscar en directorios comunes de winget/choco
    where "%LOCALAPPDATA%\ngrok\ngrok.exe" >nul 2>&1
    if %errorlevel% equ 0 (
        set "NGROK_PATH=%LOCALAPPDATA%\ngrok\ngrok.exe"
        set NGROK_FOUND=1
    ) else (
        where "%ProgramFiles%\ngrok\ngrok.exe" >nul 2>&1
        if %errorlevel% equ 0 (
            set "NGROK_PATH=%ProgramFiles%\ngrok\ngrok.exe"
            set NGROK_FOUND=1
        )
    )
)

if %NGROK_FOUND% equ 0 (
    echo.
    echo [X] ngrok no encontrado. Instalar con: winget install ngrok
    echo [X] Luego ejecutar este script de nuevo.
    echo.
    echo Servidor local disponible en: http://192.168.0.11:9090/web/index.html
    pause
    exit /b 1
)

if defined NGROK_PATH (
    set "NGROK_CMD=%NGROK_PATH%"
) else (
    set "NGROK_CMD=ngrok"
)

echo [=] Iniciando tunnel...
%NGROK_CMD% http 9090 --log=stdout
