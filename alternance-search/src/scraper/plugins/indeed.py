"""
Scraper Indeed France — offres d'alternance.

URL seed : https://fr.indeed.com/jobs?q=alternance&l={location}

Structure Indeed :
  - Page resultats : /jobs?q=...&l=...&start=N
  - Page detail : /viewjob?jk=JOB_KEY
  - Pagination : &start=0, 10, 20...
  - Anti-bot : detection possible, retry integre

Utilise Playwright (anti-bot, JS rendering).
Ne bypass PAS CAPTCHA — log et skip si bloque.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from ..exceptions import ScraperNetworkError, ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://fr.indeed.com"
SEARCH_URL = f"{BASE_URL}/jobs"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # secondes


class IndeedScraper(BaseScraper):
    """Scraper pour Indeed France (offres d'alternance)."""

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        super().__init__()
        self.headless = headless
        self.timeout = timeout
        self.max_retries = max_retries

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

        self.logger.info("Debut scraping Indeed — query='%s', location='%s', max_pages=%d", query, location, max_pages)

        try:
            offers = asyncio.run(self._scrape_async(query, location, max_pages))
            all_offers = self.validate_output(offers)
            self.logger.info("Scraping termine — %d offres validees", len(all_offers))
        except Exception as exc:
            self.logger.error("Erreur fatale: %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages, total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    async def _scrape_async(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        """Boucle principale de scraping avec pagination et retry."""
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless,
                                              args=["--disable-blink-features=AutomationControlled"])
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
                    url = self._build_search_url(query, location, start)
                    self.logger.info("Page %d/%d — %s", page_num + 1, max_pages, url)

                    # Tentative avec retry
                    offers = await self._scrape_page_with_retry(page, url, query)
                    if offers is None:
                        self.logger.warning("Page %d bloquee ou vide — arret", page_num + 1)
                        break

                    all_offers.extend(offers)
                    self.logger.info("Page %d: %d offres (total: %d)", page_num + 1, len(offers), len(all_offers))

                    if len(offers) == 0:
                        break

                    # Delai de politesse entre les pages
                    await asyncio.sleep(random.uniform(2.0, 4.0))

            finally:
                await browser.close()

        return all_offers

    async def _scrape_page_with_retry(self, page, url: str, query: str) -> list[ScrapedOffer] | None:
        """Tente de scraper une page avec retry exponentiel."""
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._scrape_single_page(page, url, query)
            except ScraperNetworkError as exc:
                self.logger.warning("Tentative %d/%d echouee: %s", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    delay = RETRY_BASE_DELAY * attempt + random.uniform(1, 3)
                    self.logger.info("Attente %.1f secondes avant retry...", delay)
                    await asyncio.sleep(delay)
                else:
                    self.logger.error("Page definitivement inaccessible apres %d tentatives", self.max_retries)
                    return None
            except Exception as exc:
                self.logger.error("Erreur inattendue page %s: %s", url, exc)
                return None
        return None

    async def _scrape_single_page(self, page, url: str, query: str) -> list[ScrapedOffer]:
        """Scrape une page de resultats Indeed."""
        from playwright.async_api import TimeoutError

        offers: list[ScrapedOffer] = []

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            await page.wait_for_timeout(3000)
        except TimeoutError:
            raise ScraperNetworkError(f"Timeout sur {url}", scraper_name=self.name)
        except Exception as exc:
            raise ScraperNetworkError(f"Navigation echouee: {exc}", scraper_name=self.name, original=exc)

        # Verifier si la page est bloquee (CAPTCHA, rate-limit, etc.)
        page_text = await page.inner_text("body") or ""
        if any(term in page_text.lower() for term in ["captcha", "verify", "robot", "automated", "bloque"]):
            raise ScraperNetworkError("Page bloquee (CAPTCHA ou rate-limit detecte)", scraper_name=self.name)

        # Attendre et extraire les resultats
        # On attend d'abord que le contenu soit charge
        await page.wait_for_timeout(5000)

        # Debug: verifier l'etat de la page
        body = (await page.inner_text("body")) or ""
        self.logger.info("Page chargee: %d caracteres, contient 'emplois': %s",
                         len(body), "'emplois' in body" in body)

        # Compter les offres avec data-jk ou jcs-JobTitle
        cards = page.locator("a[data-jk], a.jcs-JobTitle")
        card_count = await cards.count()

        # Si aucun resultat, essayer d'attendre plus longtemps
        if card_count == 0:
            self.logger.info("Aucune offre immediate, attente supplementaire...")
            try:
                await page.wait_for_selector("a.jcs-JobTitle, a[data-jk]", timeout=15000)
                cards = page.locator("a[data-jk], a.jcs-JobTitle")
                card_count = await cards.count()
            except:
                self.logger.warning("Toujours aucune offre apres attente")
                return []

        self.logger.info("%d elements a.jcs-JobTitle trouves", card_count)

        for i in range(card_count):
            card = cards.nth(i)
            try:
                offer = await self._extract_card(card, page)
                if offer:
                    offers.append(offer)
            except Exception as exc:
                self.logger.debug("Carte #%d ignoree: %s", i, exc)

        return offers

    async def _extract_card(self, card, page) -> ScrapedOffer | None:
        """Extrait une offre d'une carte de resultat Indeed."""
        # ── Titre — texte du lien <a class="jcs-JobTitle"> ──
        title = (await card.inner_text()).strip()
        if not title:
            return None

        # ── Job key (data-jk) ──
        job_key = await card.get_attribute("data-jk") or ""
        if not job_key:
            return None

        # URL de la page detail
        job_url = f"{BASE_URL}/viewjob?jk={job_key}"

        # ── Remonter a l'ancetre contenant les infos de l'offre ──
        # <a.jcs-JobTitle> → <h3> → <div> → <td.resultContent>
        try:
            ancestor = card.locator("xpath=ancestor::td[contains(@class,'resultContent')]").first()
            if await ancestor.count() == 0:
                ancestor = card.locator("xpath=ancestor::div[contains(@class,'cardOutline')]").first()
            if await ancestor.count() == 0:
                ancestor = card.locator("xpath=ancestor::div[contains(@class,'job_seen_beacon')]").first()
            if await ancestor.count() == 0:
                ancestor = card
        except Exception:
            ancestor = card

        # ── Entreprise (dans l'ancetre) ──
        company = ""
        for sel in ["[data-testid='company-name']", "[class*='company']", "span.companyName"]:
            el = ancestor.locator(sel).first()
            if await el.count() > 0:
                company = (await el.inner_text()).strip()
                if company:
                    break

        # ── Lieu ──
        location = ""
        for sel in ["[data-testid='text-location']", "[class*='location']", "div.companyLocation"]:
            el = ancestor.locator(sel).first()
            if await el.count() > 0:
                location = (await el.inner_text()).strip()
                if location:
                    break

        # ── Salaire ──
        salary_raw = ""
        for sel in ["[class*='salary']", ".salary-snippet", ".metadata.salary-snippet-container"]:
            el = ancestor.locator(sel).first()
            if await el.count() > 0:
                salary_raw = (await el.inner_text()).strip()
                if salary_raw:
                    break

        # ── Contrat ──
        contract_type = ""
        for sel in ["[class*='metadata']", "[class*='attribute']", "[class*='contract']", ".jobMetaDataGroup span"]:
            el = ancestor.locator(sel).first()
            if await el.count() > 0:
                ct = (await el.inner_text()).strip()
                if ct and any(t in ct.lower() for t in ["alternance", "apprentissage", "stage", "cdi", "cdd"]):
                    contract_type = ct
                    break

        # ── Description ──
        description = ""
        for sel in ["[class*='snippet']", ".job-snippet", ".jobCardShelfContainer", "[class*='description']"]:
            el = ancestor.locator(sel).first()
            if await el.count() > 0:
                description = (await el.inner_text()).strip()
                if description:
                    break
        if not description:
            description = title

        return ScrapedOffer(
            title=title,
            description=description or title,
            url=job_url,
            source=self.name,
            source_id=job_key,
            company=company,
            location=location,
            contract_type=contract_type,
            salary_raw=salary_raw,
        )

    async def _fetch_detail(self, page, job_key: str) -> str:
        """Recupere la description complete depuis la page detail.

        Ouvre la page /viewjob?jk=... et extrait la description.
        """
        try:
            detail_url = f"{BASE_URL}/viewjob?jk={job_key}"
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            # Verifier blocage
            body = await page.inner_text("body") or ""
            if any(t in body.lower() for t in ["captcha", "verify"]):
                self.logger.warning("Detail bloque pour jk=%s", job_key)
                return ""

            # Extraire la description
            desc_el = page.locator(
                "#jobDescriptionText, .jobsearch-JobComponent-description, "
                "[class*='description'], .jobsearch-JobComponent"
            ).first()

            if await desc_el.count() > 0:
                return (await desc_el.inner_text()).strip()[:500]

            # Fallback: tout le contenu principal
            main_el = page.locator("main, #jobsearch-ViewjobPaneWrapper, [class*='jobsearch']").first()
            if await main_el.count() > 0:
                return (await main_el.inner_text()).strip()[:500]

            return ""
        except Exception as exc:
            self.logger.debug("Erreur fetch detail jk=%s: %s", job_key, exc)
            return ""

    # ── Helpers ──

    def _build_search_url(self, query: str, location: str, start: int = 0) -> str:
        """Construit l'URL de recherche Indeed."""
        params = [f"q={self._url_encode(query)}"]
        if location:
            params.append(f"l={self._url_encode(location)}")
        params.append("radius=25")
        if start > 0:
            params.append(f"start={start}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _extract_jk(self, href: str) -> str | None:
        """Extrait le job key Indeed d'une URL.

        Ex: /viewjob?jk=abc123def → abc123def
            /rc/clk?jk=abc123def → abc123def
        """
        if not href:
            return None
        m = re.search(r'[?&]jk=([a-zA-Z0-9]+)', href)
        return m.group(1) if m else None

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
                "  Description : %.200s\n"
                "  Contrat     : %s\n"
                "  Salaire     : %s\n"
                "  URL         : %s\n"
                "  JK          : %s",
                i, o.title, o.company or "-", o.location or "-",
                o.description, o.contract_type or "-",
                o.salary_raw or "-", o.url or "-", o.source_id or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
