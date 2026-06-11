"""
Scraper Jeunes d'Avenirs — offres emploi/alternance/stages.

URL : https://jeunesdavenirs-recrut.fr/offres

Structure (2026) :
  - Offres : <a href="/offre/r_[hash]">TITRE</a>
  - Pagination : ?page=N
  - ~15 offres/page
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult


BASE_URL = "https://jeunesdavenirs-recrut.fr"
SEARCH_URL = f"{BASE_URL}/offres"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

TARGET_CONTRACTS = {"alternance", "apprentissage", "professionnalisation", "stage", "contrat d'apprentissage"}


class JeunesDAvenirsScraper(BaseScraper):
    """Scraper pour Jeunes d'Avenirs."""

    def __init__(self, headless: bool = True, timeout: int = 30) -> None:
        super().__init__()
        self.headless = headless
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "jeunes_d_avenirs"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 5,
        criteria=None,
    ) -> ScraperResult:
        _ = location
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut — query='%s', max_pages=%d", query, max_pages)

        try:
            offers = asyncio.run(self._scrape_async(query, max_pages or 9999))
            all_offers = self.validate_output(offers)
            self.logger.info("%d offres validees sur %d", len(all_offers), len(offers))
        except Exception as exc:
            self.logger.error("Erreur fatale: %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages,
                                     total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    async def _scrape_async(self, query: str, max_pages: int) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                executable_path=BRAVE_PATH,
                args=["--disable-blink-features=AutomationControlled",
                       "--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(locale="fr-FR",
                viewport={"width": 1920, "height": 1080})

            try:
                for page_num in range(max_pages):
                    url = f"{SEARCH_URL}?q={query.replace(' ', '+')}&page={page_num + 1}" if query else f"{SEARCH_URL}?page={page_num + 1}"
                    self.logger.info("Page %d/%d — %s", page_num + 1, max_pages, url)

                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=self.timeout * 1000)
                    await page.wait_for_timeout(4000)

                    # Détection du nombre total d'offres sur la page 1
                    if page_num == 0:
                        body = await page.inner_text("body") or ""
                        total_match = __import__("re").search(r"([\d\s]+)\s*offres?", body)
                        if total_match:
                            total = int(total_match.group(1).replace("\u202f", "").replace(" ", ""))
                            first_count = await page.evaluate("""
                                () => document.querySelectorAll('a[href*="/offre/"]').length
                            """)
                            if first_count > 0 and total > first_count:
                                estimated_pages = max(1, (total + first_count - 1) // first_count)
                                max_pages = min(max_pages, estimated_pages)
                                self.logger.info("~%d offres, ~%d/p, ~%d pages", total, first_count, estimated_pages)

                    offers = await page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href*="/offre/"]');
                            const seen = new Set();
                            const results = [];

                            for (const link of links) {
                                const href = link.getAttribute('href') || '';
                                if (seen.has(href)) continue;
                                seen.add(href);

                                const title = (link.innerText || '').trim();
                                if (!title || title.length < 3) continue;

                                const fullUrl = href.startsWith('http') ? href :
                                    'https://jeunesdavenirs-recrut.fr' + href;

                                results.push({
                                    title: title.substring(0, 200),
                                    url: fullUrl
                                });
                            }
                            return results;
                        }
                    """)

                    if not offers:
                        self.logger.info("Aucune offre page %d", page_num + 1)
                        break

                    # Filtrer alternance/stage
                    filtered = 0
                    for item in offers:
                        title_lower = item["title"].lower()
                        is_target = any(t in title_lower for t in
                            ["alternance", "apprentissage", "stage", "apprenti"])
                        ct = "Alternance" if is_target else ""

                        try:
                            offer = ScrapedOffer(
                                title=item["title"],
                                description=item["title"],
                                url=item["url"],
                                source=self.name,
                                contract_type=ct,
                            )
                            all_offers.append(offer)
                            if is_target:
                                filtered += 1
                        except Exception as exc:
                            self.logger.debug("Item ignore: %s", exc)

                    self.logger.info("Page %d: %d offres (%d alternance/stage)",
                                     page_num + 1, len(offers), filtered)

                    # Pagination : vérifier "Suivant"
                    if page_num + 1 < max_pages:
                        next_btn = page.locator('a:has-text("Suivant"), a:has-text("Next")').first
                        if await next_btn.count() == 0:
                            self.logger.info("Plus de pages")
                            break
                    await asyncio.sleep(1)

            finally:
                await browser.close()

        return all_offers

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            self.logger.info("Aucune offre a logger.")
            return
        self.logger.info("=== DEBUG: %d exemple(s) ===", min(3, len(offers)))
        for i, o in enumerate(offers[:3], 1):
            self.logger.info(
                "--- Offre #%d ---\n"
                "  Titre       : %s\n"
                "  Contrat     : %s\n"
                "  URL         : %s",
                i, o.title, o.contract_type or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
