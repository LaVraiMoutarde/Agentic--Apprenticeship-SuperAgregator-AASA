"""
Module de scraping — collecte d'offres d'alternance depuis des sources web.

Architecture extensible (OCP) :
    BaseScraper (ABC)
    ├── IndeedScraper      # à implémenter
    ├── ApecScraper        # à implémenter
    ├── WTTJScraper        # à implémenter
    └── ...

    ScraperManager : registre + exécution isolée

Points d'entrée :
    from src.scraper import BaseScraper, ScraperManager, ScrapedOffer, ScraperResult
"""

from .base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from .criteria import SearchCriteria
from .exceptions import (
    ScraperConfigurationError,
    ScraperError,
    ScraperNetworkError,
    ScraperParseError,
    ScraperRateLimitError,
    ScraperValidationError,
)
from .logging import get_scraper_logger, set_scraper_level, set_global_level
from .manager import ScraperManager

__all__ = [
    # Base
    "BaseScraper",
    "ScrapedOffer",
    "ScraperResult",
    "ScraperStatus",
    # Criteria
    "SearchCriteria",
    # Manager
    "ScraperManager",
    # Exceptions
    "ScraperError",
    "ScraperNetworkError",
    "ScraperParseError",
    "ScraperValidationError",
    "ScraperRateLimitError",
    "ScraperConfigurationError",
    # Logging
    "get_scraper_logger",
    "set_scraper_level",
    "set_global_level",
]
