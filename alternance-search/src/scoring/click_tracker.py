"""
Click Tracker — feedback loop simple pour améliorer le scoring.

Principe :
- Chaque clic sur une offre (depuis le tableau Results) est enregistré
- Les offres similaires (même entreprise, même source) reçoivent un bonus
- Le bonus s'estompe avec le temps (les vieux clics comptent moins)

Stockage : fichier JSON data/click_history.json
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings

HISTORY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "click_history.json"
BOOST_FACTOR = 0.15       # +15% pour offre de même entreprise
SOURCE_BOOST = 0.05       # +5% pour même source
DECAY_DAYS = 14           # les clics de plus de 14 jours comptent moitié moins
MAX_RECENT = 200          # garder max 200 entrées


class ClickTracker:
    """Enregistre et exploite les clics utilisateur pour booster le scoring."""

    def __init__(self) -> None:
        self._history: list[dict] = []

    # ── Public API ──

    def record_click(self, offer: dict | Any) -> None:
        """Enregistre un clic sur une offre.

        Args:
            offer: Dict ou objet Offer avec au moins id, company, source.
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": getattr(offer, "id", None) or offer.get("id"),
            "company": getattr(offer, "company", None) or offer.get("company", ""),
            "source": getattr(offer, "source", None) or offer.get("source", ""),
            "title": getattr(offer, "title", None) or offer.get("title", ""),
            "clicked_at": now,
        }
        self._load()
        self._history.append(entry)
        # Nettoyer les vieux si trop d'entrées
        if len(self._history) > MAX_RECENT:
            self._history = self._history[-MAX_RECENT:]
        self._save()

    def get_boost(self, offer: dict | Any) -> float:
        """Calcule le bonus à appliquer à une offre (0.0 = pas de boost).

        Args:
            offer: Dict ou objet Offer.

        Returns:
            Multiplicateur de boost (ex: 0.15 = +15%).
        """
        self._load()
        if not self._history:
            return 0.0

        company = getattr(offer, "company", None) or offer.get("company", "")
        source = getattr(offer, "source", None) or offer.get("source", "")
        now_ts = time.time()

        total_boost = 0.0

        for click in self._history:
            try:
                click_ts = datetime.fromisoformat(click["clicked_at"]).timestamp()
            except (ValueError, TypeError):
                click_ts = now_ts

            age_days = (now_ts - click_ts) / 86400
            if age_days > DECAY_DAYS * 2:
                continue  # trop vieux, ignoré

            weight = max(0.0, 1.0 - age_days / DECAY_DAYS)

            # Même entreprise
            if company and click.get("company") == company:
                total_boost += BOOST_FACTOR * weight

            # Même source
            if source and click.get("source") == source:
                total_boost += SOURCE_BOOST * weight

        return min(total_boost, 0.5)  # cap à +50%

    def get_stats(self) -> dict:
        """Retourne les statistiques des clics."""
        self._load()
        if not self._history:
            return {"total_clicks": 0, "active_boost": False, "companies": [], "recent": []}

        companies = {}
        for c in self._history:
            co = c.get("company", "—")
            companies[co] = companies.get(co, 0) + 1

        top_companies = sorted(companies.items(), key=lambda x: -x[1])[:5]

        recent = [
            {
                "title": c.get("title", "")[:60],
                "company": c.get("company", ""),
                "source": c.get("source", ""),
                "clicked_at": c.get("clicked_at", ""),
            }
            for c in reversed(self._history[-10:])
        ]

        return {
            "total_clicks": len(self._history),
            "active_boost": True,
            "boost_factor": BOOST_FACTOR,
            "decay_days": DECAY_DAYS,
            "top_companies": [{"name": n, "clicks": k} for n, k in top_companies],
            "recent": recent,
        }

    def reset(self) -> None:
        """Réinitialise tout l'historique des clics."""
        self._history = []
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink()

    # ── Persistance ──

    def _load(self) -> None:
        if self._history or not HISTORY_PATH.exists():
            return
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            self._history = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            self._history = []

    def _save(self) -> None:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps(self._history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
