"""
Scraper JobTeaser ENSEA — offres d'alternance de l'ecole ENSEA.

URL seed : https://ensea.jobteaser.com/fr/job-offers?contract=alternating

Authentification:
  - OpenID Connect via jobteaser.com (OAuth)
  - Necessite une session prealable
  - Utilise storage_state Playwright (comme Moodle)

Structure:
  - Liste d'offres (grille de cartes)
  - Filtres via URL (contract=alternating deja present)
  - Pagination : scroll infini ou bouton "Voir plus"
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://ensea.jobteaser.com"
SEARCH_URL = f"{BASE_URL}/fr/job-offers"


class JobTeaserEnseaScraper(BaseScraper):
    """Scraper pour JobTeaser ENSEA (offres d'alternance)."""

    def __init__(
        self,
        storage_state_path: str = "auth/jobteaser_ensea_state.json",
        headless: bool = True,
        timeout: int = 30,
    ) -> None:
        super().__init__()
        self.storage_state_path = self._resolve_path(storage_state_path)
        self.headless = headless
        self.timeout = timeout

    @staticmethod
    def _resolve_path(path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = (Path(__file__).parent.parent.parent.parent / p).resolve()
        return p

    @property
    def name(self) -> str:
        return "jobteaser_ensea"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 5,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut JobTeaser ENSEA — query='%s', storage=%s", query, self.storage_state_path)

        try:
            offers = asyncio.run(self._scrape_async(query, max_pages))
            all_offers = self.validate_output(offers)
            self.logger.info("%d offres validees sur %d", len(all_offers), len(offers))
        except Exception as exc:
            self.logger.error("Erreur fatale: %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages, total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    async def _scrape_async(self, query: str, max_pages: int) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            ctx_kwargs: dict = {}
            if self.storage_state_path.exists():
                ctx_kwargs["storage_state"] = str(self.storage_state_path)
                self.logger.info("Session chargee depuis %s", self.storage_state_path.name)
            else:
                self.logger.warning("Aucun storage_state — connexion necessaire")

            context = await browser.new_context(**ctx_kwargs,
                viewport={"width": 1920, "height": 1080}, locale="fr-FR")
            page = await context.new_page()

            try:
                url = self._build_url(query)
                self.logger.info("Navigation vers %s", url)
                await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                await page.wait_for_timeout(3000)

                # Detection redirection auth
                if "connect.jobteaser.com" in page.url or "login" in page.url.lower():
                    self.logger.error("Redirige vers authentification OpenID")
                    raise RuntimeError(
                        "Authentification requise. Lancez d'abord scripts/save_auth_jobteaser.py"
                    )

                for pagenum in range(max_pages):
                    self.logger.info("Page %d/%d", pagenum + 1, max_pages)

                    # Attendre le chargement des offres
                    try:
                        await page.wait_for_selector(
                            '[class*="card"], [class*="offer"], [class*="job"], '
                            '[data-testid*="offer"], [data-testid*="job"], '
                            'article, a[href*="/fr/job-offers/"]',
                            timeout=15000,
                        )
                        await page.wait_for_timeout(2000)
                    except Exception:
                        self.logger.info("Plus d'offres")
                        break

                    offers = await self._extract_offers(page)
                    all_offers.extend(offers)
                    self.logger.info("Page %d: %d offres (total: %d)", pagenum + 1, len(offers), len(all_offers))

                    if not offers:
                        break

                    # Pagination
                    has_more = await self._load_more(page)
                    if not has_more:
                        self.logger.info("Fin de la pagination")
                        break

                    await asyncio.sleep(random.uniform(1, 2))

            finally:
                await browser.close()

        return all_offers

    async def _extract_offers(self, page) -> list[ScrapedOffer]:
        """Extrait toutes les offres visibles sur la page."""
        offers: list[ScrapedOffer] = []

        # Selecteurs JobTeaser
        card_selectors = [
            'a[href*="/fr/job-offers/"]',
            '[class*="card"], article, [class*="offer"]',
            '[data-testid*="offer"], [data-testid*="job"]',
        ]
        cards = page.locator(", ".join(card_selectors))
        count = await cards.count()
        self.logger.debug("%s cartes trouvees", count)

        seen = set()
        for i in range(count):
            card = cards.nth(i)
            try:
                href = ""
                if await card.locator("..").count() > 0:
                    pass
                tag = await card.evaluate("el => el.tagName.toLowerCase()")
                link_el = card
                if tag != "a":
                    link_el = card.locator("a[href*='/fr/job-offers/']").first()
                    if await link_el.count() == 0:
                        continue
                href = (await link_el.get_attribute("href")) or ""

                if not href or href in seen:
                    continue
                seen.add(href)

                full_url = self._make_abs(href)
                title = await self._el_text(link_el, "h2, h3, [class*='title'], strong, [class*='name']")
                if not title:
                    title = (await link_el.inner_text()).strip()

                if not title:
                    continue

                # Texte complet de la carte
                card_text = await card.inner_text() if tag == "a" else await link_el.locator("..").inner_text() if await link_el.locator("..").count() > 0 else ""

                company = await self._extract_company(card, card_text)
                location = await self._extract_location(card, card_text)
                description = await self._extract_description(card, card_text) or title
                contract_type = "Alternance"

                offers.append(ScrapedOffer(
                    title=title[:200],
                    description=description[:500],
                    url=full_url,
                    source=self.name,
                    company=company,
                    location=location,
                    contract_type=contract_type,
                ))

            except Exception as exc:
                self.logger.debug("Carte #%d ignoree: %s", i, exc)

        return offers

    async def _el_text(self, parent, selector: str) -> str:
        el = parent.locator(selector).first()
        return (await el.inner_text()).strip() if await el.count() > 0 else ""

    async def _extract_company(self, card, text: str) -> str:
        """Extrait le nom de l'entreprise de la carte."""
        # Selecteurs specifiques
        for sel in ['[class*="company"], [class*="enterprise"], [itemprop="name"]']:
            v = await self._el_text(card, sel)
            if v:
                return v

        # Regex dans tout le texte de la carte
        if text:
            m = re.search(r'(?:chez|@|entreprise\s*:)\s*([A-Z][A-Za-z0-9éèêëàâîïôùûç\s&\-]{2,50})', text)
            if m:
                return m.group(1).strip()

        return ""

    async def _extract_location(self, card, text: str) -> str:
        for sel in ['[class*="location"], [class*="lieu"], [class*="city"], [itemprop*="locality"]']:
            v = await self._el_text(card, sel)
            if v:
                return v
        if text:
            m = re.search(r'(?:a|localisation\s*:)\s*([A-Z][a-zéèêëàâîïôùûç]+(?:\s?\(?\d{5}\)?)?)', text)
            if m:
                return m.group(1).strip()
        return ""

    async def _extract_description(self, card, text: str) -> str:
        for sel in ['[class*="desc"], [class*="content"], p, [class*="snippet"]']:
            v = await self._el_text(card, sel)
            if v:
                return v
        return text[:500] if text else ""

    async def _load_more(self, page) -> bool:
        """Tente de charger plus d'offres (pagination infinie ou bouton)."""
        selectors = [
            'button:has-text("Voir plus"), button:has-text("Afficher plus")',
            'button:has-text("Load more"), button:has-text("Show more")',
            '[class*="load-more"], [class*="pagination"] button',
        ]

        for sel in selectors:
            btn = page.locator(sel).first()
            if await btn.count() > 0:
                try:
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    return True
                except Exception:
                    continue

        # Scroll infini
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("document.body.scrollHeight")
            # Verifier si le contenu a change
            return True
        except Exception:
            return False

    def _build_url(self, query: str) -> str:
        params = ["contract=alternating"]
        if query:
            params.append(f"keyword={self._url_encode(query)}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _make_abs(self, href: str) -> str:
        return href if href.startswith("http") else f"{BASE_URL}{href}"

    def _url_encode(self, text: str) -> str:
        return text.replace(" ", "+")

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
                "  URL         : %s\n"
                "  Contrat     : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.url or "-", o.contract_type or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
