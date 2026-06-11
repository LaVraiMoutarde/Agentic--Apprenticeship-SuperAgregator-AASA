# ═══════════════════════════════════════════════════════════════════
# Alternance Search — Makefile
# ═══════════════════════════════════════════════════════════════════
# Usage :
#   make setup          Installe tout (venv, deps, turbovec, db)
#   make scrape         Lance le scraping multi-source
#   make index          Rebuild l'index vectoriel
#   make search         Recherche interactive
#   make serve          Lance le dashboard web (http://localhost:8000)
#   make auth           Sauvegarde auth Playwright (interactif)
#   make test           Lance les tests
#   make clean          Nettoie données, index, exports, logs
# ═══════════════════════════════════════════════════════════════════

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PORT ?= 8000

# ── Détection OS ───────────────────────────────────────────────────
ifeq ($(OS),Windows_NT)
	PYTHON := .venv/Scripts/python.exe
	PIP := .venv/Scripts/pip.exe
endif

# ═══════════════════════════════════════════════════════════════════
# Cibles principales
# ═══════════════════════════════════════════════════════════════════

.PHONY: setup
setup: venv install playwright db-init
	@echo ""
	@echo "✅ Setup terminé !"
	@echo "   Lancez 'make serve' pour le dashboard, ou 'make scrape' pour collecter des offres."

.PHONY: venv
venv:
	@if [ ! -f "$(PYTHON)" ]; then \
		echo "📦 Création du virtualenv..."; \
		python3 -m venv .venv; \
		echo "✅ Virtualenv créé."; \
	else \
		echo "✅ Virtualenv déjà présent."; \
	fi

.PHONY: install
install: venv
	@echo "📦 Installation des dépendances Python..."
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "✅ Dépendances installées."

.PHONY: playwright
playwright: venv
	@echo "🌐 Installation du navigateur Playwright..."
	$(PYTHON) -m playwright install chromium
	@echo "✅ Chromium Playwright installé."

.PHONY: db-init
db-init: venv
	@echo "🗄️  Initialisation de la base de données..."
	$(PYTHON) -m scripts.db init
	@echo "✅ Base de données initialisée."

.PHONY: db-demo
db-demo: venv
	@echo "🗄️  Insertion des offres de démonstration..."
	$(PYTHON) -m scripts.db demo

.PHONY: scrape
scrape: venv
	$(PYTHON) -m scripts.scrape --sources all --query "informatique" --max-pages 3

.PHONY: pipeline
pipeline: venv
	$(PYTHON) -m scripts.pipeline --sources all --query "alternance" --max-pages 3

.PHONY: index
index: venv
	$(PYTHON) -m scripts.index --build

.PHONY: search
search: venv
	$(PYTHON) -m scripts.search --query "$(QUERY)" --k $(K) --export --output exports/resultats.xlsx

.PHONY: serve
serve: venv
	@echo "🚀 Dashboard : http://127.0.0.1:$(PORT)"
	@echo "📖 API Docs  : http://127.0.0.1:$(PORT)/docs"
	$(PYTHON) -m uvicorn src.webapp.main:app --host 127.0.0.1 --port $(PORT) --reload

.PHONY: auth
auth: venv
	$(PYTHON) -m scripts.save_auth

.PHONY: test
test: venv
	$(PYTHON) -m pytest tests/ -v

.PHONY: clean
clean:
	@echo "🧹 Nettoyage..."
	rm -rf data/*.db data/*.tvim data/*.db-journal data/*.db-wal
	rm -rf exports/*.xlsx
	rm -rf logs/*.log
	@echo "✅ Nettoyé."

.PHONY: clean-all
clean-all: clean
	rm -rf .venv
	rm -rf __pycache__ src/**/__pycache__ scripts/__pycache__
	rm -rf *.egg-info src/*.egg-info
	@echo "✅ Nettoyage complet (venv supprimé)."
