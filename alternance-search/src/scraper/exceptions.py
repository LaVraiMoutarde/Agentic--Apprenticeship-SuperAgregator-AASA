"""
Hiérarchie d'exceptions pour le module de scraping.

Toute erreur levée par un scraper est une ScraperError,
ce qui permet au ScraperManager de les attraper sans crasher.
"""

from __future__ import annotations


class ScraperError(Exception):
    """Exception de base pour toutes les erreurs de scraping.

    Attributes:
        scraper_name: Nom du scraper ayant levé l'erreur.
        original: Exception originale si wrapping.
    """

    def __init__(
        self,
        message: str,
        *,
        scraper_name: str = "unknown",
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.scraper_name = scraper_name
        self.original = original

    def __str__(self) -> str:
        base = f"[{self.scraper_name}] {super().__str__()}"
        if self.original:
            base += f" (caused by: {type(self.original).__name__}: {self.original})"
        return base


class ScraperNetworkError(ScraperError):
    """Erreur réseau / HTTP (timeout, 403, 500…)."""


class ScraperParseError(ScraperError):
    """Erreur de parsing HTML / extraction de données."""


class ScraperValidationError(ScraperError):
    """Une ScrapedOffer ne passe pas la validation."""


class ScraperRateLimitError(ScraperNetworkError):
    """Rate-limit détecté (HTTP 429 ou détection heuristique)."""


class ScraperConfigurationError(ScraperError):
    """Configuration invalide du scraper (URL, selecteurs…)."""
