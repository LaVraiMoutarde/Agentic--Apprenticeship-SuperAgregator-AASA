"""
Setup de la base de données et affichage des statistiques.

Usage :
    python -m scripts.db --init         # initialise la base
    python -m scripts.db --stats        # affiche les stats
    python -m scripts.db --demo         # insère des offres de démo
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ajouter src/ au PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def cli() -> None:
    """Gestion de la base de données alternance-search."""


@cli.command()
def init() -> None:
    """Initialise la base de données (crée le fichier SQLite + tables)."""
    from src.store import init_db

    init_db()
    console.print("[green]✓ Base de données initialisée :[/] data/offres.db")


@cli.command()
def stats() -> None:
    """Affiche les statistiques de la base."""
    from src.store import OfferRepository

    repo = OfferRepository()
    s = repo.stats()

    table = Table(title="📊 Statistiques de la base")
    table.add_column("Métrique", style="cyan")
    table.add_column("Valeur", justify="right", style="green")

    table.add_row("Total offres", str(s["total_offers"]))
    table.add_row("Offres actives", str(s["active_offers"]))
    table.add_row("Avec embedding", str(s["with_embedding"]))
    table.add_row("Sans embedding", str(s["without_embedding"]))
    table.add_row("Sources", ", ".join(s["by_source"].keys()) or "—")

    console.print(table)


@cli.command()
def demo() -> None:
    """Insère 5 offres de démonstration dans la base."""
    from src.store import Offer, OfferRepository, init_db

    init_db()
    repo = OfferRepository()

    now = datetime.now(timezone.utc).isoformat()

    demo_offers = [
        Offer(
            source="demo",
            source_id="demo-001",
            title="Alternance Data Scientist — Startup IA",
            company="TechCorp",
            location="75001 Paris",
            region="Île-de-France",
            contract_type="apprentissage",
            domain="informatique",
            required_level="BAC+5",
            description=(
                "Rejoignez notre équipe data pour concevoir des modèles de ML "
                "appliqués à la détection de fraude. Stack : Python, PyTorch, SQL, AWS."
            ),
            salary_min=1400.0,
            salary_max=1800.0,
            published_date=now,
            scraped_date=now,
            url="https://example.com/offre-001",
        ),
        Offer(
            source="demo",
            source_id="demo-002",
            title="Développeur Full-Stack JavaScript en alternance",
            company="WebAgency",
            location="69001 Lyon",
            region="Auvergne-Rhône-Alpes",
            contract_type="apprentissage",
            domain="informatique",
            required_level="BAC+3",
            description=(
                "Développement d'applications web avec React, Node.js et PostgreSQL. "
                "Travail en méthode agile, code reviews, CI/CD."
            ),
            salary_min=1000.0,
            salary_max=1300.0,
            published_date=now,
            scraped_date=now,
            url="https://example.com/offre-002",
        ),
        Offer(
            source="demo",
            source_id="demo-003",
            title="Assistant Marketing Digital — Alternance",
            company="MarketPlus",
            location="33000 Bordeaux",
            region="Nouvelle-Aquitaine",
            contract_type="professionnalisation",
            domain="marketing",
            required_level="BAC+3",
            description=(
                "Gestion des campagnes Google Ads et réseaux sociaux. "
                "Analyse des performances, création de contenu, SEO."
            ),
            salary_min=800.0,
            salary_max=1000.0,
            published_date=now,
            scraped_date=now,
            url="https://example.com/offre-003",
        ),
        Offer(
            source="demo",
            source_id="demo-004",
            title="Alternance — Comptabilité et Gestion",
            company="Cabinet Dupont",
            location="59000 Lille",
            region="Hauts-de-France",
            contract_type="apprentissage",
            domain="comptabilite",
            required_level="BAC+2",
            description=(
                "Assistance à la tenue comptable, saisie des écritures, "
                "rapprochement bancaire, déclarations fiscales. Logiciel Cegid."
            ),
            salary_min=700.0,
            salary_max=900.0,
            published_date=now,
            scraped_date=now,
            url="https://example.com/offre-004",
        ),
        Offer(
            source="demo",
            source_id="demo-005",
            title="DevOps / Cloud Engineer en alternance (H/F)",
            company="CloudScale",
            location="75008 Paris",
            region="Île-de-France",
            contract_type="apprentissage",
            domain="informatique",
            required_level="BAC+5",
            description=(
                "Automatisation des déploiements, gestion d'infrastructure Kubernetes, "
                "monitoring Prometheus/Grafana, pipelines GitLab CI. "
                "Environnement AWS et GCP."
            ),
            salary_min=1600.0,
            salary_max=2000.0,
            published_date=now,
            scraped_date=now,
            url="https://example.com/offre-005",
        ),
    ]

    result = repo.upsert_batch(demo_offers)

    table = Table(title="🎯 Insertion des offres de démo")
    table.add_column("Statut", style="cyan")
    table.add_column("Nombre", justify="right", style="green")
    table.add_row("Nouvelles", str(result["new"]))
    table.add_row("Mises à jour", str(result["updated"]))
    console.print(table)

    # Afficher les offres insérées
    table2 = Table(title="📋 Offres en base")
    table2.add_column("ID", style="dim")
    table2.add_column("Titre", style="bold")
    table2.add_column("Entreprise")
    table2.add_column("Localisation")
    table2.add_column("Niveau")

    for offer in repo.find_active(limit=20):
        table2.add_row(
            str(offer.id),
            offer.title[:50],
            offer.company or "—",
            offer.location or "—",
            offer.required_level or "—",
        )

    console.print(table2)


if __name__ == "__main__":
    cli()
