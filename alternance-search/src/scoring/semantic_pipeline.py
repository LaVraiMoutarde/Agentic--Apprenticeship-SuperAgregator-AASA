"""
Semantic Pipeline — chaîne complète embed → index → retrieve → score.

Responsabilités :
- Générer les embeddings pour toutes les offres
- Construire/reconstruire l'index turbovec
- Ajouter les nouvelles offres à l'index après scraping
- Recherche sémantique pour pré-filtrage avant scoring LLM

Usage :
    pipe = SemanticPipeline()
    pipe.embed_and_index_all()          # rebuild complet
    pipe.search_prefilter(profile_text, top_k=200)  # pré-filtrage
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from config import settings
from src.store.repository import OfferRepository


class SemanticPipeline:
    """Pipeline embed + index + retrieve."""

    def __init__(self):
        self._embedder = None
        self._indexer = None
        self._repository = OfferRepository()
        self._retriever = None
        self.initialized = False

    def init(self) -> None:
        """Initialise tous les composants."""
        if self.initialized:
            return
        from src.embeddings.embedder import Embedder
        from src.search.indexer import Indexer
        from src.search.retriever import Retriever

        self._embedder = Embedder()
        self._indexer = Indexer()
        self._retriever = Retriever()
        self._retriever.wire(self._embedder, self._indexer, self._repository)
        self.initialized = True

    # ── Statut ──

    @property
    def index_size(self) -> int:
        if self._indexer and self._indexer._index is not None:
            return len(self._indexer._index)
        return 0

    # ── Indexing ──

    def embed_and_index_all(self) -> dict:
        """Reconstruit l'index à partir de toutes les offres en base.

        Returns:
            {"indexed": n, "elapsed_ms": ms}
        """
        self.init()
        t0 = time.monotonic()

        offers = self._repository.find(limit=100_000)
        if not offers:
            return {"indexed": 0, "elapsed_ms": 0.0}

        ids = [o.id for o in offers]
        vecs = self._embedder.embed_offer_batch(offers, show_progress=False)
        self._indexer.build(ids, vecs)

        elapsed = (time.monotonic() - t0) * 1000
        return {"indexed": len(ids), "elapsed_ms": round(elapsed, 1)}

    def index_new_offers(self, offer_ids: list[int]) -> int:
        """Ajoute de nouvelles offres à l'index existant.

        Args:
            offer_ids: Liste des IDs à indexer.

        Returns:
            Nombre d'offres indexées.
        """
        if not offer_ids:
            return 0
        self.init()

        offers = self._repository.find_by_ids(offer_ids)
        if not offers:
            return 0

        ids = [o.id for o in offers]
        vecs = self._embedder.embed_offer_batch(offers, show_progress=False)
        self._indexer.add(ids, vecs)
        return len(ids)

    # ── Pre-filtrage ──

    def search_prefilter(
        self,
        profile_text: str,
        top_k: int = 200,
    ) -> list:
        """Recherche sémantique des offres les plus proches du profil.

        Args:
            profile_text: Texte du profil candidat (à encoder en query).
            top_k: Nombre d'offres à retourner.

        Returns:
            Liste d'Offer, triées par similarité.
        """
        self.init()
        if self._indexer is None or self._indexer._index is None or len(self._indexer._index) == 0:
            return []

        query_vec = self._embedder.embed_query(profile_text)
        scores, ids = self._indexer.search(query_vec, top_k=top_k)
        if len(ids) == 0:
            return []

        id_list = [int(v) for v in ids.flatten()[:top_k]]
        return self._repository.find_by_ids(id_list)

    def search_by_profile_dict(self, profile: dict, top_k: int = 200) -> list:
        """Recherche sémantique à partir d'un profil JSON (dict)."""
        # Construire un texte de recherche à partir du profil
        parts = []
        for key in ("desired_role", "summary", "preferred_location", "preferred_contract"):
            val = profile.get(key, "")
            if val and isinstance(val, str) and val.strip():
                parts.append(val)
        skills = profile.get("skills", [])
        if skills:
            parts.append(" ".join(skills))
        education = profile.get("education_level", "")
        if education:
            parts.append(education)

        query = " ".join(parts) if parts else "alternance"
        return self.search_prefilter(query, top_k=top_k)
