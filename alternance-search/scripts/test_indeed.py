"""Test rapide du scraper Indeed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import importlib
import src.scraper.plugins.indeed
importlib.reload(src.scraper.plugins.indeed)

from src.scraper.plugins.indeed import IndeedScraper
import asyncio

s = IndeedScraper(headless=True)
result = asyncio.run(s._scrape_async("alternance", "Paris", max_pages=1))
print(f"Total: {len(result)} offres")
for o in result:
    print(f"  {o.title[:55]} | {o.company[:20]} | {o.location[:20]}")
