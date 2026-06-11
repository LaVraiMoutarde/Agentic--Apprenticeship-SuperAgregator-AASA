"""
Scraper La Bonne Alternance — API publique + Playwright fallback.

URL : https://labonnealternance.apprentissage.beta.gouv.fr/recherche

Stratégie :
  1. Playwright (Brave) pour naviguer et extraire les offres d'emploi
  2. API REST /api/v1/formations?romes=...&caller=... en fallback
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import requests as req

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError


BASE_URL = "https://labonnealternance.apprentissage.beta.gouv.fr"
SEARCH_URL = f"{BASE_URL}/recherche"
API_FORMATIONS = f"{BASE_URL}/api/v1/formations"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

DEFAULT_ROMES = ["M1805", "M1806", "M1810"]


class LaBonneAlternanceScraper(BaseScraper):
    """Scraper pour La Bonne Alternance."""

    def __init__(
        self,
        romes: list[str] | None = None,
        radius: int = 30,
        timeout: int = 30,
    ) -> None:
        super().__init__()
        self.romes = romes or DEFAULT_ROMES
        self.radius = radius
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "la_bonne_alternance"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 3,
        criteria=None,
    ) -> ScraperResult:
        _ = location
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self._criteria = criteria

        self.logger.info("Debut — query='%s'", query)

        romes = self._resolve_romes(query) or self.romes
        self.logger.info("Codes ROME: %s", romes)

        # Playwright (offres d'emploi)
        try:
            offers = asyncio.run(self._playwright_scrape(query, max_pages))
            all_offers = self.validate_output(offers)
            self.logger.info("Playwright OK — %d offres", len(all_offers))
        except Exception as exc:
            self.logger.warning("Playwright echoue: %s", str(exc)[:100])
            errors.append(exc)

        # Fallback API formations
        if not all_offers:
            try:
                offers = self._api_formations(romes)
                all_offers = self.validate_output(offers)
                self.logger.info("API formations OK — %d offres", len(all_offers))
            except Exception as exc:
                self.logger.error("API echoue: %s", str(exc)[:100])
                errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages,
                                     total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    # ═══════════════════════════════════════════════════════════════
    # Playwright
    # ═══════════════════════════════════════════════════════════════

    async def _playwright_scrape(self, query: str, max_pages: int = 3) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=BRAVE_PATH,
                args=["--disable-blink-features=AutomationControlled",
                       "--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(locale="fr-FR",
                viewport={"width": 1920, "height": 1080})

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # Remplir la recherche
            if query:
                search_input = page.locator('[role="combobox"]').first
                if await search_input.count() > 0:
                    await search_input.fill(query)
                    await page.wait_for_timeout(1500)

            # Cliquer "C'est parti"
            submit_btn = page.locator('button[type="submit"]').first
            if await submit_btn.count() > 0:
                await submit_btn.evaluate_handle("el => el.click()")
                await page.wait_for_timeout(5000)
                await page.wait_for_load_state("networkidle")

            # Extraire les offres du DOM
            for page_num in range(max_pages):
                self.logger.info("LBA page %d/%d", page_num + 1, max_pages)

                offers = await page.evaluate("""
                    () => {
                        const results = [];
                        // Chercher des cartes d'offres
                        const cards = document.querySelectorAll(
                            '[class*="card"], [class*="result"], article, ' +
                            '[class*="job"], [class*="offer"], li'
                        );
                        const seen = new Set();

                        for (const card of cards) {
                            const text = (card.innerText || '').trim();
                            if (text.length < 30) continue;

                            const link = card.querySelector('a[href]');
                            const href = link ? link.getAttribute('href') || '' : '';
                            if (href && seen.has(href)) continue;
                            if (href) seen.add(href);

                            const titleEl = card.querySelector('h2, h3, strong, [class*="title"]');
                            const title = titleEl ? titleEl.innerText.trim() : '';
                            if (!title || title.length < 5) continue;

                            const companyEl = card.querySelector('[class*="company"], [class*="employeur"]');
                            const company = companyEl ? companyEl.innerText.trim() : '';

                            const locEl = card.querySelector('[class*="lieu"], [class*="location"], [class*="ville"]');
                            const location = locEl ? locEl.innerText.trim() : '';

                            const url = href && href.startsWith('http') ? href :
                                href ? 'https://labonnealternance.apprentissage.beta.gouv.fr' + href : '';

                            results.push({
                                title: title.substring(0, 200),
                                description: text.substring(0, 500),
                                company: company.substring(0, 200),
                                location: location.substring(0, 200),
                                url: url
                            });
                        }
                        return results;
                    }
                """)

                if offers:
                    for item in offers:
                        try:
                            offer = ScrapedOffer(
                                title=item["title"],
                                description=item["description"] or item["title"],
                                url=item["url"] or SEARCH_URL,
                                source=self.name,
                                company=item["company"],
                                location=item["location"],
                            )
                            all_offers.append(offer)
                        except Exception as exc:
                            self.logger.debug("Item ignore: %s", exc)

                    self.logger.info("Page %d: %d offres (total: %d)",
                                     page_num + 1, len(offers), len(all_offers))
                else:
                    self.logger.info("Aucune offre page %d", page_num + 1)

                # Pagination
                if page_num + 1 < max_pages:
                    has_next = await self._next_page(page)
                    if not has_next:
                        break

            await browser.close()

        return all_offers

    async def _next_page(self, page) -> bool:
        """Passe à la page suivante."""
        for sel in [
            'a:has-text("Suivant"), button:has-text("Suivant")',
            'a[rel="next"], a:has-text("»")',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    await page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        return False

    # ═══════════════════════════════════════════════════════════════
    # API Formations (fallback)
    # ═══════════════════════════════════════════════════════════════

    def _api_formations(self, romes: list[str]) -> list[ScrapedOffer]:
        params = {
            "romes": ",".join(romes),
            "radius": self.radius,
            "caller": "alternance-search",
        }
        self.logger.info("API formations: %s", params)

        resp = req.get(API_FORMATIONS, params=params, timeout=self.timeout,
                       headers={"User-Agent": "alternance-search/0.1"})

        if resp.status_code != 200:
            raise ScraperNetworkError(f"API HTTP {resp.status_code}: {resp.text[:200]}",
                                       scraper_name=self.name)

        data = resp.json()
        results = data.get("results", [])
        offers: list[ScrapedOffer] = []

        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("intitule") or ""
            if not title:
                continue

            company_raw = item.get("company") or item.get("employeur") or {}
            company = company_raw.get("name") or company_raw.get("nom") or "" if isinstance(company_raw, dict) else str(company_raw)

            place = item.get("place") or item.get("lieu") or {}
            location = place.get("city") or place.get("ville") or "" if isinstance(place, dict) else str(place)

            url = item.get("url") or item.get("link") or ""
            description = item.get("description") or item.get("descriptif") or ""
            diploma = item.get("diploma") or item.get("niveau") or ""

            contact = item.get("contact") or {}
            contact_name = contact.get("name") or ""
            contact_email = contact.get("email") or ""

            try:
                offers.append(ScrapedOffer(
                    title=title.strip(),
                    description=(description.strip() or title)[:500],
                    url=url.strip() or SEARCH_URL,
                    source=self.name,
                    company=company.strip(),
                    location=location.strip(),
                    contact_name=contact_name.strip(),
                    contact_email=str(contact_email).strip(),
                    required_level=str(diploma).strip(),
                ))
            except Exception:
                continue

        return offers

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _resolve_romes(self, query: str) -> list[str] | None:
        if not query:
            return None
        q = query.lower()
        mapping = {
            "info": "M1805", "data": "M1805", "dev": "M1805",
            "python": "M1805", "java": "M1805", "web": "M1805",
            "logiciel": "M1805", "reseau": "M1810",
            "informatique": "M1805", "cloud": "M1810",
            "rh": "E1201", "compta": "E1401",
            "commerce": "D1505", "vente": "D1505",
            "marketing": "E1103", "communication": "E1103",
            "logistique": "E1505",
        }
        matches = set()
        for term, code in mapping.items():
            if term in q:
                matches.add(code)
        return list(matches)[:5] if matches else None

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            self.logger.info("Aucune offre a logger.")
            return
        for i, o in enumerate(offers[:3], 1):
            self.logger.info(
                "--- Offre #%d ---\n"
                "  Titre       : %s\n"
                "  Entreprise  : %s\n"
                "  Lieu        : %s\n"
                "  URL         : %s",
                i, o.title[:80], o.company or "-",
                o.location or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
