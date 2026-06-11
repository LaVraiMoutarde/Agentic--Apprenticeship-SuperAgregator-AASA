"""
Scraper Welcome to the Jungle — offres d'emploi/alternance.

⚠️ 2026 : WTTJ a été complètement réarchitecturé (SPA React + Algolia).
L'API REST publique n'existe plus (404). La page /fr/jobs utilise un
moteur de recherche Algolia côté client. Ce scraper est actuellement
INOPERANT et nécessite une réintégration complète.

URL : https://www.welcometothejungle.com/fr/jobs
"""

from __future__ import annotations

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus


class WTJJScraper(BaseScraper):
    """Scraper pour Welcome to the Jungle — INOPERANT (site refait)."""

    def __init__(self, **kwargs) -> None:
        super().__init__()
        _ = kwargs

    @property
    def name(self) -> str:
        return "wttj"

    def scrape(self, **kwargs) -> ScraperResult:
        self.logger.warning(
            "WTTJ a refait son site (SPA + Algolia). "
            "Le scraping n'est plus possible sans réintégration complète."
        )
        return ScraperResult(
            source=self.name,
            status=ScraperStatus.SKIPPED,
        )
