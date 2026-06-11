"""
Critères sémantiques pour le pré-filtrage hybride (embedding + boost mots-clés).

Ces critères servent UNIQUEMENT au filtrage post-scraping :
- embedding sémantique via turbovec (query naturelle)
- boost additif si des mots-clés techniques sont trouvés dans le search_text

Stockage : data/semantic_criteria.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SemanticCriteria:
    """Critères de filtrage sémantique (post-scraping)."""

    # ── Champs structurés pour la query embedding ──
    desired_role: str = ""           # ex: "Data Scientist"
    skills: str = ""                  # ex: "Python, SQL, ML, Docker"

    # ── Mots-clés techniques pour le boost additif ──
    boost_keywords: list[str] = field(default_factory=list)  # ex: ["Claude Code", "N8N", "post-processing"]
    boost_weight: str = "modéré"     # "léger" (+0.05), "modéré" (+0.10), "fort" (+0.20)

    # ── Persistance ──
    _storage_path: Path | None = field(default=None, repr=False)

    def to_query_text(self) -> str:
        """Construit une query naturelle pour l'embedding E5."""
        parts = []
        if self.desired_role.strip():
            parts.append(f"Alternance en {self.desired_role}")
        if self.skills.strip():
            parts.append(f"Compétences : {self.skills}")
        if not parts:
            return "alternance"
        return ". ".join(parts)

    @property
    def boost_value(self) -> float:
        """Valeur de boost unitaire par mot-clé trouvé."""
        return {"léger": 0.05, "modéré": 0.10, "fort": 0.20}.get(self.boost_weight, 0.10)

    @property
    def has_criteria(self) -> bool:
        """True si au moins un critère est renseigné."""
        return bool(
            self.desired_role.strip()
            or self.skills.strip()
            or self.boost_keywords
        )

    def to_dict(self) -> dict:
        return {
            "desired_role": self.desired_role,
            "skills": self.skills,
            "boost_keywords": self.boost_keywords,
            "boost_weight": self.boost_weight,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SemanticCriteria:
        return cls(
            desired_role=d.get("desired_role", ""),
            skills=d.get("skills", ""),
            boost_keywords=d.get("boost_keywords", []),
            boost_weight=d.get("boost_weight", "modéré"),
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> SemanticCriteria:
        """Charge les critères depuis le fichier JSON."""
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "data" / "semantic_criteria.json"
        elif isinstance(path, str):
            path = Path(path)
        criteria = cls(_storage_path=path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                criteria = cls.from_dict(data)
                criteria._storage_path = path
            except (json.JSONDecodeError, KeyError):
                pass
        return criteria

    def save(self) -> None:
        """Sauvegarde les critères dans le fichier JSON."""
        if self._storage_path is None:
            self._storage_path = Path(__file__).resolve().parent.parent.parent / "data" / "semantic_criteria.json"
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
