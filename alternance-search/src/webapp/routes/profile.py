"""
Routes API pour le Candidate Profile Builder.

Endpoints:
    POST /api/profile/chat      — envoie un message (+ fichiers) au LLM profile builder
    POST /api/profile/upload    — upload de fichiers, extraction de texte
    POST /api/profile/build     — finalise le profil à partir de tous les matériaux
    GET  /api/profile/current   — récupère le profil courant
    DELETE /api/profile/reset   — réinitialise la session
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src.scoring.profile_builder import ProfileBuilder, extract_text_from_bytes

router = APIRouter(prefix="/api/profile")

# ── In-memory session (one active profile session) ───────────────────
_session_id: str | None = None
_conversation: list[dict] = []  # [{role, content, attachments, timestamp}]
_collected_materials: list[str] = []  # raw texts from dropped files
_current_profile: dict = {}
_builder: ProfileBuilder | None = None

def get_chat_profile() -> dict:
    """Expose le profil construit par le chat pour d'autres modules (ex: LLM scoring)."""
    return _current_profile

def _get_builder() -> ProfileBuilder:
    """Retourne le ProfileBuilder configuré depuis llm_config.json."""
    global _builder
    if _builder is None:
        from config.settings import ScorerSettings
        scorer = ScorerSettings()
        _builder = ProfileBuilder(
            provider=scorer.provider,
            model=scorer.model,
            base_url=scorer.base_url,
        )
    return _builder


def _reload_builder() -> None:
    """Force reload du builder (après changement de config LLM)."""
    global _builder
    _builder = None


# ═══════════════════════════════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    message: str
    file_ids: list[str] = []  # IDs of previously uploaded files to attach


class ChatResponse(BaseModel):
    reply: str
    profile: dict
    profile_changed: bool


class BuildResponse(BaseModel):
    profile: dict


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/chat
# ═══════════════════════════════════════════════════════════════════════

@router.post("/chat", response_model=ChatResponse)
async def profile_chat(payload: ChatMessage):
    """
    Envoie un message au LLM profile builder.

    Le LLM reçoit l'historique de conversation + les textes des fichiers
    attachés (via file_ids). Il répond avec un message et le profil JSON mis à jour.
    """
    global _conversation, _current_profile, _session_id

    if _session_id is None:
        _session_id = uuid.uuid4().hex[:8]

    builder = _get_builder()

    # Collect attachment texts from file_ids (stored in memory by upload)
    attachment_texts = []
    # file_ids are indices into _collected_materials for simplicity
    for fid in payload.file_ids:
        try:
            idx = int(fid)
            if 0 <= idx < len(_collected_materials):
                attachment_texts.append(_collected_materials[idx])
        except (ValueError, IndexError):
            pass

    # Call the builder
    result = builder.chat(
        message=payload.message,
        attachment_texts=attachment_texts if attachment_texts else None,
        current_profile=_current_profile,
        history=_conversation,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    # Update state
    _current_profile = result.get("profile", _current_profile)

    # Record user message
    _conversation.append({
        "role": "user",
        "content": payload.message,
        "attachments": [
            {"name": f"doc-{fid}", "preview": _collected_materials[int(fid)][:200]}
            for fid in payload.file_ids
            if fid.isdigit() and int(fid) < len(_collected_materials)
        ],
    })

    # Record assistant reply
    _conversation.append({
        "role": "assistant",
        "content": result["reply"],
    })

    return ChatResponse(
        reply=result["reply"],
        profile=_current_profile,
        profile_changed=result.get("profile_changed", False),
    )


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/upload
# ═══════════════════════════════════════════════════════════════════════

@router.post("/upload")
async def profile_upload(file: UploadFile = File(...)):
    """
    Upload un fichier (CV, lettre de motivation, etc.) et extrait son texte.

    Retourne un file_id à attacher à un message chat.
    Le texte extrait est stocké en mémoire pour usage ultérieur.
    """
    global _session_id, _collected_materials

    if _session_id is None:
        _session_id = uuid.uuid4().hex[:8]

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(400, f"Erreur lecture fichier : {e}")

    if len(content) == 0:
        raise HTTPException(400, "Fichier vide")

    # Limit file size to 10MB
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Fichier trop volumineux (max 10 Mo)")

    # Extract text
    text = extract_text_from_bytes(file.filename or "document", content)

    # Store
    file_id = len(_collected_materials)
    _collected_materials.append(text)

    return {
        "file_id": str(file_id),
        "filename": file.filename,
        "size": len(content),
        "preview": text[:500],
        "text_length": len(text),
    }


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/build
# ═══════════════════════════════════════════════════════════════════════

@router.post("/build", response_model=BuildResponse)
async def profile_build():
    """
    Finalise le profil à partir de tous les matériaux collectés.

    Combine tous les textes extraits + conversation et demande au LLM
    de produire un profil final consolidé.
    """
    global _current_profile, _collected_materials

    builder = _get_builder()

    # Gather all conversation text
    conv_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in _conversation
    )

    all_materials = list(_collected_materials)
    if conv_text:
        all_materials.append(conv_text)

    result = builder.build_final_profile(
        materials=all_materials,
        partial_profile=_current_profile,
    )

    if result.get("profile"):
        _current_profile = result["profile"]

    return BuildResponse(profile=_current_profile)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/profile/current
# ═══════════════════════════════════════════════════════════════════════

@router.get("/current")
async def profile_current():
    """Retourne le profil courant et l'état de la session."""
    return {
        "session_id": _session_id,
        "profile": _current_profile,
        "conversation_length": len(_conversation),
        "materials_count": len(_collected_materials),
        "materials_preview": [
            m[:200] + "..." if len(m) > 200 else m
            for m in _collected_materials
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/profile/reset
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/reset")
async def profile_reset():
    """Réinitialise complètement la session de construction de profil."""
    global _session_id, _conversation, _collected_materials, _current_profile
    _session_id = None
    _conversation = []
    _collected_materials = []
    _current_profile = {}
    return {"status": "reset"}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile/generate-terms
# ═══════════════════════════════════════════════════════════════════════

@router.post("/generate-terms")
async def profile_generate_terms():
    """Génère automatiquement des termes de recherche à partir du profil construit par le chat.

    Appelle le LLM pour analyser le profil et proposer des intitulés de poste,
    domaines et technologies pertinents pour le scraping.

    Les termes générés sont automatiquement sauvegardés dans les critères de recherche.
    """
    global _current_profile

    if not _current_profile or not any(v for v in _current_profile.values() if v):
        raise HTTPException(400, "Construisez d'abord un profil via le chat.")

    builder = _get_builder()
    terms = builder.generate_search_terms(_current_profile)

    if not terms:
        raise HTTPException(500, "Le LLM n'a pas pu générer de termes.")

    # Sauvegarder automatiquement dans les critères
    try:
        from src.webapp.routes.api import _criteria
        _criteria.search_terms = terms
    except ImportError:
        pass

    return {"terms": terms, "count": len(terms)}
