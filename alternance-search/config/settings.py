"""
Configuration centralisée de l'application alternance-search.

Toute la configuration est pilotée par des variables d'environnement,
avec des valeurs par défaut adaptées au développement local.

Usage :
    from config import settings
    db_url = settings.database.url
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class ScraperSettings(BaseSettings):
    """Configuration des scrapers."""

    model_config = {"env_prefix": "SCRAPER_"}

    min_delay_sec: float = 2.0
    request_timeout_sec: float = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
    max_offers_per_source: int = 500


class DatabaseSettings(BaseSettings):
    """Configuration de la base de données."""

    model_config = {"env_prefix": "DB_"}

    url: str = "sqlite:///data/offres.db"
    echo: bool = False

    @property
    def path(self) -> Path:
        """Chemin absolu vers le fichier SQLite, résolu depuis ce fichier config."""
        if self.url.startswith("sqlite:///"):
            rel = self.url[len("sqlite:///"):]
            return (Path(__file__).resolve().parent.parent / rel).resolve()
        return Path(self.url)


class EmbeddingSettings(BaseSettings):
    """Configuration du modèle d'embeddings."""

    model_config = {"env_prefix": "EMBED_"}

    model_name: str = "intfloat/multilingual-e5-large"
    dim: int = 1024
    batch_size: int = 32
    device: str = "cpu"
    normalize: bool = True
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "


class TurboVecSettings(BaseSettings):
    """Configuration de l'index turbovec."""

    model_config = {"env_prefix": "TV_"}

    bit_width: int = 4
    index_path: str = "data/index.tvim"


class BrowserSettings(BaseSettings):
    """Configuration du navigateur pour le scraping."""

    model_config = {"env_prefix": "BROWSER_"}

    # Chemin vers un navigateur Chromium existant (Brave, Chrome, Edge…)
    # Si vide, utilise le Chromium installe par Playwright
    executable_path: str = ""

    # Mode headless
    headless: bool = True

    # Delai de politesse entre les requetes (secondes)
    min_delay_sec: float = 1.0
    max_delay_sec: float = 3.0


class ScorerSettings(BaseSettings):
    """Configuration du scoring LLM."""

    model_config = {"env_prefix": "SCORER_"}

    provider: str = "ollama"
    model: str = "qwen2.5:7b"
    base_url: str = "http://localhost:11434/v1"
    temperature: float = 0.0
    max_offers_per_call: int = 20
    timeout_sec: float = 60.0
    max_llm_candidates: int = 50  # Nombre max d'offres envoyées au LLM (par run)


class ExportSettings(BaseSettings):
    """Configuration de l'export Excel."""

    model_config = {"env_prefix": "EXPORT_"}

    output_dir: str = "exports/"
    max_rows_per_file: int = 10_000


class Settings(BaseSettings):
    """Configuration racine."""

    model_config = {"env_prefix": "APP_"}

    project_root: Path = Path(__file__).resolve().parent.parent

    scraper: ScraperSettings = ScraperSettings()
    database: DatabaseSettings = DatabaseSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    turbovec: TurboVecSettings = TurboVecSettings()
    scorer: ScorerSettings = ScorerSettings()
    export: ExportSettings = ExportSettings()
    browser: BrowserSettings = BrowserSettings()


# Singleton
settings = Settings()
