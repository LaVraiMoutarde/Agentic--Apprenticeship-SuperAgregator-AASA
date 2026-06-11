"""
Scraper HelloWork — offres d'alternance.

URL seed : https://www.hellowork.com/fr-fr/emploi/recherche.html?c=Alternance

Structure (2026) :
  - Chaque offre est un <a data-cy="offerTitle" href="/fr-fr/emplois/ID.html">
  - Titre : <p class="typo-l">TITLE</p>
  - Entreprise : <p class="typo-s inline">COMPANY</p>
  - Métadonnées dans aria-label:
    "Voir offre de TITLE à LOCATION, chez COMPANY, pour un CONTRAT, salaire SALAIRE"
  - Pas d'auth nécessaire
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError


BASE_URL = "https://www.hellowork.com"
SEARCH_URL = f"{BASE_URL}/fr-fr/emploi/recherche.html"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"


class HelloWorkScraper(BaseScraper):
    """Scraper pour HelloWork France (offres d'alternance)."""

    def __init__(self, headless: bool = True, timeout: int = 30) -> None:
        super().__init__()
        self.headless = headless
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "hellowork"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 10,
        criteria=None,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self._criteria = criteria

        self.logger.info("Debut HelloWork — query='%s', location='%s', max_pages=%d",
                         query, location, max_pages)

        try:
            offers = asyncio.run(self._scrape_async(query, location, max_pages or 9999))
            all_offers = self.validate_output(offers)
            self.logger.info("%d offres validees sur %d", len(all_offers), len(offers))
        except Exception as exc:
            self.logger.error("Erreur fatale: %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages,
                                     total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    # ═══════════════════════════════════════════════════════════════
    # Scraping
    # ═══════════════════════════════════════════════════════════════

    async def _scrape_async(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                executable_path=BRAVE_PATH,
                args=["--disable-blink-features=AutomationControlled",
                       "--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="fr-FR",
            )
            page = await context.new_page()

            try:
                current_url = self._build_url(query, location)
                page_num = 0

                while page_num < max_pages:
                    page_num += 1
                    self.logger.info("Page %d/%d — %s", page_num, max_pages, current_url)

                    await page.goto(current_url, wait_until="domcontentloaded",
                                    timeout=self.timeout * 1000)
                    await page.wait_for_timeout(4000)

                    # Détection blocage
                    body = await page.inner_text("body") or ""
                    if any(t in body.lower() for t in ["captcha", "verify", "robot"]):
                        self.logger.warning("Page bloquee — arret")
                        break

                    # Détection du nombre total d'offres
                    total_match = __import__("re").search(r"(\d[\s\d]*)\s*offres?", body)
                    if total_match and page_num == 1:
                        total = int(total_match.group(1).replace("\u202f", "").replace(" ", ""))
                        # Compter les offres sur la page 1
                        first_count = await page.evaluate("""
                            () => document.querySelectorAll('a[data-cy="offerTitle"]').length
                        """)
                        if first_count > 0:
                            estimated_pages = max(1, (total + first_count - 1) // first_count)
                            effective_max = min(max_pages, estimated_pages)
                            self.logger.info("~%d offres, ~%d/p, ~%d pages", total, first_count, estimated_pages)
                            max_pages = effective_max

                    # Extraction par JavaScript
                    offers = await page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[data-cy="offerTitle"]');
                            return Array.from(links).map(link => {
                                const href = link.getAttribute('href') || '';
                                const ariaLabel = link.getAttribute('aria-label') || '';

                                // Titre depuis le <p.typo-l>
                                const titleEl = link.querySelector('.typo-l, [class*="typo-l"]');
                                const title = titleEl ? titleEl.innerText.trim() : '';

                                // Entreprise depuis le <p.typo-s>
                                const companyEl = link.querySelector('.typo-s.inline, .typo-s');
                                const company = companyEl ? companyEl.innerText.trim() : '';

                                if (!title || !href) return null;

                                // Parsing de l'aria-label pour extraire lieu, contrat, salaire
                                // Format: "Voir offre de TITLE à LOCATION, chez COMPANY, super recruteur, pour un CONTRAT, avec un salaire de SALAIRE"
                                let location = '';
                                let contract = '';
                                let salary = '';

                                if (ariaLabel) {
                                    // Lieu: après "à " avant ", chez "
                                    const locMatch = ariaLabel.match(/\\u00e0\\s+([^,]+),\\s+chez\\s+/i);
                                    if (locMatch) location = locMatch[1].trim();

                                    // Contrat: après "pour un " avant ", avec"
                                    const ctrMatch = ariaLabel.match(/pour\\s+un\\s+([^,]+),?\\s*avec/i);
                                    if (ctrMatch) contract = ctrMatch[1].trim();

                                    // Salaire: après "salaire de "
                                    const salMatch = ariaLabel.match(/salaire\\s+de\\s+(.+?)(?:,|$)/i);
                                    if (salMatch) salary = salMatch[1].trim();

                                    // Si pas de contrat, chercher "Alternance" directement
                                    if (!contract && /alternance/i.test(ariaLabel)) contract = 'Alternance';
                                }

                                return {
                                    title, company, location, contract, salary,
                                    url: href.startsWith('http') ? href : 'https://www.hellowork.com' + href
                                };
                            }).filter(x => x !== null);
                        }
                    """)

                    if not offers:
                        self.logger.info("Aucune offre trouvee")
                        break

                    for item in offers:
                        try:
                            offer = ScrapedOffer(
                                title=item["title"][:500],
                                description=item["title"],
                                url=item["url"],
                                source=self.name,
                                company=item["company"][:300] if item["company"] else "",
                                location=item["location"][:300] if item["location"] else "",
                                contract_type=item["contract"][:100] if item["contract"] else "",
                                salary_raw=item["salary"][:100] if item["salary"] else "",
                            )
                            all_offers.append(offer)
                        except Exception as exc:
                            self.logger.debug("Item ignore: %s", exc)

                    self.logger.info("Page %d: %d offres (total: %d)",
                                     page_num, len(offers), len(all_offers))

                    # Pagination: URL suivante
                    next_url = await self._next_page(page, current_url)
                    if not next_url or next_url == current_url:
                        self.logger.info("Plus de pages")
                        break
                    current_url = next_url
                    await asyncio.sleep(2)

            finally:
                await browser.close()

        return all_offers

    # ═══════════════════════════════════════════════════════════════
    # Pagination
    # ═══════════════════════════════════════════════════════════════

    async def _next_page(self, page, current_url: str) -> str | None:
        """Trouve l'URL de la page suivante."""
        # Chercher le lien "Suivant" ou page=N
        try:
            next_link = page.locator('a[rel="next"], a:has-text("Suivant")').first
            if await next_link.count() > 0:
                href = await next_link.get_attribute("href") or ""
                if href and href != "#" and href != current_url:
                    return href if href.startswith("http") else f"{BASE_URL}{href}"
        except Exception:
            pass

        # Fallback: incrémenter page=N dans l'URL
        page_match = re.search(r'[?&]page=(\d+)', current_url)
        if page_match:
            curr = int(page_match.group(1))
            next_page = curr + 1
            return current_url.replace(f"page={curr}", f"page={next_page}")
        else:
            sep = "&" if "?" in current_url else "?"
            return f"{current_url}{sep}page=2"

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _build_url(self, query: str, location: str) -> str:
        params = ["c=Alternance"]
        if query:
            params.append(f"k={query.replace(' ', '+')}")
        if location:
            params.append(f"l={location.replace(' ', '+')}")

        # Rayon depuis criteria
        if hasattr(self, '_criteria') and self._criteria is not None and self._criteria.radius_km:
            params.append(f"r={self._criteria.radius_km}")

        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            self.logger.info("Aucune offre a logger.")
            return
        self.logger.info("=== DEBUG: %d exemple(s) ===", min(3, len(offers)))
        for i, o in enumerate(offers[:3], 1):
            self.logger.info(
                "--- Offre #%d ---\n"
                "  Titre       : %s\n"
                "  Entreprise  : %s\n"
                "  Lieu        : %s\n"
                "  Contrat     : %s\n"
                "  Salaire     : %s\n"
                "  URL         : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.contract_type or "-", o.salary_raw or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
