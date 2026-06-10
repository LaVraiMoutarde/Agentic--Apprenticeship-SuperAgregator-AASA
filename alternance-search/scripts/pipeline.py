"""
Pipeline global — execution complete : scraping → normalisation → stockage.

Usage :
    python -m scripts.pipeline --sources all --query "alternance" --max-pages 3
    python -m scripts.pipeline --sources indeed --query "data science" --location "Paris"
    python -m scripts.pipeline --sources iquesta --max-pages 2 --no-clean
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.command()
@click.option("--sources", default="all", help="Sources : all ou noms separes par ','")
@click.option("--query", default="alternance", help="Termes de recherche")
@click.option("--location", default="", help="Filtre geographique")
@click.option("--max-pages", default=3, help="Pages max par scraper")
@click.option("--no-clean", is_flag=True, help="Desactive la normalisation (debug)")
@click.option("--export", default="", help="Export Excel si specifie")
def main(sources: str, query: str, location: str, max_pages: int, no_clean: bool, export: str) -> None:
    """Pipeline complet : scrape → normalise → stocke → exporte."""
    from src.store import init_db, OfferRepository
    from src.scraper import ScraperManager
    from src.normalizer.pipeline import NormalizationPipeline
    from src.scraper.logging import get_scraper_logger

    logger = get_scraper_logger("pipeline")
    init_db()
    repo = OfferRepository()

    # ── Étape 1 : Enregistrer les scrapers ──
    manager = ScraperManager()
    try:
        from src.scraper.plugins import (
            IndeedScraper, IQuestaScraper, HelloWorkScraper,
            JeunesDAvenirsScraper, LaBonneAlternanceScraper,
            MoodleEnseaScraper, JobTeaserEnseaScraper, WTJJScraper,
        )
        for cls in [IndeedScraper, IQuestaScraper, HelloWorkScraper,
                     JeunesDAvenirsScraper, LaBonneAlternanceScraper,
                     MoodleEnseaScraper, JobTeaserEnseaScraper, WTJJScraper]:
            try:
                manager.add(cls())
            except Exception:
                pass
    except ImportError:
        pass

    if not manager.registered:
        console.print("[red]Aucun scraper disponible.[/]")
        return

    source_list = None if sources.lower() == "all" else [s.strip().lower() for s in sources.split(",")]
    console.print(f"[bold]Pipeline :[/] {len(manager.registered)} scrapers, query='{query}', max_pages={max_pages}")

    # ── Étape 2 : Scraping ──
    console.print("\n[bold cyan]1. Scraping...[/]")
    results = manager.scrape_and_store(
        query=query, location=location, max_pages=max_pages, sources=source_list,
    )

    total_scraped = sum(r.success_count for r in results.values())
    console.print(f"   {total_scraped} offres collectees")

    if not no_clean and total_scraped > 0:
        # ── Étape 3 : Normalisation ──
        console.print("\n[bold cyan]2. Normalisation...[/]")
        pipeline = NormalizationPipeline(log=logger)

        # Recuperer les offres brutes recentes (sans cleaned_at)
        from src.store.database import get_session
        from src.store.models import Offer

        with get_session() as s:
            raw_offers = s.query(Offer).filter(Offer.cleaned_at.is_(None)).limit(500).all()

        if raw_offers:
            clean_offers = pipeline.process(raw_offers)

            # Mettre a jour les offres avec les donnees nettoyees
            with get_session() as s:
                update_count = 0
                for clean in clean_offers:
                    existing = s.get(Offer, clean.id) if clean.id else None
                    if existing:
                        for col in ["title", "company", "location", "region",
                                     "contract_type", "description", "search_text",
                                     "is_alternance", "data_quality_score", "cleaned_at"]:
                            setattr(existing, col, getattr(clean, col))
                        update_count += 1
                s.commit()

            stats = pipeline.stats
            console.print(f"   {stats['cleaned']} nettoyees, "
                          f"{stats['duplicates']} doublons, "
                          f"{stats['rejected']} rejetees")

    # ── Étape 4 : Statistiques finales ──
    console.print("\n[bold cyan]3. Base de donnees[/]")
    db_stats = repo.stats()
    table = Table(title="📊 Statistiques finales")
    table.add_column("Métrique", style="cyan")
    table.add_column("Valeur", justify="right", style="green")
    table.add_row("Total offres", str(db_stats["total_offers"]))
    table.add_row("Offres actives", str(db_stats["active_offers"]))
    table.add_row("Avec embedding", str(db_stats["with_embedding"]))
    table.add_row("Sans embedding", str(db_stats["without_embedding"]))
    table.add_row("Sources", ", ".join(db_stats["by_source"].keys()) if db_stats["by_source"] else "—")
    console.print(table)

    if export:
        console.print(f"\n[bold green]Export Excel → {export}.xlsx[/]")
        from src.export.excel import ExcelExporter
        # Export a implementer (sort du scope actuel)

    console.print(Panel.fit("✅ Pipeline terminé", border_style="green"))


if __name__ == "__main__":
    main()
