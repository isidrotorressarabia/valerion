@echo off
setlocal
cd /d "%~dp0"
echo Preparando Valerion para Visual Studio Code...
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv .venv
if errorlevel 1 goto failed
goto dependencies
:usepython
python -m venv .venv
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" configurar.py
if errorlevel 1 goto failed
echo.
echo Listo. Abre esta carpeta en VS Code, selecciona .venv y presiona F5.
echo Completa DATABASE_URL y CLOUDINARY_URL en .env antes de iniciar.
echo Usuario: admin. Consulta tu clave en el archivo .env.
echo Alternativa: .venv\Scripts\python.exe main.py
pause
exit /b 0
:failed
echo.
echo No se pudo completar la instalacion. Revisa el error anterior.
echo Requiere Python 3.12 y conexion a Internet para instalar paquetes.
pause
exit /b 1
