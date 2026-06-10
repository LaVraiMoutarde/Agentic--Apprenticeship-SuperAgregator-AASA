"""
Schéma de données — modèle SQLAlchemy pour les offres d'alternance.

Une offre passe par 3 états dans le système :
  1. RawOffer (dict brut)        → sortie du scraper
  2. NormalizedOffer (dataclass)  → sortie du normalizer
  3. Offer (modèle ORM)           → stockée en base

┌─────────────────────────────────────────────────────────────────┐
│                          TABLE offers                           │
├────────────────┬────────────────────────────────────────────────┤
│ id             │ INTEGER PRIMARY KEY AUTOINCREMENT              │
│ source         │ TEXT NOT NULL    -- indeed, linkedin, ...      │
│ source_id      │ TEXT NOT NULL    -- ID unique chez la source   │
│ title          │ TEXT NOT NULL                                   │
│ company        │ TEXT                                             │
│ location       │ TEXT            -- ville (75001 Paris)          │
│ region         │ TEXT            -- Île-de-France, ...           │
│ contract_type  │ TEXT            -- apprentissage, pro, cdd...  │
│ domain         │ TEXT            -- informatique, commerce...   │
│ required_level │ TEXT            -- BAC+2, BAC+3, BAC+5...      │
│ description    │ TEXT NOT NULL   -- texte complet de l'offre    │
│ salary_min     │ REAL            -- salaire min mensuel (€)     │
│ salary_max     │ REAL            -- salaire max mensuel (€)     │
│ published_date │ TEXT            -- date de publication source  │
│ scraped_date   │ TEXT NOT NULL   -- date de collecte            │
│ url            │ TEXT NOT NULL   -- lien vers l'offre           │
│ is_active      │ INTEGER DEFAULT 1  -- 0 = soft-deleted         │
│ search_text    │ TEXT            -- texte concaténé pour embed  │
│ embedding_dim  │ INTEGER         -- dimension du vecteur        │
│ raw_json       │ TEXT            -- dump JSON de la raw offer   │
│ created_at     │ TEXT NOT NULL                                   │
│ updated_at     │ TEXT NOT NULL                                   │
└────────────────┴────────────────────────────────────────────────┘

Contrainte UNIQUE sur (source, source_id) pour la déduplication.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Column,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base déclarative SQLAlchemy."""


class Offer(Base):
    """Modèle ORM représentant une offre d'alternance normalisée."""

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String(300))
    location: Mapped[Optional[str]] = mapped_column(String(300), index=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    contract_type: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    domain: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    required_level: Mapped[Optional[str]] = mapped_column(String(50), index=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    salary_min: Mapped[Optional[float]] = mapped_column(Float)
    salary_max: Mapped[Optional[float]] = mapped_column(Float)

    published_date: Mapped[Optional[str]] = mapped_column(String(30))
    scraped_date: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)

    is_active: Mapped[int] = mapped_column(Integer, default=1, index=True)

    contact_name: Mapped[Optional[str]] = mapped_column(String(200))
    contact_email: Mapped[Optional[str]] = mapped_column(String(200))

    search_text: Mapped[Optional[str]] = mapped_column(Text)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer)

    # ── Qualificatifs data ──
    is_alternance: Mapped[Optional[float]] = mapped_column(Float, default=None)
    data_quality_score: Mapped[Optional[float]] = mapped_column(Float, default=None)
    llm_score: Mapped[Optional[float]] = mapped_column(Float, default=None)
    cleaned_at: Mapped[Optional[str]] = mapped_column(String(30), default=None)

    raw_json: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(
        String(30), default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String(30), default=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Propriétés calculées ──

    @property
    def raw_data(self) -> dict[str, Any] | None:
        """Désérialise le JSON brut de l'offre."""
        if self.raw_json:
            return json.loads(self.raw_json)
        return None

    @property
    def salary_display(self) -> str:
        """Affichage formaté du salaire."""
        if self.salary_min and self.salary_max:
            return f"{self.salary_min:,.0f} – {self.salary_max:,.0f} €"
        if self.salary_min:
            return f"À partir de {self.salary_min:,.0f} €"
        if self.salary_max:
            return f"Jusqu'à {self.salary_max:,.0f} €"
        return "Non communiqué"

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'offre en dictionnaire."""
        return {
            "id": self.id,
            "source": self.source,
            "source_id": self.source_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "region": self.region,
            "contract_type": self.contract_type,
            "domain": self.domain,
            "required_level": self.required_level,
            "description": self.description,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "published_date": self.published_date,
            "scraped_date": self.scraped_date,
            "url": self.url,
            "is_active": bool(self.is_active),
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "salary_display": self.salary_display,
        }

    def __repr__(self) -> str:
        return f"<Offer id={self.id} source={self.source!r} title={self.title[:60]!r}>"
