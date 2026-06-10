"""
Base abstraite pour tous les scrapers d'offres d'alternance.

Définit le contrat unique que chaque scraper (Indeed, LinkedIn, Apec…)
doit respecter. Le ScraperManager n'a besoin de connaître que cette
interface — tout scraper concret peut être ajouté sans modifier le core.

Data flow :
    1. BaseScraper.scrape() → ScraperResult (liste de ScrapedOffer)
    2. ScraperResult → NormalizationPipeline → SQLite
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .logging import get_scraper_logger


# ═══════════════════════════════════════════════════════════════════
# ScraperStatus
# ═══════════════════════════════════════════════════════════════════

class ScraperStatus(str, Enum):
    """Statut final d'une session de scraping."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


# ═══════════════════════════════════════════════════════════════════
# ScrapedOffer — modèle standardisé de sortie
# ═══════════════════════════════════════════════════════════════════

class ScrapedOffer(BaseModel):
    """Modèle standardisé d'une offre scrapée.

    Tous les scrapers DOIVENT produire des instances de cette classe.
    Champs obligatoires : title, description, url, source.
    """

    # Obligatoires
    title: str = Field(..., min_length=2, max_length=500)
    description: str = Field(..., min_length=10)
    url: str = Field(..., min_length=5, max_length=2000)
    source: str = Field(..., min_length=2, max_length=50)

    # Optionnels — métadonnées
    source_id: str = ""
    company: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=300)
    region: str = Field(default="", max_length=100)
    contract_type: str = Field(default="", max_length=100)
    domain: str = Field(default="", max_length=200)
    required_level: str = Field(default="", max_length=50)

    # Optionnels — salaire
    salary_raw: str = ""
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)

    # Optionnels — contact
    contact_name: str = Field(default="", max_length=200)
    contact_email: str = Field(default="", max_length=200)

    # Dates
    published_date: str = ""
    scraped_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Champs de qualite (ajoutes apres normalisation) ──
    is_alternance: float | None = Field(default=None, ge=0, le=1, description="Score alternance 0..1")
    data_quality_score: float | None = Field(default=None, ge=0, le=1, description="Score qualite 0..1")
    cleaned_at: str = Field(default="")

    @field_validator("url")
    @classmethod
    def url_must_have_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL sans scheme : {v[:80]}")
        return v

    @field_validator("source")
    @classmethod
    def source_must_be_slug(cls, v: str) -> str:
        if " " in v or v != v.lower():
            raise ValueError(f"Source invalide (slug attendu) : '{v}'")
        return v


# ═══════════════════════════════════════════════════════════════════
# ScraperResult
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScraperResult:
    """Résultat complet d'une session de scraping."""

    source: str
    status: ScraperStatus = ScraperStatus.SUCCESS
    offers: list[ScrapedOffer] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    pages_scraped: int = 0
    total_found: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""

    @property
    def success_count(self) -> int:
        return len(self.offers)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return (datetime.fromisoformat(self.finished_at) - datetime.fromisoformat(self.started_at)).total_seconds()

    def add_error(self, error: Exception) -> None:
        self.errors.append(error)
        if not self.offers:
            self.status = ScraperStatus.FAILED
        else:
            self.status = ScraperStatus.PARTIAL

    def mark_complete(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        if self.status == ScraperStatus.SUCCESS and not self.offers:
            self.status = ScraperStatus.FAILED

    def to_summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "offers_collected": self.success_count,
            "errors": self.error_count,
            "pages_scraped": self.pages_scraped,
            "total_found": self.total_found,
            "duration_seconds": self.duration_seconds,
        }


# ═══════════════════════════════════════════════════════════════════
# BaseScraper
# ═══════════════════════════════════════════════════════════════════

class BaseScraper(ABC):
    """Classe abstraite pour tous les scrapers.

    Chaque implémentation concrète doit fournir :
    - `name` (property) : slug unique (ex: 'indeed')
    - `scrape()`        : logique de collecte → ScraperResult

    Points d'extension optionnels :
    - `parse()`         : parsing post-fetch
    - `validate_output()` : filtrage des offres invalides
    """

    def __init__(self) -> None:
        self.logger = get_scraper_logger(self.name)

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom unique du scraper (slug, ex: 'indeed')."""
        ...

    @abstractmethod
    def scrape(
        self,
        query: str,
        *,
        location: str = "",
        max_pages: int = 1,
    ) -> ScraperResult:
        """Exécute le scraping et retourne les résultats standardisés.

        Ne doit JAMAIS lever d'exception non attrapée.
        Utiliser `result.add_error()` en cas d'erreur.
        """
        ...

    def parse(self, raw_data: Any) -> list[ScrapedOffer]:
        """Optionnel — parse une donnée brute en ScrapedOffer."""
        _ = raw_data
        return []

    def validate_output(self, offers: list[ScrapedOffer]) -> list[ScrapedOffer]:
        """Valide et filtre les offres. Logge les rejets."""
        valid: list[ScrapedOffer] = []
        for i, offer in enumerate(offers):
            try:
                ScrapedOffer.model_validate(offer.model_dump())
                valid.append(offer)
            except Exception as exc:
                self.logger.warning("Offre #%d rejetee : %s", i, str(exc)[:120])
        return valid

    def _build_result(
        self,
        offers: list[ScrapedOffer],
        pages: int = 0,
        total_found: int = 0,
        errors: list[Exception] | None = None,
    ) -> ScraperResult:
        """Construit un ScraperResult avec statut automatique."""
        result = ScraperResult(
            source=self.name,
            offers=offers,
            pages_scraped=pages,
            total_found=total_found,
            errors=errors or [],
        )
        if result.errors and not result.offers:
            result.status = ScraperStatus.FAILED
        elif result.errors:
            result.status = ScraperStatus.PARTIAL
        result.mark_complete()
        return result
