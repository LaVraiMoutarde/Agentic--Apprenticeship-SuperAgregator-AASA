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

        Calcule aussi les scores de similarité embedding vs.
        la query sémantique (criteria) et les stocke en base.

        Returns:
            {"indexed": n, "embedding_scored": n, "elapsed_ms": ms}
        """
        self.init()
        t0 = time.monotonic()

        offers = self._repository.find(limit=100_000)
        if not offers:
            return {"indexed": 0, "embedding_scored": 0, "elapsed_ms": 0.0}

        ids = [o.id for o in offers]
        vecs = self._embedder.embed_offer_batch(offers, show_progress=False)
        self._indexer.build(ids, vecs)

        # ── Calculer et stocker les scores de similarité embedding ──
        scored = self._compute_and_store_embedding_scores(offers, vecs)

        elapsed = (time.monotonic() - t0) * 1000
        return {
            "indexed": len(ids),
            "embedding_scored": scored,
            "elapsed_ms": round(elapsed, 1),
        }

    def _compute_and_store_embedding_scores(
        self, offers: list, vecs: np.ndarray
    ) -> int:
        """Calcule la similarité cosinus entre chaque offre et une
        query de référence (critères sémantiques ou fallback 'alternance')
        et persiste le score dans Offer.embedding_score.

        Args:
            offers: Liste d'Offer (dans le même ordre que vecs).
            vecs: ndarray (n, dim) float32, déjà normalisés.

        Returns:
            Nombre de scores stockés.
        """
        try:
            from src.scoring.semantic_criteria import SemanticCriteria
            sc = SemanticCriteria.load()
            query_text = sc.to_query_text() if sc.has_criteria else "alternance"
        except Exception:
            query_text = "alternance"

        # Encoder la query de référence (prefixée E5)
        query_vec = self._embedder.embed_query(query_text)[0]  # (dim,)

        # Similarité cosinus (les vecteurs sont déjà normalisés → dot product)
        similarities = np.dot(vecs, query_vec)  # (n,)

        # Normaliser en 0..1 : sigmoïde × linéaire
        # Dot product entre vecteurs normalisés → [-1, 1]
        # On projette en [0, 1] par (x + 1) / 2
        normalized = (similarities + 1.0) / 2.0
        normalized = np.clip(normalized, 0.0, 1.0)

        # Stocker en base par batches
        from src.store.database import get_session
        from src.store.models import Offer

        stored = 0
        with get_session() as s:
            for o, sim in zip(offers, normalized):
                s.query(Offer).filter(Offer.id == o.id).update(
                    {"embedding_score": round(float(sim), 4)}
                )
                stored += 1
            s.commit()

        return stored

    def index_new_offers(self, offer_ids: list[int]) -> int:
        """Ajoute de nouvelles offres à l'index existant.

        Calcule aussi les scores de similarité embedding pour
        les nouvelles offres.

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

        # Calculer et stocker les scores embedding pour les nouvelles offres
        self._compute_and_store_embedding_scores(offers, vecs)

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

    # ── Recherche hybride (embedding + boost mots-clés) ──

    def search_hybrid(
        self,
        criteria,  # SemanticCriteria
        top_k: int = 500,
    ) -> list[tuple]:
        """Recherche hybride : embedding sémantique + boost additif mots-clés.

        Args:
            criteria: Instance de SemanticCriteria.
            top_k: Nombre de candidats à récupérer avant boost.

        Returns:
            Liste de tuples (offer, final_score), triée par score décroissant.
            final_score = normalized_embedding_similarity + Σ(boost par mot-clé trouvé).
        """
        self.init()
        if self._indexer is None or self._indexer._index is None or len(self._indexer._index) == 0:
            return []

        # ── Étape 1 : Embedding sémantique ──
        query_text = criteria.to_query_text()
        query_vec = self._embedder.embed_query(query_text)
        scores, ids = self._indexer.search(query_vec, top_k=top_k)
        if len(ids) == 0:
            return []

        id_list = [int(v) for v in ids.flatten()[:top_k]]
        db_offers = self._repository.find_by_ids(id_list)
        db_map = {o.id: o for o in db_offers}

        # Normaliser les scores turbovec en 0..1 (dot-product, higher = better)
        raw_scores = np.asarray(scores.flatten()[:len(id_list)], dtype=np.float32)
        s_min, s_max = raw_scores.min(), raw_scores.max()
        if s_max - s_min > 1e-8:
            normalized = (raw_scores - s_min) / (s_max - s_min)
        else:
            normalized = np.zeros_like(raw_scores)

        # ── Étape 2 : Boost mots-clés ──
        boost_kw = [kw.lower() for kw in criteria.boost_keywords if kw.strip()]
        boost_val = criteria.boost_value

        results = []
        for i, oid in enumerate(id_list):
            offer = db_map.get(oid)
            if offer is None:
                continue

            base_score = float(normalized[i])

            # Boost additif : chaque mot-clé trouvé dans le search_text ajoute boost_val
            search_text = (getattr(offer, "search_text", "") or "").lower()
            matched_kw = sum(1 for kw in boost_kw if kw in search_text)
            boost = matched_kw * boost_val
            final = base_score + boost

            results.append((offer, final, base_score, matched_kw))

        # Trier par score final décroissant
        results.sort(key=lambda x: x[1], reverse=True)
        return results
