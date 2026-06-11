"""
Module central de gestion de la base de données.

Fournit :
- `init_db()` : crée le fichier SQLite et les tables si absents
- `get_engine()` : moteur SQLAlchemy (singleton)
- `get_session()` : factory de sessions

Usage :
    from src.store.database import init_db, get_session

    init_db()
    with get_session() as s:
        s.add(Offer(...))
        s.commit()
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings

# ── Singletons ──

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Retourne le moteur SQLAlchemy (créé une seule fois)."""
    global _engine
    if _engine is None:
        # Résoudre le chemin absolu du fichier SQLite
        db_url = settings.database.url
        if db_url.startswith("sqlite:///"):
            db_path = settings.project_root / db_url[len("sqlite:///"):]
            db_url = f"sqlite:///{db_path.resolve()}"

        _engine = create_engine(
            db_url,
            echo=settings.database.echo,
            # SQLite : activer WAL pour les accès concurrents
            connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        )
    return _engine


def get_session() -> Session:
    """Retourne une nouvelle session SQLAlchemy.

    Usage recommandé :
        with get_session() as session:
            ...
        # session automatiquement fermée
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine())
    return _session_factory()


def init_db() -> None:
    """Crée le dossier data/, le fichier SQLite et toutes les tables.

    Applique aussi les migrations de schéma (nouvelles colonnes).

    Idempotent : peut être appelé plusieurs fois sans effet de bord.
    """
    from .models import Base

    # Créer le dossier data/ si absent
    data_dir = settings.project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Obtenir l'engine (crée le fichier SQLite si absent)
    engine = get_engine()

    # Créer toutes les tables
    Base.metadata.create_all(engine)

    # Appliquer les migrations (nouvelles colonnes)
    _apply_migrations(engine)


def drop_db() -> None:
    """Supprime toutes les tables (⚠ destructeur, usage debug uniquement)."""
    from .models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)


def clear_db() -> int:
    """Supprime toutes les lignes de la table offers, mais garde la structure.

    Returns:
        Nombre d'offres supprimées.
    """
    from .models import Offer

    with get_session() as session:
        count = session.query(Offer).count()
        session.query(Offer).delete()
        session.commit()
        return count


def _apply_migrations(engine: Engine) -> None:
    """Applique les migrations de schéma manquantes (colonnes ajoutées)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "offers" not in inspector.get_table_names():
        return  # Table pas encore créée

    existing = {col["name"] for col in inspector.get_columns("offers")}

    migrations: dict[str, str] = {
        "embedding_score": "ALTER TABLE offers ADD COLUMN embedding_score FLOAT",
        "llm_details": "ALTER TABLE offers ADD COLUMN llm_details TEXT",
    }

    with engine.connect() as conn:
        for col_name, sql in migrations.items():
            if col_name not in existing:
                conn.execute(text(sql))
                conn.commit()
