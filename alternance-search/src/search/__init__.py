"""
Module search — indexation et recherche vectorielle via turbovec.

Responsabilités :
- Indexer : construire et maintenir un IdMapIndex turbovec
- Rechercher : requête vectorielle → top-k IDs d'offres
- Filtrer : filtrage post-recherche (niveau, localisation, etc.)
- Synchroniser : aligner l'index avec la base (ajouts, suppressions)
"""

from .indexer import Indexer
from .retriever import Retriever, SearchResult

__all__ = ["Indexer", "Retriever", "SearchResult"]
