"""
Helper de configuration du navigateur Playwright.

Detecte Brave, Chrome ou Edge installes localement.
Utilisation :

    from src.scraper.browser import get_browser_kwargs
    browser = await p.chromium.launch(**get_browser_kwargs())
"""

import os

# Chemins des navigateurs installes (ordre de priorite)
BROWSER_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def get_browser_kwargs(headless: bool = False) -> dict:
    """Retourne les kwargs pour p.chromium.launch().

    Args:
        headless: Mode headless (False par defaut car
                  certains sites bloquent les navigateurs headless).
    """
    kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    for path in BROWSER_PATHS:
        if os.path.exists(path):
            kwargs["executable_path"] = path
            break
    return kwargs
