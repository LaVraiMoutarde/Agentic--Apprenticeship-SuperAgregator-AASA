"""
Scraper JobTeaser ENSEA — offres d'alternance de l'ecole ENSEA.

URL seed : https://ensea.jobteaser.com/fr/job-offers?contract=alternating

Authentification : OpenID Connect via storage_state Playwright.

Structure (découverte via debug) :
    - Les offres sont listées en grille (pas de classes CSS exploitables)
    - Chaque offre est un lien <a href="/fr/job-offers/{uuid}-...">
    - Le texte visible suit le pattern :
        [Entreprise]
        [Titre du poste]
        Alternance X à Y mois
        [Ville, France]
    - Pagination : ?contract=alternating&page=N
    - ~8 466 offres au total
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult
from ..exceptions import ScraperNetworkError


BASE_URL = "https://ensea.jobteaser.com"
SEARCH_URL = f"{BASE_URL}/fr/job-offers"


class JobTeaserEnseaScraper(BaseScraper):
    """Scraper pour JobTeaser ENSEA (offres d'alternance).

    Authentification OpenID Connect requise : lancer 'python -m scripts.save_auth'
    avant le premier scraping pour sauvegarder le storage_state.
    """

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
        criteria=None,
    ) -> ScraperResult:
        _ = location
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Debut JobTeaser ENSEA — query='%s', storage=%s",
                         query, self.storage_state_path)

        try:
            offers = asyncio.run(self._scrape_async(query, max_pages))
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
    # Scraping asynchrone
    # ═══════════════════════════════════════════════════════════════

    async def _scrape_async(self, query: str, max_pages: int) -> list[ScrapedOffer]:
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            from ..browser import get_browser_kwargs
            browser_kwargs = get_browser_kwargs(headless=self.headless)
            browser = await p.chromium.launch(**browser_kwargs)
            ctx_kwargs: dict = {}
            if self.storage_state_path.exists():
                ctx_kwargs["storage_state"] = str(self.storage_state_path)
                self.logger.info("Session chargee depuis %s", self.storage_state_path.name)
            else:
                self.logger.warning(
                    "⚠ Fichier storage_state introuvable : %s\n"
                    "  → Lancez 'python -m scripts.save_auth' pour vous authentifier\n"
                    "  → ou 'make auth' si vous utilisez le Makefile.",
                    self.storage_state_path,
                )

            context = await browser.new_context(**ctx_kwargs,
                viewport={"width": 1920, "height": 1080}, locale="fr-FR")
            page = await context.new_page()

            try:
                url = self._build_url(query)
                self.logger.info("Navigation vers %s", url)
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=self.timeout * 1000)
                await page.wait_for_timeout(8000)

                # Détection redirection auth
                if "connect.jobteaser.com" in page.url or "login" in page.url.lower():
                    self.logger.error("Redirige vers authentification OpenID")
                    raise RuntimeError(
                        "Authentification requise. "
                        "Lancez d'abord scripts/save_auth_jobteaser.py"
                    )

                self.logger.info("Page chargee — URL: %s", page.url[:80])

                # Détecter le nombre total d'offres sur la page
                total_offers = await self._detect_total_offers(page)
                offers_per_page = await self._count_offers_per_page(page) or 22
                if total_offers and offers_per_page:
                    total_pages = max(1, (total_offers + offers_per_page - 1) // offers_per_page)
                    effective_max = min(max_pages, total_pages)
                    self.logger.info(
                        "~%d offres, ~%d/p, ~%d pages max (%d demandees)",
                        total_offers, offers_per_page, total_pages, max_pages,
                    )
                else:
                    effective_max = max_pages

                for pagenum in range(effective_max):
                    self.logger.info("Page %d/%d", pagenum + 1, effective_max)

                    # Navigation (sauf pour la page 1 déjà chargée)
                    if pagenum > 0:
                        next_url = self._build_page_url(query, pagenum + 1)
                        self.logger.info("Page suivante: %s", next_url)
                        await page.goto(next_url, wait_until="domcontentloaded",
                                        timeout=self.timeout * 1000)
                        await page.wait_for_timeout(5000)

                    # Extraction avec retry si pas d'offres
                    offers = await self._extract_offers(page)
                    if not offers:
                        self.logger.info("Attente supplementaire...")
                        await page.wait_for_timeout(5000)
                        offers = await self._extract_offers(page)

                    if not offers:
                        self.logger.info("Plus d'offres — fin")
                        break

                    all_offers.extend(offers)
                    self.logger.info("Page %d: %d offres (total: %d)",
                                     pagenum + 1, len(offers), len(all_offers))

            finally:
                await browser.close()

        return all_offers

    # ═══════════════════════════════════════════════════════════════
    # Détection du nombre de pages
    # ═══════════════════════════════════════════════════════════════

    async def _detect_total_offers(self, page) -> int | None:
        """Détecte le nombre total d'offres depuis le texte de la page.

        Pattern JobTeaser : "8 466 offres" ou "8466 offres"
        """
        try:
            body = await page.inner_text("body")
            m = re.search(r"([\d\s]+)\s*offres?", body)
            if m:
                raw = m.group(1).strip()
                # Nettoyer les espaces insécables
                num = int(raw.replace("\u202f", "").replace(" ", ""))
                if num > 0:
                    self.logger.debug("Total offres detecte: %d", num)
                    return num
            self.logger.debug("Total offres non detecte")
            return None
        except Exception as exc:
            self.logger.debug("Erreur detection total: %s", exc)
            return None

    async def _count_offers_per_page(self, page) -> int | None:
        """Compte les offres sur la page courante pour estimer le nombre par page."""
        try:
            offers = await self._extract_offers(page)
            return len(offers) if offers else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # Extraction
    # ═══════════════════════════════════════════════════════════════

    async def _extract_offers(self, page) -> list[ScrapedOffer]:
        """Extrait les offres de la page via les liens /fr/job-offers/."""
        offers: list[ScrapedOffer] = []

        job_links = page.locator("a[href*='/fr/job-offers/']")
        count = await job_links.count()
        self.logger.debug("Liens d'offres trouvés : %d", count)

        seen_urls: set[str] = set()
        for i in range(count):
            try:
                link = job_links.nth(i)
                href = (await link.get_attribute("href")) or ""

                if not href or "?" in href or "#" in href:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                full_url = self._make_abs(href)

                # Récupérer tout le texte du conteneur d'offre
                block_text = await link.evaluate("""
                    (el) => {
                        // Remonter jusqu'au conteneur de l'offre (carte)
                        let node = el;
                        let best = el.innerText || '';
                        for (let d = 0; d < 10; d++) {
                            if (!node || !node.parentElement) break;
                            node = node.parentElement;
                            const t = (node.innerText || '').trim();
                            // Un conteneur d'offre contient typiquement 80-300 caractères
                            if (t.length >= 60 && t.length <= 600) { best = t; break; }
                            if (t.length > best.length && t.length < 2000) best = t;
                        }
                        return best;
                    }
                """)
                if not block_text:
                    block_text = await link.inner_text()

                title = self._extract_title_from_block(block_text)
                if not title or len(title) < 5:
                    continue

                company = self._extract_company_from_block(block_text, title)
                location = self._extract_location_from_block(block_text)
                contract_type = self._extract_contract_from_block(block_text)
                description = block_text[:2000]

                # Fallback : extraire depuis l'URL JobTeaser
                # Format: /fr/job-offers/{uuid}-{company}-{title}-{location}
                slug_parts = href.strip("/").split("/")[-1].split("-")
                if not company and len(slug_parts) > 3:
                    company = self._company_from_slug(slug_parts, title)
                if not location:
                    location = self._location_from_slug(slug_parts)

                offers.append(ScrapedOffer(
                    title=title[:200],
                    description=description,
                    url=full_url,
                    source=self.name,
                    company=company[:300] if company else "",
                    location=location[:300] if location else "",
                    contract_type=contract_type[:100],
                ))

            except Exception as exc:
                self.logger.debug("Lien #%d ignoré: %s", i, exc)

        return offers

    # ═══════════════════════════════════════════════════════════════
    # Parsing du texte
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_title_from_block(text: str) -> str:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines[:5]):
            if re.search(
                r"(Alternance|Apprenti|Stage|CDD|CDI|Ingénieur|Chargé|Développeur|"
                r"Data|Analyst|Consultant|Responsable|Chef|Technicien)",
                line, re.IGNORECASE,
            ):
                return line[:200]
        return lines[1][:200] if len(lines) >= 2 else lines[0][:200] if lines else ""

    @staticmethod
    def _extract_company_from_block(text: str, title: str) -> str:
        """Extrait le nom de l'entreprise. C'est la 1re ligne du bloc."""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:6]:
            if line == title:
                continue
            if len(line) < 2 or len(line) > 80:
                continue
            # Ignorer les métadonnées, dates, URLs, etc.
            if re.search(
                r"^(Alternance|Apprenti|Stage|CDD|CDI|\d+\s*(mois|an|h)|"
                r"semaine|candidatur|ghost|recruteur|newsfeed|Campagne|"
                r"il y a|Offre de|Candidature|Sauvegarder|Parcourez)",
                line, re.IGNORECASE,
            ):
                continue
            if re.search(r"^(https?://|/[a-z])", line):
                continue
            return line[:100]
        return ""

    @staticmethod
    def _extract_location_from_block(text: str) -> str:
        """Extrait la localisation (Ville, France)."""
        if not text:
            return ""
        # Pattern: "Ville, France" ou "Ville (CP), France"
        m = re.search(
            r"([A-Z][a-zéèêëàâîïôùûç\-]+(?:[-\s][A-Z][a-zéèêëàâîïôùûç\-]+)*)"
            r"\s*(?:\(\d{4,5}\))?\s*,\s*"
            r"(?:France|Ile[-\s]de[-\s]France|Île[-\s]de[-\s]France|Paris)",
            text,
        )
        if m:
            return m.group(0).strip()
        return ""

    @staticmethod
    def _extract_contract_from_block(text: str) -> str:
        m = re.search(
            r"(Alternance|Stage|Apprentissage|Contrat\s*(pro|d'apprentissage|CDD|CDI))"
            r"(\s*\d+\s*(à|a|to)\s*\d+\s*(mois|ans|month|year))?",
            text, re.IGNORECASE,
        )
        return m.group(0).strip()[:100] if m else "Alternance"

    @staticmethod
    def _company_from_slug(slug_parts: list[str], title: str) -> str:
        """Extrait le nom de l'entreprise depuis le slug de l'URL.

        Le slug JobTeaser: {uuid}-{company-slug}-{title-slug}
        On saute l'UUID (partie avec des chiffres) et on prend jusqu'au premier mot du titre.
        """
        # Sauter les parties UUID (hexa, courtes) et les parties trop courtes
        clean_parts = [
            p for p in slug_parts
            if not re.match(r"^[0-9a-f]{4,}$", p) and len(p) > 1
        ]
        title_first = title.split()[0].lower() if title else ""
        company_words = []
        for part in clean_parts:
            if part.lower() == title_first or part.lower() in ("alternance", "apprenti", "stage"):
                break
            company_words.append(part.capitalize())
        result = " ".join(company_words).strip()
        return result[:100] if result else ""

    @staticmethod
    def _location_from_slug(slug_parts: list[str]) -> str:
        """Tente d'extraire la localisation depuis la fin du slug."""
        KNOWN_CITIES = {
            "paris", "lyon", "marseille", "lille", "toulouse", "bordeaux",
            "nantes", "strasbourg", "montpellier", "rennes", "nice", "toulon",
            "saint-cloud", "boulogne", "charenton", "versailles", "orleans",
            "saint-denis", "cergy", "pontoise", "la-hague", "la-defense",
            "neuilly", "issy", "ivry", "courbevoie", "nanterre", "vitry",
            "creteil", "noisy", "montreuil", "aubervilliers", "clichy",
            "pantin", "bagnolet", "fontenay", "guyancourt", "velizy",
            "clamart", "meudon", "suresnes", "puteaux", "levallois",
            "longueil", "saint-quentin", "evry",
            "argenteuil", "colombes", "rueil", "asnieres", "gennevilliers",
            "sartrouville", "massy", "antony",
            "saint-ouen", "bezons", "saint-germain", "poissy",
            "gentilly", "villejuif", "montrouge", "malakoff",
        }
        # Chercher de droite à gauche les mots de ville
        result_parts = []
        i = len(slug_parts) - 1
        while i >= 0:
            part = slug_parts[i]
            if part.lower() in KNOWN_CITIES:
                result_parts.insert(0, part.capitalize())
                i -= 1
                # Vérifier si le mot précédent fait partie du nom de la ville
                # (ex: "charenton-le-pont" → parts: ["charenton", "le", "pont"])
                if i >= 0 and slug_parts[i].lower() in ("le", "la", "les", "sur", "sous", "en"):
                    result_parts.insert(0, slug_parts[i].capitalize())
                    i -= 1
            elif result_parts:
                break
            i -= 1
        if result_parts:
            location = " ".join(result_parts).replace("-", "-")
            if not any(c in location.lower() for c in ("france", "belgique", "suisse")):
                location += ", France"
            return location
        return ""

    # ═══════════════════════════════════════════════════════════════
    # URL
    # ═══════════════════════════════════════════════════════════════

    def _build_url(self, query: str) -> str:
        params = ["contract=alternating"]
        if query:
            params.append(f"keyword={query.replace(' ', '+')}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _build_page_url(self, query: str, page_num: int) -> str:
        params = ["contract=alternating", f"page={page_num}"]
        if query:
            params.append(f"keyword={query.replace(' ', '+')}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    def _make_abs(self, href: str) -> str:
        return href if href.startswith("http") else f"{BASE_URL}{href}"

    # ═══════════════════════════════════════════════════════════════
    # Logging
    # ═══════════════════════════════════════════════════════════════

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
