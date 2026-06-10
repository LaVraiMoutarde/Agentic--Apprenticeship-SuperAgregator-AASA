"""
Module embeddings — génération de vecteurs à partir du texte des offres.

Utilise sentence-transformers avec un modèle multilingue performant
(par défaut : intfloat/multilingual-e5-large, 1024-dim).

Responsabilités :
- Encodage par batchs des textes des offres
- Gestion du préfixe "passage:" / "query:" (format E5)
- Normalisation L2
- Encodage des requêtes utilisateur (format "query:")
"""

from .embedder import Embedder

__all__ = ["Embedder"]
