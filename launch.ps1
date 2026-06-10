<#
.SYNOPSIS
    Lance le dashboard Alternance Search.
.DESCRIPTION
    Active le venv et demarre uvicorn.
    Usage: .\launch.ps1
#>

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $root "alternance-search"
$python = Join-Path (Join-Path (Join-Path $project ".venv") "Scripts") "python.exe"
$port = 8000
$url = "http://127.0.0.1:$port"

Write-Host ""
Write-Host ("=" * 55) -ForegroundColor Cyan
Write-Host "  Alternance Search - Super Agregator" -ForegroundColor Cyan
Write-Host "  Dashboard : $url" -ForegroundColor Cyan
Write-Host "  API Docs  : ${url}/docs" -ForegroundColor Cyan
Write-Host ("=" * 55) -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $python)) {
    Write-Host "[ERREUR] Environnement virtuel introuvable : $python" -ForegroundColor Red
    Write-Host ""
    Write-Host "Creez-le avec :" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv"
    Write-Host "    .venv\Scripts\pip install -e ."
    Write-Host ""
    pause
    exit 1
}

Push-Location $project

Write-Host "[INFO] Demarrage du serveur sur $url ..." -ForegroundColor Green
Write-Host "[INFO] Ctrl+C pour arreter." -ForegroundColor DarkGray
Write-Host ""

try {
    & $python -m uvicorn src.webapp.main:app --host 127.0.0.1 --port $port --reload
} finally {
    Pop-Location
}
