@echo off
setlocal
cd /d "%~dp0"

echo =============================================
echo  AsesorIA - Deploy en un clic (Railway)
echo =============================================
echo.
echo [1/3] Ejecutando la suite de tests...
python -X utf8 -m pytest tests/ -q
if errorlevel 1 (
    echo.
    echo [FALLO] Hay tests en rojo: NO se despliega.
    pause
    exit /b 1
)

echo.
echo [2/3] Subiendo el codigo a Railway...
railway up --detach
if errorlevel 1 (
    echo.
    echo [FALLO] No se pudo subir el codigo. Revisa el login: railway login
    pause
    exit /b 1
)

echo.
echo [3/3] Esperando a que la app este en linea (max 6 min)...
set /a intentos=0
:esperar
timeout /t 20 /nobreak >nul
set /a intentos+=1
curl.exe -s -o nul -w "%%{http_code}" https://asesoria.up.railway.app/health > "%TEMP%\asesoria_health.txt"
set /p CODE=<"%TEMP%\asesoria_health.txt"
if "%CODE%"=="200" (
    echo.
    echo [OK] App en linea: https://asesoria.up.railway.app
    railway status
    pause
    exit /b 0
)
if %intentos% geq 18 (
    echo.
    echo [AVISO] La app aun no responde 200 ^(codigo %CODE%^). Mira el dashboard de Railway.
    railway status
    pause
    exit /b 1
)
goto esperar
