"""
Script de scraping — collecte les offres via le ScraperManager.

Usage :
    python -m scripts.scrape --sources all --query "informatique" --max-pages 3
    python -m scripts.scrape --sources indeed --query "data science" --location "Paris"
    python -m scripts.scrape --sources all --query "alternance" --no-store
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
@click.option(
    "--sources", default="all",
    help="Scrapers à exécuter : 'all' ou noms séparés par des virgules",
)
@click.option("--query", default="alternance", help="Termes de recherche")
@click.option("--location", default="", help="Filtre géographique")
@click.option("--max-pages", default=3, help="Pages max par scraper")
@click.option("--store/--no-store", default=True, help="Stocker les offres en base")
def main(sources: str, query: str, location: str, max_pages: int, store: bool) -> None:
    """Scrape des offres d'alternance depuis les sources enregistrées."""
    from src.scraper import ScraperManager

    manager = ScraperManager()

    # ── Enregistrement des scrapers disponibles ──
    try:
        from src.scraper.plugins import HelloWorkScraper
        from src.scraper.plugins import IndeedScraper
        from src.scraper.plugins import IQuestaScraper
        from src.scraper.plugins import JeunesDAvenirsScraper
        from src.scraper.plugins import JobTeaserEnseaScraper
        from src.scraper.plugins import MoodleEnseaScraper
        from src.scraper.plugins import LaBonneAlternanceScraper
        from src.scraper.plugins import WTJJScraper
        manager.add(HelloWorkScraper())
        manager.add(IndeedScraper())
        manager.add(IQuestaScraper())
        manager.add(JeunesDAvenirsScraper())
        manager.add(JobTeaserEnseaScraper())
        manager.add(MoodleEnseaScraper())
        manager.add(LaBonneAlternanceScraper())
        manager.add(WTJJScraper())
    except ImportError:
        pass  # Playwright non installé
    # manager.add(IndeedScraper())   # à venir
    # manager.add(ApecScraper())     # à venir

    if not manager.registered:
        console.print(Panel.fit(
            "Aucun scraper enregistré.\n\n"
            "Ajoutez des scrapers dans le registre :\n"
            "  [bold]manager.add(IndeedScraper())[/]\n"
            "  [bold]manager.add(ApecScraper())[/]\n\n"
            "Puis relancez ce script.",
            title="[yellow]Scraper Manager vide[/]",
            border_style="yellow",
        ))
        return

    source_list: list[str] | None = None
    if sources.lower() != "all":
        source_list = [s.strip().lower() for s in sources.split(",") if s.strip()]

    console.print(f"[bold blue]Scraping :[/] query='{query}', max_pages={max_pages}")

    if store:
        results = manager.scrape_and_store(
            query=query, location=location, max_pages=max_pages, sources=source_list,
        )
    else:
        results = manager.run_all(
            query=query, location=location, max_pages=max_pages, sources=source_list,
        )

    table = Table(title="Resultats du scraping")
    table.add_column("Source", style="cyan")
    table.add_column("Statut")
    table.add_column("Offres", justify="right")
    table.add_column("Erreurs", justify="right")
    table.add_column("Duree", justify="right")

    for name, result in results.items():
        style = {"success": "green", "partial": "yellow", "failed": "red", "skipped": "dim"}
        s = style.get(result.status.value, "white")
        table.add_row(
            name,
            f"[{s}]{result.status.value}[/]",
            str(result.success_count),
            str(result.error_count),
            f"{result.duration_seconds:.1f}s" if result.duration_seconds else "--",
        )

    console.print(table)


if __name__ == "__main__":
    main()
