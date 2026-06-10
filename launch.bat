@echo off
REM ═══════════════════════════════════════════════════════════════
REM Alternance Search — Launch script (Windows)
REM ═══════════════════════════════════════════════════════════════
REM Usage : double-clic ou `.\launch.bat` dans un terminal
REM
REM L'application sera disponible sur :
REM   http://127.0.0.1:8000
REM ═══════════════════════════════════════════════════════════════

cd /d "%~dp0alternance-search"

echo.
echo  ╔═══════════════════════════════════════════════════════════╗
echo  ║     Alternance Search — Super Agregator                 ║
echo  ║     Dashboard : http://127.0.0.1:8000                   ║
echo  ║     API Docs  : http://127.0.0.1:8000/docs              ║
echo  ╚═══════════════════════════════════════════════════════════╝
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Environnement virtuel introuvable.
    echo.
    echo Creez-le avec :
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -e .
    echo.
    pause
    exit /b 1
)

echo [INFO] Demarrage du serveur...
echo [INFO] Appuyez sur Ctrl+C pour arreter.
echo.
".venv\Scripts\python.exe" -m uvicorn src.webapp.main:app --host 127.0.0.1 --port 8000 --reload

pause
