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

    # Récupérer le profil chat (pour affichage dashboard)
    try:
        from src.webapp.routes.profile import get_chat_profile, get_chat_profile_dict
        _chat_md = get_chat_profile()
        chat_profile = {
            "profile_md": _chat_md,
            "has_content": bool(_chat_md and _chat_md.strip()),
        }
    except ImportError:
        chat_profile = {"profile_md": "", "has_content": False}

    return {
        "total_offers": total,
        "offers_scraped_today": today_count,
        "scrapers_registered": scrapers_registered,
        "scrapers_count": len(scrapers_registered),
        "active_jobs": len(running),
        "jobs": jobs[:10],
        "criteria": _criteria.to_dict(),
        "profile": _profile,
        "chat_profile": chat_profile,
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

    # Vérifier qu'on a des termes de recherche
    queries = _criteria.active_queries
    if not queries:
        update_job(job_id, status="failed", log_line="Aucun terme de recherche. Configurez des critères d'abord.")
        return {"job_id": job_id, "status": "no_terms"}

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
# Helpers — extraction from Markdown profile
# ═══════════════════════════════════════════════════════════════════════

import re as _re

def _extract_md_field(md: str, field: str) -> str:
    """Extrait la valeur d'un champ Markdown (ex: '## Poste recherché')."""
    pattern = rf'##\s+{_re.escape(field)}\s*\n(.*?)(?:\n##|\n#|\Z)'
    match = _re.search(pattern, md, _re.DOTALL)
    if match:
        value = match.group(1).strip()
        if value.startswith('- '):
            value = value[2:].strip()
        return value
    return ""

def _extract_md_list(md: str, field: str) -> list[str]:
    """Extrait une liste à puces d'un champ Markdown."""
    pattern = rf'##\s+{_re.escape(field)}\s*\n(.*?)(?:\n##|\n#|\Z)'
    match = _re.search(pattern, md, _re.DOTALL)
    if not match:
        return []
    block = match.group(1)
    items = _re.findall(r'-\s+(.+)', block)
    return [item.strip() for item in items if item.strip()]


# ═══════════════════════════════════════════════════════════════════════
# POST /api/llm/run
# ═══════════════════════════════════════════════════════════════════════

@router.post("/llm/run")
async def trigger_llm_scoring(payload: dict = {}):
    """Lance le scoring LLM sur les offres existantes en utilisant le profil Markdown du chat builder.

    Body optionnel : {"max_candidates": 50} (10-200, défaut: settings.scorer.max_llm_candidates)
    """
    from src.webapp.routes.profile import get_chat_profile

    profile_md = get_chat_profile()  # Markdown string

    if not profile_md or not profile_md.strip():
        raise HTTPException(
            400,
            "Aucun profil candidat détecté. "
            "Construisez d'abord votre profil via le chat Candidate Profile, "
            "ou écrivez-le directement dans l'éditeur Markdown."
        )

    # Récupérer max_candidates depuis le payload (clampé 10-200)
    from config import settings
    _raw_max = payload.get("max_candidates", settings.scorer.max_llm_candidates)
    try:
        max_candidates = max(10, min(200, int(_raw_max)))
    except (TypeError, ValueError):
        max_candidates = settings.scorer.max_llm_candidates

    job_id = create_job("llm")

    def _run():
        update_job(job_id, status="running", log_line=f"Starting LLM scoring...")
        update_job(job_id, log_line=f"Max candidates: {max_candidates}")
        try:
            from src.scoring.llm_scorer import LLMScorer, CandidateProfile
            from src.store.repository import OfferRepository

            update_job(job_id, log_line="Chargement du profil...")

            # Construire un CandidateProfile à partir du markdown (pour le scoring LLM uniquement)
            profile = CandidateProfile(
                current_level=_extract_md_field(profile_md, "Niveau d'études") or _extract_md_field(profile_md, "Niveau d'etudes"),
                target_level=_extract_md_field(profile_md, "Niveau d'études") or _extract_md_field(profile_md, "Niveau d'etudes"),
                domain=_extract_md_field(profile_md, "Poste recherché") or _extract_md_field(profile_md, "Poste recherche"),
                skills=_extract_md_list(profile_md, "Compétences techniques") or _extract_md_list(profile_md, "Competences techniques"),
                languages=_extract_md_list(profile_md, "Langues"),
                preferred_locations=[_extract_md_field(profile_md, "Localisation souhaitée") or _extract_md_field(profile_md, "Localisation souhaitee")],
                preferred_contract=_extract_md_field(profile_md, "Contrat"),
                project=_extract_md_field(profile_md, "Résumé") or _extract_md_field(profile_md, "Resume") or profile_md[:500],
            )

            # Étape 1 : Pré-filtrage sémantique HYBRIDE (embedding + boost mots-clés)
            pipe = _get_semantic_pipeline()
            repo = OfferRepository()
            total_in_db = repo.count_all()

            if pipe is not None and pipe.index_size > 0:
                sc = _get_semantic_criteria()
                if sc.has_criteria:
                    # top_k pour search_hybrid : 2× max_candidates (max 500)
                    search_k = min(max_candidates * 2, 500)
                    update_job(job_id, log_line=f"Index trouvé ({pipe.index_size} offres), pré-filtrage hybride (top {search_k})...")
                    update_job(job_id, log_line=f"Query: {sc.to_query_text()}")
                    if sc.boost_keywords:
                        update_job(job_id, log_line=f"Boost mots-clés ({sc.boost_weight}): {', '.join(sc.boost_keywords)}")

                    hybrid_results = pipe.search_hybrid(sc, top_k=search_k)
                    offers = [offer for offer, final, base, matched in hybrid_results[:max_candidates]]
                    update_job(job_id, log_line=f"Pré-filtrage hybride : {len(offers)} offres candidates (sur {total_in_db} en base)")
                else:
                    update_job(job_id, log_line="Critères sémantiques vides, fallback sur recherche 'alternance'...")
                    offers = pipe.search_prefilter("alternance", top_k=max_candidates)
                    update_job(job_id, log_line=f"Pré-filtrage fallback : {len(offers)} offres candidates (sur {total_in_db} en base)")
            else:
                update_job(job_id, log_line=f"Pas d'index sémantique, scoring sur les {max_candidates} plus récentes...")
                offers = repo.find(limit=max_candidates)

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

            # Persister les scores en base (score + détails complets)
            try:
                repo = OfferRepository()
                saved = 0
                for sr in scored_results:
                    if sr is None:
                        continue
                    offer_id = sr.search_result.offer.id
                    score_val = sr.llm_score.global_score  # /100
                    if score_val and score_val > 0:
                        repo.update_llm_details(
                            offer_id,
                            score_val / 100.0,
                            sr.llm_score.to_dict(),
                        )
                        saved += 1
                if saved:
                    update_job(job_id, log_line=f"{saved} scores + détails sauvegardés en base")
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
# POST /api/llm/review/{offer_id}  —  analyse LLM d'une offre unique
# ═══════════════════════════════════════════════════════════════════════

@router.post("/llm/review/{offer_id}")
async def review_single_offer(offer_id: int):
    """Analyse une offre unique avec le LLM, en comparant au profil candidat.

    Si l'offre a déjà été scorée, retourne les détails en cache (base).
    Sinon, appelle le LLM pour une analyse focalisée sur une seule offre.
    """
    from src.webapp.routes.profile import get_chat_profile
    from src.scoring.llm_scorer import LLMScorer, CandidateProfile
    from src.store.repository import OfferRepository

    repo = OfferRepository()

    # 1. Vérifier le cache en base
    cached = repo.get_llm_details(offer_id)
    if cached:
        return {"offer_id": offer_id, "cached": True, "review": cached}

    # 2. Charger l'offre
    offer = repo.get_by_id(offer_id)
    if offer is None:
        raise HTTPException(404, f"Offre {offer_id} introuvable.")

    # 3. Charger le profil candidat
    profile_md = get_chat_profile()
    if not profile_md or not profile_md.strip():
        raise HTTPException(400, "Aucun profil candidat. Construisez-le d'abord via le chat.")

    profile = CandidateProfile(
        current_level=_extract_md_field(profile_md, "Niveau d'études") or _extract_md_field(profile_md, "Niveau d'etudes"),
        target_level=_extract_md_field(profile_md, "Niveau d'études") or _extract_md_field(profile_md, "Niveau d'etudes"),
        domain=_extract_md_field(profile_md, "Poste recherché") or _extract_md_field(profile_md, "Poste recherche"),
        skills=_extract_md_list(profile_md, "Compétences techniques") or _extract_md_list(profile_md, "Competences techniques"),
        languages=_extract_md_list(profile_md, "Langues"),
        preferred_locations=[_extract_md_field(profile_md, "Localisation souhaitée") or _extract_md_field(profile_md, "Localisation souhaitee")],
        preferred_contract=_extract_md_field(profile_md, "Contrat"),
        project=_extract_md_field(profile_md, "Résumé") or _extract_md_field(profile_md, "Resume") or profile_md[:500],
    )

    # 4. Récupérer le texte complet de la page web (pour une analyse plus riche)
    from src.scraper.page_fetcher import fetch_job_page_text
    import logging

    page_fetched = False
    full_page_text = None
    if offer.url:
        try:
            logging.info("Fetching full page for offer %d: %s", offer_id, offer.url[:80])
            full_page_text = fetch_job_page_text(offer.url)
            if full_page_text:
                page_fetched = True
                logging.info("Page fetched successfully: %d chars", len(full_page_text))
        except Exception:
            logging.warning("Failed to fetch page for offer %d", offer_id)

    # 5. Appeler le LLM pour une analyse focalisée (1 offre)
    scorer = LLMScorer()
    result = scorer.score_offer_with_llm(profile, offer, full_page_text=full_page_text)

    # 6. Sauvegarder en base (cache)
    score_val = result.global_score
    if score_val and score_val > 0:
        repo.update_llm_details(offer_id, score_val / 100.0, result.to_dict())

    return {
        "offer_id": offer_id,
        "cached": False,
        "page_fetched": page_fetched,
        "review": result.to_dict(),
    }


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
# SEMANTIC CRITERIA endpoints (pré-filtrage embedding + boost mots-clés)
# ═══════════════════════════════════════════════════════════════════════

_semantic_criteria = None


def _get_semantic_criteria():
    """Retourne les SemanticCriteria (singleton, chargé depuis le disque)."""
    global _semantic_criteria
    if _semantic_criteria is None:
        from src.scoring.semantic_criteria import SemanticCriteria
        _semantic_criteria = SemanticCriteria.load()
    return _semantic_criteria


class SemanticCriteriaPayload(BaseModel):
    desired_role: str = ""
    skills: str = ""
    boost_keywords: list[str] = []
    boost_weight: str = "modéré"


@router.get("/semantic-criteria")
async def get_semantic_criteria():
    """Retourne les critères sémantiques actuels."""
    sc = _get_semantic_criteria()
    return sc.to_dict()


@router.post("/semantic-criteria/save")
async def save_semantic_criteria(payload: SemanticCriteriaPayload):
    """Enregistre les critères sémantiques."""
    global _semantic_criteria
    from src.scoring.semantic_criteria import SemanticCriteria
    _semantic_criteria = SemanticCriteria(
        desired_role=payload.desired_role,
        skills=payload.skills,
        boost_keywords=payload.boost_keywords,
        boost_weight=payload.boost_weight,
    )
    _semantic_criteria.save()
    return _semantic_criteria.to_dict()


@router.delete("/semantic-criteria/reset")
async def reset_semantic_criteria():
    """Réinitialise les critères sémantiques."""
    global _semantic_criteria
    from src.scoring.semantic_criteria import SemanticCriteria
    _semantic_criteria = SemanticCriteria()
    _semantic_criteria.save()
    return _semantic_criteria.to_dict()


@router.delete("/semantic-criteria/reset")
async def reset_semantic_criteria():
    """Réinitialise les critères sémantiques."""
    global _semantic_criteria
    from src.scoring.semantic_criteria import SemanticCriteria
    _semantic_criteria = SemanticCriteria()
    _semantic_criteria.save()
    return _semantic_criteria.to_dict()


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
    # Score hybride : LLM > embedding > rien (ne pas montrer data_quality comme pertinence)
    score = o.llm_score or o.embedding_score
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
        "score": score,
        "score_type": "llm" if o.llm_score is not None else ("embedding" if o.embedding_score is not None else None),
        "has_review": o.llm_details is not None,
        "data_quality": o.data_quality_score,
    }


# ═══════════════════════════════════════════════════════════════════════
# GET /api/results  —  scoring live via search_hybrid() sur toute la DB
# ═══════════════════════════════════════════════════════════════════════

@router.get("/results")
async def get_results(
    top: int = Query(50, ge=10, le=500),
    sort_mode: str = Query("hybride", pattern="^(index|llm|hybride|date)$"),
):
    """Retourne le top-X des offres scorées en direct avec les critères actuels.

    Modes de tri :
    - index   : embedding_score pur (similarité cosinus)
    - llm     : llm_score pur (score LLM stocké en base)
    - hybride : 0.6×index + 0.4×llm (comme HybridRanker)
    - date    : par date de scraping décroissante
    """
    try:
        init_db()
        repo = OfferRepository()

        # Vérifier que l'index est prêt
        pipe = _get_semantic_pipeline()
        if pipe is None or pipe.index_size == 0:
            # Fallback : pas d'index, on retourne par date
            offers = repo.find(limit=top, order_by_score=False)
            return {
                "results": [_offer_dict(o) for o in offers],
                "total": repo.count_all(),
                "mode": "date",
                "warning": "Index non construit. Lancez 'Build Index' pour activer le scoring sémantique.",
            }

        # Critères sémantiques
        sc = _get_semantic_criteria()
        pipe.init()

        if sort_mode == "date":
            # Mode date : pas de scoring, retour direct
            offers = repo.find(limit=top, order_by_score=False)
            return {
                "results": [_offer_dict(o) for o in offers],
                "total": repo.count_all(),
                "mode": "date",
                "criteria": sc.to_dict() if sc.has_criteria else None,
            }

        # Modes avec scoring : index / llm / hybride
        # search_hybrid retourne (offer, final_score, base_score, matched_kw)
        if sc.has_criteria:
            hybrid_results = pipe.search_hybrid(sc, top_k=min(top * 2, 500))
        else:
            hybrid_results = []
            # Fallback sans critères : utiliser les embedding_score stockés
            offers = repo.find(limit=min(top * 3, 500), order_by_score=True)
            query_vec = pipe._embedder.embed_query("alternance")[0] if pipe._embedder else None
            for o in offers:
                if o.embedding_score is not None:
                    hybrid_results.append((o, o.embedding_score, o.embedding_score, 0))

        # Appliquer le mode de tri
        results = _rank_results(hybrid_results, sort_mode, top)

        return {
            "results": results,
            "total_candidates": len(hybrid_results),
            "total_db": repo.count_all(),
            "mode": sort_mode,
            "criteria": sc.to_dict() if sc.has_criteria else None,
            "query": sc.to_query_text() if sc.has_criteria else "alternance",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


def _rank_results(
    hybrid_results: list,
    sort_mode: str,
    top: int,
) -> list[dict]:
    """Trie et formate les résultats selon le mode choisi.

    hybrid_results : [(offer, final_score, base_score, matched_kw), ...]
    """
    scored: list[dict] = []
    for offer, final, base, matched in hybrid_results:
        llm = offer.llm_score or 0.0
        scored.append({
            "offer": offer,
            "embedding_score": round(float(base), 4),
            "llm_score": round(float(llm), 4),
            "final_score": round(float(final), 4),
            "matched_keywords": matched,
        })

    if sort_mode == "index":
        scored.sort(key=lambda x: x["embedding_score"], reverse=True)
    elif sort_mode == "llm":
        scored.sort(key=lambda x: x["llm_score"], reverse=True)
    elif sort_mode == "hybride":
        # Hybride : 0.6×embedding + 0.4×llm
        for s in scored:
            s["hybrid_score"] = 0.6 * s["embedding_score"] + 0.4 * s["llm_score"]
        scored.sort(key=lambda x: x["hybrid_score"], reverse=True)

    top_results = scored[:top]
    return [
        {
            "id": s["offer"].id,
            "title": s["offer"].title or "",
            "company": s["offer"].company or "",
            "location": s["offer"].location or "",
            "region": s["offer"].region or "",
            "source": s["offer"].source or "",
            "source_id": s["offer"].source_id or "",
            "url": s["offer"].url or "",
            "contract_type": s["offer"].contract_type or "",
            "required_level": s["offer"].required_level or "",
            "domain": s["offer"].domain or "",
            "description": (s["offer"].description or "")[:500],
            "scraped_date": s["offer"].scraped_date or "",
            "embedding_score": s["embedding_score"],
            "llm_score": s["llm_score"],
            "final_score": s["final_score"],
            "hybrid_score": s.get("hybrid_score", s["final_score"]),
            "matched_keywords": s["matched_keywords"],
            "has_review": s["offer"].llm_details is not None,
            "score_type": "llm" if s["offer"].llm_score is not None and s["offer"].llm_score > 0 else "embedding",
        }
        for s in top_results
    ]


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
# FAVORITES endpoints
# ═══════════════════════════════════════════════════════════════════════

_favorites_store = None


def _get_favorites_store():
    """Retourne le FavoritesStore (singleton)."""
    global _favorites_store
    if _favorites_store is None:
        from src.store.favorites import get_favorites_store
        _favorites_store = get_favorites_store()
    return _favorites_store


@router.post("/favorites/toggle")
async def toggle_favorite(payload: dict):
    """Ajoute ou retire une offre des favoris."""
    try:
        store = _get_favorites_store()
        result = store.toggle(payload)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/favorites")
async def list_favorites():
    """Retourne la liste de tous les favoris."""
    try:
        store = _get_favorites_store()
        return {
            "favorites": store.get_all(),
            "count": store.count(),
            "ids": list(store.get_favorite_ids()),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/favorites/check/{offer_id}")
async def check_favorite(offer_id: int):
    """Vérifie si une offre est dans les favoris."""
    try:
        store = _get_favorites_store()
        return {"favorite": store.is_favorite(offer_id)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/favorites/export")
async def export_favorites_excel():
    """Génère et retourne un fichier Excel des favoris."""
    try:
        from src.export.excel import ExcelExporter

        store = _get_favorites_store()
        favorites = store.get_all()

        if not favorites:
            raise HTTPException(400, "Aucun favori à exporter. Ajoutez d'abord des offres aux favoris.")

        exporter = ExcelExporter()
        filepath = exporter.export_favorites(favorites, "mes-favoris.xlsx")

        return FileResponse(
            path=str(filepath),
            filename="mes-favoris.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/favorites/clear")
async def clear_favorites():
    """Supprime tous les favoris."""
    try:
        store = _get_favorites_store()
        count = store.count()
        store.clear()
        return {"status": "ok", "cleared": count}
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

    # Recharger le ProfileBuilder pour qu'il utilise la nouvelle config
    try:
        from src.scoring.profile_builder import ProfileBuilder
        # Le builder sera recree au prochain appel generate-terms
    except ImportError:
        pass

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
