"""
Favorites Store — gestion des offres favorites (sauvegarde JSON).

Stocke les favoris dans un fichier JSON persistant.
Chaque favori contient les informations essentielles de l'offre
pour pouvoir les exporter en Excel ultérieurement.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings


class FavoritesStore:
    """Gère la liste des offres favorites via un fichier JSON."""

    def __init__(self, filepath: str | Path | None = None) -> None:
        if filepath is None:
            filepath = settings.project_root / "data" / "favorites.json"
        self._path = Path(filepath)
        self._lock = threading.Lock()
        self._favorites: list[dict[str, Any]] = []
        self._load()

    # ── Persistance ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Charge les favoris depuis le fichier JSON."""
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                self._favorites = json.loads(raw)
            except (json.JSONDecodeError, OSError):
                self._favorites = []
        else:
            self._favorites = []

    def _save(self) -> None:
        """Sauvegarde les favoris dans le fichier JSON."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._favorites, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── CRUD ─────────────────────────────────────────────────────────

    def add(self, offer_data: dict[str, Any]) -> bool:
        """Ajoute une offre aux favoris. Retourne False si déjà présente (par ID)."""
        with self._lock:
            offer_id = offer_data.get("id")
            # Vérifier si déjà présent
            if offer_id is not None:
                for fav in self._favorites:
                    if fav.get("id") == offer_id:
                        return False

            entry = {
                "id": offer_data.get("id"),
                "title": offer_data.get("title", ""),
                "company": offer_data.get("company", ""),
                "location": offer_data.get("location", ""),
                "source": offer_data.get("source", ""),
                "url": offer_data.get("url", ""),
                "contract_type": offer_data.get("contract_type", ""),
                "required_level": offer_data.get("required_level", ""),
                "description": (offer_data.get("description") or "")[:500],
                "scraped_date": offer_data.get("scraped_date", ""),
                "score": offer_data.get("score") or offer_data.get("final_score") or offer_data.get("embedding_score"),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            self._favorites.append(entry)
            self._save()
            return True

    def remove(self, offer_id: int) -> bool:
        """Retire une offre des favoris par son ID. Retourne True si trouvée."""
        with self._lock:
            for i, fav in enumerate(self._favorites):
                if fav.get("id") == offer_id:
                    self._favorites.pop(i)
                    self._save()
                    return True
            return False

    def toggle(self, offer_data: dict[str, Any]) -> dict[str, Any]:
        """Ajoute ou retire une offre des favoris. Retourne le nouvel état."""
        offer_id = offer_data.get("id")
        with self._lock:
            for i, fav in enumerate(self._favorites):
                if fav.get("id") == offer_id:
                    self._favorites.pop(i)
                    self._save()
                    return {"favorite": False, "count": len(self._favorites)}
            # Ajouter
            entry = {
                "id": offer_data.get("id"),
                "title": offer_data.get("title", ""),
                "company": offer_data.get("company", ""),
                "location": offer_data.get("location", ""),
                "source": offer_data.get("source", ""),
                "url": offer_data.get("url", ""),
                "contract_type": offer_data.get("contract_type", ""),
                "required_level": offer_data.get("required_level", ""),
                "description": (offer_data.get("description") or "")[:500],
                "scraped_date": offer_data.get("scraped_date", ""),
                "score": offer_data.get("score") or offer_data.get("final_score") or offer_data.get("embedding_score"),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            self._favorites.append(entry)
            self._save()
            return {"favorite": True, "count": len(self._favorites)}

    def is_favorite(self, offer_id: int) -> bool:
        """Vérifie si une offre est dans les favoris."""
        with self._lock:
            return any(fav.get("id") == offer_id for fav in self._favorites)

    def get_all(self) -> list[dict[str, Any]]:
        """Retourne la liste complète des favoris."""
        with self._lock:
            return list(self._favorites)

    def get_favorite_ids(self) -> set[int]:
        """Retourne l'ensemble des IDs des offres favorites."""
        with self._lock:
            return {fav["id"] for fav in self._favorites if fav.get("id") is not None}

    def count(self) -> int:
        """Nombre de favoris."""
        with self._lock:
            return len(self._favorites)

    def clear(self) -> None:
        """Supprime tous les favoris."""
        with self._lock:
            self._favorites = []
            self._save()

    def get_path(self) -> Path:
        """Chemin du fichier JSON des favoris."""
        return self._path.resolve()


# ── Singleton global ─────────────────────────────────────────────────
_favorites_store: FavoritesStore | None = None


def get_favorites_store() -> FavoritesStore:
    """Retourne le singleton FavoritesStore."""
    global _favorites_store
    if _favorites_store is None:
        _favorites_store = FavoritesStore()
    return _favorites_store
