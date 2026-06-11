"""Script utilitaire — sauvegarde un storage_state Playwright apres connexion manuelle."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _find_browser() -> str | None:
    """Detecte le chemin d'un navigateur Chromium installe."""
    candidates: list[str] = []
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        candidates = [
            "/usr/bin/brave-browser",
            "/usr/bin/google-chrome",
            "/snap/bin/brave",
            "/snap/bin/chromium",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


async def main() -> None:
    from playwright.async_api import async_playwright

    output = Path(__file__).resolve().parent.parent / "auth" / "moodle_ensea_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    browser_path = _find_browser()
    launch_kwargs: dict = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if browser_path:
        print(f"Navigateur detecte : {browser_path}")
        launch_kwargs["executable_path"] = browser_path
    else:
        print("Aucun navigateur detecte, utilisation du Chromium Playwright.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigation vers Moodle ENSEA...")
        await page.goto(
            "https://moodle.ensea.fr/mod/data/view.php?id=14716",
            wait_until="domcontentloaded",
        )

        print("\n" + "=" * 60)
        print("  CONNECTEZ-VOUS MANUELLEMENT AU CAS ENSEA")
        print("  Une fois sur la page des offres, appuyez sur Entree.")
        print("=" * 60)
        input()

        await context.storage_state(path=str(output))
        print(f"\nStorage state sauvegarde -> {output}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
