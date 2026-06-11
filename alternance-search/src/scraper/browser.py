"""
Helper de configuration du navigateur Playwright.

Detecte Brave, Chrome ou Edge installes localement (Windows, Linux, macOS).
Priorite : variable d'environnement BROWSER_EXECUTABLE_PATH, puis auto-detection.

Utilisation :

    from src.scraper.browser import get_browser_kwargs
    browser = await p.chromium.launch(**get_browser_kwargs())
"""

from __future__ import annotations

import os
import sys

# Chemins des navigateurs installes (ordre de priorite)
_BROWSER_PATHS: list[str] = []

if sys.platform == "win32":
    _BROWSER_PATHS = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
elif sys.platform == "darwin":
    _BROWSER_PATHS = [
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
else:  # Linux
    _BROWSER_PATHS = [
        "/usr/bin/brave-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/microsoft-edge",
        "/snap/bin/brave",
        "/snap/bin/chromium",
    ]


def get_browser_kwargs(headless: bool = False) -> dict:
    """Retourne les kwargs pour p.chromium.launch().

    Args:
        headless: Mode headless (False par defaut car
                  certains sites bloquent les navigateurs headless).
    """
    kwargs: dict = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }

    # Priorite : variable d'environnement BROWSER_EXECUTABLE_PATH
    from config import settings
    if settings.browser.executable_path:
        kwargs["executable_path"] = settings.browser.executable_path
        return kwargs

    for path in _BROWSER_PATHS:
        if os.path.exists(path):
            kwargs["executable_path"] = path
            break
    return kwargs
