@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo       Lancement des tests
echo ========================================

REM Creer l'environnement virtuel s'il n'existe pas encore.
if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement virtuel...
    py -3.11 -m venv .venv
    if errorlevel 1 goto error
)

REM Installer les dependances necessaires aux tests.
echo Verification des dependances...
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto error

echo.
echo Execution de pytest...
".venv\Scripts\python.exe" -m pytest
if errorlevel 1 goto error

echo.
echo Tous les tests sont passes.
pause
exit /b 0

:error
echo.
echo ERREUR : L'installation des dependances ou l'execution des tests a echoue.
pause
exit /b 1
