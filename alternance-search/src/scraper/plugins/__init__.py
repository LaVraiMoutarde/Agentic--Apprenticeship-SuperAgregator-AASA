"""Scrapers concrets — implementations de BaseScraper."""

from .hellowork import HelloWorkScraper
from .indeed import IndeedScraper
from .iquesta import IQuestaScraper
from .jeunes_d_avenirs import JeunesDAvenirsScraper
from .jobteaser_ensea import JobTeaserEnseaScraper
from .la_bonne_alternance import LaBonneAlternanceScraper
from .moodle_ensea import MoodleEnseaScraper
from .wttj import WTJJScraper

__all__ = [
    "HelloWorkScraper",
    "IndeedScraper",
    "IQuestaScraper",
    "JeunesDAvenirsScraper",
    "JobTeaserEnseaScraper",
    "LaBonneAlternanceScraper",
    "MoodleEnseaScraper",
    "WTJJScraper",
]
