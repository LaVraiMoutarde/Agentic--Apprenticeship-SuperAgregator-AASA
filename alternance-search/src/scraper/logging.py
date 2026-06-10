"""
Système de logging dédié au scraping.

Chaque scraper obtient son propre logger via `get_scraper_logger(name)`.
Les logs sont séparés par scraper et incluent automatiquement :
- Timestamp ISO 8601
- Niveau (DEBUG, INFO, WARNING, ERROR)
- Nom du scraper
- Message

Usage :
    from src.scraper.logging import get_scraper_logger

    logger = get_scraper_logger("indeed")
    logger.info("Début du scraping — query='data science'")
    logger.error("Page 3 inaccessible", exc_info=True)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── Format commun à tous les loggers scraper ──

_SCRAPER_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(scraper_name)-12s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _setup_root_scraper_logger() -> logging.Logger:
    """Configure le logger racine du module scraper (appelé une seule fois)."""
    root = logging.getLogger("scraper")
    root.setLevel(logging.DEBUG)

    # Éviter la propagation vers le logger racine Python
    root.propagate = False

    # Handler console (stderr)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(_SCRAPER_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(console)

    # Handler fichier (logs/scraper.log)
    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "scraper.log"

    if not any(
        isinstance(h, logging.FileHandler)
        and h.baseFilename == str(log_file.resolve())
        for h in root.handlers
    ):
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_SCRAPER_LOG_FORMAT, _DATE_FORMAT))
        root.addHandler(file_handler)

    return root


# Initialisation unique
_root_logger = _setup_root_scraper_logger()


class _ScraperAdapter(logging.LoggerAdapter):
    """Adaptateur qui injecte automatiquement `scraper_name` dans les logs."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["scraper_name"] = self.extra.get("scraper_name", "unknown")
        return msg, kwargs


def get_scraper_logger(scraper_name: str) -> logging.LoggerAdapter:
    """Retourne un logger préfixé par le nom du scraper.

    Args:
        scraper_name: Identifiant unique du scraper (ex: "indeed", "linkedin").

    Returns:
        LoggerAdapter configuré avec les handlers console + fichier.

    Example:
        >>> logger = get_scraper_logger("indeed")
        >>> logger.info("Scraping page 1/5")
        2026-06-10 14:30:01 | INFO     | indeed      | Scraping page 1/5
    """
    child = _root_logger.getChild(scraper_name)
    adapter = _ScraperAdapter(child, {"scraper_name": scraper_name})
    return adapter


def set_scraper_level(scraper_name: str, level: int) -> None:
    """Change le niveau de log d'un scraper spécifique.

    Args:
        scraper_name: Nom du scraper.
        level: Niveau logging (ex: logging.DEBUG).
    """
    logger = logging.getLogger(f"scraper.{scraper_name}")
    logger.setLevel(level)


def set_global_level(level: int) -> None:
    """Change le niveau de log global de tous les scrapers."""
    _root_logger.setLevel(level)
