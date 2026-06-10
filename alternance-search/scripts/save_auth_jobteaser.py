"""
Sauvegarde storage_state Playwright pour JobTeaser ENSEA.

Usage :
    python -m scripts.save_auth_jobteaser

1. Ouvre un navigateur visible
2. Navigue vers JobTeaser ENSEA (redirige vers OpenID)
3. Se connecter manuellement
4. Appuyer sur Entrée → sauvegarde auth/jobteaser_ensea_state.json
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main() -> None:
    from playwright.async_api import async_playwright

    output = Path(__file__).resolve().parent.parent / "auth" / "jobteaser_ensea_state.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigation vers JobTeaser ENSEA...")
        await page.goto(
            "https://ensea.jobteaser.com/fr/job-offers?contract=alternating",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(1000)

        print()
        print("=" * 55)
        print("  CONNECTEZ-VOUS A JobTeaser (OpenID)")
        print("  Une fois sur la page des offres : Entree")
        print("=" * 55)
        input()

        await context.storage_state(path=str(output))
        print(f"\nStorage state sauvegarde -> {output}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
