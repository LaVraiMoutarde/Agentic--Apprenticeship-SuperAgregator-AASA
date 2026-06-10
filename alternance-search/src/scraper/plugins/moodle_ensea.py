"""
Scraper Moodle ENSEA — extrait les offres de la Database Activity Moodle.

URL cible : https://moodle.ensea.fr/mod/data/view.php?id=14716

Structure Moodle Database Activity :
    - Mode liste par défaut (table.generaltable)
    - Chaque ligne = une entrée, colonnes = champs configurés
    - Mode vue (view.php?rid=XXX) pour les détails
    - Pagination en bas de page

Authentification :
    - CAS ENSEA via Playwright (pas de cookies hardcodés)
    - Utilise `storage_state` sauvegardé après connexion manuelle

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
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..base import BaseScraper, ScrapedOffer, ScraperResult, ScraperStatus
from ..exceptions import ScraperParseError


# ═══════════════════════════════════════════════════════════════════
# Configuration du scraper
# ═══════════════════════════════════════════════════════════════════

# URL de l'activité Database Moodle
SEED_URL = "https://moodle.ensea.fr/mod/data/view.php?id=14716"
BASE_URL = "https://moodle.ensea.fr"

# Mapping des colonnes du tableau Moodle → champs ScrapedOffer.
# Adaptez ces valeurs selon la configuration réelle de la Database Moodle.
# Les clés sont les noms de colonnes affichés dans le tableau Moodle.
COLUMN_MAPPING: dict[str, str] = {
    "Titre": "title",
    "Entreprise": "company",
    "Lieu": "location",
    "Description": "description",
    "Contact": "contact_name",
    "Email": "contact_email",
    "Niveau": "required_level",
    "Domaine": "domain",
    "Type de contrat": "contract_type",
    "Salaire": "salary_raw",
    "URL": "url",
    "Date de publication": "published_date",
}

# Sélecteurs CSS pour la Database Activity Moodle
SELECTORS = {
    "table": "table.generaltable, table.flexible, table.dataview",
    "rows": "tbody tr, tr.r0, tr.r1",
    "pagination": "nav.pagination ul.pagination li.page-item a.page-link, div.paging a",
    "view_link": "a[href*='view.php?rid=']",
    "single_entry": "div.entry, div.entrybox",
    "fields_container": "div.field, div.fields, table.dataview",
    "field_name": ".field-name, th.c0, .field-label",
    "field_value": ".field-value, td.c1, .field-content",
}


class MoodleEnseaScraper(BaseScraper):
    """Scraper pour l'activité Database Moodle de l'ENSEA.

    Extrait les offres d'alternance depuis la base de données Moodle
    en utilisant Playwright pour le rendu dynamique et l'authentification CAS.

    Args:
        storage_state_path: Chemin vers le fichier JSON de session Playwright
                            (obtenu après une connexion manuelle au CAS ENSEA).
        headless: Exécute le navigateur en mode headless.
        column_mapping: Mapping personnalisé (nom colonne Moodle → champ ScrapedOffer).
        base_url: URL de base du Moodle ENSEA.
        seed_url: URL de l'activité Database à scraper.
    """

    def __init__(
        self,
        storage_state_path: str | Path = "auth/moodle_ensea_state.json",
        headless: bool = True,
        column_mapping: dict[str, str] | None = None,
        base_url: str = BASE_URL,
        seed_url: str = SEED_URL,
    ) -> None:
        super().__init__()
        self.storage_state_path = Path(storage_state_path)
        self.headless = headless
        self.column_mapping = column_mapping or COLUMN_MAPPING
        self.base_url = base_url.rstrip("/")
        self.seed_url = seed_url

        # Résoudre le chemin absolu du storage_state
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
        """Exécute le scraping de la base de données Moodle.

        Args:
            query: Non utilisé (la Database Moodle n'a pas de recherche textuelle).
            location: Non utilisé.
            max_pages: Nombre max de pages à parcourir.

        Returns:
            ScraperResult avec les offres collectées.
        """
        _ = query, location
        errors: list[Exception] = []
        all_offers: list[ScrapedOffer] = []

        self.logger.info("Démarrage du scraper Moodle ENSEA — seed_url=%s", self.seed_url)
        self.logger.info("Storage state : %s (existe=%s)", self.storage_state_path,
                         self.storage_state_path.exists())

        try:
            offers, pages, total = asyncio.run(
                self._run_scraping_async(max_pages=max_pages)
            )
            all_offers = self.validate_output(offers)
            self.logger.info(
                "Scraping terminé — %d offres validées sur %d récupérées, %d pages",
                len(all_offers), len(offers), pages,
            )
        except Exception as exc:
            self.logger.error("Erreur fatale : %s: %s", type(exc).__name__, exc)
            errors.append(exc)

        result = self._build_result(all_offers, pages=0, errors=errors)
        if all_offers:
            result.pages_scraped = max_pages
            result.total_found = len(all_offers)

        # ── Log de debug : 3 exemples ──
        self._log_examples(all_offers)

        return result

    async def _run_scraping_async(self, max_pages: int) -> tuple[list[ScrapedOffer], int, int]:
        """Logique de scraping asynchrone avec Playwright.

        Returns:
            (offres, pages_scrapées, total_entrées)
        """
        from playwright.async_api import async_playwright

        all_offers: list[ScrapedOffer] = []
        pages_scraped = 0

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)

            # Charger le storage_state si disponible
            context_kwargs: dict[str, Any] = {}
            if self.storage_state_path.exists():
                context_kwargs["storage_state"] = str(self.storage_state_path)
                self.logger.info("Session chargée depuis %s", self.storage_state_path.name)
            else:
                self.logger.warning(
                    "Fichier storage_state introuvable : %s — "
                    "connectez-vous manuellement et sauvegardez l'état.",
                    self.storage_state_path,
                )

            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            try:
                # ── 1. Navigation vers la page cible ──
                self.logger.info("Navigation vers %s", self.seed_url)
                await page.goto(self.seed_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)  # Attendre le rendu JS Moodle

                # Vérifier si on est redirigé vers CAS
                current_url = page.url
                if "cas" in current_url.lower() or "login" in current_url.lower():
                    self.logger.error("Redirigé vers la page de login CAS. URL: %s", current_url)
                    raise ScraperParseError(
                        "Authentification CAS requise. Connectez-vous manuellement, "
                        "sauvegardez l'état avec page.context.storage_state(path=...), "
                        "puis relancez le scraper.",
                        scraper_name=self.name,
                    )

                self.logger.info("Page chargée — URL: %s", current_url)

                # ── 2. Parsing de la page courante ──
                for page_num in range(1, max_pages + 1):
                    self.logger.info("Parsing page %d/%d", page_num, max_pages)
                    await page.wait_for_timeout(1000)

                    page_offers = await self._parse_list_page(page)
                    all_offers.extend(page_offers)
                    pages_scraped = page_num
                    self.logger.info("Page %d : %d offres extraites", page_num, len(page_offers))

                    # Pagination → page suivante
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

        return all_offers, pages_scraped, len(all_offers)

    async def _parse_list_page(self, page) -> list[ScrapedOffer]:
        """Parse une page de liste Moodle Database Activity.

        Stratégie :
        1. Chercher le tableau principal (table.generaltable)
        2. Extraire les en-têtes pour mapper les colonnes
        3. Parcourir chaque ligne, mapper les valeurs aux champs ScrapedOffer
        4. Si pas de tableau, tenter le mode "vue simple" (.entry)

        Args:
            page: Page Playwright.

        Returns:
            Liste de ScrapedOffer parsées.
        """
        offers: list[ScrapedOffer] = []

        # ── Détection du mode d'affichage ──
        table_visible = await page.locator(SELECTORS["table"]).first().count() > 0
        entries_visible = await page.locator(SELECTORS["single_entry"]).first().count() > 0

        if table_visible:
            offers = await self._parse_table_mode(page)
        elif entries_visible:
            offers = await self._parse_entry_mode(page)
        else:
            # Tentative de détection automatique
            self.logger.warning(
                "Aucun sélecteur reconnu — tentative de parsing générique"
            )
            offers = await self._parse_generic(page)

        # ── Enrichissement : récupérer l'URL de détail ──
        offers = await self._enrich_with_detail_urls(page, offers)

        return offers

    async def _parse_table_mode(self, page) -> list[ScrapedOffer]:
        """Parse le mode tableau (le plus courant pour Database Activity).

        Structure Moodle typique :
            <table class="generaltable">
                <thead><tr><th>Colonne1</th><th>Colonne2</th>...</tr></thead>
                <tbody>
                    <tr>
                        <td class="c0">valeur</td>
                        <td class="c1">valeur</td>
                        ...
                        <td><a href="view.php?rid=123">Voir</a></td>
                    </tr>
                </tbody>
            </table>
        """
        offers: list[ScrapedOffer] = []

        # 1. Extraire les en-têtes
        headers_raw = await page.locator(
            f"{SELECTORS['table']} thead th, {SELECTORS['table']} thead td"
        ).all_inner_texts()

        headers = [h.strip().lower().capitalize() for h in headers_raw if h.strip()]
        self.logger.debug("Colonnes détectées : %s", json.dumps(headers))

        if not headers:
            self.logger.warning("Aucune colonne détectée dans le tableau")
            return offers

        # 2. Construire le mapping d'indices
        # index → nom_champ_ScrapedOffer
        mapping: dict[int, str] = {}
        for idx, header in enumerate(headers):
            field = self._resolve_field(header)
            if field:
                mapping[idx] = field

        self.logger.debug("Mapping colonnes→champs : %s", json.dumps(mapping))

        # 3. Parcourir les lignes du corps du tableau
        rows = page.locator(SELECTORS["rows"])

        row_count = await rows.count()
        self.logger.debug("Lignes détectées : %d", row_count)

        for i in range(row_count):
            row = rows.nth(i)
            cells = row.locator("td")
            cell_count = await cells.count()

            if cell_count < 2:
                continue  # ligne vide ou d'en-tête

            offer_data: dict[str, Any] = {
                "source": self.name,
                "source_id": "",
                "title": "",
                "description": "",
                "url": "",
            }

            for j in range(cell_count):
                cell = cells.nth(j)
                text = (await cell.inner_text()).strip()

                # Détecter le lien de détail (view.php?rid=XXX)
                detail_link = cell.locator("a[href*='view.php?rid=']").first()
                if await detail_link.count() > 0:
                    href = await detail_link.get_attribute("href") or ""
                    rid = self._extract_rid(href)
                    if rid:
                        offer_data["source_id"] = rid
                        offer_data["url"] = f"{self.base_url}{href}" if href.startswith("/") else href
                    # Si le lien contient du texte, c'est probablement le titre
                    if text and not offer_data["title"]:
                        offer_data["title"] = text
                    continue

                # Détecter un email
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                if email_match and not offer_data.get("contact_email"):
                    offer_data["contact_email"] = email_match.group(0)
                    # Enlever l'email du texte pour le champ correspondant
                    text = text.replace(email_match.group(0), "").strip()
                    if not text:
                        continue

                # Mapper via l'index de colonne
                if j in mapping:
                    field_name = mapping[j]
                    if field_name == "title" and not offer_data.get("title"):
                        offer_data["title"] = text
                    elif field_name == "description" and text:
                        offer_data["description"] = text
                    elif field_name == "url" and text and not offer_data.get("url"):
                        if text.startswith("http"):
                            offer_data["url"] = text
                    else:
                        if text and not offer_data.get(field_name):
                            offer_data[field_name] = text

            # Si pas de source_id trouvé via le lien, chercher dans toute la ligne
            if not offer_data["source_id"]:
                # Chercher n'importe quel lien view.php dans la ligne
                view_link = row.locator("a[href*='rid=']").first()
                if await view_link.count() > 0:
                    href = await view_link.get_attribute("href") or ""
                    rid = self._extract_rid(href)
                    if rid:
                        offer_data["source_id"] = rid
                        if href.startswith("/"):
                            offer_data["url"] = f"{self.base_url}{href}"

            # Validation minimale
            if offer_data["title"] or offer_data["description"]:
                try:
                    offer = ScrapedOffer(**offer_data)
                    offers.append(offer)
                except Exception as exc:
                    self.logger.warning("Offre invalide ligne %d : %s", i, exc)

        return offers

    async def _parse_entry_mode(self, page) -> list[ScrapedOffer]:
        """Parse le mode vue individuelle (une entrée par page ou liste verticale).

        Structure Moodle typique :
            <div class="entry">
                <div class="field">
                    <div class="field-name">Titre</div>
                    <div class="field-value">...</div>
                </div>
                ...
            </div>
        """
        offers: list[ScrapedOffer] = []
        entries = page.locator(SELECTORS["single_entry"])
        count = await entries.count()

        for i in range(count):
            entry = entries.nth(i)
            fields = entry.locator("div.field, div.fields table tr, .dataview tr")
            field_count = await fields.count()

            offer_data: dict[str, Any] = {
                "source": self.name,
                "title": "",
                "description": "",
                "url": "",
            }

            for j in range(field_count):
                field = fields.nth(j)

                # Extraire nom et valeur du champ
                name_el = field.locator(SELECTORS["field_name"]).first()
                value_el = field.locator(SELECTORS["field_value"]).first()

                if await name_el.count() == 0 or await value_el.count() == 0:
                    # Essayer th/td pour les tables
                    name_el = field.locator("th, .c0").first()
                    value_el = field.locator("td, .c1").first()

                if await name_el.count() == 0:
                    continue

                name = (await name_el.inner_text()).strip()
                value = (await value_el.inner_text()).strip() if await value_el.count() > 0 else ""

                field_key = self._resolve_field(name)
                if field_key and value:
                    if field_key not in offer_data or not offer_data[field_key]:
                        offer_data[field_key] = value

                # Détecter un email dans la valeur
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', value)
                if email_match:
                    offer_data["contact_email"] = email_match.group(0)

                # Détecter un lien
                link = value_el.locator("a").first() if await value_el.count() > 0 else None
                if link and await link.count() > 0 if link else False:
                    href = await link.get_attribute("href")
                    if href:
                        rid = self._extract_rid(href)
                        if rid:
                            offer_data["source_id"] = rid
                        if href.startswith("http"):
                            offer_data["url"] = href

            # URL de l'entrée (mode vue)
            view_link = entry.locator("a[href*='rid=']").first()
            if await view_link.count() > 0:
                href = await view_link.get_attribute("href") or ""
                rid = self._extract_rid(href)
                if rid and not offer_data.get("source_id"):
                    offer_data["source_id"] = rid
                if not offer_data.get("url"):
                    offer_data["url"] = f"{self.base_url}{href}" if href.startswith("/") else href

            if offer_data["title"] or offer_data["description"]:
                try:
                    offer = ScrapedOffer(**offer_data)
                    offers.append(offer)
                except Exception as exc:
                    self.logger.warning("Offre invalide entrée %d : %s", i, exc)

        return offers

    async def _parse_generic(self, page) -> list[ScrapedOffer]:
        """Parsing de dernière chance — scanne toutes les zones de texte."""
        offers: list[ScrapedOffer] = []

        # Récupérer tout le texte visible
        body_text = await page.inner_text("body")
        if not body_text.strip():
            return offers

        # Tentative : chercher des blocs qui ressemblent à des offres
        # Chercher des patterns récurrents (ex: séparateurs entre offres)
        sections = re.split(r'\n{3,}', body_text)
        for section in sections:
            section = section.strip()
            if len(section) < 20:
                continue

            offer_data = {
                "source": self.name,
                "title": "",
                "description": section[:500],
                "url": self.seed_url,
            }

            # Première ligne comme titre probable
            lines = section.split("\n")
            if lines and len(lines[0]) < 200:
                offer_data["title"] = lines[0].strip()
            else:
                offer_data["title"] = section[:100].split("\n")[0].strip()

            # Chercher un email
            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', section)
            if email_match:
                offer_data["contact_email"] = email_match.group(0)

            # Chercher un contact (nom après "Contact :" ou "contact :")
            contact_match = re.search(
                r'(?:contact|contact\s*:)\s*([A-Z][a-zéèêëàâîïôûùç]+\s+[A-Z][a-zéèêëàâîïôûùç]+)',
                section, re.IGNORECASE,
            )
            if contact_match:
                offer_data["contact_name"] = contact_match.group(1)

            try:
                offer = ScrapedOffer(**offer_data)
                offers.append(offer)
            except Exception:
                pass

        return offers

    async def _enrich_with_detail_urls(
        self, page, offers: list[ScrapedOffer]
    ) -> list[ScrapedOffer]:
        """Enrichit les offres avec les URLs de détail si manquantes."""
        if not offers:
            return offers

        # Chercher les liens view.php dans la page courante
        links = page.locator("a[href*='view.php?rid=']")
        link_count = await links.count()

        if link_count == 0:
            return offers

        # Construire un mapping rid → url
        rid_url_map: dict[str, str] = {}
        for i in range(link_count):
            href = await links.nth(i).get_attribute("href") or ""
            rid = self._extract_rid(href)
            if rid:
                full_url = f"{self.base_url}{href}" if href.startswith("/") else href
                rid_url_map[rid] = full_url

        # Enrichir
        for offer in offers:
            if offer.source_id and offer.source_id in rid_url_map and not offer.url:
                offer.url = rid_url_map[offer.source_id]

        return offers

    async def _go_to_next_page(self, page) -> bool:
        """Navigue vers la page suivante via la pagination Moodle.

        Retourne True si une page suivante a été trouvée et chargée.
        """
        # Chercher le lien "Suivant" ou "Next" ou la page suivante
        next_link = page.locator(
            f"{SELECTORS['pagination']}, "
            "a:has-text('Suivant'), "
            "a:has-text('Next'), "
            "a:has-text('►'), "
            "a:has-text('>')"
        ).last()

        if await next_link.count() == 0:
            return False

        # Vérifier que le lien n'est pas désactivé
        parent = next_link.locator("..")
        parent_class = await parent.get_attribute("class") or ""
        if "disabled" in parent_class:
            return False

        try:
            self.logger.info("Navigation vers la page suivante...")
            await next_link.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            return True
        except Exception as exc:
            self.logger.warning("Impossible d'aller à la page suivante : %s", exc)
            return False

    # ── Helpers ──

    def _resolve_field(self, header: str) -> str | None:
        """Résout un nom de colonne Moodle en nom de champ ScrapedOffer.

        Args:
            header: Nom de la colonne tel qu'affiché dans Moodle.

        Returns:
            Nom du champ ScrapedOffer, ou None si non reconnu.
        """
        # Nettoyage
        clean = header.strip().lower().capitalize()

        # Correspondance exacte
        if clean in self.column_mapping:
            return self.column_mapping[clean]

        # Correspondance partielle (le header Moodle peut contenir du HTML)
        for moodle_name, field_name in self.column_mapping.items():
            if moodle_name.lower() in clean.lower():
                return field_name

        # Heuristiques supplémentaires
        lower = header.lower()
        if any(w in lower for w in ["titre", "intitulé", "poste", "sujet", "title"]):
            return "title"
        if any(w in lower for w in ["entreprise", "société", "company", "employeur"]):
            return "company"
        if any(w in lower for w in ["lieu", "ville", "localisation", "location"]):
            return "location"
        if any(w in lower for w in ["description", "descriptif", "mission"]):
            return "description"
        if any(w in lower for w in ["contact", "nom"]):
            return "contact_name"
        if any(w in lower for w in ["email", "mail", "courriel"]):
            return "contact_email"
        if any(w in lower for w in ["niveau", "bac", "master", "licence"]):
            return "required_level"
        if any(w in lower for w in ["domaine", "filière", "spécialité"]):
            return "domain"
        if any(w in lower for w in ["contrat", "type"]):
            return "contract_type"
        if any(w in lower for w in ["salaire", "rémunération"]):
            return "salary_raw"
        if any(w in lower for w in ["url", "lien", "site", "web"]):
            return "url"
        if any(w in lower for w in ["date", "publi"]):
            return "published_date"

        return None

    def _extract_rid(self, href: str) -> str | None:
        """Extrait l'ID d'enregistrement Moodle (rid) depuis une URL.

        Exemple : '/mod/data/view.php?rid=123' → '123'
        """
        if not href:
            return None
        match = re.search(r'rid=(\d+)', href)
        return match.group(1) if match else None

    def _log_examples(self, offers: list[ScrapedOffer]) -> None:
        """Logge 3 exemples d'offres extraites (debug)."""
        if not offers:
            self.logger.info("Aucune offre à logger.")
            return

        sample = offers[:3]
        self.logger.info("=== DEBUG : %d exemple(s) d'offre(s) extraite(s) ===", len(sample))
        for i, offer in enumerate(sample, 1):
            self.logger.info(
                "--- Offre #%d ---\n"
                "  Title       : %s\n"
                "  Company     : %s\n"
                "  Location    : %s\n"
                "  Description : %.200s\n"
                "  Contact     : %s <%s>\n"
                "  Level       : %s\n"
                "  Domain      : %s\n"
                "  Contract    : %s\n"
                "  Salary      : %s\n"
                "  URL         : %s\n"
                "  Source ID   : %s\n"
                "  Scraped at  : %s",
                i,
                offer.title,
                offer.company or "(non spécifié)",
                offer.location or "(non spécifié)",
                offer.description,
                offer.contact_name or "(non spécifié)",
                offer.contact_email or "(non spécifié)",
                offer.required_level or "(non spécifié)",
                offer.domain or "(non spécifié)",
                offer.contract_type or "(non spécifié)",
                offer.salary_raw or "(non spécifié)",
                offer.url or "(non spécifié)",
                offer.source_id or "(non spécifié)",
                offer.scraped_at,
            )
        self.logger.info("=== FIN DEBUG ===")
