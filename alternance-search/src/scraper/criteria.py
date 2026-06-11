"""
Critères de recherche structurés pour le scraping d'offres d'alternance.

Ces critères optionnels permettent aux scrapers de construire des
requêtes plus précises (URL params, filtres) au-delà des mots-clés libres.

Chaque scraper utilise ce qu'il supporte — les critères non supportés
sont ignorés silencieusement.

Usage dans Manager :
    criteria = SearchCriteria(
        keywords=["python", "data"],
        location="Paris",
        radius_km=30,
        education_level="BAC+5",
        contract="apprentissage",
    )
    manager.run_all(query=criteria.keywords_str, criteria=criteria, max_pages=3)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchCriteria:
    """Critères optionnels de recherche d'alternance.

    Tous les champs sont optionnels — si vides, le scraper utilise
    uniquement les mots-clés libres.
    """

    # ── Mots-clés / Termes de recherche ──
    keywords: list[str] = field(default_factory=list)  # legacy
    search_terms: list[str] = field(default_factory=list)  # chaque terme = une recherche séparée

    # ── Localisation ──
    location: str = ""  # ex: "Paris", "Île-de-France"
    radius_km: int = 0  # 0 = pas de limite, ex: 30

    # ── Formation ──
    education_levels: list[str] = field(default_factory=list)  # ex: ["BAC+3", "BAC+5"]

    # ── Contrat ──
    contract: str = ""  # ex: "apprentissage", "professionnalisation"

    @property
    def keywords_str(self) -> str:
        """Mots-clés concaténés (fallback si aucun search_terms)."""
        return " ".join(self.keywords) if self.keywords else ""

    @property
    def active_queries(self) -> list[str]:
        """Retourne la liste des requêtes à exécuter.
        Si search_terms est rempli, chaque terme est une requête séparée.
        Sinon, fallback sur keywords_str.
        Si rien, retourne ['alternance'].
        """
        if self.search_terms:
            return [t.strip() for t in self.search_terms if t.strip()]
        kw = self.keywords_str
        if kw:
            return [kw]
        return []

    @property
    def has_filters(self) -> bool:
        """True si au moins un critère structuré est renseigné."""
        return bool(self.location or self.education_levels or self.contract)

    @property
    def location_with_radius(self) -> str:
        """Localisation formatée avec rayon (ex: 'Paris (30 km)')."""
        if not self.location:
            return ""
        if self.radius_km > 0:
            return f"{self.location} ({self.radius_km} km)"
        return self.location

    def to_dict(self) -> dict:
        return {
            "keywords": self.keywords,
            "search_terms": self.search_terms,
            "location": self.location,
            "radius_km": self.radius_km,
            "education_levels": self.education_levels,
            "contract": self.contract,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SearchCriteria:
        return cls(
            keywords=d.get("keywords", []),
            search_terms=d.get("search_terms", []),
            location=d.get("location", ""),
            radius_km=d.get("radius_km", 0),
            education_levels=d.get("education_levels", []),
            contract=d.get("contract", ""),
        )
