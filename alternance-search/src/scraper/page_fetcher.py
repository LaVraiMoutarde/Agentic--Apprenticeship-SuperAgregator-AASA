"""
Page Fetcher — récupère le texte complet d'une page d'offre d'emploi via Playwright.

Utilisé par le LLM pour analyser une offre en lisant la page réelle
(et non seulement la description tronquée stockée en base).

Usage :
    from src.scraper.page_fetcher import fetch_job_page_text
    text = fetch_job_page_text("https://fr.indeed.com/viewjob?jk=...")
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from .browser import get_browser_kwargs


# Nettoyage du texte extrait
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MAX_TEXT_LENGTH = 15_000  # caractères max à envoyer au LLM


def fetch_job_page_text(url: str, timeout_sec: int = 15) -> Optional[str]:
    """Ouvre une page d'offre d'emploi et retourne tout son texte visible.

    Args:
        url: URL complète de l'offre.
        timeout_sec: Timeout max pour le chargement de la page.

    Returns:
        Texte visible de la page (nettoyé), ou None en cas d'échec.
    """
    try:
        return asyncio.run(_fetch_async(url, timeout_sec))
    except Exception:
        return None


async def _fetch_async(url: str, timeout_sec: int) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(**get_browser_kwargs(headless=True))
        try:
            page = await browser.new_page(
                locale="fr-FR",
                viewport={"width": 1280, "height": 720},
            )

            await page.goto(url, wait_until="domcontentloaded",
                            timeout=timeout_sec * 1000)
            await page.wait_for_timeout(3000)  # laisser le JS s'exécuter

            # Extraire tout le texte visible du body
            text = await page.evaluate("""
                () => {
                    // Supprimer les éléments non pertinents
                    const skipSelectors = [
                        'script', 'style', 'noscript', 'iframe', 'svg',
                        'header nav', 'footer', '.footer', '#footer',
                        '[role="navigation"]', 'nav',
                    ];
                    const body = document.body.cloneNode(true);
                    for (const sel of skipSelectors) {
                        try {
                            body.querySelectorAll(sel).forEach(el => el.remove());
                        } catch(e) {}
                    }
                    return body.innerText || '';
                }
            """)

            return _clean_text(text)

        finally:
            await browser.close()


def _clean_text(text: str) -> str:
    """Nettoie le texte brut extrait de la page."""
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    text = text.strip()
    # Tronquer si trop long (sécurité tokens LLM)
    if len(text) > _MAX_TEXT_LENGTH:
        text = text[:_MAX_TEXT_LENGTH] + "\n… (contenu tronqué)"
    return text
