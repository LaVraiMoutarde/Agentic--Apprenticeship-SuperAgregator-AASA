"""
Script utilitaire — sauvegarde un storage_state Playwright après connexion manuelle.

Usage :
    python -m scripts.save_auth

Ce script ouvre un navigateur Chromium visible, navigue vers la page
Moodle ENSEA, et attend que l'utilisateur se connecte manuellement au CAS.
Une fois connecté, appuyer sur Entrée pour sauvegarder l'état de session.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main() -> None:
    from playwright.async_api import async_playwright

    output = Path(__file__).resolve().parent.parent / "auth" / "moodle_ensea_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigation vers Moodle ENSEA...")
        await page.goto(
            "https://moodle.ensea.fr/mod/data/view.php?id=14716",
            wait_until="domcontentloaded",
        )

        print("\n" + "=" * 60)
        print("  CONNECTEZ-VOUS MANUELLEMENT AU CAS ENSEA")
        print("  Une fois sur la page des offres, appuyez sur Entrée.")
        print("=" * 60)
        input()

        await context.storage_state(path=str(output))
        print(f"\nStorage state sauvegardé → {output}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
