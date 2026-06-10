"""
Script de recherche — recherche sémantique + scoring LLM + ranking hybride + export.

PHASE 6 — Flux complet :
    1. Requête texte → embedding → recherche turbovec (top-K élargi)
    2. Filtrage optionnel (niveau, domaine, région, contrat)
    3. LLM scoring sur top-K candidats (sub-batchs optimisés)
    4. Ranking hybride (0.6×embedding + 0.4×LLM)
    5. Export Excel avec toutes les colonnes TASK 5

Usage :
    python -m scripts.search --query "data science Paris" --k 20 --score --export
    python -m scripts.search --query "développeur web" --level "BAC+3" --k 50 --score
    python -m scripts.search --query "informatique industrielle" --region "Île-de-France" --k 30 --score --export --output "top30.xlsx"
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
@click.option("--query", required=True, help="Texte de la recherche")
@click.option("--level", default="", help="Niveau visé (BAC+2, BAC+3, BAC+5…)")
@click.option("--field", default="", help="Domaine (informatique, data science…)")
@click.option("--domain", default="", help="Domaine de l'offre (filtre DB)")
@click.option("--region", default="", help="Région (Île-de-France, Auvergne-Rhône-Alpes…)")
@click.option("--contract", default="", help="Type de contrat (Alternance, Stage…)")
@click.option("--skills", default="", help="Compétences (séparées par des virgules)")
@click.option("--project", default="", help="Description du projet professionnel")
@click.option("--k", default=20, help="Nombre de résultats finaux")
@click.option("--candidates", default=200, help="Nombre de candidats envoyés au LLM (max)")
@click.option("--no-score", is_flag=True, help="Désactive le scoring LLM")
@click.option("--export", is_flag=True, help="Exporte en Excel")
@click.option("--output", default="resultats.xlsx", help="Nom du fichier Excel")
@click.option("--verbose", is_flag=True, help="Mode verbeux")
def main(
    query: str,
    level: str,
    field: str,
    domain: str,
    region: str,
    contract: str,
    skills: str,
    project: str,
    k: int,
    candidates: int,
    no_score: bool,
    export: bool,
    output: str,
    verbose: bool,
) -> None:
    """Recherche sémantique d'offres d'alternance avec scoring LLM optionnel."""
    console.print(Panel.fit(
        f"[bold blue]🔎 Recherche :[/] '{query}'  "
        f"[dim](k={k}, candidates_llm={candidates}, score_llm={not no_score})[/]",
        border_style="blue",
    ))

    # ── Étape 1 : Initialisation ──
    from config import settings
    from src.store import init_db, OfferRepository
    from src.embeddings.embedder import Embedder
    from src.search.retriever import Retriever, SearchFilters
    from src.search.indexer import Indexer
    from src.scoring.llm_scorer import (
        CandidateProfile, LLMScorer, HybridRanker, ScoringStats,
    )
    from src.export.excel import ExcelExporter

    init_db()
    repo = OfferRepository()

    # ── Étape 2 : Construire le profil candidat ──
    if not no_score:
        profile = CandidateProfile(
            current_level=level,
            target_level=level,
            field=field,
            skills=[s.strip() for s in skills.split(",") if s.strip()],
            preferred_locations=[region] if region else [],
            preferred_domains=[domain] if domain else [],
            preferred_contract=contract,
            project=project,
        )
        if verbose:
            console.print("[dim]Profil candidat :[/]")
            console.print(f"  {profile.to_prompt_text()}")
        scorer = LLMScorer()
        ranker = HybridRanker()
    else:
        profile = None

    # ── Étape 3 : Recherche sémantique (vector search) ──
    embedder = Embedder()
    indexer = Indexer()
    retriever = Retriever()
    retriever.wire(embedder, indexer, repo)

    if not indexer.load():
        console.print("[red]❌ Index non trouvé. Lancez d'abord : python -m scripts.index --build[/]")
        return

    console.print(f"[dim]Index chargé : {indexer.size} vecteurs[/]")

    # Construire les filtres DB
    filters = SearchFilters(
        required_level=level if level else None,
        domain=domain if domain else None,
        region=region if region else None,
        contract_type=contract if contract else None,
    )

    # Élargir la recherche pour le LLM (top candidates)
    search_k = max(k, candidates) + 50  # marge pour filtrage
    search_k = min(search_k, indexer.size)

    console.print(f"[dim]Recherche turbovec top-{search_k}...[/]")
    resp = retriever.search_by_text(query, top_k=search_k, filters=filters)

    if not resp.results:
        console.print("[yellow]⚠ Aucun résultat.[/]")
        return

    console.print(f"[dim]{resp.total_candidates} offres candidates, "
                   f"recherche en {resp.elapsed_ms:.0f}ms[/]")

    # ── Étape 4 : Scoring LLM (si activé) ──
    stats = ScoringStats()

    if no_score or profile is None:
        # Mode simple : pas de LLM, afficher les résultats bruts
        final_results = resp.results[:k]
        display_simple(final_results, k)

    else:
        # Pré-filtrage : LLM uniquement sur les top candidates
        llm_candidates = resp.results[:candidates]
        console.print(f"[dim]Scoring LLM sur {len(llm_candidates)} offres...[/]")

        scored = scorer.score_offers_batch(profile, llm_candidates)

        if verbose:
            console.print(f"  [dim]Appels LLM : {scorer._cache and 'cache actif' or 'pas de cache'}"
                           f" | {len(scored)} offres scorées[/]")

        # ── Étape 5 : Ranking hybride ──
        ranked = ranker.rank(scored, top_n=k)

        # ── Étape 6 : Affichage ──
        display_ranked(ranked, k)

    # ── Étape 7 : Export Excel ──
    if export:
        exporter = ExcelExporter(settings.export.output_dir)
        if no_score:
            path = exporter.export_matches(final_results, output)
        else:
            path = exporter.export_ranked_offers(ranked, output)
        console.print(f"\n[green]📊 Export Excel → {path}[/]")

    console.print(Panel.fit("✅ Recherche terminée", border_style="green"))


def display_simple(results, k: int) -> None:
    """Affiche les résultats simples (sans LLM)."""
    table = Table(title=f"Résultats (top {min(k, len(results))})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Sim.", justify="right", style="green", width=7)
    table.add_column("Titre", style="bold", max_width=50)
    table.add_column("Entreprise", max_width=25)
    table.add_column("Localisation", max_width=20)

    for i, r in enumerate(results[:k], 1):
        o = r.offer
        table.add_row(
            str(i),
            f"{r.similarity_score:.3f}",
            (o.title or "")[:48],
            (o.company or "")[:23],
            (o.location or "")[:18],
        )
    console.print(table)


def display_ranked(ranked, k: int) -> None:
    """Affiche les résultats rankés (avec LLM)."""
    table = Table(title=f"🏆 Top {min(k, len(ranked))} — Ranking Hybride")
    table.add_column("#", style="dim", width=4)
    table.add_column("Final", justify="right", style="bold cyan", width=7)
    table.add_column("Emb.", justify="right", style="green", width=7)
    table.add_column("LLM", justify="right", style="yellow", width=6)
    table.add_column("Titre", style="bold", max_width=45)
    table.add_column("Entreprise", max_width=22)
    table.add_column("Explication", style="dim", max_width=50)

    for r in ranked[:k]:
        table.add_row(
            str(r.rank),
            f"{r.final_score:.1f}",
            f"{r.embedding_score:.3f}",
            str(r.llm_global_score),
            (r.title or "")[:43],
            (r.company or "")[:20],
            (r.explanation or "")[:48],
        )
    console.print(table)


if __name__ == "__main__":
    main()
