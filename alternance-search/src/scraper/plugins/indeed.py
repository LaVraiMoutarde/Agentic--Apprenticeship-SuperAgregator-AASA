"""
Scraper Indeed France — offres d'alternance.

URL seed : https://fr.indeed.com/jobs?q=alternance&l={location}

Structure Indeed (2026) :
  - Page resultats : /jobs?q=...&l=...&start=N
  - Chaque offre est dans <td class="resultContent">
  - Titre : <a class="jcs-JobTitle" data-jk="xxx">
  - Entreprise : <span data-testid="company-name">
  - Lieu : <div data-testid="text-location">
  - Salaire/contrat : <li data-testid="attribute_snippet_testid">
  - Pagination : &start=0, 10, 20...

Utilise Playwright avec Brave.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from ..exceptions import ScraperNetworkError


BASE_URL = "https://fr.indeed.com"
SEARCH_URL = f"{BASE_URL}/jobs"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"


class IndeedScraper(BaseScraper):
    """Scraper pour Indeed France (offres d'alternance)."""

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30,
    ) -> None:
        super().__init__()
        self.headless = headless
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "indeed"

    def scrape(
        self,
        query: str = "alternance",
        *,
        location: str = "",
        max_pages: int = 10,
        criteria=None,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self._criteria = criteria

        self.logger.info("Debut Indeed — query='%s', location='%s', max_pages=%d",
                         query, location, max_pages)

        try:
            offers = asyncio.run(self._scrape_async(query, location, max_pages or 9999))
            all_offers = self.validate_output(offers)
            self.logger.info("Scraping termine — %d offres validees", len(all_offers))
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
                executable_path=BRAVE_PATH if not self.headless else None,
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
                for page_num in range(max_pages):
                    start = page_num * 10
                    url = self._build_url(query, location, start)
                    self.logger.info("Page %d/%d — %s", page_num + 1, max_pages, url)

                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=self.timeout * 1000)
                    await page.wait_for_timeout(5000)

                    # Détection blocage
                    body = await page.inner_text("body") or ""
                    if any(t in body.lower() for t in ["captcha", "verify", "robot"]):
                        self.logger.warning("Page bloquee (CAPTCHA) — arret")
                        break

                    # Extraction par JavaScript
                    offers = await page.evaluate("""
                        () => {
                            // Essayer td.resultContent (page 1)
                            let cards = document.querySelectorAll('td.resultContent');
                            if (cards.length === 0) {
                                // Fallback: chercher les liens directement (page 2+)
                                const links = document.querySelectorAll('a.jcs-JobTitle');
                                const results = [];
                                const seen = new Set();
                                for (const link of links) {
                                    const jk = link.getAttribute('data-jk') || '';
                                    if (!jk || seen.has(jk)) continue;
                                    seen.add(jk);
                                    const title = (link.innerText || '').trim();
                                    if (!title) continue;
                                    // Remonter au td.resultContent si possible
                                    let td = link;
                                    for (let d = 0; d < 6; d++) {
                                        if (td && td.tagName === 'TD' && td.className.includes('resultContent')) break;
                                        if (td) td = td.parentElement;
                                    }
                                    const companyEl = td ? td.querySelector('[data-testid="company-name"]') : null;
                                    const company = companyEl ? companyEl.innerText.trim() : '';
                                    const locationEl = td ? td.querySelector('[data-testid="text-location"]') : null;
                                    const location = locationEl ? locationEl.innerText.trim() : '';
                                    const snippets = td ? td.querySelectorAll('[data-testid="attribute_snippet_testid"]') : [];
                                    let salary = '', contract = '';
                                    for (const s of snippets) {
                                        const t = (s.innerText || '').trim();
                                        if (t.match(/[€€0-9]/)) salary = t;
                                        else if (t.match(/alternance|apprentissage|stage|cdi|cdd/i)) contract = t;
                                    }
                                    results.push({ title, jk, company, location, salary, contract });
                                }
                                return results;
                            }
                            // Mode standard td.resultContent (page 1)
                            return Array.from(cards).map(td => {
                                const link = td.querySelector('a.jcs-JobTitle');
                                if (!link) return null;
                                const title = (link.innerText || '').trim();
                                const jk = link.getAttribute('data-jk') || '';
                                if (!title || !jk) return null;
                                const companyEl = td.querySelector('[data-testid="company-name"]');
                                const company = companyEl ? companyEl.innerText.trim() : '';
                                const locationEl = td.querySelector('[data-testid="text-location"]');
                                const location = locationEl ? locationEl.innerText.trim() : '';
                                const snippets = td.querySelectorAll('[data-testid="attribute_snippet_testid"]');
                                let salary = '', contract = '';
                                for (const s of snippets) {
                                    const t = (s.innerText || '').trim();
                                    if (t.match(/[€€0-9]/)) salary = t;
                                    else if (t.match(/alternance|apprentissage|stage|cdi|cdd/i)) contract = t;
                                }
                                return { title, jk, company, location, salary, contract };
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
                                url=f"{BASE_URL}/viewjob?jk={item['jk']}",
                                source=self.name,
                                source_id=item["jk"],
                                company=item["company"][:300],
                                location=item["location"][:300],
                                contract_type=item["contract"][:100],
                                salary_raw=item["salary"][:100],
                            )
                            all_offers.append(offer)
                        except Exception as exc:
                            self.logger.debug("Item ignore: %s", exc)

                    self.logger.info("Page %d: %d offres (total: %d)",
                                     page_num + 1, len(offers), len(all_offers))

                    # Vérifier s'il y a une page suivante
                    if page_num + 1 < max_pages:
                        next_btn = page.locator('a[data-testid="pagination-page-next"]').first
                        if await next_btn.count() > 0:
                            await asyncio.sleep(2)
                        else:
                            break

            finally:
                await browser.close()

        return all_offers

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _build_url(self, query: str, location: str, start: int = 0) -> str:
        params = [f"q={query.replace(' ', '+')}"]
        if location:
            params.append(f"l={location.replace(' ', '+')}")

        # Rayon depuis criteria (defaut 25)
        radius = 25
        if hasattr(self, '_criteria') and self._criteria is not None:
            if self._criteria.radius_km:
                radius = self._criteria.radius_km
            # Contrat : jt=apprenticeship ou internship
            if self._criteria.contract == "apprentissage":
                params.append("jt=apprenticeship")
            elif self._criteria.contract == "professionnalisation":
                params.append("jt=internship")
            elif self._criteria.contract:
                params.append(f"jt={self._criteria.contract.replace(' ', '+')}")

        params.append(f"radius={radius}")
        if start > 0:
            params.append(f"start={start}")
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
                "  Salaire     : %s\n"
                "  Contrat     : %s\n"
                "  JK          : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.salary_raw or "-", o.contract_type or "-",
                o.source_id or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
