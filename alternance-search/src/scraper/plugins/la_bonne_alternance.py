"""
Scraper La Bonne Alternance — API publique + Playwright fallback.

URL seed : https://labonnealternance.apprentissage.beta.gouv.fr/recherche

Strategie :
  1. API REST publique (formations) ou Matcha (jobs)
  2. Playwright fallback avec interaction formulaire + parsing DOM

APIs decouvertes :
  - /api/v1/formations?romes=M1805&radius=30  → formations (200 OK)
  - /api/v1/jobs n'existe pas en GET → utilise Playwright pour les offres
  - Les offres d'emploi sont chargees via RSC (React Server Components) Next.js
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from ..exceptions import ScraperNetworkError, ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://labonnealternance.apprentissage.beta.gouv.fr"
SEARCH_URL = f"{BASE_URL}/recherche"
API_FORMATIONS = f"{BASE_URL}/api/v1/formations"

DEFAULT_ROMES = ["M1805", "M1806", "M1810"]  # Informatique


class LaBonneAlternanceScraper(BaseScraper):
    """Scraper pour La Bonne Alternance (API formations + Playwright pour offres)."""

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
        max_pages: int = 5,
        criteria=None,
    ) -> ScraperResult:
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut — query='%s', location='%s'", query, location)

        # Resoudre les ROMEs depuis la query
        romes = self._resolve_romes(query) or self.romes
        self.logger.info("Codes ROME: %s", romes)

        # Essayer l'API Playwright d'abord (charge les offres d'emploi)
        try:
            offers = asyncio.run(self._playwright_scrape(query, location, max_pages))
            all_offers = self.validate_output(offers)
            self.logger.info("Playwright OK — %d offres", len(all_offers))
        except Exception as exc:
            self.logger.warning("Playwright echoue: %s. Fallback API formations...", exc)
            errors.append(exc)

        # Fallback API formations
        if not all_offers:
            try:
                offers = self._api_formations(romes)
                all_offers = self.validate_output(offers)
                self.logger.info("API formations OK — %d offres", len(all_offers))
            except Exception as exc:
                self.logger.error("API echoue: %s", exc)
                errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages, total_found=len(all_offers), errors=errors)
        self._log_examples(all_offers)
        return result

    # ── Strategie 1 : Playwright avec interaction formulaire ──

    async def _playwright_scrape(self, query: str, location: str, max_pages: int = 5) -> list[ScrapedOffer]:
        """Charge la page, interagit avec le formulaire, parse les resultats.

        Parcourt automatiquement les pages suivantes via la pagination LBA.
        """
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            # Intercepter les reponses RSC pour capturer les donnees
            rsc_data = []

            async def on_response(response):
                url = response.url
                if "_rsc=" in url or "api" in url:
                    try:
                        body = await response.text()
                        if len(body) > 50 and body.strip():
                            rsc_data.append({"url": url, "body": body[:5000]})
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Remplir le champ de recherche si fourni
            if query:
                search_input = page.locator('[role="combobox"]').first()
                await search_input.click()
                await search_input.fill(query)
                await page.wait_for_timeout(1500)

            # Cliquer sur "C'est parti"
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await submit_btn.evaluate_handle("el => el.click()")
                await page.wait_for_timeout(5000)
                await page.wait_for_load_state("networkidle")

            # ── Boucle de pagination ──
            import random

            for page_num in range(1, max_pages + 1):
                self.logger.info("LBA page %d/%d", page_num, max_pages)

                # Réinitialiser les données RSC pour chaque page
                rsc_data.clear()

                # Attendre le chargement des résultats
                if page_num > 1:
                    # Essayer d'aller à la page suivante
                    has_next = await self._next_page_lba(page)
                    if not has_next:
                        self.logger.info("Plus de pages disponibles sur LBA")
                        break
                    await page.wait_for_timeout(3000)
                    await page.wait_for_load_state("networkidle")

                # Essayer d'extraire les donnees des reponses RSC
                page_offers = self._parse_rsc_data(rsc_data)
                if not page_offers:
                    # Fallback : parser le DOM
                    page_offers = await self._parse_dom(page)
                if not page_offers:
                    # Dernier recours : chercher dans les scripts inline
                    page_source = await page.content()
                    page_offers = self._parse_page_source(page_source)

                if page_offers:
                    all_offers.extend(page_offers)
                    self.logger.info("Page %d: %d offres (total: %d)", page_num, len(page_offers), len(all_offers))
                else:
                    self.logger.info("Aucune offre trouvee page %d — fin", page_num)
                    break

                # Délai de politesse entre les pages
                await asyncio.sleep(random.uniform(1.5, 3.0))

            await browser.close()

        return all_offers

    async def _next_page_lba(self, page) -> bool:
        """Trouve et clique sur le bouton de page suivante sur La Bonne Alternance.

        Essaie plusieurs stratégies de pagination dans l'ordre :
        1. Bouton "Suivant" / "Page suivante" / "Next"
        2. Liens de pagination numérotés avec aria-label
        3. Bouton "Voir plus de résultats"
        4. Incrémentation manuelle du paramètre ?page= dans l'URL
        """
        # Stratégie 1 : bouton "Suivant" ou flèche
        next_selectors = [
            'a:has-text("Suivant"), button:has-text("Suivant")',
            'a:has-text("Page suivante"), button:has-text("Page suivante")',
            'a[rel="next"], button[rel="next"]',
            'a:has-text("›"), a:has-text("»"), a:has-text(">")',
            'button:has-text("Voir plus de résultats")',
            'button:has-text("Afficher plus")',
            '[class*="pagination"] a:not([disabled]):not([aria-disabled="true"])',
            'nav[aria-label="pagination"] a:not([aria-current])',
            'a[aria-label*="suivant"], button[aria-label*="suivant"]',
            'a[aria-label*="next"], button[aria-label*="next"]',
        ]

        for sel in next_selectors:
            try:
                el = page.locator(sel).first()
                if await el.count() > 0:
                    is_disabled = await el.get_attribute("disabled") or ""
                    aria_disabled = await el.get_attribute("aria-disabled") or ""
                    if "disabled" in str(is_disabled).lower() or "true" in str(aria_disabled).lower():
                        continue
                    await el.click()
                    self.logger.info("Clic sur selecteur: %s", sel[:60])
                    return True
            except Exception:
                continue

        # Stratégie 2 : incrémenter le paramètre ?page= dans l'URL
        current_url = page.url
        import re
        page_match = re.search(r'[?&]page=(\d+)', current_url)
        if page_match:
            current_page = int(page_match.group(1))
            next_page = current_page + 1
            new_url = current_url.replace(f"page={current_page}", f"page={next_page}")
            self.logger.info("Navigation directe vers %s", new_url)
            try:
                await page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
                return True
            except Exception as exc:
                self.logger.warning("Echec navigation page %d: %s", next_page, exc)
                return False

        # Stratégie 3 : ajouter ?page=2 si pas de paramètre page
        if "?" in current_url:
            new_url = f"{current_url}&page=2"
        else:
            new_url = f"{current_url}?page=2"
        self.logger.info("Tentative ajout page=2: %s", new_url)
        try:
            await page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception:
            return False

    def _parse_rsc_data(self, rsc_data: list[dict]) -> list[ScrapedOffer]:
        """Parse les donnees RSC (React Server Components) pour extraire les offres."""
        offers: list[ScrapedOffer] = []
        for entry in rsc_data:
            body = entry["body"]
            if not body or body.startswith("0:"):
                continue
            # Chercher des structures JSON dans le flux RSC
            # RSC utilise un format "1:{\"data\":...}"
            for match in re.finditer(r'\d+:\s*(\{.*\})\s*', body, re.DOTALL):
                try:
                    data = json.loads(match.group(1))
                    items = self._extract_items_from_dict(data)
                    for item in items:
                        offer = self._dict_to_offer(item)
                        if offer:
                            offers.append(offer)
                except (json.JSONDecodeError, TypeError):
                    continue
            # Chercher les tableaux JSON
            for match in re.finditer(r'\[(.*?)\]', body, re.DOTALL):
                try:
                    data = json.loads(f"[{match.group(1)}]")
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "title" in item:
                                offer = self._dict_to_offer(item)
                                if offer:
                                    offers.append(offer)
                except (json.JSONDecodeError, TypeError):
                    continue
        return offers

    def _extract_items_from_dict(self, data: dict) -> list[dict]:
        """Extrait recursivement les items potentiels d'un JSON."""
        items = []
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        if "title" in item or "intitule" in item:
                            items.append(item)
                        else:
                            items.extend(self._extract_items_from_dict(item))
            elif isinstance(value, dict):
                items.extend(self._extract_items_from_dict(value))
        return items

    async def _parse_dom(self, page) -> list[ScrapedOffer]:
        """Parse les resultats dans le DOM apres recherche."""
        offers: list[ScrapedOffer] = []
        page_source = await page.content()

        # Methode 1 : script type="application/json"
        for script in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', page_source, re.DOTALL):
            try:
                data = json.loads(script)
                items = self._extract_items_from_dict(data) if isinstance(data, dict) else data
                for item in (items if isinstance(items, list) else [items]):
                    if isinstance(item, dict):
                        offer = self._dict_to_offer(item)
                        if offer:
                            offers.append(offer)
            except json.JSONDecodeError:
                pass

        if offers:
            return offers

        # Methode 2 : parse les elements HTML des resultats
        result_cards = page.locator('[class*="result"], [class*="card"], article, li.fr-card, [class*="fr-card"]')
        count = await result_cards.count()

        for i in range(count):
            card = result_cards.nth(i)
            try:
                texts = await card.all_inner_texts()
                full_text = " ".join(texts)
                title_el = card.locator("h2, h3, [class*='title'], strong").first()
                title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else ""

                if not title and full_text:
                    lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                    title = lines[0] if lines else ""

                if not title:
                    continue

                desc = full_text[:500]

                company = ""
                company_el = card.locator("[class*='company'], [class*='employeur'], [class*='nom']").first()
                if await company_el.count() > 0:
                    company = (await company_el.inner_text()).strip()

                location = ""
                loc_el = card.locator("[class*='lieu'], [class*='location'], [class*='city'], [class*='ville']").first()
                if await loc_el.count() > 0:
                    location = (await loc_el.inner_text()).strip()

                url = ""
                link = card.locator("a").first()
                if await link.count() > 0:
                    url = (await link.get_attribute("href")) or ""

                offers.append(ScrapedOffer(
                    title=title, description=desc, company=company,
                    location=location, url=url or SEARCH_URL,
                    source=self.name,
                ))
            except Exception:
                continue

        return offers

    def _parse_page_source(self, html: str) -> list[ScrapedOffer]:
        """Parse le HTML pour trouver les offres dans les donnees inline."""
        offers: list[ScrapedOffer] = []

        # Chercher des JSON dans les attributs data-*
        for match in re.finditer(r'data-props="([^"]+)"', html):
            try:
                data = json.loads(match.group(1).replace("&quot;", '"'))
                items = self._extract_items_from_dict(data) if isinstance(data, dict) else data
                for item in (items if isinstance(items, list) else [items]):
                    if isinstance(item, dict):
                        offer = self._dict_to_offer(item)
                        if offer:
                            offers.append(offer)
            except (json.JSONDecodeError, TypeError):
                continue

        return offers

    # ── Strategie 2 : API Formations (fallback) ──

    def _api_formations(self, romes: list[str]) -> list[ScrapedOffer]:
        """Appelle l'API publique /api/v1/formations.

        Retourne les formations (pas des offres d'emploi, mais utile
        comme source de donnees supplementaire).
        """
        offers: list[ScrapedOffer] = []
        import requests as req

        params = {
            "romes": ",".join(romes),
            "radius": self.radius,
        }

        self.logger.info("API formations: %s", params)
        resp = req.get(API_FORMATIONS, params=params, timeout=self.timeout,
                       headers={"User-Agent": "alternance-search/0.1"})

        if resp.status_code != 200:
            raise ScraperNetworkError(f"API HTTP {resp.status_code}", scraper_name=self.name)

        data = resp.json()
        results = data.get("results", [])

        for item in results:
            if isinstance(item, dict):
                offer = self._formation_to_offer(item)
                if offer:
                    offers.append(offer)

        return offers

    def _formation_to_offer(self, item: dict) -> ScrapedOffer | None:
        """Convertit un item formation en ScrapedOffer."""
        title = item.get("title") or item.get("intitule") or ""
        if not title:
            return None

        company_raw = item.get("company") or item.get("employeur") or {}
        company = company_raw.get("name") or company_raw.get("nom") or "" if isinstance(company_raw, dict) else str(company_raw)

        place = item.get("place") or item.get("lieu") or {}
        location = place.get("city") or place.get("ville") or place.get("libelle") or "" if isinstance(place, dict) else str(place)

        # URL = lien vers la formation
        url = item.get("url") or item.get("link") or item.get("permalink") or ""

        # Contact
        contact = item.get("contact") or {}
        contact_name = contact.get("name") or contact.get("nom") or ""
        contact_email = contact.get("email") or contact.get("mail") or ""

        # Description
        description = item.get("description") or item.get("descriptif") or ""
        if not description:
            description = f"Formation en alternance: {title}"

        # Niveau
        diploma = item.get("diploma") or item.get("niveau") or item.get("level") or ""
        if isinstance(diploma, dict):
            diploma = diploma.get("label") or diploma.get("libelle") or ""

        return ScrapedOffer(
            title=title.strip(),
            description=description.strip()[:500],
            url=url.strip() or SEARCH_URL,
            source=self.name,
            company=company.strip(),
            location=location.strip(),
            contact_name=contact_name.strip(),
            contact_email=str(contact_email).strip(),
            required_level=str(diploma).strip(),
            domain="formation",  # tag pour differencier
        )

    # ── Helpers ──

    def _dict_to_offer(self, item: dict) -> ScrapedOffer | None:
        """Convertit n'importe quel dict en ScrapedOffer si possible."""
        title = item.get("title") or item.get("intitule") or item.get("label") or ""
        if not title:
            return None

        description = item.get("description") or item.get("descriptif") or item.get("detail") or ""

        company_raw = item.get("company") or item.get("employeur") or item.get("entreprise") or {}
        company = company_raw.get("name") or company_raw.get("nom") or "" if isinstance(company_raw, dict) else str(company_raw)

        place = item.get("place") or item.get("lieu") or item.get("localisation") or {}
        location = place.get("city") or place.get("ville") or "" if isinstance(place, dict) else str(place)

        url = item.get("url") or item.get("link") or item.get("permalink") or ""
        contract = item.get("contract") or item.get("typeContrat") or item.get("contractType") or ""
        if isinstance(contract, dict):
            contract = contract.get("label") or contract.get("libelle") or ""

        level = item.get("diploma") or item.get("niveau") or item.get("level") or ""
        if isinstance(level, dict):
            level = level.get("label") or level.get("libelle") or ""

        salary = item.get("salary") or item.get("salaire") or ""
        if isinstance(salary, dict):
            salary = salary.get("amount") or salary.get("montant") or ""

        source_id = str(item.get("id") or item.get("_id") or item.get("ideaId") or "")

        contact = item.get("contact") or {}
        contact_name = contact.get("name") or contact.get("nom") or item.get("contactName") or ""
        contact_email = contact.get("email") or contact.get("mail") or item.get("contactEmail") or ""

        return ScrapedOffer(
            title=str(title).strip(),
            description=str(description).strip() or str(title).strip(),
            url=str(url).strip() or SEARCH_URL,
            source=self.name,
            source_id=str(source_id).strip(),
            company=str(company).strip(),
            location=str(location).strip(),
            contract_type=str(contract).strip(),
            required_level=str(level).strip(),
            salary_raw=str(salary).strip(),
            contact_name=str(contact_name).strip(),
            contact_email=str(contact_email).strip(),
        )

    def _resolve_romes(self, query: str) -> list[str] | None:
        """Resout une query texte en codes ROME."""
        if not query:
            return None
        q = query.lower()

        mapping = {
            "info": "M1805", "data": "M1805", "dev": "M1805", "python": "M1805",
            "java": "M1805", "web": "M1805", "logiciel": "M1805", "reseau": "M1810",
            "sysadmin": "M1810", "cloud": "M1810", "support": "M1802",
            "informatique": "M1805", "it": "M1805",
            "rh": "E1201", "ressource": "E1201",
            "compta": "E1401", "comptabilite": "E1401", "finance": "E1402",
            "commerce": "D1505", "vente": "D1505", "marketing": "E1103",
            "communication": "E1103", "design": "E1104",
            "logistique": "E1505", "supply": "E1510",
            "boulanger": "D1102", "boucher": "D1101", "cuisinier": "G1401",
            "soin": "J1501", "sante": "J1501", "aide soignant": "J1501",
        }

        matches = set()
        for term, code in mapping.items():
            if term in q:
                matches.add(code)

        # Chercher dans le dictionnaire ROME complet
        for code, label in ROMES.items():
            if any(term in label.lower() for term in q.split()):
                matches.add(code)

        return list(matches)[:5] if matches else None

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
                "  Niveau      : %s\n"
                "  Salaire     : %s\n"
                "  URL         : %s\n"
                "  Contact     : %s <%s>",
                i, o.title, o.company or "-", o.location or "-",
                o.description, o.contract_type or "-",
                o.required_level or "-", o.salary_raw or "-",
                o.url or "-", o.contact_name or "-", o.contact_email or "-",
            )
        self.logger.info("=== FIN DEBUG ===")


# Dictionnaire ROME partiel
ROMES: dict[str, str] = {
    "M1805": "Etudes et developpement informatique",
    "M1810": "Production et exploitation de systemes d'information",
    "M1801": "Administration de systemes d'information",
    "M1802": "Expertise et support en systemes d'information",
    "M1803": "Direction des systemes d'information",
    "M1806": "Conseil et maitrise d'ouvrage en systemes d'information",
    "E1101": "Animation de site multimedia",
    "E1102": "Conception de contenus multimedia",
    "E1103": "Communication",
    "E1104": "Design de produits et services multimedia",
    "E1105": "Etudes et recherche en informatique",
    "E1201": "Gestion de ressources humaines",
    "E1401": "Comptabilite",
    "E1402": "Gestion financiere et comptable",
    "D1102": "Boulangerie, patisserie, chocolaterie",
    "D1505": "Vente technique, commercialisation de produits technologiques",
    "E1505": "Management de la logistique",
    "E1510": "Management de la supply chain",
    "G1401": "Preparation et confection de plats a servir",
    "J1501": "Soins infirmiers generalistes",
}
