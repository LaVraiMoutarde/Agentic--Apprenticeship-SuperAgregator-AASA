"""
Scraper HelloWork — offres d'alternance.

URL seed : https://www.hellowork.com/fr-fr/emploi/recherche.html?c=Alternance

Structure DOM :
  - Liste d'offres : <ul role="list"> <li> (listitem)
  - Titre + entreprise : <h3> contenant le lien
  - Lien detail : <a href="/fr-fr/emplois/ID.html">
  - Lieu, contrat, salaire : <div> generiques
  - Pagination : ?page=N ou infini
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://www.hellowork.com"
SEARCH_URL = f"{BASE_URL}/fr-fr/emploi/recherche.html"


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

        self.logger.info("Debut HelloWork — query='%s', location='%s', max_pages=%d", query, location, max_pages)

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
        page_num = 0
        current_url = self._build_url(query, location)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
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
                while page_num < max_pages:
                    page_num += 1
                    self.logger.info("Page %d/%d — %s", page_num, max_pages, current_url)

                    offers = await self._scrape_page(page, current_url)
                    if offers is None:
                        self.logger.warning("Page bloque ou vide — arret")
                        break

                    all_offers.extend(offers)
                    self.logger.info("Page %d: %d offres (total: %d)", page_num, len(offers), len(all_offers))

                    if not offers:
                        break

                    # Prochaine page
                    next_url = await self._next_page(page)
                    if not next_url or next_url == current_url:
                        self.logger.info("Plus de pages")
                        break

                    current_url = next_url
                    await asyncio.sleep(random.uniform(1.5, 3.0))

            finally:
                await browser.close()

        return all_offers

    async def _scrape_page(self, page, url: str) -> list[ScrapedOffer] | None:
        """Scrape une page de resultats HelloWork, retourne None si bloque."""
        from playwright.async_api import TimeoutError as PWTimeout

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            await page.wait_for_timeout(3000)
        except PWTimeout:
            raise ScraperNetworkError(f"Timeout {url}", scraper_name=self.name)

        # Detection blocage
        body = (await page.inner_text("body")) or ""
        if any(t in body.lower() for t in ["captcha", "verify", "robot", "bloque"]):
            self.logger.warning("Page bloquee (CAPTCHA/anti-bot)")
            return None

        # Attendre le chargement des resultats
        try:
            await page.wait_for_selector(
                'ul[role="list"], [class*="list-offers"], [class*="resultats"], [class*="card-offer"]',
                timeout=10000,
            )
            await page.wait_for_timeout(1000)
        except PWTimeout:
            self.logger.warning("Aucun resultat trouve")
            return []

        offers = await self._extract_offers(page)
        return offers

    async def _extract_offers(self, page) -> list[ScrapedOffer]:
        """Extrait toutes les offres de la page courante."""
        offers: list[ScrapedOffer] = []

        # HelloWork utilise <li> dans <ul[role="list"]>
        cards = page.locator('ul[role="list"] > li, [class*="list-offers"] > div, .card-offer')
        count = await cards.count()
        self.logger.debug("%s cartes trouvees", count)

        for i in range(count):
            card = cards.nth(i)
            try:
                offer = await self._extract_card(card)
                if offer:
                    offers.append(offer)
            except Exception as exc:
                self.logger.debug("Carte #%d ignoree: %s", i, exc)

        return offers

    async def _extract_card(self, card) -> ScrapedOffer | None:
        """Extrait une offre d'une carte de resultat HelloWork."""
        # ── Lien detail + titre ──
        link_el = card.locator("a[href*='/emplois/']").first()
        if await link_el.count() == 0:
            return None

        href = (await link_el.get_attribute("href")) or ""
        if not href:
            return None

        full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

        # Titre depuis l'attribut title du lien (contenant tout)
        title_attr = (await link_el.get_attribute("title")) or ""
        # Fallback: texte du <h3>
        h3 = card.locator("h3, [class*='title']").first()
        h3_text = (await h3.inner_text()).strip() if await h3.count() > 0 else ""

        # Extraire titre et entreprise depuis title_attr
        # Format: "Voir offre de Alternant Juriste Droit Social H/F à Niort, chez ..., pour un Alternance, avec un salaire de ..."
        title, company = self._parse_title_attr(title_attr) if title_attr else (h3_text, "")

        # Fallback: depuis le h3
        if not title and h3_text:
            title = h3_text

        if not title:
            return None

        # ── Entreprise (depuis le texte du lien ou depuis un span) ──
        if not company:
            # Chercher le nom dans le lien sous le h3
            paras = await card.locator("h3 + p, h3 ~ p, [class*='company']").all_inner_texts()
            for p in paras:
                p = p.strip()
                if p and len(p) < 200 and p != title:
                    company = p
                    break

        # ── Autres metadonnees ──
        labels = await card.locator(
            "[class*='label'], [class*='tag'], [class*='chip'], "
            "[class*='badge'], [class*='info'], li[class*='item']"
        ).all_inner_texts()

        location = ""
        contract_type = "Alternance"
        salary_raw = ""
        remote = ""

        for label in labels:
            lab = label.strip()
            if not lab:
                continue
            # Lieu (contient un code postal)
            if re.search(r'\d{5}', lab) or re.search(r'^\d{2}\s', lab):
                location = lab
            # Type de contrat
            elif lab.lower() in ("alternance", "apprentissage", "contrat d'apprentissage", "contrat de professionnalisation"):
                contract_type = lab
            # Salaire
            elif re.search(r'[€€]', lab):
                salary_raw = lab
            # Remote
            elif "telétravail" in lab.lower() or "distanciel" in lab.lower() or "remote" in lab.lower():
                remote = lab

        # Fallback: chercher le lieu dans les divs generiques
        if not location:
            loc_el = card.locator("text= - ").first()  # "Niort - 79"
            if await loc_el.count() > 0:
                location = (await loc_el.inner_text()).strip()
            else:
                # Dernier recours: scanner tout le texte de la carte
                all_text = (await card.inner_text()) or ""
                loc_match = re.search(r'([A-Z][a-zéèêëàâîïôùûç]+(?:\s-\s\d{2,5})?)', all_text)
                if loc_match:
                    location = loc_match.group(1)

        # ── Description (pas dans la liste, optionnellement depuis page detail) ──
        description = title

        return ScrapedOffer(
            title=title[:200],
            description=description[:500],
            url=full_url,
            source=self.name,
            company=company[:200] if company else "",
            location=location[:200] if location else "",
            contract_type=contract_type,
            salary_raw=salary_raw,
        )

    def _parse_title_attr(self, title_attr: str) -> tuple[str, str]:
        """Parse l'attribut title du lien pour extraire titre et entreprise.

        Format HelloWork:
        "Voir offre de Alternant Juriste Droit Social H/F à Niort - 79, chez Groupe IMA, super recruteur,
         pour un Alternance, avec un salaire de 492,22 - 1 823,03 EUR/ mois..."
        """
        if not title_attr:
            return ("", "")

        # Extraire titre: entre "offre de " et " a "
        title = ""
        m = re.search(r'offre\s+de\s+(.+?)\s+a\s+', title_attr, re.I)
        if m:
            title = m.group(1).strip()

        # Extraire entreprise: entre " chez " et ", "
        company = ""
        m = re.search(r'chez\s+(.+?)(?:[,]|super recruteur|pour un)', title_attr, re.I)
        if m:
            company = m.group(1).strip()

        return (title, company)

    async def _next_page(self, page) -> str | None:
        """Trouve l'URL de la page suivante dans la pagination HelloWork."""
        # Chercher differents patterns de pagination
        selectors = [
            'a[rel="next"], a:has-text("Suivant"), a:has-text("Page suivante")',
            '[class*="pagination"] a:not([disabled]):has-text(">")',
            '[class*="pagination"] a:not([disabled]):has-text("›")',
            '[class*="pagination"] li:not(.disabled) a[href*="page="]',
            'nav[aria-label="pagination"] a[href]:not([aria-disabled="true"])',
            'button:has-text("Afficher plus")',
            'a:has-text("charger plus")',
        ]

        for selector in selectors:
            try:
                link = page.locator(selector).first()
                if await link.count() > 0:
                    # Verifier si c'est un bouton (chargement AJAX)
                    tag = (await link.evaluate("el => el.tagName")).lower()
                    if tag in ("a",):
                        href = await link.get_attribute("href")
                        if href and href != "#":
                            return href if href.startswith("http") else f"{BASE_URL}{href}"
                    elif tag in ("button",):
                        # Cliquer et attendre le chargement AJAX
                        self.logger.info("Clic sur 'Afficher plus'...")
                        await link.click()
                        await page.wait_for_timeout(3000)
                        return page.url
            except Exception:
                continue

        # Fallback: incrementer le param page= dans l'URL
        current_url = page.url
        page_match = re.search(r'[?&]page=(\d+)', current_url)
        if page_match:
            current_page = int(page_match.group(1))
            return current_url.replace(f"page={current_page}", f"page={current_page + 1}")
        else:
            # Ajouter ?page=2 (ou &page=2)
            sep = "&" if "?" in current_url else "?"
            return f"{current_url}{sep}page=2"

        return None

    def _build_url(self, query: str, location: str) -> str:
        """Construit l'URL de recherche HelloWork."""
        params = ["c=Alternance"]  # Filtrer par defaut
        if query:
            params.append(f"k={self._url_encode(query)}")
        if location:
            params.append(f"l={self._url_encode(location)}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _url_encode(self, text: str) -> str:
        """Encode simple pour URL."""
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
                "  Contrat     : %s\n"
                "  Salaire     : %s\n"
                "  URL         : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.contract_type or "-", o.salary_raw or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
