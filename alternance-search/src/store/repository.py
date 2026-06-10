"""
Repository — couche d'accès aux données pour les offres.

Opérations CRUD complètes sur la table `offers` :
- upsert (déduplication par source+source_id)
- get_by_id / find / find_by_ids
- soft_delete / restore
- comptage et synchronisation embedding
"""

from __future__ import annotations

from datetime import datetime, timezone

from .database import get_session
from .models import Offer


class OfferRepository:
    """Repository CRUD pour les offres d'alternance."""

    # ── CRUD ──

    def upsert(self, offer: Offer) -> tuple[Offer, bool]:
        """Insère ou met à jour. Retourne (offer, is_new). Déduplication par (source, source_id)."""
        now = datetime.now(timezone.utc).isoformat()
        with get_session() as s:
            existing = (
                s.query(Offer)
                .filter(Offer.source == offer.source, Offer.source_id == offer.source_id)
                .first()
            )
            if existing:
                for col in [
                    "title", "company", "location", "region", "contract_type",
                    "domain", "required_level", "description",
                    "salary_min", "salary_max", "published_date",
                    "url", "contact_name", "contact_email",
                    "search_text", "raw_json",
                ]:
                    setattr(existing, col, getattr(offer, col))
                existing.scraped_date = offer.scraped_date
                existing.updated_at = now
                existing.is_active = 1
                s.commit()
                s.refresh(existing)
                return existing, False
            else:
                offer.created_at = now
                offer.updated_at = now
                s.add(offer)
                s.commit()
                s.refresh(offer)
                return offer, True

    def upsert_batch(self, offers: list[Offer]) -> dict[str, int]:
        """Insère ou met à jour un lot. Retourne {'new': n, 'updated': n}."""
        stats = {"new": 0, "updated": 0}
        for offer in offers:
            _, is_new = self.upsert(offer)
            if is_new:
                stats["new"] += 1
            else:
                stats["updated"] += 1
        return stats

    def get_by_id(self, offer_id: int) -> Offer | None:
        """Récupère une offre par son ID interne."""
        with get_session() as s:
            return s.get(Offer, offer_id)

    def soft_delete(self, offer_id: int) -> bool:
        """Soft-delete (is_active=0). Retourne True si trouvée."""
        with get_session() as s:
            offer = s.get(Offer, offer_id)
            if offer is None:
                return False
            offer.is_active = 0
            offer.updated_at = datetime.now(timezone.utc).isoformat()
            s.commit()
            return True

    # ── Requêtes ──

    def find(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        source: str | None = None,
    ) -> list[Offer]:
        """Récupère les offres actives, triées par date de scrape décroissante."""
        with get_session() as s:
            q = s.query(Offer).filter(Offer.is_active == 1)
            if source:
                q = q.filter(Offer.source == source)
            return q.order_by(Offer.scraped_date.desc()).limit(limit).offset(offset).all()

    def find_active(
        self,
        *,
        source: str | None = None,
        domain: str | None = None,
        required_level: str | None = None,
        region: str | None = None,
        contract_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Offer]:
        """Recherche filtrée d'offres actives, triées par date de scraping décroissante."""
        with get_session() as s:
            q = s.query(Offer).filter(Offer.is_active == 1)
            if source:
                q = q.filter(Offer.source == source)
            if domain:
                q = q.filter(Offer.domain == domain)
            if required_level:
                q = q.filter(Offer.required_level == required_level)
            if region:
                q = q.filter(Offer.region == region)
            if contract_type:
                q = q.filter(Offer.contract_type == contract_type)
            return q.order_by(Offer.scraped_date.desc()).limit(limit).offset(offset).all()

    def find_by_ids(self, ids: list[int]) -> list[Offer]:
        """Récupère plusieurs offres par IDs (actives uniquement)."""
        if not ids:
            return []
        with get_session() as s:
            return s.query(Offer).filter(Offer.id.in_(ids), Offer.is_active == 1).all()

    def count_active(self) -> int:
        """Nombre d'offres actives."""
        with get_session() as s:
            return s.query(Offer).filter(Offer.is_active == 1).count()

    def count_by_source(self) -> dict[str, int]:
        """Nombre d'offres actives par source."""
        with get_session() as s:
            from sqlalchemy import func
            rows = (
                s.query(Offer.source, func.count(Offer.id))
                .filter(Offer.is_active == 1)
                .group_by(Offer.source)
                .all()
            )
            return {src: cnt for src, cnt in rows}

    # ── Synchronisation embedding ──

    def get_ids_without_embedding(self, limit: int = 500) -> list[int]:
        """IDs des offres actives sans embedding_dim."""
        with get_session() as s:
            rows = (
                s.query(Offer.id)
                .filter(Offer.is_active == 1, Offer.embedding_dim.is_(None))
                .limit(limit)
                .all()
            )
            return [r[0] for r in rows]

    def update_embedding_metadata(self, offer_id: int, dim: int, search_text: str) -> None:
        """Marque une offre comme indexée."""
        with get_session() as s:
            offer = s.get(Offer, offer_id)
            if offer:
                offer.embedding_dim = dim
                offer.search_text = search_text
                offer.updated_at = datetime.now(timezone.utc).isoformat()
                s.commit()

    def get_all_active_ids(self) -> set[int]:
        """Ensemble des IDs actifs (pour sync index)."""
        with get_session() as s:
            rows = s.query(Offer.id).filter(Offer.is_active == 1).all()
            return {r[0] for r in rows}

    # ── Statistiques ──

    def stats(self) -> dict:
        """Résumé statistique de la base."""
        from sqlalchemy import func
        active = self.count_active()
        by_source = self.count_by_source()
        with get_session() as s:
            total = s.query(func.count(Offer.id)).scalar() or 0
            with_emb = (
                s.query(func.count(Offer.id))
                .filter(Offer.is_active == 1, Offer.embedding_dim.isnot(None))
                .scalar()
            ) or 0
        return {
            "total_offers": total,
            "active_offers": active,
            "with_embedding": with_emb,
            "without_embedding": active - with_emb,
            "by_source": by_source,
        }
