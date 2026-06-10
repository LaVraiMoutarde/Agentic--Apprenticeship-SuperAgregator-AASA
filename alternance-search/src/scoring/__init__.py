"""
PHASE 6 — LLM Scoring + Intelligent Ranking.

Composants :
- llm_scorer.CandidateProfile  : Profil candidat (runtime, pas d'embedding)
- llm_scorer.LLMScorer         : score_offer_with_llm() + score_offers_batch()
- llm_scorer.HybridRanker      : ranking hybride 0.6×embedding + 0.4×LLM
- llm_scorer.RankedOffer       : Structure de sortie finale (TASK 5)
"""

from src.scoring.llm_scorer import (
    CandidateProfile,
    HybridRanker,
    LLMScoreBreakdown,
    LLMScorer,
    OfferRisk,
    RankedOffer,
    ScoredOffer,
    ScoringStats,
)

__all__ = [
    "CandidateProfile",
    "HybridRanker",
    "LLMScoreBreakdown",
    "LLMScorer",
    "OfferRisk",
    "RankedOffer",
    "ScoredOffer",
    "ScoringStats",
]
