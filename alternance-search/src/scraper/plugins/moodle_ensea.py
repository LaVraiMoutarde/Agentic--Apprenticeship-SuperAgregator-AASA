"""
Scraper Moodle ENSEA — extrait les offres de la Database Activity Moodle.

URL cible : https://moodle.ensea.fr/mod/data/view.php?id=14716

Structure réelle (découverte via debug) :
    - La page contient un unique div.box.py-3
    - Tous les champs de toutes les offres sont des enfants séquentiels de ce div
    - Chaque champ a class="data-field-link" (lien fichier/URL) ou class="data-field-html" (description)
    - La plupart des offres = 1 lien + 1 html ; certaines offres = juste html (lien externe dans le html)
    - Pagination : a[href*='&page=']

Authentification :
    - CAS ENSEA via Playwright (storage_state sauvegardé après connexion manuelle)

Usage :
    from src.scraper.plugins.moodle_ensea import MoodleEnseaScraper

    scraper = MoodleEnseaScraper(
        storage_state_path="auth/moodle_ensea_state.json",
        headless=True,
    )
    result = scraper.scrape(query="", max_pages=5)
    # result.offers → list[ScrapedOffer]
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from ..exceptions import ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

SEED_URL = "https://moodle.ensea.fr/mod/data/view.php?id=14716"
BASE_URL = "https://moodle.ensea.fr"


class MoodleEnseaScraper(BaseScraper):
    """Scraper pour l'activite Database Moodle de l'ENSEA.

    Extrait les offres d'alternance depuis la base de donnees Moodle
    en utilisant Playwright pour le rendu dynamique et l'authentification CAS.

    Authentification requise : lancer 'python -m scripts.save_auth' avant
    le premier scraping pour sauvegarder le storage_state.
    """

    def __init__(
        self,
        storage_state_path: str | Path = "auth/moodle_ensea_state.json",
        headless: bool = True,
        base_url: str = BASE_URL,
        seed_url: str = SEED_URL,
    ) -> None:
        super().__init__()
        self.storage_state_path = Path(storage_state_path)
        self.headless = headless
        self.base_url = base_url.rstrip("/")
        self.seed_url = seed_url

        # Resoudre le chemin absolu du storage_state
        if not self.storage_state_path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            self.storage_state_path = (project_root / storage_state_path).resolve()

    @property
    def name(self) -> str:
        return "moodle_ensea"

    def scrape(
        self,
        query: str = "",
        *,
        location: str = "",
        max_pages: int = 5,
        criteria=None,
    ) -> ScraperResult:
        """Exécute le scraping de la base de données Moodle."""
        _ = query, location
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Démarrage du scraper Moodle ENSEA — seed_url=%s", self.seed_url)
        self.logger.info("Storage state : %s (existe=%s)", self.storage_state_path,
                         self.storage_state_path.exists())

        try:
            offers = asyncio.run(
                self._run_scraping_async(max_pages=max_pages or 9999)
            )
            all_offers = self.validate_output(offers)
            self.logger.info(
                "Scraping terminé — %d offres validées sur %d récupérées",
                len(all_offers), len(offers),
            )
        except Exception as exc:
            self.logger.error("Erreur fatale : %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=max_pages, errors=errors)
        if all_offers:
            result.total_found = len(all_offers)

        self._log_examples(all_offers)
        return result

    # ────────────────────────────────────────────────────────────────
    # Scraping asynchrone (Playwright)
    # ────────────────────────────────────────────────────────────────

    async def _run_scraping_async(self, max_pages: int) -> list[ScrapedOffer]:
        """Scrape la base Moodle page par page.

        Structure réelle :
            - Un seul div.box.py-3 contient tous les champs de toutes les offres
            - Les champs data-field-link et data-field-html se succèdent
            - Chaque offre = 1 link + 1 html (ou parfois juste html)
        """
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []

        async with async_playwright() as p:
            # Utiliser le helper cross-platform pour la detection du navigateur
            from ..browser import get_browser_kwargs
            browser_kwargs = get_browser_kwargs(headless=self.headless)
            browser = await p.chromium.launch(**browser_kwargs)

            context_kwargs: dict[str, Any] = {}
            if self.storage_state_path.exists():
                context_kwargs["storage_state"] = str(self.storage_state_path)
                self.logger.info("Session chargee depuis %s", self.storage_state_path.name)
            else:
                self.logger.warning(
                    "⚠ Fichier storage_state introuvable : %s\n"
                    "  → Lancez 'python -m scripts.save_auth' pour vous authentifier\n"
                    "  → ou 'make auth' si vous utilisez le Makefile.",
                    self.storage_state_path,
                )

            context = await browser.new_context(**context_kwargs,
                viewport={"width": 1920, "height": 1080})
            page = await context.new_page()

            try:
                # Navigation vers la page cible
                self.logger.info("Navigation vers %s", self.seed_url)
                await page.goto(self.seed_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                # Vérifier redirection CAS
                current_url = page.url
                if "cas" in current_url.lower() or "login" in current_url.lower():
                    self.logger.error("Redirige vers la page de login CAS. URL: %s", current_url)
                    raise ScraperParseError(
                        "Authentification CAS ENSEA requise.\n"
                        "  → Le storage_state est expire ou invalide.\n"
                        "  → Relancez l'authentification : python -m scripts.save_auth\n"
                        "  → Puis relancez le scraping.",
                        scraper_name=self.name,
                    )

                self.logger.info("Page chargée — URL: %s", current_url)

                # Scraper les pages
                page_num = 0
                while page_num < max_pages:
                    page_num += 1
                    self.logger.info("Parsing page %d/%d", page_num, max_pages)
                    await page.wait_for_timeout(1000)

                    page_offers = await self._parse_current_page(page)
                    all_offers.extend(page_offers)
                    self.logger.info("Page %d : %d offres extraites (total: %d)",
                                     page_num, len(page_offers), len(all_offers))

                    # Aller à la page suivante
                    if page_num < max_pages:
                        has_next = await self._go_to_next_page(page)
                        if not has_next:
                            self.logger.info("Plus de pages disponibles")
                            break

            except ScraperParseError:
                raise
            except Exception as exc:
                self.logger.error("Erreur Playwright : %s", exc)
                raise ScraperParseError(
                    f"Erreur lors du scraping Playwright : {exc}",
                    scraper_name=self.name,
                    original=exc,
                ) from exc
            finally:
                await browser.close()

        return all_offers

    # ────────────────────────────────────────────────────────────────
    # Parsing d'une page
    # ────────────────────────────────────────────────────────────────

    async def _parse_current_page(self, page) -> list[ScrapedOffer]:
        """Parse la page courante en extrayant les offres du div.box.py-3.

        Returns:
            Liste de ScrapedOffer parsées.
        """
        offers: list[ScrapedOffer] = []

        # Vérifier que le box existe
        box = page.locator("div.box.py-3")
        if await box.count() == 0:
            self.logger.warning("div.box.py-3 introuvable sur la page")
            return []

        # Récupérer tous les data-field elements dans l'ordre du DOM
        field_elements = await page.evaluate("""
            () => {
                const box = document.querySelector('div.box.py-3');
                if (!box) return [];
                const all = box.querySelectorAll('[class*="data-field"]');
                return Array.from(all).map(el => {
                    const html = el.innerHTML || '';
                    const text = (el.textContent || '').trim();
                    const cls = el.className;
                    const tag = el.tagName;
                    let href = '';
                    if (tag === 'A' && el.href) href = el.href;
                    // Pour les div data-field-html, chercher un lien externe
                    let externalUrl = '';
                    if (cls.includes('data-field-html')) {
                        const links = el.querySelectorAll('a[href]');
                        for (const lnk of links) {
                            const h = lnk.href;
                            if (h && !h.includes('moodle.ensea.fr') && !h.startsWith('mailto:') && !h.startsWith('#')) {
                                externalUrl = h;
                                break;
                            }
                        }
                    }
                    return { cls, tag, text, href, externalUrl, html };
                });
            }
        """)

        if not field_elements:
            self.logger.warning("Aucun champ data-field trouvé dans le box")
            return []

        self.logger.debug("Champs trouvés : %d", len(field_elements))

        # Grouper les champs en entrées
        entries = self._group_fields_into_entries(field_elements)
        self.logger.debug("Entrées groupées : %d", len(entries))

        for idx, entry_fields in enumerate(entries):
            try:
                offer = self._fields_to_offer(entry_fields, idx + 1)
                if offer:
                    offers.append(offer)
            except Exception as exc:
                self.logger.debug("Entrée #%d ignorée: %s", idx + 1, exc)

        return offers

    def _group_fields_into_entries(self, fields: list[dict]) -> list[list[dict]]:
        """Groupe les champs séquentiels en entrées.

        Règle :
            - data-field-link démarre une nouvelle entrée, le data-field-html suivant lui appartient
            - data-field-html sans link précédent = entrée à lui seul
        """
        entries: list[list[dict]] = []
        current_entry: list[dict] = []
        pending_link = False

        for field in fields:
            is_link = "data-field-link" in field["cls"]
            is_html = "data-field-html" in field["cls"]

            if is_link:
                # Nouvelle entrée : sauvegarder la précédente si elle a du contenu
                if current_entry:
                    entries.append(current_entry)
                current_entry = [field]
                pending_link = True

            elif is_html:
                if pending_link:
                    # Ce html appartient à l'entrée du link précédent
                    current_entry.append(field)
                    pending_link = False
                else:
                    # html seul = nouvelle entrée
                    if current_entry:
                        entries.append(current_entry)
                    current_entry = [field]

        if current_entry:
            entries.append(current_entry)

        return entries

    def _fields_to_offer(self, entry_fields: list[dict], entry_num: int) -> ScrapedOffer | None:
        """Convertit une liste de champs en ScrapedOffer.

        Extrait le titre, l'URL, la description, l'entreprise, la localisation,
        et le type de contrat depuis les champs.
        """
        link_field = None
        html_field = None

        for f in entry_fields:
            if "data-field-link" in f["cls"]:
                link_field = f
            elif "data-field-html" in f["cls"]:
                html_field = f

        # ── Titre ──
        title = ""
        if link_field and link_field["text"]:
            # Nettoyer le nom de fichier pour en faire un titre
            title = self._clean_filename_title(link_field["text"])
        if not title and html_field and html_field["text"]:
            title = self._extract_title_from_html(html_field["text"])

        if not title or len(title) < 3:
            return None

        # ── URL ──
        url = ""
        if link_field and link_field["href"]:
            url = link_field["href"]
        if not url and html_field and html_field["externalUrl"]:
            url = html_field["externalUrl"]
        if not url:
            url = SEED_URL

        # ── Description ──
        description = ""
        if html_field and html_field["html"]:
            description = self._clean_html(html_field["html"])
        if not description:
            description = title

        # ── Métadonnées depuis le texte HTML ──
        text_content = html_field["text"] if html_field else ""

        company = self._extract_company(text_content)
        location = self._extract_location(text_content)
        contract_type = self._extract_contract_type(text_content)
        required_level = self._extract_required_level(text_content)

        return ScrapedOffer(
            title=title[:500],
            description=description[:5000],
            url=url,
            source=self.name,
            company=company[:300] if company else "",
            location=location[:300] if location else "",
            contract_type=contract_type[:100] if contract_type else "",
            required_level=required_level[:50] if required_level else "",
        )

    # ────────────────────────────────────────────────────────────────
    # Helpers d'extraction
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_filename_title(filename: str) -> str:
        """Nettoie un nom de fichier pour en faire un titre lisible."""
        name = filename.rsplit(".", 1)[0] if "." in filename else filename
        # Remplacer les underscores par des espaces
        name = name.replace("_", " ").replace("-", " ").strip()
        # Supprimer les répétitions d'espaces
        name = re.sub(r"\s+", " ", name)
        return name[:200]

    @staticmethod
    def _extract_title_from_html(text: str) -> str:
        """Extrait le titre depuis le contenu HTML (premier <strong> ou <h1>)."""
        if not text:
            return ""
        # Prendre la première ligne significative
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:5]:
            line = line.replace("\u00a0", " ").strip()
            if len(line) > 5 and not line.startswith("http"):
                return line[:200]
        return text[:200]

    @staticmethod
    def _clean_html(html: str) -> str:
        """Nettoie le HTML pour en faire une description textuelle."""
        text = re.sub(r"<[^>]+>", "\n", html)
        text = text.replace("&amp;", "&").replace("&nbsp;", " ") \
                   .replace("&lt;", "<").replace("&gt;", ">")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()[:5000]

    @staticmethod
    def _extract_company(text: str) -> str:
        """Extrait le nom de l'entreprise du texte."""
        if not text:
            return ""
        patterns = [
            r"Entreprise[:\s]+([A-Z][A-Za-z0-9éèêëàâîïôùûç\s&\-]{2,40})",
            r"Organisme[:\s]+([A-Z][A-Za-z0-9éèêëàâîïôùûç\s&\-]{2,40})",
            r"Société[:\s]+([A-Z][A-Za-z0-9éèêëàâîïôùûç\s&\-]{2,40})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return ""

    @staticmethod
    def _extract_location(text: str) -> str:
        """Extrait la localisation du texte."""
        if not text:
            return ""
        # Chercher le marqueur emoji 📍 ou le texte "Ville :"
        m = re.search(r"📍.*?Ville\s*:\s*([^\n]+)", text)
        if m:
            loc = m.group(1).strip().rstrip(")")
            loc = re.sub(r"\s*\(\d+[^)]*\)", "", loc)
            return loc[:200]
        patterns = [
            r"Ville[:\s]+([A-Za-zéèêëàâîïôùûç\s\-]+?)(?:\s*[-\n]|\s*$)",
            r"Lieu[:\s]+([A-Za-zéèêëàâîïôùûç\s\-]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                loc = m.group(1).strip().rstrip(")")
                return loc[:200]
        return ""

    @staticmethod
    def _extract_contract_type(text: str) -> str:
        """Extrait le type de contrat."""
        if not text:
            return ""
        m = re.search(
            r"(Contrat[^)]*|Stage[^)]*|Alternance[^)]*|Apprentissage[^)]*"
            r"|Professionnalisation[^)]*)",
            text, re.IGNORECASE,
        )
        if m:
            ct = m.group(0).strip()[:100]
            if ":" in ct:
                ct = ct.split(":")[-1].strip()
            return ct
        return ""

    @staticmethod
    def _extract_required_level(text: str) -> str:
        """Extrait le niveau requis (Bac+3, Bac+5, etc.)."""
        if not text:
            return ""
        m = re.search(r"(Bac[+]?\d*|Master|Licence|Ingénieur|Bachelor|M1|M2|L3)", text)
        return m.group(1) if m else ""

    # ────────────────────────────────────────────────────────────────
    # Pagination
    # ────────────────────────────────────────────────────────────────

    async def _go_to_next_page(self, page) -> bool:
        """Trouve et navigue vers la page suivante.

        Fallback: construire l'URL directement (pagination = &page=N).
        """
        try:
            current_url = page.url
            self.logger.debug("Recherche pagination sur: %s", current_url)

            # Méthode 1: chercher un lien &page= (non headless)
            next_btn = page.locator("a[href*='&page=']").first
            if await next_btn.count() > 0:
                href = await next_btn.get_attribute("href") or ""
                if href and href != "#":
                    full_url = href if href.startswith("http") else f"{self.base_url}{href}"
                    self.logger.info("Page suivante: %s", full_url)
                    await page.goto(full_url, wait_until="networkidle", timeout=30000)
                    await page.wait_for_timeout(2000)
                    return True

            # Méthode 2: construire l'URL directement (headless)
            # Pattern Moodle: ?d=18&advanced=0&paging&page=N
            if "?" in current_url:
                base = current_url.split("?")[0]
            else:
                base = current_url
            # Chercher le paramètre d dans l'URL
            d_match = __import__("re").search(r'[?&]d=(\d+)', current_url)
            d_val = d_match.group(1) if d_match else "18"
            # Chercher le paramètre page courant
            page_match = __import__("re").search(r'[?&]page=(\d+)', current_url)
            current_page = int(page_match.group(1)) if page_match else 0
            next_page = current_page + 1
            next_url = f"{base}?d={d_val}&advanced=0&paging&page={next_page}"
            self.logger.info("Page suivante (fallback): %s", next_url)
            await page.goto(next_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            return True

        except Exception as exc:
            self.logger.debug("Erreur pagination: %s", exc)
            return False

    # ────────────────────────────────────────────────────────────────
    # Logging
    # ────────────────────────────────────────────────────────────────

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        if not offers:
            self.logger.info("Aucune offre à logger.")
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
