"""
Scraper Welcome to the Jungle — offres d'emploi/alternance.

URL seed : https://www.welcometothejungle.com/fr/companies

Strategie :
  1. API REST https://api.welcometothejungle.com/api/v1/jobs
  2. Fallback Playwright si API ne repond pas

API endpoints detectes :
  - api.welcometothejungle.com/api/v1/jobs (offres)
  - api.welcometothejungle.com/api/v1/companies (entreprises)
  - api.welcometothejungle.com/api/v2/users/me (utilisateur)
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import requests as req

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError, ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://www.welcometothejungle.com"
API_URL = "https://api.welcometothejungle.com/api/v1"
SEARCH_URL = f"{API_URL}/jobs"

# Types de contrats WTTJ
# Valeurs observees dans l'API
WTTJ_CONTRACT_TYPES = {
    "apprenticeship": "Apprentissage",
    "internship": "Stage",
    "full_time": "CDI",
    "part_time": "Temps partiel",
    "fixed_term": "CDD",
    "temporary": "Intérim",
    "freelance": "Freelance",
    "alternance": "Alternance",
    "professionalization": "Professionnalisation",
    "stage": "Stage",
    "contrat d'apprentissage": "Apprentissage",
    "contrat de professionnalisation": "Professionnalisation",
}


class WTJJScraper(BaseScraper):
    """Scraper pour Welcome to the Jungle."""

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
        return "wttj"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 5,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut WTTJ — query='%s', location='%s', max_pages=%d", query, location, max_pages)

        # API REST first
        try:
            offers = self._scrape_api(query, location, max_pages)
            all_offers = self.validate_output(offers)
            self.logger.info("API OK: %d offres validees", len(all_offers))
        except Exception as exc:
            self.logger.warning("API echoue: %s. Fallback Playwright...", exc)
            errors.append(exc)

            # Playwright fallback
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

    # ── Strategie 1 : API REST ──

    def _scrape_api(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        """Scrape via l'API REST de Welcome to the Jungle.

        Endpoint : GET /api/v1/jobs?query=...&page=...&contract_type[]=...
        """
        offers: list[ScrapedOffer] = []

        for page in range(1, max_pages + 1):
            params = {
                "page": page,
                "per_page": 20,
                "contract_type[]": ["internship", "apprenticeship", "alternance", "professionalization"],
                "remote": "all",
                "sort_by": "relevance",
            }
            if query:
                params["query"] = query
            if location:
                params["location"] = location

            self.logger.info("API page %d — query='%s', location='%s'", page, query, location)

            try:
                resp = req.get(
                    SEARCH_URL,
                    params=self._flatten_params(params),
                    headers=self._api_headers(),
                    timeout=self.timeout,
                )
            except Exception as exc:
                raise ScraperNetworkError(f"API request failed: {exc}", scraper_name=self.name, original=exc)

            if resp.status_code == 404:
                break
            if resp.status_code not in (200, 201):
                raise ScraperNetworkError(
                    f"API HTTP {resp.status_code}: {resp.text[:200]}",
                    scraper_name=self.name,
                )

            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                raise ScraperParseError(f"JSON invalide: {exc}", scraper_name=self.name, original=exc)

            page_offers = self._parse_api_response(data)
            offers.extend(page_offers)
            self.logger.info("Page %d: %d offres (total: %d)", page, len(page_offers), len(offers))

            if len(page_offers) < 20:
                break

        return offers

    def _parse_api_response(self, data: Any) -> list[ScrapedOffer]:
        """Parse la reponse JSON de l'API WTTJ.

        Structure WTTJ (adaptee) :
        {
            "data": [...],
            "meta": { "current_page": 1, "total_pages": ... }
        }
        ou
        {
            "jobs": [...],
            "pagination": { ... }
        }
        """
        offers: list[ScrapedOffer] = []

        # Essayer differentes structures
        jobs = data.get("data") or data.get("jobs") or data.get("results") or data or []

        if isinstance(jobs, dict):
            jobs = [jobs]

        for item in jobs:
            if not isinstance(item, dict):
                continue

            try:
                offer = self._api_item_to_offer(item)
                if offer:
                    offers.append(offer)
            except Exception as exc:
                self.logger.debug("Item ignore: %s", exc)

        return offers

    def _api_item_to_offer(self, item: dict) -> ScrapedOffer | None:
        """Convertit un item API WTTJ en ScrapedOffer."""
        # Titre
        title = item.get("title") or item.get("name") or item.get("intitule") or ""
        if not title:
            return None

        # Description
        description = item.get("description") or item.get("body") or item.get("description_html") or ""

        # URL (slug)
        slug = item.get("slug") or item.get("id") or ""
        if slug and not slug.startswith("http"):
            org_slug = item.get("organization", {}).get("slug") or item.get("company", {}).get("slug") or ""
            url = f"{BASE_URL}/fr/jobs/{slug}"
            if org_slug:
                url = f"{BASE_URL}/fr/companies/{org_slug}/jobs/{slug}"
        else:
            url = slug if slug.startswith("http") else item.get("url") or BASE_URL

        # Entreprise
        org = item.get("organization") or item.get("company") or {}
        company = org.get("name") if isinstance(org, dict) else str(org)
        if not company:
            company = item.get("company_name") or item.get("employer") or ""

        # Lieu
        place = item.get("place") or item.get("office") or item.get("location") or {}
        if isinstance(place, dict):
            location = place.get("city") or place.get("name") or place.get("libelle") or ""
        else:
            location = str(place) if place else ""

        # Contrat
        contract_type_raw = item.get("contract_type") or item.get("contractType") or item.get("type") or ""
        if isinstance(contract_type_raw, dict):
            contract_type_raw = contract_type_raw.get("value") or contract_type_raw.get("name") or contract_type_raw.get("label") or ""
        contract_type = WTTJ_CONTRACT_TYPES.get(str(contract_type_raw).lower(), str(contract_type_raw))

        # Salaire
        salary = item.get("salary") or item.get("remuneration") or item.get("compensation") or ""
        if isinstance(salary, dict):
            salary = salary.get("text") or salary.get("amount") or salary.get("range") or ""

        # Niveau
        experience = item.get("experience_level") or item.get("experience") or item.get("niveau") or ""
        if isinstance(experience, dict):
            experience = experience.get("value") or experience.get("label") or experience.get("name") or ""

        # Contact
        contact_name = item.get("contact_name") or item.get("recruiter_name") or item.get("recruiter", {}).get("name") or ""
        contact_email = item.get("contact_email") or item.get("recruiter", {}).get("email") or ""

        # Source ID
        source_id = str(item.get("id") or item.get("slug") or item.get("_id") or "")

        return ScrapedOffer(
            title=str(title).strip(),
            description=str(description).strip()[:500] or str(title).strip(),
            url=str(url).strip(),
            source=self.name,
            source_id=str(source_id).strip(),
            company=str(company).strip(),
            location=str(location).strip(),
            contract_type=str(contract_type).strip(),
            required_level=str(experience).strip(),
            salary_raw=str(salary).strip(),
            contact_name=str(contact_name).strip(),
            contact_email=str(contact_email).strip(),
        )

    # ── Strategie 2 : Playwright ──

    async def _scrape_playwright(self, query: str, location: str, max_pages: int) -> list[ScrapedOffer]:
        """Fallback Playwright — navigation et extraction du DOM ou API intercepter."""
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

            # Intercepter les reponses API
            api_responses = []

            async def on_response(response):
                if "api.welcometothejungle.com" in response.url:
                    try:
                        body = await response.text()
                        if body and len(body) < 100000:
                            api_responses.append({"url": response.url, "body": body})
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                # Navigation vers la page de recherche
                search_url = "https://www.welcometothejungle.com/fr/search/jobs"
                params = []
                if query:
                    params.append(f"query={self._encode(query)}")
                params.append("contractType[]=apprenticeship")
                params.append("contractType[]=internship")
                if params:
                    search_url += "?" + "&".join(params)

                self.logger.info("Navigation Playwright vers %s", search_url)
                await page.goto(search_url, wait_until="networkidle", timeout=self.timeout * 1000)
                await page.wait_for_timeout(5000)

                # Scroll pour declencher le lazy loading
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)

                # Extraire les donnees des reponses API intercepter
                for resp in api_responses:
                    url = resp["url"]
                    if "/jobs" in url or "/search" in url:
                        try:
                            data = json.loads(resp["body"])
                            parsed = self._parse_api_response(data)
                            if parsed:
                                all_offers.extend(parsed)
                        except (json.JSONDecodeError, TypeError):
                            continue

                # Fallback: parser le DOM
                if not all_offers:
                    all_offers = await self._parse_dom(page)

            finally:
                await browser.close()

        return all_offers

    async def _parse_dom(self, page) -> list[ScrapedOffer]:
        """Parse les offres depuis le DOM Playwright."""
        offers: list[ScrapedOffer] = []

        try:
            await page.wait_for_selector(
                'a[href*="/jobs/"], [class*="card"], [class*="job"], article',
                timeout=10000,
            )
        except Exception:
            return []

        cards = page.locator('a[href*="/jobs/"], [class*="card"], [class*="job-card"], article')
        count = await cards.count()

        seen = set()
        for i in range(count):
            card = cards.nth(i)
            try:
                href = (await card.get_attribute("href")) or ""
                if href and "/jobs/" in href:
                    full_url = f"https://www.welcometothejungle.com{href}" if href.startswith("/") else href
                    if full_url in seen:
                        continue
                    seen.add(full_url)

                    title = (await card.inner_text()).strip()
                    if not title:
                        continue

                    offers.append(ScrapedOffer(
                        title=title[:200],
                        description=title[:500],
                        url=full_url,
                        source=self.name,
                    ))
            except Exception:
                continue

        return offers

    # ── Helpers ──

    def _flatten_params(self, params: dict) -> dict:
        """Aplatit les parametres liste pour requests."""
        flat: dict = {}
        for key, value in params.items():
            if isinstance(value, list):
                for i, v in enumerate(value):
                    flat[f"{key}"] = v
                    if i > 0:
                        flat[f"{key}{i}"] = v
            else:
                flat[key] = value
        return flat

    def _api_headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": "https://www.welcometothejungle.com/",
            "Origin": "https://www.welcometothejungle.com",
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
