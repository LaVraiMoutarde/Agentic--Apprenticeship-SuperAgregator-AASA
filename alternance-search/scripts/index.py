"""
Script d'indexation — construit ou met à jour l'index turbovec.

Usage :
    python -m scripts.index --build        # construction complète
    python -m scripts.index --sync         # synchronisation incrémentale
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.progress import Progress

console = Console()


@click.command()
@click.option("--build", is_flag=True, help="Construit l'index depuis zéro")
@click.option("--sync", is_flag=True, help="Synchronise l'index avec la base")
def main(build: bool, sync: bool) -> None:
    """Construit ou synchronise l'index vectoriel turbovec."""
    from config import settings

    console.print(f"[bold blue]📇 Indexation :[/] dim={settings.embedding.dim}, "
                   f"bit_width={settings.turbovec.bit_width}")

    # TODO: implémenter
    # 1. Charger/créer l'index turbovec
    # 2. Récupérer les offres sans embedding depuis la base
    # 3. Générer les embeddings par batchs
    # 4. Ajouter à l'index
    # 5. Sauvegarder

    console.print("[yellow]⚠ Script non implémenté — structure en place.[/]")


if __name__ == "__main__":
    main()
