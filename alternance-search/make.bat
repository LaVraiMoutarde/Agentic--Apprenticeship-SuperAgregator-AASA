@echo off
REM ═══════════════════════════════════════════════════════════════
REM Alternance Search — make.bat (Windows)
REM ═══════════════════════════════════════════════════════════════
REM Usage : .\make.bat setup | scrape | index | search | serve | clean
REM ═══════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

set PYTHON=.venv\Scripts\python.exe
set PIP=.venv\Scripts\pip.exe
set PORT=8000

if "%1"=="" (
    echo Usage: make.bat [setup^|scrape^|pipeline^|index^|search^|serve^|auth^|db-demo^|test^|clean^|clean-all]
    exit /b 1
)

goto :%1 2>nul || (
    echo Cible inconnue : %1
    echo Cibles : setup, scrape, pipeline, index, search, serve, auth, db-demo, test, clean, clean-all
    exit /b 1
)

:venv
    if not exist ".venv\Scripts\python.exe" (
        echo [INFO] Creation du virtualenv...
        python -m venv .venv
        echo [OK] Virtualenv cree.
    ) else (
        echo [OK] Virtualenv deja present.
    )
    goto :eof

:setup
    call :venv
    echo [INFO] Installation des dependances...
    %PIP% install --upgrade pip
    %PIP% install -e ".[dev]"
    echo [OK] Dependances installees.
    echo [INFO] Installation du navigateur Playwright...
    %PYTHON% -m playwright install chromium
    echo [OK] Chromium Playwright installe.
    echo [INFO] Initialisation de la base de donnees...
    %PYTHON% -m scripts.db init
    echo [OK] Base de donnees initialisee.
    echo.
    echo ============================================
    echo  Setup termine !
    echo  Lancez 'make.bat serve' pour le dashboard
    echo ============================================
    goto :eof

:db-demo
    call :venv
    %PYTHON% -m scripts.db demo
    goto :eof

:scrape
    call :venv
    %PYTHON% -m scripts.scrape --sources all --query "informatique" --max-pages 3
    goto :eof

:pipeline
    call :venv
    %PYTHON% -m scripts.pipeline --sources all --query "alternance" --max-pages 3
    goto :eof

:index
    call :venv
    %PYTHON% -m scripts.index --build
    goto :eof

:search
    call :venv
    %PYTHON% -m scripts.search --query "%2" --k 20 --export --output exports\resultats.xlsx
    goto :eof

:serve
    call :venv
    echo [INFO] Dashboard : http://127.0.0.1:%PORT%
    echo [INFO] API Docs  : http://127.0.0.1:%PORT%/docs
    %PYTHON% -m uvicorn src.webapp.main:app --host 127.0.0.1 --port %PORT% --reload
    goto :eof

:auth
    call :venv
    %PYTHON% -m scripts.save_auth
    goto :eof

:test
    call :venv
    %PYTHON% -m pytest tests/ -v
    goto :eof

:clean
    echo [INFO] Nettoyage...
    if exist data\*.db del /q data\*.db 2>nul
    if exist data\*.tvim del /q data\*.tvim 2>nul
    if exist data\*.db-journal del /q data\*.db-journal 2>nul
    if exist data\*.db-wal del /q data\*.db-wal 2>nul
    if exist exports\*.xlsx del /q exports\*.xlsx 2>nul
    if exist logs\*.log del /q logs\*.log 2>nul
    echo [OK] Nettoye.
    goto :eof

:clean-all
    call :clean
    if exist .venv rmdir /s /q .venv
    for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
    for /d /r . %%d in (*.egg-info) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
    echo [OK] Nettoyage complet.
    goto :eof
