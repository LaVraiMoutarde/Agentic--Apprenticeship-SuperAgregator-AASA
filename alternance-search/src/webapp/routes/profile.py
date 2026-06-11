"""
Routes API pour le Candidate Profile (Markdown persistant).

Stocke le profil dans data/profile_candidat.md sur disque.

Endpoints:
    POST /api/profile/manual    — sauvegarde le .md sur disque
    GET  /api/profile/current   — lit le .md depuis le disque
    DELETE /api/profile/reset   — reinitialise le .md
    POST /api/profile/generate-terms — genere des termes de recherche (appelle le LLM)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/profile")

# ── Fichier profil persistant ───────────────────────────────────────
_PROFILE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "profile_candidat.md"

DEFAULT_PROFILE_MD = """# Profil Candidat

## Poste recherche
(a remplir)

## Competences techniques
- 

## Niveau d'etudes
(a remplir)

## Formation
- 

## Experience
- 

## Localisation souhaitee
(a remplir)

## Contrat
(a remplir)

## Langues
- 

## Soft skills
- 

## Resume
(a remplir)
"""


def get_chat_profile() -> str:
    """Retourne le profil Markdown (lu depuis data/profile_candidat.md)."""
    if _PROFILE_PATH.exists():
        return _PROFILE_PATH.read_text(encoding="utf-8")
    return ""


def get_chat_profile_dict() -> dict:
    """Retourne le profil sous forme de dict (compatibilite API status)."""
    md = get_chat_profile()
    return {"profile_md": md, "has_content": bool(md and md.strip())}


# ═══════════════════════════════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════════════════════════════

class ManualProfilePayload(BaseModel):
    profile_md: str


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/manual
# ═══════════════════════════════════════════════════════════════════════

@router.post("/manual")
async def profile_manual_update(payload: ManualProfilePayload):
    """Sauvegarde le profil Markdown sur disque (data/profile_candidat.md)."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(payload.profile_md, encoding="utf-8")
    return {"status": "saved", "path": str(_PROFILE_PATH)}


# ═══════════════════════════════════════════════════════════════════════
# GET /api/profile/current
# ═══════════════════════════════════════════════════════════════════════

@router.get("/current")
async def profile_current():
    """Lit le profil Markdown depuis le disque."""
    if _PROFILE_PATH.exists():
        profile_md = _PROFILE_PATH.read_text(encoding="utf-8")
    else:
        profile_md = ""
    return {
        "profile_md": profile_md,
        "has_content": bool(profile_md and profile_md.strip()),
        "path": str(_PROFILE_PATH),
    }


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/profile/reset
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/reset")
async def profile_reset():
    """Efface le profil et le remplace par le template par defaut."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(DEFAULT_PROFILE_MD, encoding="utf-8")
    return {"status": "reset"}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/generate-terms
# ═══════════════════════════════════════════════════════════════════════

@router.post("/generate-terms")
async def profile_generate_terms():
    """Genere des termes de recherche a partir du profil Markdown (appelle le LLM)."""
    profile_md = get_chat_profile()

    if not profile_md or not profile_md.strip():
        raise HTTPException(400, "Le profil est vide. Remplissez-le et cliquez Appliquer d'abord.")

    # Charger la config LLM
    config_path = _PROFILE_PATH.parent / "llm_config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        cfg = {"provider": "ollama", "model": "qwen2.5:7b", "base_url": "http://localhost:11434/v1", "api_key": ""}

    from src.scoring.profile_builder import ProfileBuilder
    builder = ProfileBuilder(
        provider=cfg.get("provider", "ollama"),
        model=cfg.get("model", "qwen2.5:7b"),
        base_url=cfg.get("base_url", "http://localhost:11434/v1"),
        api_key=cfg.get("api_key", ""),
    )
    terms = builder.generate_search_terms(profile_md)

    if not terms:
        raise HTTPException(500, "Le LLM n'a pas pu generer de termes.")

    # Sauvegarder dans les criteres
    try:
        from src.webapp.routes.api import _criteria
        _criteria.search_terms = terms
    except ImportError:
        pass

    return {"terms": terms, "count": len(terms)}
