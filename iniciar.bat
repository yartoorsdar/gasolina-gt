@echo off
REM gasolina-gt — Iniciar dashboard local en http://localhost:9090
REM Paso 1: Actualizar datos y exportar JSON
python collector/main.py --all --export
if errorlevel 1 (
    echo.
    echo [!] Error al actualizar datos. Intentando continuar de todas formas...
)

REM Paso 2: Iniciar servidor web en puerto 9090
echo.
echo Iniciando dashboard en http://localhost:9090/web/index.html ...
python serve.py 9090
