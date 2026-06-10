"""
Routes API — endpoints REST pour le dashboard.

Endpoints:
    GET  /api/status          → état système
    POST /api/scrape/run      → lancer scraping
    POST /api/llm/run         → lancer scoring LLM
    POST /api/keywords        → ajouter mots-clés
    GET  /api/results         → résultats depuis la DB
    GET  /api/export/excel    → télécharger Excel
    GET  /api/jobs            → liste des jobs
    GET  /api/jobs/{job_id}   → statut d'un job

Architecture:
    - Les endpoints "run" créent un job et le lancent en background thread
    - Les endpoints "status/results" interrogent la base de données
    - Toute la logique métier est déléguée aux modules src/* existants
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.store import init_db, get_session, Offer, OfferRepository
from src.webapp.jobs import (
    create_job,
    get_job,
    list_jobs,
    update_job,
    Job,
    JobStatus,
)

router = APIRouter(prefix="/api")

# ── Références aux modules métier (lazy, évite imports circulaires) ───
_scraper_manager = None
_scorer = None
_keywords: list[str] = []
_profile: dict = {}


def _get_scraper_manager():
    """Retourne le ScraperManager (singleton)."""
    global _scraper_manager
    if _scraper_manager is None:
        try:
            from src.scraper.manager import ScraperManager
            _scraper_manager = ScraperManager()
            # Enregistrer les scrapers disponibles
            try:
                from src.scraper.plugins import (
                    HelloWorkScraper, IndeedScraper, IQuestaScraper,
                    JeunesDAvenirsScraper, JobTeaserEnseaScraper,
                    MoodleEnseaScraper, LaBonneAlternanceScraper, WTJJScraper,
                )
                _scraper_manager.add(HelloWorkScraper())
                _scraper_manager.add(IndeedScraper())
                _scraper_manager.add(IQuestaScraper())
                _scraper_manager.add(JeunesDAvenirsScraper())
                _scraper_manager.add(JobTeaserEnseaScraper())
                _scraper_manager.add(MoodleEnseaScraper())
                _scraper_manager.add(LaBonneAlternanceScraper())
                _scraper_manager.add(WTJJScraper())
            except ImportError:
                pass  # Playwright non installé
        except Exception:
            _scraper_manager = None
    return _scraper_manager


# ═══════════════════════════════════════════════════════════════════════
# Modèles Pydantic
# ═══════════════════════════════════════════════════════════════════════

class KeywordsPayload(BaseModel):
    keywords: list[str]


class ProfilePayload(BaseModel):
    profile: dict


# ═══════════════════════════════════════════════════════════════════════
# GET /api/status
# ═══════════════════════════════════════════════════════════════════════

@router.get("/status")
async def system_status():
    """Retourne l'état global du système."""
    # Compte des offres en base
    try:
        init_db()
        with get_session() as session:
            total = session.query(Offer).filter(Offer.is_active == 1).count()
            # Scrapées aujourd'hui (approx)
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_count = (
                session.query(Offer)
                .filter(Offer.is_active == 1, Offer.scraped_date.like(f"{today}%"))
                .count()
            )
    except Exception:
        total, today_count = 0, 0

    # Jobs en cours
    jobs = list_jobs()
    running = [j for j in jobs if j["status"] in ("pending", "running")]

    # Scrapers registered
    manager = _get_scraper_manager()
    scrapers_registered = manager.registered if manager else []

    return {
        "total_offers": total,
        "offers_scraped_today": today_count,
        "scrapers_registered": scrapers_registered,
        "scrapers_count": len(scrapers_registered),
        "active_jobs": len(running),
        "jobs": jobs[:10],
        "keywords": _keywords,
        "profile": _profile,
    }


# ═══════════════════════════════════════════════════════════════════════
# POST /api/scrape/run
# ═══════════════════════════════════════════════════════════════════════

@router.post("/scrape/run")
async def trigger_scrape():
    """Lance le scraping global et retourne un job_id."""
    manager = _get_scraper_manager()
    if manager is None or not manager.registered:
        raise HTTPException(400, "Aucun scraper enregistré. Installez Playwright.")

    job_id = create_job("scrape")
    update_job(job_id, status="pending", log_line="Scraping job created")

    # Lancement en background
    def _run():
        update_job(job_id, status="running", log_line="Starting all scrapers...")
        try:
            query = " ".join(_keywords) if _keywords else "alternance"
            results = manager.run_all(
                query=query,
                max_pages=3,
            )
            total = sum(r.success_count for r in results.values())
            failures = sum(1 for r in results.values() if r.status.value == "failed")

            # Stocker les offres en base
            try:
                from src.normalizer.pipeline import NormalizationPipeline
                repo = OfferRepository()
                pipe = NormalizationPipeline(log=None)
                stored = 0
                for name, result in results.items():
                    if result.offers:
                        clean = pipe.process(result.offers)
                        stats = repo.upsert_batch(clean)
                        stored += stats["new"] + stats["updated"]
                update_job(job_id, log_line=f"Stored {stored} offers in DB")
            except Exception as e:
                update_job(job_id, log_line=f"Store warning: {e}")

            update_job(
                job_id,
                status="done",
                result={"total_offers": total, "failures": failures, "sources": len(results)},
                log_line=f"Done: {total} offers, {failures} failures",
            )
        except Exception as e:
            update_job(job_id, status="failed", error=str(e), log_line=f"Failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    time.sleep(0.1)  # let thread start

    return {"job_id": job_id, "status": "started"}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/llm/run
# ═══════════════════════════════════════════════════════════════════════

@router.post("/llm/run")
async def trigger_llm_scoring():
    """Lance le scoring LLM sur les offres existantes."""
    if not _profile:
        raise HTTPException(400, "Configurez d'abord le profil candidat.")

    job_id = create_job("llm")

    def _run():
        update_job(job_id, status="running", log_line="Starting LLM scoring...")
        try:
            from src.scoring.llm_scorer import LLMScorer, CandidateProfile
            from src.store.repository import OfferRepository

            repo = OfferRepository()
            offers = repo.find(limit=50)

            if not offers:
                update_job(job_id, status="done", result={"scored": 0}, log_line="No offers to score")
                return

            profile = CandidateProfile(
                desired_role=_profile.get("desired_role", ""),
                skills=_profile.get("skills", []),
                education_level=_profile.get("education_level", ""),
                preferred_location=_profile.get("preferred_location", ""),
                preferred_domain=_profile.get("preferred_domain", ""),
            )

            scorer = LLMScorer()
            results = scorer.score_offers_batch(offers, profile)
            scored_count = len([r for r in results if r is not None])

            update_job(
                job_id,
                status="done",
                result={"scored": scored_count, "total": len(offers)},
                log_line=f"Done: {scored_count}/{len(offers)} scored",
            )
        except Exception as e:
            update_job(job_id, status="failed", error=str(e), log_line=f"Failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/keywords
# ═══════════════════════════════════════════════════════════════════════

@router.post("/keywords")
async def add_keywords(payload: KeywordsPayload):
    """Ajoute des mots-clés pour les scrapers."""
    global _keywords
    for kw in payload.keywords:
        kw = kw.strip()
        if kw and kw not in _keywords:
            _keywords.append(kw)
    return {"keywords": _keywords, "count": len(_keywords)}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/profile
# ═══════════════════════════════════════════════════════════════════════

@router.post("/profile")
async def set_profile(payload: ProfilePayload):
    """Définit le profil candidat."""
    global _profile
    _profile = payload.profile
    return {"profile": _profile}


# ═══════════════════════════════════════════════════════════════════════
# GET /api/results
# ═══════════════════════════════════════════════════════════════════════

@router.get("/results")
async def get_results(limit: int = Query(50, ge=1, le=500)):
    """Retourne les offres depuis la base, triées par date de scrape."""
    try:
        init_db()
        repo = OfferRepository()
        offers = repo.find(limit=limit)
        return [
            {
                "id": o.id,
                "title": o.title or "",
                "company": o.company or "",
                "location": o.location or "",
                "source": o.source or "",
                "url": o.url or "",
                "scraped_date": o.scraped_date or "",
                "score": o.data_quality_score or 0,
                "contract_type": o.contract_type or "",
            }
            for o in offers
        ]
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# GET /api/export/excel
# ═══════════════════════════════════════════════════════════════════════

@router.get("/export/excel")
async def export_excel():
    """Génère et retourne un fichier Excel des offres."""
    try:
        from src.export.excel import ExcelExporter

        init_db()
        repo = OfferRepository()
        offers = repo.find(limit=10_000)

        exporter = ExcelExporter()
        filepath = exporter.export_offers(offers, "export_dashboard.xlsx")

        return FileResponse(
            path=str(filepath),
            filename="export_dashboard.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# LLM Configuration
# ═══════════════════════════════════════════════════════════════════════

import json as _json

_LLM_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "llm_config.json"

# Provider presets
_PROVIDER_PRESETS = {
    "ollama":    {"model": "qwen2.5:7b",      "base_url": "http://localhost:11434/v1", "needs_key": False},
    "picoclaw":  {"model": "picoclaw:latest",  "base_url": "http://localhost:9090/v1",  "needs_key": False},
    "openai":    {"model": "gpt-4o-mini",      "base_url": "https://api.openai.com/v1","needs_key": True},
    "anthropic": {"model": "claude-3-haiku",   "base_url": "https://api.anthropic.com/v1","needs_key": True},
    "custom":    {"model": "",                 "base_url": "",                          "needs_key": False},
}


def _load_llm_config() -> dict:
    """Charge la config LLM depuis le fichier JSON."""
    if _LLM_CONFIG_PATH.exists():
        return _json.loads(_LLM_CONFIG_PATH.read_text(encoding="utf-8"))
    return {"provider": "ollama", "model": "", "base_url": "", "api_key": ""}


def _save_llm_config(config: dict) -> None:
    """Sauvegarde la config LLM dans le fichier JSON."""
    _LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    config["configured_at"] = datetime.now(timezone.utc).isoformat()
    _LLM_CONFIG_PATH.write_text(_json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


class LLMConfigPayload(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""


@router.get("/llm/config")
async def get_llm_config():
    """Retourne la config LLM (clé API masquée)."""
    cfg = _load_llm_config()
    safe = {
        "provider": cfg.get("provider", ""),
        "model": cfg.get("model", ""),
        "base_url": cfg.get("base_url", ""),
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_preview": ("••••" + cfg["api_key"][-4:]) if cfg.get("api_key") and len(cfg.get("api_key", "")) > 4 else "",
        "configured_at": cfg.get("configured_at", ""),
    }
    return safe


@router.post("/llm/config")
async def set_llm_config(payload: LLMConfigPayload):
    """Sauvegarde la config LLM."""
    cfg = {
        "provider": payload.provider,
        "model": payload.model,
        "base_url": payload.base_url,
        "api_key": payload.api_key if payload.api_key else _load_llm_config().get("api_key", ""),
    }
    _save_llm_config(cfg)

    # Mettre à jour les settings runtime
    try:
        from config import settings
        settings.scorer.provider = payload.provider
        settings.scorer.model = payload.model
        settings.scorer.base_url = payload.base_url or settings.scorer.base_url
    except Exception:
        pass  # settings peut ne pas exister dans certains contextes

    return {"status": "saved", "provider": payload.provider}


@router.post("/llm/test")
async def test_llm_connection():
    """Teste la connexion au provider LLM configuré."""
    cfg = _load_llm_config()
    provider = cfg.get("provider", "")
    base_url = cfg.get("base_url", "")
    api_key = cfg.get("api_key", "")

    if not base_url:
        return {"status": "error", "message": "Base URL non configurée."}

    import urllib.request
    import urllib.error

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # GET /models pour tester la connexion
        url = base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = _json.loads(resp.read().decode())

        # Extraire la liste des modèles
        models = []
        if isinstance(data, dict) and "data" in data:
            models = [m.get("id", "") for m in data["data"]]
        elif isinstance(data, list):
            models = [m.get("id", "") if isinstance(m, dict) else str(m) for m in data]

        return {
            "status": "ok",
            "message": f"Connecté ({len(models)} modèles disponibles)",
            "models": models[:20],
        }
    except urllib.error.HTTPError as e:
        return {"status": "error", "message": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"Connexion refusée: {e.reason}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# GET /api/jobs — liste des jobs
# ═══════════════════════════════════════════════════════════════════════

@router.get("/jobs")
async def api_list_jobs(job_type: Optional[str] = Query(None)):
    """Liste les jobs récents."""
    return list_jobs(job_type=job_type, limit=30)


@router.get("/jobs/{job_id}")
async def api_get_job(job_id: str):
    """Récupère le statut d'un job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} introuvable")
    return job.to_dict()
