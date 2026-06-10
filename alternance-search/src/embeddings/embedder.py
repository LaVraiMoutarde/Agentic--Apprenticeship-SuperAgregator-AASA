"""
Embedder — generation de vecteurs via sentence-transformers.

Modele : intfloat/multilingual-e5-large (1024-dim)
Format E5 : prefixe "passage: " pour les offres, "query: " pour les recherches.

Usage :
    emb = Embedder()
    vec = emb.offer_to_embedding(offer)    # (1, dim)
    vec = emb.embed_query("data science")  # (1, dim)
    vecs = emb.embed_batch(offers, 32)     # (n, dim)
"""

from __future__ import annotations

import numpy as np
from config import settings


class Embedder:
    """Generateur d'embeddings via sentence-transformers (lazy-init)."""

    def __init__(self) -> None:
        self.model_name = settings.embedding.model_name
        self.dim = settings.embedding.dim
        self.batch_size = settings.embedding.batch_size
        self.device = settings.embedding.device
        self.normalize = settings.embedding.normalize
        self.query_prefix = settings.embedding.query_prefix
        self.passage_prefix = settings.embedding.passage_prefix
        self._model = None

    # ── API principale ──

    def offer_to_embedding(self, offer) -> np.ndarray:
        """Encode une offre → vecteur (1, dim)."""
        text = getattr(offer, "search_text", None) or self._build_text(offer)
        return self._encode(text, self.passage_prefix)

    def embed_offer_batch(self, offers: list, show_progress: bool = True) -> np.ndarray:
        """Encode un lot d'offres → (n, dim)."""
        texts = [f"{self.passage_prefix}{getattr(o, 'search_text', None) or self._build_text(o)}" for o in offers]
        return self._encode_batch(texts, show_progress)

    def embed_query(self, query: str) -> np.ndarray:
        """Encode une requete → (1, dim)."""
        return self._encode(query, self.query_prefix)

    def embed_queries(self, queries: list[str]) -> np.ndarray:
        """Encode N requetes → (n, dim)."""
        return self._encode_batch([f"{self.query_prefix}{q}" for q in queries], False)

    # ── Core ──

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
            actual = self._model.get_sentence_embedding_dimension()
            if actual != self.dim:
                self.dim = actual
        except ImportError:
            raise RuntimeError("pip install sentence-transformers")

    def _encode(self, text: str, prefix: str = "") -> np.ndarray:
        self._ensure_loaded()
        return self._model.encode(
            [f"{prefix}{text}" if prefix else text],
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)

    def _encode_batch(self, texts: list[str], show_progress: bool = False) -> np.ndarray:
        self._ensure_loaded()
        return self._model.encode(
            texts,
            normalize_embeddings=self.normalize,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
            batch_size=self.batch_size,
        ).astype(np.float32)

    def _build_text(self, offer) -> str:
        parts = []
        for attr in ("title", "company", "required_level", "contract_type", "location", "description"):
            v = getattr(offer, attr, None)
            if v and str(v).strip():
                parts.append(str(v).strip())
        return ". ".join(parts)[:2000]
        raise NotImplementedError
