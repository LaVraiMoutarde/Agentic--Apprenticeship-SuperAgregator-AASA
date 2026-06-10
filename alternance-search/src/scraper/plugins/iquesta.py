"""
Scraper iQuesta — offres de stage, alternance, emploi.

URL seed : https://www.iquesta.com/
URL offres : https://www.iquesta.com/Offres-Stage-Emploi-Alternance.html

Structure :
  - Page d'accueil : annonces a la une
  - Page offres : grille complete avec pagination
  - Server-side rendered (pas de SPA)
  - Detail : /Offre-{slug}-{id}.html
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError, ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://www.iquesta.com"
SEARCH_URL = f"{BASE_URL}/Offres-Stage-Emploi-Alternance.html"

TARGET_TYPES = {"stage", "alternance", "apprentissage", "job étudiant", "1er emploi"}


class IQuestaScraper(BaseScraper):
    """Scraper pour iQuesta (offres stage/alternance/emploi)."""

    def __init__(self, headless: bool = True, timeout: int = 30) -> None:
        super().__init__()
        self.headless = headless
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "iquesta"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 5,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut iQuesta — query='%s', location='%s', max_pages=%d", query, location, max_pages)

        try:
            # Strategie 1 : requests + BS4 (rapide, server-side)
            offers = self._scrape_requests(query, location, max_pages)
            all_offers = self.validate_output(offers)
            self.logger.info("requests OK: %d offres validees", len(all_offers))
        except Exception as exc:
            self.logger.warning("requests echoue: %s. Fallback Playwright...", exc)
            errors.append(exc)

            # Fallback Playwright
            if not all_offers:
                try:
                    offers = asyncio.run(self._scrape_playwright(query, location, max_pages))
                    all_offers = self.validate_output(offers)
                    self.logger.info("Playwright OK: %d offres", len(all_offers))
                except Exception as exc2:
                    self.logger.error("Fallback echoue: %s", exc2)
                    errors.append(exc2)

        result = self._build_result(all_offers, pages=max_pages, total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    # ── Strategie 1 : requests + BeautifulSoup ──

    def _scrape_requests(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        """Scrape iQuesta avec requests + BeautifulSoup (server-side)."""
        import requests as req
        from bs4 import BeautifulSoup

        offers: list[ScrapedOffer] = []

        for page in range(1, max_pages + 1):
            url = self._build_url(query, location, page)
            self.logger.info("Page %d/%d — %s", page, max_pages, url)

            try:
                resp = req.get(url, headers=self._headers(), timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:
                raise ScraperNetworkError(f"HTTP {url}: {exc}", scraper_name=self.name, original=exc)

            soup = BeautifulSoup(resp.text, "lxml")
            page_offers = self._parse_html(soup, url)
            offers.extend(page_offers)
            self.logger.info("Page %d: %d offres", page, len(page_offers))

            if not page_offers:
                break

        return offers

    def _parse_html(self, soup, current_url: str) -> list[ScrapedOffer]:
        """Parse le HTML iQuesta pour extraire les offres."""
        offers: list[ScrapedOffer] = []

        # Selecteurs specifiques iQuesta
        # Les offres sont dans des blocs <div> avec un titre et meta-donnees
        card_selectors = [
            "div.card, div.offre-item, div.job-card",
            "a[href*='/Offre-']",
            "div:has(> a[href*='/Offre-'])",
            "li:has(a[href*='/Offre-'])",
        ]

        cards = []
        for sel in card_selectors:
            found = soup.select(sel)
            if found:
                cards = found
                self.logger.debug("Selecteur '%s': %s cartes", sel, len(found))
                break

        if not cards:
            # Fallback: chercher directement les liens
            cards = soup.select("a[href*='/Offre-']")
            if not cards:
                self.logger.warning("Aucune carte trouvee")
                return offers

        seen = set()
        for card in cards:
            try:
                # Lien
                link = card if card.name == "a" and "/Offre-" in (card.get("href") or "") else card.select_one("a[href*='/Offre-']")
                if not link:
                    continue
                href = link.get("href", "").strip()
                if not href or href in seen:
                    continue
                seen.add(href)
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                card_text = card.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in card_text.split("\n") if l.strip()]

                # Titre (premier element de la carte OU element <h3>/h2)
                title_el = link.select_one("h3, h2, strong, [class*='title']")
                title = title_el.get_text(strip=True) if title_el else (link.get("title") or link.get_text(strip=True))

                if not title:
                    continue

                # Extraire les infos par position dans la carte iQuesta
                # Structure typique: [Titre] [Logo] [Entreprise] [Lieu] [Contrat] [Description] [Duree] [Date]
                company = self._extract_field(card, card_text, [
                    "[class*='company']", "[class*='name']", "span.entreprise",
                    "[class*='employer']",
                ])
                location = self._extract_field(card, card_text, [
                    "[class*='location']", "[class*='lieu']", "[class*='city']",
                    "[class*='place']", "[class*='region']",
                ])
                contract_type = self._extract_field(card, card_text, [
                    "[class*='type']", "[class*='contrat']", "[class*='badge']",
                    "span.contrat", "[class*='tag']",
                ])
                description = self._extract_field(card, card_text, [
                    "[class*='desc']", "[class*='content']", "p",
                    "[class*='snippet']",
                ])

                # Fallback regex si les selecteurs ne marchent pas
                if not company:
                    m = re.search(r'(?:Groupe|SA|SAS|SARL|EURL)\s+([A-Z][A-Za-zéèêëàâîïôùûç\-\s]{2,60})', card_text)
                    if m:
                        company = m.group(0).strip()
                    else:
                        # Derniere ligne significative non-reconnue
                        for line in reversed(lines):
                            if line not in (title, location, contract_type) and len(line) > 5:
                                company = line
                                break

                if not location:
                    # iQuesta: lieu apres "chez" ou "a" ou seul sur une ligne
                    m = re.search(r'📍?\s*([A-Z][a-zéèêëàâîïôùûç]+(?:\s?\(?\d{5}\)?)?)', card_text)
                    if m:
                        location = m.group(1).strip()
                    else:
                        for line in lines:
                            if re.search(r'[A-Z][a-zéèêëàâîïôùûç]+\s*\d{5}', line):
                                location = line
                                break

                if not contract_type:
                    m = re.search(r'(Stage|Alternance|Apprentissage|CDI|CDD|Job étudiant|1er emploi|Contrat\s+en\s+alternance)', card_text, re.I)
                    if m:
                        contract_type = m.group(1).strip()

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
                self.logger.debug("Carte ignoree: %s", str(exc)[:80])

        return offers

    def _extract_field(self, card, text: str, selectors: list[str]) -> str:
        """Extrait un champ depuis un selecteur CSS."""
        from bs4 import Tag
        for sel in selectors:
            el = card.select_one(sel) if isinstance(card, Tag) else None
            if el:
                val = el.get_text(strip=True)
                if val:
                    return val
        return ""

    # ── Fallback Playwright ──

    async def _scrape_playwright(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        """Fallback Playwright si requests echoue (JS lourd, anti-bot)."""
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
                for pagenum in range(max_pages):
                    url = self._build_url(query, location, pagenum + 1)
                    self.logger.info("Page %d/%d — %s", pagenum + 1, max_pages, url)

                    await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    await page.wait_for_timeout(3000)

                    page_offers = await self._parse_playwright_page(page)
                    all_offers.extend(page_offers)
                    self.logger.info("Page %d: %d offres", pagenum + 1, len(page_offers))

                    if not page_offers:
                        break

            finally:
                await browser.close()

        return all_offers

    async def _parse_playwright_page(self, page) -> list[ScrapedOffer]:
        """Parse les offres avec Playwright."""
        offers: list[ScrapedOffer] = []

        try:
            await page.wait_for_selector("a[href*='/Offre-'], article.offer-card, [class*='card']", timeout=10000)
        except Exception:
            return []

        cards = page.locator("a[href*='/Offre-'], article.offer-card, [class*='card']")
        count = await cards.count()

        seen = set()
        for i in range(count):
            card = cards.nth(i)
            try:
                tag = await card.evaluate("el => el.tagName.toLowerCase()")
                link_el = card
                if tag != "a" or "/Offre-" not in ((await card.get_attribute("href")) or ""):
                    link_el = card.locator("a[href*='/Offre-']").first()
                    if await link_el.count() == 0:
                        continue

                href = (await link_el.get_attribute("href")) or ""
                if not href or href in seen:
                    continue
                seen.add(href)
                full_url = f"{BASE_URL}{href}" if href.startswith("/") else href

                title = (await link_el.inner_text()).strip()
                if not title:
                    continue

                card_text = await card.inner_text()
                company, location, contract_type = "", "", ""

                # Extraire les infos du texte de la carte
                for line in card_text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # Detection entreprise (mots en majuscules, noms d'entreprises)
                    if any(kw in line.lower() for kw in ["s.a.s", "sarl", "sa", "groupe", "entreprise"]) and not company:
                        company = line
                    # Detection lieu (codes postaux ou villes)
                    import re
                    if re.search(r"\d{5}", line) and not location:
                        location = line
                    # Detection type de contrat
                    if "stage" in line.lower() and not contract_type:
                        contract_type = line
                    elif "alternance" in line.lower() and not contract_type:
                        contract_type = line
                    elif "apprentissage" in line.lower() and not contract_type:
                        contract_type = line

                # Fallback: regex dans tout le texte
                import re
                if not company:
                    m = re.search(r"(Groupe|SA|SAS|SARL|EURL|SCOP)\s+([A-Z][A-Za-z\-\s]+)", card_text)
                    if m:
                        company = m.group(0).strip()
                if not location:
                    m = re.search(r"([A-Z][a-zéèêëàâîïôùûç]+(?:\s?\(?\d{5}\)?)?)", card_text)
                    if m:
                        location = m.group(1).strip()

                offers.append(ScrapedOffer(
                    title=title[:200], description=title[:500], url=full_url,
                    source=self.name, company=company, location=location,
                    contract_type=contract_type,
                ))
            except Exception:
                continue

        return offers

    # ── Helpers ──

    def _build_url(self, query: str, location: str, page: int = 1) -> str:
        params: list[str] = []
        if query:
            params.append(f"mots={self._encode(query)}")
        if location:
            params.append(f"localisation={self._encode(location)}")
        if page > 1:
            params.append(f"page={page}")
        return f"{SEARCH_URL}?{'&'.join(params)}" if params else SEARCH_URL

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9",
        }

    def _encode(self, text: str) -> str:
        return text.replace(" ", "+")

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            self.logger.info("Aucune offre a logger.")
            return
        self.logger.info("=== DEBUG: %d exemple(s) ===", min(3, len(offers)))
        for i, o in enumerate(offers[:3], 1):
            self.logger.info(
                "--- Offre #%d ---\n  Titre: %s\n  Entreprise: %s\n  Lieu: %s\n  Contrat: %s\n  URL: %s",
                i, o.title, o.company or "-", o.location or "-",
                o.contract_type or "-", o.url or "-",
            )
        self.logger.info("=== FIN DEBUG ===")
