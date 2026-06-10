# Instructions pour obtenir le fichier storage_state Moodle ENSEA

Ce dossier contient les états de session Playwright pour l'authentification.

## Obtenir un storage_state valide

1. Créer un script Python (ex: `scripts/save_auth.py`) :

```python
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # 1. Naviguer vers la page de login CAS
        await page.goto("https://moodle.ensea.fr/mod/data/view.php?id=14716")
        await page.wait_for_timeout(1000)

        # 2. Attendre que l'utilisateur se connecte manuellement
        print("Connectez-vous manuellement dans le navigateur...")
        print("Appuyez sur Entrée une fois connecté et arrivé sur la page des offres.")
        input()

        # 3. Sauvegarder l'état
        await context.storage_state(path="auth/moodle_ensea_state.json")
        print("Storage state sauvegardé dans auth/moodle_ensea_state.json")
        await browser.close()

asyncio.run(main())
```

2. Lancer le script :
```bash
python scripts/save_auth.py
```

3. Se connecter manuellement au CAS ENSEA dans la fenêtre navigateur qui s'ouvre
4. Appuyer sur Entrée une fois connecté
5. Le fichier `auth/moodle_ensea_state.json` est créé

## ⚠ Important

- Ne jamais commit ce fichier (contient des cookies de session)
- Ajouté au `.gitignore`
- Régénérer si la session expire
