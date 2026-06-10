"""
Module webapp — Dashboard de pilotage du système de recherche d'alternance.

Responsabilités :
- Interface web simple (FastAPI + Jinja2 + CSS minimal)
- Affichage du statut système
- Contrôles de pilotage (scraping, scoring, export)
- Visualisation des résultats de recherche
- Logs système

Ne contient AUCUNE logique métier : le webapp est une couche
de présentation pure, qui délègue tout traitement aux modules
src/* existants.

Architecture :
    routes/dashboard.py   → route "/" (dashboard HTML)
    routes/api.py         → routes API REST (à venir)
    routes/__init__.py    → agrégateur de routes

    templates/base.html   → layout Jinja2 commun
    templates/dashboard.html → page dashboard principale

    static/css/style.css  → feuille de style minimale
"""
