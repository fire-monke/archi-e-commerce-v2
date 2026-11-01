@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo       Lancement du Payment Service
echo ========================================

REM Creer l'environnement virtuel s'il n'existe pas encore.
if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement virtuel...
    py -3.11 -m venv .venv
    if errorlevel 1 goto error
)

REM Installer ou mettre a jour les dependances du projet.
echo Verification des dependances...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto error

echo.
echo Le service demarre sur http://127.0.0.1:8000
echo Documentation Swagger : http://127.0.0.1:8000/docs
echo Appuyez sur Ctrl+C pour arreter le service.
echo.

".venv\Scripts\python.exe" -m uvicorn payment_service.adapters.inbound.api:app --reload --app-dir src --no-access-log
if errorlevel 1 goto error
exit /b 0

:error
echo.
echo ERREUR : Le lancement a echoue. Verifiez que Python 3.11 ou plus recent est installe.
pause
exit /b 1
