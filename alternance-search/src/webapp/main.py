#!/usr/bin/env python3
"""
Point d'entrée FastAPI pour le webapp Alternance Search.

Usage:
    uvicorn src.webapp.main:app --reload --port 8000
    python src/webapp/main.py              # Lancement direct (dev)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.webapp.routes import webapp_router

# ═══════════════════════════════════════════════════════════════════════
# Application FastAPI
# ═══════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Alternance Search — Dashboard",
    description="Interface de pilotage du système de recherche d'alternance",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Montage des fichiers statiques ────────────────────────────────────
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ── Montage des routes ────────────────────────────────────────────────
app.include_router(webapp_router)


# ═══════════════════════════════════════════════════════════════════════
# Lancement direct (dev uniquement)
# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.webapp.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )
