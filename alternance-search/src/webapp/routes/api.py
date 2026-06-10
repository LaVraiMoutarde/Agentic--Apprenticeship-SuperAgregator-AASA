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
from src.scraper.criteria import SearchCriteria
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
_criteria = SearchCriteria()

_profile: dict = {}
_semantic_pipeline = None
_click_tracker = None


def _get_semantic_pipeline():
    """Retourne le SemanticPipeline (singleton)."""
    global _semantic_pipeline
    if _semantic_pipeline is None:
        try:
            from src.scoring.semantic_pipeline import SemanticPipeline
            _semantic_pipeline = SemanticPipeline()
        except ImportError:
            pass
    return _semantic_pipeline


def _get_click_tracker():
    """Retourne le ClickTracker (singleton)."""
    global _click_tracker
    if _click_tracker is None:
        from src.scoring.click_tracker import ClickTracker
        _click_tracker = ClickTracker()
    return _click_tracker


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


class KeywordsRemovePayload(BaseModel):
    keyword: str


class CriteriaPayload(BaseModel):
    search_terms: list[str] = []
    location: str = ""
    radius_km: int = 0
    education_levels: list[str] = []
    contract: str = ""


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
        "criteria": _criteria.to_dict(),
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
            location = _criteria.location
            queries = _criteria.active_queries
            total_offers = 0
            total_failures = 0
            total_sources = 0

            for qi, query in enumerate(queries):
                update_job(job_id, log_line=f"Terme {qi+1}/{len(queries)}: '{query}'...")
                results = manager.run_all(
                    query=query,
                    location=location,
                    max_pages=3,
                    criteria=_criteria if _criteria.has_filters else None,
                )
                total_offers += sum(r.success_count for r in results.values())
                total_failures += sum(1 for r in results.values() if r.status.value == "failed")
                total_sources = len(results)

                # Stocker les offres en base
                try:
                    from src.normalizer.pipeline import NormalizationPipeline
                    repo = OfferRepository()
                    pipe = NormalizationPipeline(log=None)
                    for name, result in results.items():
                        if result.offers:
                            clean = pipe.process(result.offers)
                            repo.upsert_batch(clean)
                except Exception as e:
                    update_job(job_id, log_line=f"Store warning: {e}")

            # Auto-indexation : mise à jour de l'index sémantique
            try:
                pipe = _get_semantic_pipeline()
                if pipe is not None:
                    update_job(job_id, log_line="Mise à jour de l'index sémantique...")
                    result = pipe.embed_and_index_all()
                    update_job(job_id, log_line=f"Index : {result['indexed']} offres encodées ({result['elapsed_ms']:.0f}ms)")
            except Exception as e:
                update_job(job_id, log_line=f"Indexation ignorée : {e}")

            update_job(
                job_id,
                status="done",
                result={"total_offers": total_offers, "failures": total_failures, "sources": total_sources, "queries": len(queries)},
                log_line=f"Done: {total_offers} offers from {len(queries)} queries, {total_failures} failures",
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
    """Lance le scoring LLM sur les offres existantes en utilisant le profil du chat builder.

    Si aucun profil n'a été construit via le chat, utilise le profil legacy (_profile).
    """
    # Récupérer le profil depuis le chat builder (import lazy pour éviter circ)
    from src.webapp.routes.profile import get_chat_profile

    chat_profile = get_chat_profile()

    # Fallback : profil legacy si le chat n'a rien produit
    profile_source = chat_profile if chat_profile and any(
        v for v in chat_profile.values() if v
    ) else _profile

    if not profile_source:
        raise HTTPException(400, "Configurez d'abord un profil candidat via le chat (ou le profil legacy).")

    job_id = create_job("llm")

    def _run():
        update_job(job_id, status="running", log_line="Starting LLM scoring...")
        try:
            from src.scoring.llm_scorer import LLMScorer, CandidateProfile
            from src.store.repository import OfferRepository

            update_job(job_id, log_line="Chargement du profil...")

            # Mapper le profil JSON (chat builder) vers CandidateProfile
            profile = CandidateProfile(
                current_level=profile_source.get("education_level", ""),
                target_level=profile_source.get("education_level", ""),
                domain=profile_source.get("desired_role", ""),
                skills=profile_source.get("skills", []),
                languages=profile_source.get("languages", []),
                preferred_locations=(
                    [profile_source["preferred_location"]]
                    if profile_source.get("preferred_location")
                    else []
                ),
                preferred_contract=profile_source.get("preferred_contract", ""),
                project=(
                    profile_source.get("summary", "")
                    or profile_source.get("project", "")
                ),
            )

            # Étape 1 : Pré-filtrage sémantique (si l'index est dispo)
            pipe = _get_semantic_pipeline()
            repo = OfferRepository()
            total_in_db = repo.count_all()

            if pipe is not None and pipe.index_size > 0:
                update_job(job_id, log_line=f"Index trouvé ({pipe.index_size} offres), pré-filtrage sémantique...")
                offers = pipe.search_by_profile_dict(profile_source, top_k=200)
                update_job(job_id, log_line=f"Pré-filtrage : {len(offers)} offres candidates (sur {total_in_db} en base)")
            else:
                update_job(job_id, log_line="Pas d'index sémantique, scoring sur les 50 plus récentes...")
                offers = repo.find(limit=50)

            if not offers:
                update_job(job_id, status="done", result={"scored": 0, "total_in_db": total_in_db},
                           log_line="No offers to score")
                return

            # Étape 2 : Scoring LLM sur les offres pré-filtrées
            update_job(job_id, log_line=f"Scoring LLM sur {len(offers)} offres...")
            scorer = LLMScorer()

            # Convertir les Offer en SearchResult (format attendu par le scorer)
            from src.search.retriever import SearchResult
            search_results = [
                SearchResult(offer=o, similarity_score=0.0, rank=i+1)
                for i, o in enumerate(offers)
            ]

            scored_results = scorer.score_offers_batch(profile, search_results)
            scored_count = len([r for r in scored_results if r is not None])

            # Persister les scores en base
            try:
                saved = 0
                for sr in scored_results:
                    if sr is None:
                        continue
                    offer_id = sr.search_result.offer.id
                    score_val = sr.llm_score.global_score  # /100
                    if score_val and score_val > 0:
                        with get_session() as sess:
                            sess.query(Offer).filter(Offer.id == offer_id).update(
                                {"llm_score": score_val / 100.0}
                            )
                            sess.commit()
                        saved += 1
                if saved:
                    update_job(job_id, log_line=f"{saved} scores sauvegardés en base")
            except Exception as e:
                update_job(job_id, log_line=f"Sauvegarde scores ignorée : {e}")

            update_job(
                job_id,
                status="done",
                result={"scored": scored_count, "total": len(offers), "total_in_db": total_in_db,
                        "prefiltered": pipe is not None and pipe.index_size > 0},
                log_line=f"Done: {scored_count}/{len(offers)} scored (pre-filtered from {total_in_db} offers)",
            )
        except Exception as e:
            update_job(job_id, status="failed", error=str(e), log_line=f"Failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


# ═══════════════════════════════════════════════════════════════════════
# POST /api/index/build
# ═══════════════════════════════════════════════════════════════════════

@router.post("/index/build")
async def build_index():
    """Construit/reconstruit l'index sémantique à partir de toutes les offres en base."""
    job_id = create_job("index")

    def _run():
        update_job(job_id, status="running", log_line="Building semantic index...")
        try:
            pipe = _get_semantic_pipeline()
            if pipe is None:
                update_job(job_id, status="failed", error="pip install sentence-transformers turbovec")
                return
            result = pipe.embed_and_index_all()
            update_job(
                job_id, status="done",
                result=result,
                log_line=f"Indexed {result['indexed']} offers in {result['elapsed_ms']:.0f}ms",
            )
        except Exception as e:
            update_job(job_id, status="failed", error=str(e), log_line=f"Index failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "started"}


# ═══════════════════════════════════════════════════════════════════════
# GET /api/index/status
# ═══════════════════════════════════════════════════════════════════════

@router.get("/index/status")
async def index_status():
    """Retourne le statut de l'index sémantique."""
    pipe = _get_semantic_pipeline()
    if pipe is None:
        return {"ready": False, "size": 0, "message": "Dépendances manquantes (sentence-transformers/turbovec)"}
    pipe.init()
    return {"ready": pipe.index_size > 0, "size": pipe.index_size}


# ═══════════════════════════════════════════════════════════════════════
# Feedback loop — track clicks for scoring boost
# ═══════════════════════════════════════════════════════════════════════

@router.post("/feedback/click")
async def record_click(payload: dict):
    """Enregistre un clic sur une offre."""
    try:
        tracker = _get_click_tracker()
        tracker.record_click(payload)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/feedback/stats")
async def feedback_stats():
    """Statistiques du feedback utilisateur."""
    tracker = _get_click_tracker()
    return tracker.get_stats()


@router.delete("/feedback/reset")
async def feedback_reset():
    """Réinitialise tout l'historique des clics."""
    tracker = _get_click_tracker()
    tracker.reset()
    return {"status": "reset"}


# ═══════════════════════════════════════════════════════════════════════
# CRITERIA endpoints
# ═══════════════════════════════════════════════════════════════════════

@router.get("/criteria")
async def get_criteria():
    """Retourne les critères actuels."""
    return _criteria.to_dict()


@router.post("/criteria/save")
async def save_criteria(payload: CriteriaPayload):
    """Enregistre les critères (termes de recherche, localisation, niveau, contrat…)."""
    global _criteria
    _criteria.search_terms = payload.search_terms
    _criteria.location = payload.location
    _criteria.radius_km = payload.radius_km
    _criteria.education_levels = payload.education_levels
    _criteria.contract = payload.contract
    return _criteria.to_dict()


@router.delete("/criteria/reset")
async def reset_criteria():
    """Réinitialise tous les critères."""
    global _criteria
    _criteria = SearchCriteria()
    return _criteria.to_dict()


# ═══════════════════════════════════════════════════════════════════════
# GET/POST /api/profile (legacy — pour compatibilité temporaire)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/profile")
async def set_profile(payload: ProfilePayload):
    """Définit le profil candidat (legacy)."""
    global _profile
    _profile = payload.profile
    return {"profile": _profile}


# ═══════════════════════════════════════════════════════════════════════
# Helper — sérialisation d'une offre
# ═══════════════════════════════════════════════════════════════════════

def _offer_dict(o) -> dict:
    return {
        "id": o.id,
        "title": o.title or "",
        "company": o.company or "",
        "location": o.location or "",
        "region": o.region or "",
        "source": o.source or "",
        "source_id": o.source_id or "",
        "url": o.url or "",
        "contract_type": o.contract_type or "",
        "required_level": o.required_level or "",
        "domain": o.domain or "",
        "description": (o.description or "")[:500],
        "scraped_date": o.scraped_date or "",
        "score": o.llm_score or o.data_quality_score or 0,
    }


# ═══════════════════════════════════════════════════════════════════════
# GET /api/results
# ═══════════════════════════════════════════════════════════════════════

@router.get("/results")
async def get_results(
    limit: int = Query(50, ge=1, le=500),
    sort: str = "score",
):
    """Retourne les offres depuis la base, triées par score LLM ou par date."""
    try:
        init_db()
        repo = OfferRepository()
        order_by_score = sort == "score"
        offer_list = repo.find(limit=limit, order_by_score=order_by_score)
        return [_offer_dict(o) for o in offer_list]
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# GET /api/results/search
# ═══════════════════════════════════════════════════════════════════════

@router.get("/results/search")
async def search_results(
    q: str = "",
    source: str = "",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Recherche texte dans les offres (titre, entreprise, description)."""
    try:
        init_db()
        repo = OfferRepository()
        offers = repo.search(query=q, source=source or None, limit=limit, offset=offset)
        total = repo.count_all()
        return {"offers": [_offer_dict(o) for o in offers], "total": total}
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════
# GET /api/results/sources
# ═══════════════════════════════════════════════════════════════════════

@router.get("/results/sources")
async def get_sources():
    """Retourne la liste des sources disponibles."""
    try:
        init_db()
        with get_session() as s:
            rows = s.query(Offer.source).filter(Offer.is_active == 1).distinct().all()
            return {"sources": [r[0] for r in rows]}
    except Exception as e:
        return {"sources": []}


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
# DELETE /api/db/clear
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/db/clear")
async def clear_database():
    """Supprime toutes les offres de la base de données.

    Les tables restent intactes, seules les données sont effacées.
    Utile pour repartir à zéro après des tests.
    """
    try:
        from src.store import clear_db
        init_db()
        deleted = clear_db()
        return {"status": "ok", "deleted_count": deleted}
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
