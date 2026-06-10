"""
NormalizationPipeline — nettoie, enrichit et deduplique les offres scrapees.

Etapes :
  1. Nettoyage HTML residuel + Unicode + espaces
  2. Detection alternance (confidence score 0..1)
  3. Calcul data_quality_score (0..1)
  4. Deduplication par (title, company, url)
  5. Construction search_text pour embeddings futurs

Produit des instances Offer (ORM) pretes pour la base SQLite.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from src.store.models import Offer


class NormalizationPipeline:
    """Pipeline de normalisation et nettoyage des offres scrapees."""

    _HTML_TAG_RE = re.compile(r"<[^>]+>")
    _MULTI_SPACE_RE = re.compile(r"\s+")
    _MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

    _ALTERNANCE_POSITIVE = re.compile(
        r"\balternance\b|\bapprentissage\b|\bcontrat\s+(?:en|d')\s+alternance\b|"
        r"\bcontrat\s+(?:en|d')\s+apprentissage\b|\bprofessionnalisation\b|"
        r"\bbac\s*\+\s*\d\b|\bbts\b|\bbut\b|\bbachelor\b|"
        r"\btitre\s+(?:professionnel|rncp)\b|\bcfa\b|\bformation\s+en\s+alternance\b",
        re.IGNORECASE,
    )

    _NOT_ALTERNANCE_NEGATIVE = re.compile(
        r"\bcdi\b|\bcdd\b|\bint[eé]rim\b|\bfreelance\b|"
        r"\b5\s*ans\s+d.experience\b|\b10\s*ans\b|\bs[eé]nior\b|"
        r"\bconfirm[eé]\b.*\bans\b",
        re.IGNORECASE,
    )

    def __init__(self, log=None):
        self.log = log
        self.stats = {"input": 0, "cleaned": 0, "duplicates": 0, "rejected": 0, "errors": 0}

    # ═══════════════════════════════════════════════════════════════
    # Point d'entree
    # ═══════════════════════════════════════════════════════════════

    def process(self, raw_offers: list) -> list[Offer]:
        """Pipeline complet : scraped offers -> Offer ORM ready."""
        self.stats["input"] = len(raw_offers)
        results: list[Offer] = []

        for raw in raw_offers:
            try:
                offer = self._normalize_one(raw)
                if offer:
                    results.append(offer)
                    self.stats["cleaned"] += 1
                else:
                    self.stats["rejected"] += 1
            except Exception as exc:
                self.stats["errors"] += 1
                if self.log:
                    self.log.warning("Offre ignoree : %s", str(exc)[:120])

        unique = self._deduplicate(results)
        self.stats["duplicates"] = len(results) - len(unique)

        if self.log:
            self.log.info(
                "Pipeline termine : %d input -> %d cleaned -> %d unique "
                "(%d duplicates, %d rejected, %d errors)",
                self.stats["input"], self.stats["cleaned"], len(unique),
                self.stats["duplicates"], self.stats["rejected"], self.stats["errors"],
            )

        return unique

    # ═══════════════════════════════════════════════════════════════
    # Etape 1 : Normalisation unitaire
    # ═══════════════════════════════════════════════════════════════

    def _normalize_one(self, raw) -> Offer | None:
        """Normalise une ScrapedOffer en Offer ORM."""
        title = self._get(raw, "title")
        description = self._get(raw, "description")
        if not title and not description:
            return None

        company = self._get(raw, "company")
        url = self._get(raw, "url")
        source = self._get(raw, "source")
        source_id = self._get(raw, "source_id")
        contract_type = self._get(raw, "contract_type")
        location = self._get(raw, "location")
        region = self._get(raw, "region")
        domain = self._get(raw, "domain")
        required_level = self._get(raw, "required_level")
        salary_min = self._get_num(raw, "salary_min")
        salary_max = self._get_num(raw, "salary_max")
        published_date = self._get(raw, "published_date")
        scraped_date = self._get(raw, "scraped_at") or self._get(raw, "scraped_date")
        contact_name = self._get(raw, "contact_name")
        contact_email = self._get(raw, "contact_email")
        raw_json = self._get(raw, "raw_json")

        # Nettoyage
        title = self._clean_text(title) or "Sans titre"
        description = self._clean_description(description) or title
        company = self._clean_text(company)
        location = self._clean_location(location)
        contract_type = self._normalize_contract(contract_type)

        # Enrichissement
        is_alt_score = self._compute_alternance_score(title, description, contract_type)
        quality_score = self._compute_quality_score(title, description, company, location, url)
        search_text = self._build_search_text(title, company, domain, required_level,
                                              contract_type, location, description)

        now = datetime.now(timezone.utc).isoformat()

        return Offer(
            source=source,
            source_id=source_id or "",
            title=title,
            company=company,
            location=location,
            region=region,
            contract_type=contract_type,
            domain=domain,
            required_level=required_level,
            description=description,
            salary_min=salary_min,
            salary_max=salary_max,
            published_date=published_date,
            scraped_date=scraped_date or now,
            url=url,
            contact_name=contact_name,
            contact_email=contact_email,
            search_text=search_text,
            raw_json=raw_json,
            is_active=1,
            is_alternance=is_alt_score,
            data_quality_score=quality_score,
            cleaned_at=now,
            created_at=now,
            updated_at=now,
        )

    # ═══════════════════════════════════════════════════════════════
    # Nettoyage
    # ═══════════════════════════════════════════════════════════════

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = self._HTML_TAG_RE.sub(" ", text)
        text = unicodedata.normalize("NFKC", text)
        text = self._MULTI_SPACE_RE.sub(" ", text)
        return str(text).strip()

    def _clean_description(self, text: str) -> str:
        text = self._clean_text(text)
        return self._MULTI_NEWLINE_RE.sub("\n\n", text).strip()

    def _clean_location(self, loc: str) -> str:
        loc = self._clean_text(loc)
        return re.sub(r"\b(\d{5})\b", r"\1 ", loc).strip() if loc else ""

    def _normalize_contract(self, ct: str) -> str:
        ct_lower = (ct or "").strip().lower()
        mapping = {
            "alternance": "Alternance", "apprentissage": "Alternance",
            "contrat d'apprentissage": "Alternance", "contrat en alternance": "Alternance",
            "contrat d'alternance": "Alternance", "professionnalisation": "Alternance",
            "contrat de professionnalisation": "Alternance",
            "stage": "Stage", "cdi": "CDI", "cdd": "CDD",
            "intérim": "Intérim", "interim": "Intérim",
            "freelance": "Freelance", "temps plein": "CDI", "temps partiel": "CDD",
        }
        for key, val in mapping.items():
            if key in ct_lower:
                return val
        return ct.capitalize() if ct_lower else ""

    # ═══════════════════════════════════════════════════════════════
    # Scoring
    # ═══════════════════════════════════════════════════════════════

    def _compute_alternance_score(self, title: str, description: str, contract_type: str) -> float:
        ct_lower = (contract_type or "").lower()
        if any(t in ct_lower for t in ("alternance", "apprentissage", "professionnalisation")):
            return 1.0

        text = f"{title} {description}"
        positives = len(self._ALTERNANCE_POSITIVE.findall(text))
        negatives = len(self._NOT_ALTERNANCE_NEGATIVE.findall(text))

        if positives == 0 and negatives == 0:
            return 0.5
        return round(min(1.0, (positives / max(1, positives + negatives)) * 1.2), 2)

    def _compute_quality_score(self, title: str, description: str, company: str,
                               location: str, url: str) -> float:
        score = 1.0
        if not title:
            score -= 0.3
        if not description or len(description) < 50:
            score -= 0.3
        if not url:
            score -= 0.1
        if title and len(title) < 5:
            score -= 0.2
        if title and title.lower() in ("offre", "recrutement", "cdi", "cdd", "alternance", "stage"):
            score -= 0.3
        if description and len(description) < 20:
            score -= 0.3
        if description and self._HTML_TAG_RE.search(description):
            score -= 0.1
        if company:
            score = min(1.0, score + 0.05)
        if location:
            score = min(1.0, score + 0.05)
        if url and not url.startswith(("http://", "https://")):
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 2)

    # ═══════════════════════════════════════════════════════════════
    # Deduplication
    # ═══════════════════════════════════════════════════════════════

    def _deduplicate(self, offers: list[Offer]) -> list[Offer]:
        """Deduplication en deux passes :
        1. URL + company identiques → doublon (meme si titre varie)
        2. source + source_id identiques → doublon
        """
        seen_url_company: dict[str, Offer] = {}
        seen_sid: dict[str, Offer] = {}
        unique: list[Offer] = []

        for o in offers:
            # Cle URL + company (independante du titre)
            c = re.sub(r"\s+", " ", (o.company or "").lower().strip())
            u = (o.url or "").lower().strip().rstrip("/")
            uc_key = f"{u}|{c}"

            if uc_key in seen_url_company:
                self.stats["duplicates"] += 1
                # Garder l'offre avec la meilleure qualite
                existing = seen_url_company[uc_key]
                if (o.data_quality_score or 0) > (existing.data_quality_score or 0):
                    unique.remove(existing)
                    unique.append(o)
                    seen_url_company[uc_key] = o
                continue

            # Cle source + source_id
            if o.source and o.source_id:
                sid_key = f"{o.source}|{o.source_id}"
                if sid_key in seen_sid:
                    self.stats["duplicates"] += 1
                    continue
                seen_sid[sid_key] = o

            seen_url_company[uc_key] = o
            unique.append(o)

        return unique

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    def _get(self, obj, attr: str, default: str = "") -> str:
        if isinstance(obj, dict):
            return str(obj.get(attr, default) or default)
        return str(getattr(obj, attr, default) or default) if hasattr(obj, attr) else default

    def _get_num(self, obj, attr: str) -> float | None:
        val = self._get(obj, attr, "")
        if not val:
            return None
        try:
            return float(val.replace(",", ".").replace(" ", "").replace("€", "").replace("EUR", ""))
        except (ValueError, AttributeError):
            return None

    def _build_search_text(self, title: str, company: str, domain: str,
                           level: str, contract: str, location: str, description: str) -> str:
        parts = [p for p in [title, company, domain, level, contract, location, description] if p]
        return ". ".join(parts)[:2000]
