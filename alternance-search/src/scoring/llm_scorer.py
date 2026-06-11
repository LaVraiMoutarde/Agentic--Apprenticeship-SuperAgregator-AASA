"""
LLM Scorer — re-ranking intelligent des offres via LLM (Ollama ou API cloud OpenAI).

PHASE 6 : LLM SCORING + INTELLIGENT RANKING
───────────────────────────────────────────
Ce module contient toute l'intelligence de scoring et de ranking hybride.

Architecture :
  1. CandidateProfile    → profil candidat (runtime, pas d'embedding)
  2. LLMScorer           → score_offer_with_llm() + score_offers_batch()
  3. HybridRanker        → ranking final : 0.6×embedding + 0.4×LLM + top-200

Principe :
  - Le LLM reçoit le profil candidat (texte structuré) + l'offre complète
  - Il ne reçoit JAMAIS d'embedding directement
  - Pré-filtrage obligatoire : LLM uniquement sur top-K (100-300 max)
  - Optimisation coût : sub-batchs, caching, lazy evaluation

Format de sortie attendu du LLM (JSON structuré) :
  {
    "scores": [
      {
        "offer_index": 0,
        "global_score": 82,
        "technical_score": 78,
        "profile_adequacy_score": 88,
        "explanation": "Très bonne adéquation technique mais stack légèrement différente.",
        "strengths": ["Mission alignée avec le profil", "Entreprise reconnue"],
        "weaknesses": ["Stack partiellement différente", "Localisation éloignée"],
        "risks": [
          {"type": "stack", "detail": "L'offre demande Java, profil Python"},
          {"type": "entreprise", "detail": "Startup early-stage, risque de stabilité"}
        ]
      }
    ]
  }
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from config import settings
from src.search.retriever import SearchResult
from src.store.models import Offer


# ═══════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OfferRisk:
    """Risque identifié par le LLM pour une offre."""

    type: str = ""  # "stack", "entreprise", "mission", "localisation", "contrat"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "detail": self.detail}


@dataclass
class LLMScoreBreakdown:
    """Scoring LLM complet d'une offre — tous les axes sur /100."""

    global_score: int = 0  # Score global /100
    technical_score: int = 0  # Adéquation technique (stack, compétences) /100
    profile_adequacy_score: int = 0  # Adéquation au profil candidat /100
    explanation: str = ""  # Explication synthétique (2-4 phrases)
    strengths: list[str] = field(default_factory=list)  # Points forts (3 max)
    weaknesses: list[str] = field(default_factory=list)  # Points faibles (3 max)
    risks: list[OfferRisk] = field(default_factory=list)  # Risques identifiés

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_score": self.global_score,
            "technical_score": self.technical_score,
            "profile_adequacy_score": self.profile_adequacy_score,
            "explanation": self.explanation,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "risks": [r.to_dict() for r in self.risks],
        }


@dataclass
class ScoredOffer:
    """Offre avec son score LLM complet."""

    search_result: SearchResult
    llm_score: LLMScoreBreakdown

    @property
    def global_score(self) -> int:
        return self.llm_score.global_score

    @property
    def embedding_score(self) -> float:
        return self.search_result.similarity_score

    @property
    def final_score(self) -> float:
        """Score hybride final (calculé par HybridRanker)."""
        return getattr(self, "_final_score", 0.0)


@dataclass
class CandidateProfile:
    """Profil du candidat fourni au runtime — pas d'embedding ici.

    Ce profil est injecté directement dans le prompt LLM.
    Il ne dépend d'aucune donnée interne, il est purement texte.
    """

    # ── Formation ──
    current_level: str = ""  # ex: "BAC+2 (BTS SIO)"
    target_level: str = ""  # ex: "BAC+3 (BUT Informatique)"
    domain: str = ""  # ex: "Informatique, développement logiciel"

    # ── Compétences ──
    skills: list[str] = field(default_factory=list)  # ex: ["Python", "SQL", "Git"]
    tools: list[str] = field(default_factory=list)  # ex: ["VS Code", "Docker"]
    languages: list[str] = field(default_factory=list)  # ex: ["Français (natif)", "Anglais (B2)"]

    # ── Préférences ──
    preferred_locations: list[str] = field(default_factory=list)  # ex: ["Paris", "Île-de-France"]
    preferred_domains: list[str] = field(default_factory=list)  # ex: ["Data Science", "Web"]
    preferred_contract: str = ""  # ex: "Apprentissage", "Professionnalisation"
    max_distance_km: int = 0  # 0 = pas de limite

    # ── Projet professionnel ──
    project: str = ""  # Description libre du projet pro, aspirations
    constraints: str = ""  # Contraintes particulières (mobilité, rythme, etc.)

    def to_prompt_text(self) -> str:
        """Sérialise le profil en texte injectable dans un prompt LLM."""
        parts: list[str] = []

        if self.current_level or self.target_level:
            level = f"Niveau actuel : {self.current_level}" if self.current_level else ""
            target = f"Recherche : {self.target_level}" if self.target_level else ""
            parts.append(f"📚 Formation : {level} → {target}".strip())

        if self.domain:
            parts.append(f"🎯 Domaine : {self.domain}")

        if self.skills:
            parts.append(f"💻 Compétences techniques : {', '.join(self.skills)}")
        if self.tools:
            parts.append(f"🛠 Outils : {', '.join(self.tools)}")
        if self.languages:
            parts.append(f"🌐 Langues : {', '.join(self.languages)}")

        if self.preferred_locations:
            parts.append(f"📍 Localisations souhaitées : {', '.join(self.preferred_locations)}")
        if self.preferred_domains:
            parts.append(f"🏢 Domaines préférés : {', '.join(self.preferred_domains)}")
        if self.preferred_contract:
            parts.append(f"📝 Type de contrat : {self.preferred_contract}")

        if self.project:
            parts.append(f"🚀 Projet professionnel : {self.project}")
        if self.constraints:
            parts.append(f"⚠ Contraintes : {self.constraints}")

        return "\n".join(parts) if parts else "Profil non spécifié."


# ═══════════════════════════════════════════════════════════════════
# Prompt Engineering
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu es un expert en recrutement spécialisé dans l'alternance en France.
Tu évalues des offres d'alternance pour un candidat spécifique.

TON RÔLE :
Pour chaque offre, tu produis une évaluation structurée et honnête.
Tu identifies les vrais points forts ET les risques.
Tu es objectif : tu ne sur-évalues pas une offre par complaisance.

CRITÈRES D'ÉVALUATION :
1. Score technique (/100) : adéquation stack technique, outils, compétences demandées
2. Score adéquation profil (/100) : correspondance avec le niveau, le domaine, le projet du candidat
3. Score global (/100) : synthèse prenant en compte la qualité de l'offre, l'entreprise, la mission

RÈGLES STRICTES :
- Chaque score est entre 0 et 100
- Tu DOIS mentionner au moins UN point faible ou risque par offre
- Les explications doivent être concrètes (pas de « bonne offre » vague)
- Tu réponds UNIQUEMENT en JSON, sans texte avant ni après
- Si une information est absente de l'offre, tu le signales dans les faiblesses
- Une offre sans description détaillée ne peut pas dépasser 60/100 en global"""


def build_offers_context(
    offers: list[Offer],
    start_index: int = 0,
    full_texts: dict[int, str] | None = None,
) -> str:
    """Construit le contexte textuel des offres pour le prompt LLM.

    Args:
        offers: Liste d'offres à présenter au LLM.
        start_index: Index de départ pour la numérotation (pour les sub-batchs).
        full_texts: Dictionnaire optionnel {index_global: texte_complet_page}.
                    Si fourni, le texte de la page réelle est inclus en complément
                    de la description tronquée.

    Returns:
        Texte formaté décrivant toutes les offres.
    """
    full_texts = full_texts or {}
    parts: list[str] = []
    for i, offer in enumerate(offers):
        idx = start_index + i
        desc = offer.description[:1500] if offer.description else 'N/C'

        # Si on a le texte complet de la page, l'ajouter en complément
        page_text = full_texts.get(idx)
        page_section = ""
        if page_text:
            page_section = f"\nContenu complet de la page :\n{page_text[:10000]}"

        parts.append(f"""
--- Offre #{idx} ---
Titre : {offer.title or 'N/C'}
Entreprise : {offer.company or 'N/C'}
Localisation : {offer.location or 'N/C'}
Région : {offer.region or 'N/C'}
Type de contrat : {offer.contract_type or 'N/C'}
Domaine : {offer.domain or 'N/C'}
Niveau requis : {offer.required_level or 'N/C'}
Salaire : {offer.salary_display or 'N/C'}
URL : {offer.url or 'N/C'}
Description : {desc}{page_section}
---""")
    return "\n".join(parts)


def build_user_prompt(profile_text: str, offers_context: str, num_offers: int) -> str:
    """Construit le prompt utilisateur complet.

    Args:
        profile_text: Profil candidat sérialisé (CandidateProfile.to_prompt_text()).
        offers_context: Contexte des offres (build_offers_context()).
        num_offers: Nombre total d'offres dans ce batch.

    Returns:
        Prompt utilisateur complet.
    """
    return f"""
PROFIL DU CANDIDAT :
─────────────────────
{profile_text}

OFFRES À ÉVALUER ({num_offers} offres) :
────────────────────────────────────────
{offers_context}

INSTRUCTIONS :
Évalue chaque offre selon les critères définis.
Retourne UNIQUEMENT un objet JSON avec la structure suivante :

{{
  "scores": [
    {{
      "offer_index": 0,
      "global_score": 82,
      "technical_score": 78,
      "profile_adequacy_score": 88,
      "explanation": "Explication synthétique en 2-4 phrases.",
      "strengths": ["Point fort 1", "Point fort 2"],
      "weaknesses": ["Point faible 1"],
      "risks": [
        {{"type": "stack|entreprise|mission|localisation|contrat", "detail": "Description du risque"}}
      ]
    }}
  ]
}}

IMPORTANT :
- offer_index commence à 0 et correspond à l'ordre des offres ci-dessus
- Chaque score est un entier entre 0 et 100
- Au moins 1 point faible par offre
- Maximum 3 points forts, 3 points faibles par offre
- risks est optionnel (tableau vide si aucun risque)
- Types de risques valides : stack, entreprise, mission, localisation, contrat
"""


# ═══════════════════════════════════════════════════════════════════
# LLM Scorer
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoringStats:
    """Statistiques d'une session de scoring."""

    offers_scored: int = 0
    llm_calls: int = 0
    total_tokens_estimate: int = 0
    elapsed_ms: float = 0.0
    cache_hits: int = 0
    errors: int = 0


class LLMScorer:
    """Scorer utilisant un LLM pour le re-ranking intelligent.

    TASK 1 — score_offer_with_llm(profile, offer) → LLMScoreBreakdown
    TASK 3 — score_offers_batch(profile, offers) → list[ScoredOffer]

    Optimisations :
    - Sub-batchs configurables (max_offers_per_call)
    - Caching optionnel (hash du profil + offre)
    - Timeout configurable
    - Gestion des erreurs par offre
    """

    def __init__(self) -> None:
        self.provider: str = settings.scorer.provider
        self.model: str = settings.scorer.model
        self.base_url: str = settings.scorer.base_url
        self.temperature: float = settings.scorer.temperature
        self.max_offers_per_call: int = settings.scorer.max_offers_per_call
        self.timeout_sec: float = settings.scorer.timeout_sec
        self._client: Any = None
        self._cache: dict[str, LLMScoreBreakdown] = {}

    # ── TASK 1 : Score d'une offre unique ──

    def score_offer_with_llm(
        self,
        profile: CandidateProfile,
        offer: Offer,
        full_page_text: str | None = None,
    ) -> LLMScoreBreakdown:
        """Score une offre unique avec le LLM.

        Le LLM ne reçoit PAS d'embedding, uniquement :
        - Le profil candidat (texte structuré)
        - L'offre complète (tous les champs textuels)

        Args:
            profile: Profil du candidat (runtime, pas de données internes).
            offer: L'offre à évaluer.
            full_page_text: Texte complet de la page web de l'offre (optionnel).
                            Si fourni, le LLM aura accès au contenu réel de la page
                            en plus de la description tronquée.

        Returns:
            Score LLM complet avec explications, forces, faiblesses, risques.
        """
        # Vérifier le cache
        cache_key = self._cache_key(profile, offer)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Construire le prompt pour une offre unique
        profile_text = profile.to_prompt_text()
        full_texts = {0: full_page_text} if full_page_text else None
        offers_context = build_offers_context([offer], full_texts=full_texts)
        prompt = build_user_prompt(profile_text, offers_context, 1)

        # Appeler le LLM
        raw = self._call_llm(prompt)
        scores = self._parse_response(raw, 1)

        if not scores:
            return LLMScoreBreakdown(
                global_score=0,
                explanation="Erreur LLM : impossible de parser la réponse.",
                weaknesses=["Erreur de scoring"],
            )

        result = scores[0]

        # Mettre en cache
        self._cache[cache_key] = result
        return result

    # ── TASK 3 : Batch scoring ──

    def score_offers_batch(
        self,
        profile: CandidateProfile,
        results: list[SearchResult],
        use_cache: bool = True,
    ) -> list[ScoredOffer]:
        """Score un batch d'offres par sous-batchs pour limiter le coût LLM.

        Stratégie :
        1. Découper en sub-batchs de max_offers_per_call
        2. Pour chaque sub-batch, construire un prompt avec toutes les offres
        3. Parser la réponse JSON structurée
        4. Assembler les résultats

        Args:
            profile: Profil du candidat.
            results: Résultats de recherche sémantique (déjà pré-filtrés !).
            use_cache: Active le cache (hash profil+offre).

        Returns:
            Liste de ScoredOffer, dans l'ordre d'entrée.
        """
        t0 = time.monotonic()
        stats = ScoringStats(offers_scored=len(results))

        if not results:
            return []

        profile_text = profile.to_prompt_text()
        scored_map: dict[int, ScoredOffer] = {}

        # Découper en sous-batchs
        batch_size = self.max_offers_per_call
        for batch_start in range(0, len(results), batch_size):
            batch_results = results[batch_start : batch_start + batch_size]
            batch_offers = [r.offer for r in batch_results]

            # Vérifier le cache pour ce sous-batch
            if use_cache:
                cached, remaining = self._check_batch_cache(profile, batch_results, batch_start)
                for sr in cached:
                    scored_map[id(sr.search_result)] = sr
                if not remaining:
                    stats.cache_hits += len(batch_results)
                    continue
                batch_results = remaining
                batch_offers = [r.offer for r in batch_results]
                stats.cache_hits += len(cached)

            # Construire le prompt
            offers_context = build_offers_context(batch_offers, batch_start)
            prompt = build_user_prompt(profile_text, offers_context, len(batch_offers))

            # Appeler le LLM
            try:
                raw = self._call_llm(prompt)
                scores = self._parse_response(raw, len(batch_offers))
                stats.llm_calls += 1
            except Exception:
                stats.errors += len(batch_offers)
                # Fallback : scores vides
                scores = [
                    LLMScoreBreakdown(
                        global_score=0,
                        explanation="Erreur LLM lors du scoring batch.",
                        weaknesses=["Erreur technique"],
                    )
                    for _ in batch_offers
                ]

            # Assembler
            for i, sr in enumerate(batch_results):
                if i < len(scores):
                    scored = ScoredOffer(search_result=sr, llm_score=scores[i])
                else:
                    scored = ScoredOffer(
                        search_result=sr,
                        llm_score=LLMScoreBreakdown(
                            global_score=0,
                            explanation="Index manquant dans la réponse LLM.",
                            weaknesses=["Erreur de parsing"],
                        ),
                    )
                scored_map[id(sr)] = scored

                # Mettre en cache
                if use_cache:
                    cache_key = self._cache_key(profile, sr.offer)
                    self._cache[cache_key] = scores[i] if i < len(scores) else scored.llm_score

        stats.elapsed_ms = (time.monotonic() - t0) * 1000

        # Retourner dans l'ordre d'entrée
        return [scored_map[id(r)] for r in results if id(r) in scored_map]

    # ── Internals ──

    def _call_llm(self, prompt: str) -> str:
        """Appelle le LLM (Ollama ou API OpenAI-compatible).

        Utilise le client OpenAI-compatible configuré dans settings.scorer.
        """
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                timeout=self.timeout_sec,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc

    def _get_client(self) -> Any:
        """Retourne le client OpenAI (lazy init)."""
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key="ollama")
            return self._client
        except ImportError:
            raise RuntimeError("pip install openai")

    def _parse_response(self, raw: str, n_offers: int) -> list[LLMScoreBreakdown]:
        """Parse la réponse JSON du LLM en liste de LLMScoreBreakdown.

        Robuste : nettoie le markdown, extrait le JSON, valide la structure.
        """
        # Nettoyer la réponse (code blocks markdown, etc.)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Essayer d'extraire un objet JSON partiel
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._fallback_scores(n_offers)
            else:
                return self._fallback_scores(n_offers)

        if not isinstance(data, dict) or "scores" not in data:
            return self._fallback_scores(n_offers)

        scores_raw = data["scores"]
        if not isinstance(scores_raw, list):
            return self._fallback_scores(n_offers)

        results: list[LLMScoreBreakdown] = []
        for item in scores_raw[:n_offers]:
            try:
                risks = [
                    OfferRisk(type=r.get("type", ""), detail=r.get("detail", ""))
                    for r in item.get("risks", [])
                ]
                score = LLMScoreBreakdown(
                    global_score=max(0, min(100, int(item.get("global_score", 0)))),
                    technical_score=max(0, min(100, int(item.get("technical_score", 0)))),
                    profile_adequacy_score=max(0, min(100, int(item.get("profile_adequacy_score", 0)))),
                    explanation=str(item.get("explanation", ""))[:500],
                    strengths=[str(s)[:200] for s in item.get("strengths", [])[:3]],
                    weaknesses=[str(w)[:200] for w in item.get("weaknesses", [])[:3]],
                    risks=risks[:5],
                )
                results.append(score)
            except Exception:
                results.append(self._fallback_single())

        # Pad avec fallback si moins de résultats que d'offres
        while len(results) < n_offers:
            results.append(self._fallback_single())

        return results

    def _cache_key(self, profile: CandidateProfile, offer: Offer) -> str:
        """Génère une clé de cache déterministe."""
        raw = (
            f"{profile.current_level}|{profile.target_level}|{profile.domain}|"
            f"{','.join(sorted(profile.skills))}|"
            f"{offer.source}|{offer.source_id}|{offer.title}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _check_batch_cache(
        self, profile: CandidateProfile, results: list[SearchResult], start_idx: int
    ) -> tuple[list[ScoredOffer], list[SearchResult]]:
        """Vérifie le cache pour un batch. Retourne (hits, misses)."""
        cached: list[ScoredOffer] = []
        remaining: list[SearchResult] = []
        for sr in results:
            key = self._cache_key(profile, sr.offer)
            if key in self._cache:
                cached.append(ScoredOffer(search_result=sr, llm_score=self._cache[key]))
            else:
                remaining.append(sr)
        return cached, remaining

    def clear_cache(self) -> None:
        """Vide le cache interne."""
        self._cache.clear()

    @staticmethod
    def _fallback_scores(n: int) -> list[LLMScoreBreakdown]:
        return [LLMScoreBreakdown(
            global_score=0,
            explanation="Erreur : réponse LLM invalide.",
            weaknesses=["Erreur de parsing JSON"],
        ) for _ in range(n)]

    @staticmethod
    def _fallback_single() -> LLMScoreBreakdown:
        return LLMScoreBreakdown(
            global_score=0,
            explanation="Erreur de parsing.",
            weaknesses=["Donnée corrompue"],
        )


# ═══════════════════════════════════════════════════════════════════
# TASK 4 : Hybrid Ranking
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RankedOffer:
    """Offre rankée avec tous les scores (TASK 5 output)."""

    # ── Identité ──
    title: str
    company: str
    location: str
    url: str
    source: str
    contract_type: str

    # ── Scores ──
    embedding_score: float  # Similarité cosinus (0..1)
    llm_global_score: int  # Score global LLM (0..100)
    llm_technical_score: int  # Score technique LLM (0..100)
    llm_profile_score: int  # Score adéquation profil LLM (0..100)
    final_score: float  # Score hybride final (0..100)

    # ── Explication ──
    explanation: str
    strengths: list[str]
    weaknesses: list[str]
    risks: list[dict[str, str]]

    # ── Métadonnées ──
    rank: int
    offer_id: int
    description_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "contract_type": self.contract_type,
            "embedding_score": round(self.embedding_score, 4),
            "llm_global_score": self.llm_global_score,
            "llm_technical_score": self.llm_technical_score,
            "llm_profile_score": self.llm_profile_score,
            "final_score": round(self.final_score, 2),
            "explanation": self.explanation,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "risks": self.risks,
            "offer_id": self.offer_id,
            "description_snippet": self.description_snippet,
        }


class HybridRanker:
    """Ranking hybride combinant embedding + LLM.

    Formule :
      final_score = 0.6 × normalized_embedding_score + 0.4 × (llm_score / 100)

    L'embedding_score est normalisé dans [0, 1] par min-max scaling sur le batch.
    Le llm_global_score est déjà dans [0, 100], divisé par 100 pour normalisation.

    Sortie :
    - Top 200 offres triées par final_score décroissant
    """

    EMBEDDING_WEIGHT: float = 0.6
    LLM_WEIGHT: float = 0.4
    MAX_RESULTS: int = 200

    def rank(
        self,
        scored_offers: list[ScoredOffer],
        top_n: int = MAX_RESULTS,
    ) -> list[RankedOffer]:
        """Calcule le score hybride et retourne le top-N.

        Args:
            scored_offers: Offres scorées par le LLM (toutes).
            top_n: Nombre maximum de résultats à retourner.

        Returns:
            Liste de RankedOffer triée par final_score décroissant.
        """
        if not scored_offers:
            return []

        # Normaliser les embedding scores dans [0, 1]
        emb_scores = [so.embedding_score for so in scored_offers]
        emb_min = min(emb_scores)
        emb_max = max(emb_scores)
        emb_range = emb_max - emb_min if emb_max > emb_min else 1.0

        ranked: list[RankedOffer] = []
        for so in scored_offers:
            offer = so.search_result.offer

            # Normaliser l'embedding score
            emb_norm = (so.embedding_score - emb_min) / emb_range

            # Score LLM normalisé dans [0, 1]
            llm_norm = so.global_score / 100.0

            # Score hybride final
            final = (
                self.EMBEDDING_WEIGHT * emb_norm
                + self.LLM_WEIGHT * llm_norm
            ) * 100  # Scale 0..100

            ranked.append(RankedOffer(
                title=offer.title or "N/C",
                company=offer.company or "N/C",
                location=offer.location or "N/C",
                url=offer.url or "",
                source=offer.source or "",
                contract_type=offer.contract_type or "",
                embedding_score=so.embedding_score,
                llm_global_score=so.global_score,
                llm_technical_score=so.llm_score.technical_score,
                llm_profile_score=so.llm_score.profile_adequacy_score,
                final_score=final,
                explanation=so.llm_score.explanation,
                strengths=so.llm_score.strengths,
                weaknesses=so.llm_score.weaknesses,
                risks=[r.to_dict() for r in so.llm_score.risks],
                rank=0,
                offer_id=offer.id,
                description_snippet=(offer.description or "")[:200],
            ))

        # Trier par score final décroissant
        ranked.sort(key=lambda r: r.final_score, reverse=True)

        # Limiter au top-N
        ranked = ranked[:top_n]

        # Assigner les rangs
        for i, r in enumerate(ranked):
            r.rank = i + 1

        return ranked
