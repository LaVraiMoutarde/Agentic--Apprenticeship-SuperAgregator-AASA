"""
Module export — génération de fichiers Excel.

Utilise pandas + openpyxl pour produire des classeurs formatés :
- Colonnes principales visibles (titre, entreprise, score, lien)
- Fiche détaillée en seconde feuille
- Mise en forme conditionnelle (score, fraîcheur)
- Filtres automatiques sur les en-têtes
"""

from .excel import ExcelExporter

__all__ = ["ExcelExporter"]
