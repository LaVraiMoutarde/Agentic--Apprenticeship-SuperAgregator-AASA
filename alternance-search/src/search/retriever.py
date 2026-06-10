"""
Retriever — moteur de recherche semantique par embedding.

Interface generique, agnostique du profil utilisateur :
- search_by_embedding(embedding, top_k) → SearchResponse
- search_by_text(query, top_k) → SearchResponse
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from src.store.models import Offer


@dataclass
class SearchResult:
    """Un resultat de recherche."""
    offer: Offer
    similarity_score: float
    rank: int


@dataclass
class SearchResponse:
    """Reponse complete d'une recherche."""
    query: str | None
    results: list[SearchResult]
    total_candidates: int
    elapsed_ms: float


@dataclass
class SearchFilters:
    """Filtres post-recherche."""
    required_level: str | None = None
    domain: str | None = None
    region: str | None = None
    contract_type: str | None = None
    min_similarity: float = 0.0
    min_quality: float = 0.0


class Retriever:
    """Moteur de recherche semantique (agnostique du profil)."""

    def __init__(self) -> None:
        self._embedder = None
        self._indexer = None
        self._repository = None

    def wire(self, embedder, indexer, repository) -> None:
        self._embedder = embedder
        self._indexer = indexer
        self._repository = repository

    def search_by_embedding(
        self, embedding: np.ndarray, top_k: int = 20,
        filters: SearchFilters | None = None,
    ) -> SearchResponse:
        """Recherche par vecteur d'embedding."""
        t0 = time.monotonic()
        if self._indexer is None or self._repository is None:
            return SearchResponse(query=None, results=[], total_candidates=0, elapsed_ms=0.0)

        scores, ids = self._indexer.search(embedding, top_k=top_k)
        if len(ids) == 0:
            return SearchResponse(query=None, results=[], total_candidates=0, elapsed_ms=0.0)

        similarities = self._to_similarity(scores)
        id_list = [int(v) for v in ids.flatten()[:top_k]]
        db_offers = self._repository.find_by_ids(id_list)
        db_map = {o.id: o for o in db_offers}

        results = []
        for i, oid in enumerate(id_list):
            offer = db_map.get(oid)
            if offer is None:
                continue
            sim = float(similarities.flatten()[i])
            if filters:
                if filters.min_similarity > 0 and sim < filters.min_similarity:
                    continue
                if filters.min_quality > 0 and (offer.data_quality_score or 0) < filters.min_quality:
                    continue
                if filters.required_level and filters.required_level != offer.required_level:
                    continue
                if filters.domain and filters.domain != offer.domain:
                    continue
                if filters.region and filters.region != offer.region:
                    continue
                if filters.contract_type and filters.contract_type != offer.contract_type:
                    continue
            results.append(SearchResult(offer=offer, similarity_score=round(sim, 4), rank=i + 1))

        elapsed = (time.monotonic() - t0) * 1000
        return SearchResponse(query=None, results=results, total_candidates=self._indexer.size, elapsed_ms=round(elapsed, 1))

    def search_by_text(
        self, query: str, top_k: int = 20, filters: SearchFilters | None = None,
    ) -> SearchResponse:
        """Recherche par texte (encode → search)."""
        if self._embedder is None:
            return SearchResponse(query=query, results=[], total_candidates=0, elapsed_ms=0.0)
        embedding = self._embedder.embed_query(query)
        resp = self.search_by_embedding(embedding, top_k=top_k, filters=filters)
        resp.query = query
        return resp

    def _to_similarity(self, distances: np.ndarray) -> np.ndarray:
        """Convertit distances turbovec → similarites 0..1."""
        d = np.asarray(distances, dtype=np.float32)
        d_max = d.max()
        if d_max <= 2.0:
            return 1.0 - d
        d_min = d.min()
        return 1.0 - (d - d_min) / (d_max - d_min + 1e-8)
