"""
Agrégateur de routes du webapp.

Toutes les routes sont enregistrées ici et montées sur le FastAPI principal.
"""

from fastapi import APIRouter
from .dashboard import router as dashboard_router
from .api import router as api_router
from .profile import router as profile_router

# ── Router principal du webapp ──────────────────────────────────────
webapp_router = APIRouter()
webapp_router.include_router(dashboard_router)
webapp_router.include_router(api_router)
webapp_router.include_router(profile_router)

__all__ = ["webapp_router"]
