"""
Indexer — gestion de l'index vectoriel turbovec (IdMapIndex).

Usage :
    idx = Indexer()
    idx.build(ids, vectors)
    scores, ids = idx.search(query_vec, top_k=10)
"""

from __future__ import annotations

import numpy as np
from config import settings


class Indexer:
    """Gestionnaire de l'index vectoriel turbovec."""

    def __init__(self) -> None:
        self.dim = settings.embedding.dim
        self.bit_width = settings.turbovec.bit_width
        self.index_path = settings.turbovec.index_path
        self._index = None

    # ═══════════════════════════════════════════════════════════════
    # API principale
    # ═══════════════════════════════════════════════════════════════

    def build(self, ids: list[int], vectors: np.ndarray) -> None:
        """Construit l'index depuis zero avec les vecteurs donnes.

        Args:
            ids: Liste des IDs d'offres (int, sera caste en uint64).
            vectors: ndarray (n, dim) float32.
        """
        self._index = self._make_index()
        ids_arr = np.array(ids, dtype=np.uint64)
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(-1, self.dim)
        self._index.add_with_ids(vectors, ids_arr)

    def add(self, ids: list[int], vectors: np.ndarray) -> None:
        """Ajoute des vecteurs a l'index existant."""
        self._ensure_index()
        if self._index is None:
            self._index = self._make_index()
        ids_arr = np.array(ids, dtype=np.uint64)
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(-1, self.dim)
        self._index.add_with_ids(vectors, ids_arr)

    def remove(self, offer_ids: list[int]) -> int:
        """Supprime des vecteurs par leurs IDs. Retourne le nombre supprime."""
        self._ensure_index()
        if self._index is None:
            return 0
        removed = 0
        for oid in offer_ids:
            try:
                if self._index.contains(oid) and self._index.remove(oid):
                    removed += 1
            except Exception:
                pass
        return removed

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> tuple[np.ndarray, np.ndarray]:
        """Recherche les top-k offres les plus proches.

        Args:
            query_vector: Vecteur de requete (dim,) ou (1, dim).
            top_k: Nombre de resultats.

        Returns:
            (scores, ids) — scores: distances, ids: IDs externes.
        """
        self._ensure_index()
        if self._index is None or len(self._index) == 0:
            return np.array([]), np.array([])

        q = np.asarray(query_vector, dtype=np.float32)
        if q.ndim == 1:
            q = q.reshape(1, -1)

        scores, ids = self._index.search(q, k=min(top_k, len(self._index)))
        return scores, ids

    def contains(self, offer_id: int) -> bool:
        """Verifie si un ID est dans l'index."""
        if self._index is None:
            return False
        return self._index.contains(offer_id)

    def save(self) -> None:
        """Sauvegarde l'index sur disque."""
        if self._index is not None:
            self._index.prepare()
            self._index.write(self.index_path)

    def load(self) -> bool:
        """Charge l'index depuis le disque. Retourne False si absent."""
        import os
        if not os.path.exists(self.index_path):
            return False
        self._index = self._load_index()
        return self._index is not None

    def sync_with_db(self, db_active_ids: set[int]) -> dict[str, int]:
        """Synchronise avec la base (ajouts/suppressions)."""
        self._ensure_index()
        stats = {"added": 0, "removed": 0}
        if self._index is None:
            return stats
        # Supprimer les IDs de l'index qui ne sont plus actifs
        for oid in list(db_active_ids)[:1000]:
            if not self._index.contains(oid):
                continue
            if oid not in db_active_ids:
                if self._index.remove(oid):
                    stats["removed"] += 1
        return stats

    @property
    def size(self) -> int:
        """Nombre de vecteurs dans l'index."""
        if self._index is None:
            return 0
        return len(self._index)

    # ═══════════════════════════════════════════════════════════════
    # Internals
    # ═══════════════════════════════════════════════════════════════

    def _ensure_index(self) -> None:
        """Charge ou cree l'index."""
        if self._index is not None:
            return
        if not self.load():
            self._index = self._make_index()

    def _make_index(self):
        """Cree un nouvel IdMapIndex."""
        try:
            from turbovec import IdMapIndex
            return IdMapIndex(dim=self.dim, bit_width=self.bit_width)
        except ImportError:
            raise RuntimeError(
                "turbovec non installe. pip install turbovec-0.8.1/turbovec-python/"
            )

    def _load_index(self):
        """Charge l'index depuis le disque."""
        try:
            from turbovec import IdMapIndex
            return IdMapIndex.load(self.index_path)
        except ImportError:
            return None
        except Exception:
            return None
