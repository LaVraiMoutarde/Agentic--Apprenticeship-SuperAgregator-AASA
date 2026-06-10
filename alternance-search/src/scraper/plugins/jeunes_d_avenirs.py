"""
Scraper Jeunes d'Avenirs — offres emploi/alternance/stages.

URL seed : https://jeunesdavenirs-recrut.fr/
URL recherche : https://jeunesdavenirs-recrut.fr/offres

Structure DOM :
  - Accueil : cartes d'offres recentes
  - Page /offres : grille complete avec filtres
  - Chaque carte : titre, entreprise, lieu, contrat, date
  - Lien detail : /offre/i_[hash] ou /offre/r_[hash]
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://jeunesdavenirs-recrut.fr"
SEARCH_URL = f"{BASE_URL}/offres"
HOME_URL = BASE_URL

# Types de contrats alternance/stage a filtrer
TARGET_CONTRACTS = {"alternance", "apprentissage", "professionnalisation", "stage", "contrat d'apprentissage"}


class JeunesDAvenirsScraper(BaseScraper):
    """Scraper pour Jeunes d'Avenirs (offres emploi/alternance/stage)."""

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
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut Jeunes d'Avenirs — query='%s', location='%s', max_pages=%d", query, location, max_pages)

        try:
            offers = asyncio.run(self._scrape_async(query, location, max_pages))
            all_offers = self.validate_output(offers)
            self.logger.info("%d offres validees sur %d", len(all_offers), len(offers))
        except Exception as exc:
            self.logger.error("Erreur fatale: %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages, total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    async def _scrape_async(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="fr-FR",
            )
            page = await context.new_page()

            try:
                url = self._build_search_url(query, location)
                self.logger.info("Navigation vers %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                await page.wait_for_timeout(3000)

                # Attendre les offres
                try:
                    await page.wait_for_selector(
                        'a[href*="/offre/"], [class*="card"], [class*="offer"], article',
                        timeout=15000,
                    )
                except Exception:
                    self.logger.warning("Aucune offre trouvee sur la page")

                for pagenum in range(max_pages):
                    self.logger.info("Page %d/%d", pagenum + 1, max_pages)
                    await page.wait_for_timeout(1000)

                    offers = await self._extract_offers(page)
                    # Filtrer alternance/stage uniquement
                    filtered = [o for o in offers if self._is_alternance(o)]
                    all_offers.extend(filtered)
                    self.logger.info("Page %d: %d offres (%d alternance/stage)", pagenum + 1, len(offers), len(filtered))

                    if not offers:
                        break

                    if not await self._go_next(page):
                        break

            finally:
                await browser.close()

        return all_offers

    async def _extract_offers(self, page) -> list[ScrapedOffer]:
        """Extrait les offres de la page courante."""
        offers: list[ScrapedOffer] = []

        # Selecteurs de cartes d'offres Jeunes d'Avenirs
        card_selectors = [
            'a[href*="/offre/"]',
            "[class*='card-offer'], [class*='offer-card']",
            "article, [class*='job-card']",
            "a[href*='/offre/i_'], a[href*='/offre/r_']",
        ]

        cards = page.locator(", ".join(card_selectors))
        count = await cards.count()
        self.logger.debug("%s cartes trouvees", count)

        seen = set()
        for i in range(count):
            card = cards.nth(i)
            try:
                tag = await card.evaluate("el => el.tagName.toLowerCase()")

                # Si c'est un lien direct vers une offre
                href = ""
                if tag == "a":
                    href = (await card.get_attribute("href")) or ""
                    link_el = card
                else:
                    link_el = card.locator("a[href*='/offre/']").first()
                    if await link_el.count() == 0:
                        continue
                    href = (await link_el.get_attribute("href")) or ""

                if not href or "/offre/" not in href or href in seen:
                    continue
                seen.add(href)

                full_url = f"{BASE_URL}{href.strip()}" if href.strip().startswith("/") else href.strip()

                # Titre
                title = await self._el_text(link_el, "h2, h3, [class*='title'], [class*='heading'], strong")
                if not title:
                    title = (await link_el.inner_text()).strip()

                if not title:
                    continue

                title = title.strip()

                # Texte de la carte pour extraction
                card_text = (await card.inner_text()) if tag == "a" else (await link_el.locator("..").inner_text()) if await link_el.locator("..").count() > 0 else ""

                # Entreprise (souvent apres le titre dans le lien)
                company = self._extract_by_class(card, card_text,
                    ['[class*="company"]', '[class*="enterprise"]', '[class*="employer"]'])
                if not company:
                    company = self._extract_regex(card_text, [
                        r'(?:chez|@)\s*([A-Z][A-Za-z0-9éèêëàâîïôùûç\s&\-\']{2,60})',
                    ])

                # Lieu
                location = self._extract_by_class(card, card_text,
                    ['[class*="location"]', '[class*="lieu"]', '[class*="city"]', '[class*="place"]'])

                # Type de contrat
                contract_type = self._extract_by_class(card, card_text,
                    ['[class*="contract"]', '[class*="contrat"]', '[class*="type"]', '[class*="badge"]'])
                if not contract_type:
                    contract_type = self._extract_regex(card_text, [
                        r'(Alternance|Apprentissage|Stage|CDI|CDD|Contrat d\'apprentissage|Professionnalisation)',
                    ])

                # Date
                date_text = self._extract_by_class(card, card_text,
                    ['[class*="date"]', '[class*="postee"]', '[class*="time"]'])

                # Description
                description = self._extract_by_class(card, card_text,
                    ['[class*="desc"]', '[class*="content"]', 'p'])
                if not description:
                    description = title

                offer = ScrapedOffer(
                    title=title[:200],
                    description=description[:500],
                    url=full_url,
                    source=self.name,
                    company=company[:200] if company else "",
                    location=location[:200] if location else "",
                    contract_type=contract_type or "",
                )
                offers.append(offer)

            except Exception as exc:
                self.logger.debug("Carte #%d ignoree: %s", i, exc)

        return offers

    async def _el_text(self, parent, selector: str) -> str:
        el = parent.locator(selector).first()
        return (await el.inner_text()).strip() if await el.count() > 0 else ""

    def _extract_by_class(self, card, text: str, selectors: list[str]) -> str:
        """Extrait un champ depuis un selecteur CSS, ou du texte brut."""
        return ""  # La recherche par selecteur sera faite dans extract_offers

    def _extract_regex(self, text: str, patterns: list[str]) -> str:
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).strip() if m.lastindex else m.group(0).strip()
        return ""

    def _is_alternance(self, offer: ScrapedOffer) -> bool:
        """Filtre les offres : garde uniquement alternance/stage."""
        ct = (offer.contract_type or "").lower()
        title = offer.title.lower()
        for target in TARGET_CONTRACTS:
            if target in ct or target in title:
                return True
        return False

    async def _go_next(self, page) -> bool:
        """Pagination : page suivante ou bouton 'Voir plus'."""
        selectors = [
            'a:has-text("Suivant"), a[rel="next"], a:has-text("Page suivante")',
            'button:has-text("Voir plus"), button:has-text("Afficher plus")',
            '[class*="pagination"] a:not([disabled])',
            'a[class*="next"], button[class*="load-more"]',
        ]

        for sel in selectors:
            try:
                el = page.locator(sel).first()
                if await el.count() > 0:
                    tag = (await el.evaluate("e => e.tagName.toLowerCase()"))
                    is_disabled = await el.get_attribute("disabled") or ""
                    parent_class = (await el.locator("..").get_attribute("class")) or ""
                    if "disabled" in str(is_disabled) or "disabled" in parent_class:
                        continue
                    await el.click()
                    await page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue

        return False

    def _build_search_url(self, query: str, location: str) -> str:
        """URL de recherche : /offres?q=...&l=..."""
        params = {}
        if query:
            params["q"] = query
        if location:
            params["l"] = location
        if params:
            return f"{SEARCH_URL}?{'&'.join(f'{k}={v.replace(' ', '+')}' for k, v in params.items())}"
        return SEARCH_URL

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
                "  URL         : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.contract_type or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
