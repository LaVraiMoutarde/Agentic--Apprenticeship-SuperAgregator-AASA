"""
Module store — persistance SQLite des offres via SQLAlchemy.

Responsabilités :
- Définition du schéma (modèle ORM `Offer`)
- CRUD : insert, update, upsert, soft-delete
- Requêtes filtrées : par source, date, localisation, niveau, domaine
- Déduplication : détection par (source, source_id)
"""

from .database import clear_db, drop_db, get_engine, get_session, init_db
from .models import Offer
from .repository import OfferRepository

__all__ = [
    "init_db",
    "drop_db",
    "get_session",
    "get_engine",
    "Offer",
    "OfferRepository",
]
