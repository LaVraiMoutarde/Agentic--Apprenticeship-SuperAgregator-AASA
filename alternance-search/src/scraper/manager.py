"""
ScraperManager — registre et orchestrateur de scrapers.

Responsabilités :
- Enregistrer des scrapers (add)
- Exécuter tous les scrapers (run_all)
- Exécuter un scraper spécifique (run_one)
- Collecter et logger les résultats agrégés
- Isoler les erreurs : un scraper qui échoue ne bloque pas les autres

Usage typique :
    manager = ScraperManager()
    manager.add(IndeedScraper())
    manager.add(ApecScraper())

    results = manager.run_all(query="data science", max_pages=3)
    # results : dict[str, ScraperResult]

    result = manager.run_one("indeed", query="python alternance")
    # result : ScraperResult

Pattern : Registry + Strategy
- OCP respecté : ajouter un scraper = add() sans modifier Manager
- Chaque scraper est un plugin autonome
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from .base import BaseScraper, ScraperResult, ScraperStatus
from .exceptions import ScraperError
from .logging import get_scraper_logger

if TYPE_CHECKING:
    from .criteria import SearchCriteria


class ScraperManager:
    """Orchestrateur de scrapers.

    Maintient un registre {name: BaseScraper} et fournit des méthodes
    pour exécuter un ou plusieurs scrapers de manière isolée.
    """

    def __init__(self) -> None:
        self._scrapers: dict[str, BaseScraper] = {}
        self.logger = get_scraper_logger("manager")

    # ── Registre ──

    @property
    def registered(self) -> list[str]:
        """Noms des scrapers enregistrés, triés alphabétiquement."""
        return sorted(self._scrapers.keys())

    def add(self, scraper: BaseScraper) -> None:
        """Enregistre un scraper dans le registre.

        Lève une ValueError si un scraper du même nom existe déjà.

        Args:
            scraper: Instance concrète de BaseScraper.

        Raises:
            ValueError: Si le nom est déjà enregistré.
        """
        name = scraper.name
        if name in self._scrapers:
            raise ValueError(
                f"Scraper '{name}' déjà enregistré. "
                f"Utilisez remove('{name}') d'abord ou choisissez un nom unique."
            )
        self._scrapers[name] = scraper
        self.logger.info("Scraper '%s' enregistré (total: %d)", name, len(self._scrapers))

    def remove(self, name: str) -> bool:
        """Retire un scraper du registre. Retourne True s'il existait."""
        if name in self._scrapers:
            del self._scrapers[name]
            self.logger.info("Scraper '%s' retiré du registre", name)
            return True
        return False

    def get(self, name: str) -> BaseScraper | None:
        """Récupère un scraper par son nom, ou None."""
        return self._scrapers.get(name)

    # ── Exécution ──

    def run_all(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 1,
        sources: list[str] | None = None,
        criteria: SearchCriteria | None = None,
    ) -> dict[str, ScraperResult]:
        """Exécute tous les scrapers enregistrés (ou une sélection).

        Chaque scraper tourne dans une bulle d'isolation.

        Args:
            query: Termes de recherche (transmis à chaque scraper).
            location: Filtre géographique optionnel.
            max_pages: Pages max par scraper.
            sources: Si spécifié, exécute uniquement ces scrapers.
            criteria: Critères structurés (niveau, contrat, rayon…).

        Returns:
            dict[name, ScraperResult] — un résultat par scraper exécuté.
        """
        targets = sources or list(self._scrapers.keys())
        results: dict[str, ScraperResult] = {}

        self.logger.info(
            "Lancement de %d scrapers — query='%s', location='%s', max_pages=%d",
            len(targets), query, location, max_pages,
        )

        for name in targets:
            scraper = self._scrapers.get(name)
            if scraper is None:
                self.logger.warning("Scraper '%s' non trouvé — ignoré", name)
                results[name] = ScraperResult(
                    source=name,
                    status=ScraperStatus.SKIPPED,
                )
                continue

            self.logger.info("Démarrage du scraper '%s'…", name)
            t0 = time.monotonic()

            try:
                result = scraper.scrape(
                    query=query,
                    location=location,
                    max_pages=max_pages,
                    criteria=criteria,
                )
            except Exception as exc:
                self.logger.error(
                    "Scraper '%s' crash — %s: %s",
                    name, type(exc).__name__, exc,
                )
                result = ScraperResult(
                    source=name,
                    status=ScraperStatus.FAILED,
                    errors=[exc],
                )
                result.mark_complete()

            elapsed = time.monotonic() - t0
            self.logger.info(
                "Scraper '%s' terminé — status=%s, offres=%d, erreurs=%d (%.1fs)",
                name, result.status.value, result.success_count,
                result.error_count, elapsed,
            )
            results[name] = result

        total_offers = sum(r.success_count for r in results.values())
        failures = sum(1 for r in results.values() if r.status == ScraperStatus.FAILED)
        self.logger.info(
            "Session terminée — %d scrapers, %d offres, %d échecs",
            len(results), total_offers, failures,
        )

        return results

    def run_one(
        self,
        name: str,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 1,
    ) -> ScraperResult:
        """Exécute un scraper spécifique par son nom.

        Args:
            name: Nom du scraper à exécuter.
            query, location, max_pages: Paramètres de scraping.

        Returns:
            ScraperResult.

        Raises:
            ValueError: Si le scraper n'est pas enregistré.
            ScraperError: Si le scraper lève une exception.
        """
        scraper = self._scrapers.get(name)
        if scraper is None:
            available = ", ".join(self.registered) or "aucun"
            raise ValueError(
                f"Scraper '{name}' non trouvé. Disponibles : {available}"
            )

        self.logger.info("Exécution du scraper '%s'…", name)
        t0 = time.monotonic()

        try:
            result = scraper.scrape(query=query, location=location, max_pages=max_pages)
        except Exception as exc:
            self.logger.error("Scraper '%s' crash — %s: %s", name, type(exc).__name__, exc)
            raise ScraperError(
                f"Le scraper '{name}' a crashé",
                scraper_name=name,
                original=exc,
            ) from exc

        elapsed = time.monotonic() - t0
        self.logger.info(
            "Scraper '%s' terminé — %d offres (%.1fs)",
            name, result.success_count, elapsed,
        )
        return result

    # ── Pipeline : scraping → stockage ──

    def scrape_and_store(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 1,
        sources: list[str] | None = None,
    ) -> dict[str, ScraperResult]:
        """Exécute les scrapers ET stocke les offres en base.

        Combine run_all() + persistance dans SQLite via OfferRepository.

        Args:
            query, location, max_pages, sources: Identiques à run_all().

        Returns:
            Résultats de chaque scraper (les offres sont déjà en base).
        """
        from src.store import Offer, OfferRepository, init_db

        results = self.run_all(
            query=query, location=location, max_pages=max_pages, sources=sources,
        )

        # Persistance
        init_db()
        repo = OfferRepository()
        total_stored = 0

        for name, result in results.items():
            if not result.offers:
                continue
            for scraped in result.offers:
                offer = Offer(
                    source=scraped.source,
                    source_id=scraped.source_id,
                    title=scraped.title,
                    company=scraped.company,
                    location=scraped.location,
                    region=scraped.region,
                    contract_type=scraped.contract_type,
                    domain=scraped.domain,
                    required_level=scraped.required_level,
                    description=scraped.description,
                    salary_min=scraped.salary_min,
                    salary_max=scraped.salary_max,
                    published_date=scraped.published_date,
                    scraped_date=scraped.scraped_at,
                    url=scraped.url,
                    search_text="",
                    raw_json=scraped.model_dump_json(),
                )
                _, is_new = repo.upsert(offer)
                if is_new:
                    total_stored += 1

        self.logger.info("Stockage : %d nouvelles offres en base", total_stored)
        return results
