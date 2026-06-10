"""
Module normalizer — nettoyage et normalisation des offres scrapees.

Transforme les ScrapedOffer brutes en Offer nettoyees, dedupliquees
et enrichies (score alternance, qualite).

Usage :
    from src.normalizer.pipeline import NormalizationPipeline

    pipe = NormalizationPipeline(log=logger)
    clean_offers = pipe.process(raw_offers)
"""

from .pipeline import NormalizationPipeline

__all__ = ["NormalizationPipeline"]
