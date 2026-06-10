# 🔍 Alternance Search — Recherche & Ranking Sémantique d'Offres d'Alternance

Système complet de collecte, indexation, recherche sémantique et scoring
LLM d'offres d'alternance.

---

## 📁 Architecture du projet

```
alternance-search/
├── pyproject.toml                 # Dépendances et configuration projet
├── README.md                      # ← ce fichier
│
├── config/                        # Configuration centralisée
│   ├── __init__.py                # Re-export settings
│   └── settings.py                # Classes Pydantic pour tous les réglages
│       ├── ScraperSettings        #   SCRAPER_* (delais, timeout, user-agent)
│       ├── DatabaseSettings       #   DB_* (URL SQLite, echo)
│       ├── EmbeddingSettings      #   EMBED_* (modèle, dim, batch_size)
│       ├── TurboVecSettings       #   TV_* (bit_width, chemin index)
│       ├── ScorerSettings         #   SCORER_* (provider, model, API)
│       └── ExportSettings         #   EXPORT_* (output_dir, max_rows)
│
├── src/                           # Code source
│   ├── scraper/                   # 📥 Collecte des offres
│   │   ├── __init__.py
│   │   └── base.py                # BaseScraper (ABC), RawOffer, ScraperResult
│   │
│   ├── normalizer/                # 🧹 Nettoyage & normalisation
│   │   ├── __init__.py
│   │   └── pipeline.py            # NormalizationPipeline, NormalizedOffer
│   │
│   ├── store/                     # 💾 Persistance SQLite
│   │   ├── __init__.py
│   │   ├── models.py              # Modèle ORM Offer (SQLAlchemy)
│   │   └── repository.py          # OfferRepository (CRUD + queries)
│   │
│   ├── embeddings/                # 🧠 Génération de vecteurs
│   │   ├── __init__.py
│   │   └── embedder.py            # Embedder (sentence-transformers)
│   │
│   ├── search/                    # 🔎 Recherche vectorielle
│   │   ├── __init__.py
│   │   ├── indexer.py             # Indexer (turbovec IdMapIndex)
│   │   └── retriever.py           # Retriever, SearchResult, SearchFilters
│   │
│   ├── scoring/                   # ⭐ Re-ranking LLM
│   │   ├── __init__.py
│   │   └── llm_scorer.py          # LLMScorer, CandidateProfile, ScoredOffer
│   │
│   └── export/                    # 📊 Export Excel
│       ├── __init__.py
│       └── excel.py               # ExcelExporter
│
├── scripts/                       # Points d'entrée CLI
│   ├── scrape.py                  # python -m scripts.scrape
│   ├── index.py                   # python -m scripts.index
│   └── search.py                  # python -m scripts.search
│
├── data/                          # Données (générées, .gitignore)
│   ├── offres.db                  # Base SQLite
│   └── index.tvim                 # Index turbovec sauvegardé
│
├── notebooks/                     # Exploration & analyse (Jupyter)
│   └── exploration.ipynb
│
├── exports/                       # Fichiers Excel générés
│
└── tests/                         # Tests unitaires & intégration
    ├── test_scraper/
    ├── test_normalizer/
    ├── test_store/
    ├── test_embeddings/
    ├── test_search/
    └── test_scoring/
```

---

## 🏗️ Composants principaux

### 1. Scraper (`src/scraper/`)

**Rôle** : Collecter les offres brutes depuis les sites d'emploi partenaires.

| Classe | Responsabilité |
|--------|---------------|
| `BaseScraper` (ABC) | Interface commune : `name`, `scrape(query, max_pages, location)` |
| `RawOffer` (TypedDict) | Structure de données brute, champs optionnels |
| `ScraperResult` | Résultat d'une session : offres + erreurs + stats |

**Sources cibles** :
- Indeed.fr
- LinkedIn Jobs
- HelloWork
- Welcome to the Jungle
- 1jeune1solution (gouvernement)

**Contrat** : Chaque scraper produit une `list[RawOffer]` → `NormalizationPipeline`

---

### 2. Normalizer (`src/normalizer/`)

**Rôle** : Transformer les données brutes hétérogènes en offres structurées.

| Classe | Responsabilité |
|--------|---------------|
| `NormalizedOffer` | Dataclass avec tous les champs standardisés |
| `NormalizationPipeline` | Chaîne de traitement des offres brutes |

**Étapes de normalisation** :
1. Strip HTML résiduel
2. Normalisation Unicode (NFKC)
3. Extraction regex du niveau (`BAC+2`, `BAC+3`, `BAC+5`, `Master`, `Licence`…)
4. Géocodage sommaire (ville → région via dictionnaire)
5. Standardisation du type de contrat
6. Construction du `search_text` = `"{title}. {company}. {domain}. {required_level}. {contract_type}. {location}. {description}"`

**Contrat** : `list[RawOffer]` → `list[NormalizedOffer]`

---

### 3. Store (`src/store/`)

**Rôle** : Persistance des offres en base locale SQLite.

| Classe | Responsabilité |
|--------|---------------|
| `Offer` (SQLAlchemy ORM) | Modèle de données, mapping table `offers` |
| `OfferRepository` | CRUD, requêtes filtrées, synchronisation embedding |

**Opérations clés** :
- `upsert` : déduplication par `(source, source_id)`
- `find_active(domain, level, region, contract_type)` : recherche filtrée SQL
- `get_ids_without_embedding()` : offres en attente d'indexation
- `get_all_active_ids()` : pour synchronisation turbovec

**Contrat** : `NormalizedOffer` → `Offer` (ORM) → `id` (int, utilisé comme ID externe turbovec)

---

### 4. Embeddings (`src/embeddings/`)

**Rôle** : Générer les vecteurs d'embedding à partir du `search_text`.

| Classe | Responsabilité |
|--------|---------------|
| `Embedder` | Encapsule un modèle sentence-transformers |

**Modèle recommandé** : `intfloat/multilingual-e5-large` (1024-dim)
- Format E5 : préfixe `"passage: "` pour les offres, `"query: "` pour les recherches
- Normalisation L2
- Batch encoding avec barre de progression

**Alternative FR légère** : `dangvantuan/sentence-camembert-base` (768-dim)

**Contrat** : `list[str]` → `np.ndarray (n, dim)`

---

### 5. Search (`src/search/`)

**Rôle** : Recherche vectorielle ANN via turbovec.

| Classe | Responsabilité |
|--------|---------------|
| `Indexer` | Gestion du `IdMapIndex` turbovec (add, remove, save, load, sync) |
| `Retriever` | Pipeline complet : query → embedding → turbovec → DB → SearchResult |
| `SearchResult` | Offre + score cosine + rang |
| `SearchFilters` | Filtres post-recherche (niveau, domaine, région, contrat) |

**Fonctionnement turbovec** :
- `IdMapIndex(dim=1024, bit_width=4)` : compression 4-bit, ~4× plus compact que float32
- `add_with_ids(vectors, ids)` : ajout avec IDs uint64 (= `offer.id`)
- `search(queries, k)` → `(scores, indices)` : scores = distances, indices = IDs externes
- `remove(id)` / `contains(id)` / `write(path)` / `load(path)`

**Pipeline de recherche** :
```
Query texte → Embedder.encode_query() → vecteur (1, 1024)
  → Indexer.search(k=100) → top-100 IDs + distances
  → Repository.find_by_ids(ids) → list[Offer]
  → Application des SearchFilters
  → Conversion distance → similarité cosine
  → Tri par similarité décroissante
  → SearchResponse
```

**Contrat** : `str (query)` → `SearchResponse (top-k offers)`

---

### 6. Scoring (`src/scoring/`)

**Rôle** : Re-ranking des offres par un LLM selon le profil candidat.

| Classe | Responsabilité |
|--------|---------------|
| `CandidateProfile` | Profil candidat : niveau, domaine, localisation, compétences |
| `LLMScorer` | Appel LLM, construction de prompt, parsing JSON |
| `ScoredOffer` | SearchResult + ScoreBreakdown |
| `ScoreBreakdown` | 5 axes /20 : niveau, domaine, localisation, qualité, attractivité |

**Axes de scoring (chacun /20, total /100)** :
| # | Axe | Description |
|---|-----|-------------|
| 1 | `level_match` | Adéquation entre le niveau requis et celui du candidat |
| 2 | `domain_match` | Correspondance domaine / compétences |
| 3 | `location_match` | Proximité géographique |
| 4 | `offer_quality` | Qualité de rédaction, précision, informations fournies |
| 5 | `overall_attractiveness` | Attractivité globale (entreprise, mission, salaire) |

**LLM supporté** :
- **Ollama** (local, recommandé) : `qwen2.5:7b`, `mistral:7b`, `llama3:8b`
- **OpenAI** : `gpt-4o-mini` (cloud)
- **OpenRouter** : accès multi-modèles

**Contrat** : `(CandidateProfile, list[SearchResult])` → `list[ScoredOffer]` trié par score

---

### 7. Export (`src/export/`)

**Rôle** : Générer un fichier Excel formaté avec les résultats scorés.

| Classe | Responsabilité |
|--------|---------------|
| `ExcelExporter` | Conversion pandas DataFrame → Excel via openpyxl |

**Structure du classeur** :
- **Feuille "Offres"** : tableau principal (rang, scores, titre, entreprise, lien)
- **Feuille "Détails"** : descriptions complètes + scores détaillés
- Mise en forme conditionnelle, filtres auto, colonnes ajustées

**Contrat** : `list[ScoredOffer]` → `Path (fichier .xlsx)`

---

## 🗄️ Schéma de données

### Table `offers`

| Colonne | Type | Description |
|---------|------|-------------|
| `id` | INTEGER | PK auto-incrément, utilisé comme ID externe turbovec |
| `source` | TEXT (50) | Source : "indeed", "linkedin", "hellowork"… |
| `source_id` | TEXT (255) | ID unique chez la source |
| `title` | TEXT (500) | Titre de l'offre |
| `company` | TEXT (300) | Nom de l'entreprise |
| `location` | TEXT (300) | Ville (ex: "75001 Paris") |
| `region` | TEXT (100) | Région (ex: "Île-de-France") |
| `contract_type` | TEXT (100) | "apprentissage", "professionnalisation" |
| `domain` | TEXT (200) | Domaine (ex: "informatique") |
| `required_level` | TEXT (50) | "BAC+2", "BAC+3", "BAC+5"… |
| `description` | TEXT | Texte complet de l'offre |
| `salary_min` | REAL | Salaire minimum mensuel (€) |
| `salary_max` | REAL | Salaire maximum mensuel (€) |
| `published_date` | TEXT (30) | Date de publication (format ISO) |
| `scraped_date` | TEXT (30) | Date de collecte (format ISO) |
| `url` | TEXT (2000) | Lien vers l'offre originale |
| `is_active` | INTEGER | 1=actif, 0=soft-deleted |
| `search_text` | TEXT | Texte concaténé pour l'embedding |
| `embedding_dim` | INTEGER | Dimension du vecteur (None = pas encore indexé) |
| `raw_json` | TEXT | JSON brut original (debug) |
| `created_at` | TEXT (30) | Timestamp création |
| `updated_at` | TEXT (30) | Timestamp dernière modification |

**Contrainte** : `UNIQUE(source, source_id)` → déduplication automatique.

### Flux de données complet

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FLUX DE DONNÉES                              │
│                                                                      │
│  [Sites web]                                                         │
│      │                                                               │
│      ▼                                                               │
│  ┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌─────────┐  │
│  │ SCRAPER  │ →   │ NORMALIZER  │ →   │  STORE   │ →   │ EMBED   │  │
│  │ RawOffer │     │ Normalized  │     │  Offer   │     │ vecteur │  │
│  └──────────┘     └─────────────┘     └──────────┘     └─────────┘  │
│                                                              │       │
│                                                              ▼       │
│                                                         ┌─────────┐  │
│                                                         │ INDEXER │  │
│                                                         │turbovec │  │
│                                                         └─────────┘  │
│                                                                      │
│  [Utilisateur]                                                       │
│      │                                                               │
│      ▼                                                               │
│  ┌──────────┐     ┌─────────────┐     ┌──────────┐     ┌─────────┐  │
│  │  QUERY   │ →   │  RETRIEVER  │ →   │  SCORER  │ →   │ EXPORT  │  │
│  │ "data    │     │  top-k ANN  │     │  LLM     │     │ Excel   │  │
│  │  science │     │  + filtres  │     │  re-rank │     │ .xlsx   │  │
│  │  Paris"  │     └─────────────┘     └──────────┘     └─────────┘  │
│  └──────────┘                                                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Workflow utilisateur type

### Phase 1 : Collecte

```bash
# Scraper les offres depuis les sources configurées
python -m scripts.scrape --sources indeed,linkedin --query "informatique" --max-pages 5
```

### Phase 2 : Indexation

```bash
# Normaliser + stocker + générer les embeddings + construire l'index turbovec
python -m scripts.index --build
```

### Phase 3 : Recherche & Scoring

```bash
# Recherche sémantique + scoring LLM + export Excel
python -m scripts.search \
  --query "data science alternance Paris" \
  --level "BAC+3" \
  --domain "informatique" \
  --k 20 \
  --score \
  --export
```

---

## 🔧 Installation

```bash
# 1. Cloner le projet
git clone <repo> && cd alternance-search

# 2. Créer un environnement virtuel
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# 3. Installer les dépendances
pip install -e ".[dev]"

# 4. Installer turbovec (depuis le dossier local)
pip install turbovec-0.8.1/turbovec-python/

# 5. (Optionnel) Installer Ollama pour le scoring local
# https://ollama.com
ollama pull qwen2.5:7b
```

---

## ⚙️ Configuration

Toute la configuration se fait via variables d'environnement (préfixées) :

```bash
# Base de données
export DB_URL="sqlite:///data/offres.db"

# Embeddings
export EMBED_MODEL_NAME="intfloat/multilingual-e5-large"
export EMBED_DEVICE="cpu"

# Turbovec
export TV_BIT_WIDTH=4
export TV_INDEX_PATH="data/index.tvim"

# Scoring LLM
export SCORER_PROVIDER="ollama"
export SCORER_MODEL="qwen2.5:7b"
export SCORER_BASE_URL="http://localhost:11434/v1"
```

Ou créer un fichier `.env` à la racine.

---

## 🧪 Tests

```bash
pytest tests/ -v
```

---

## 📋 Choix techniques

| Composant | Choix | Justification |
|-----------|-------|---------------|
| Base de données | **SQLite** | Zéro configuration, portable, suffisant pour < 500k offres |
| Scraping | **requests + BS4** (principal), **Playwright** (fallback JS) | BS4 pour 90% des sites, Playwright pour les sites render-side |
| Embeddings | **multilingual-e5-large** | Excellent sur le français, 1024-dim, format E5 optimisé recherche |
| Vector Search | **turbovec** (IdMapIndex) | Compression 4-bit, SIMD, mapping IDs stable, pas de serveur |
| Scoring LLM | **Ollama** (local) + OpenAI API (fallback) | Gratuit, privé, pas de limite de quota |
| Export | **pandas + openpyxl** | Formaté, familier, multi-feuilles |
| Config | **pydantic-settings** | Validation au démarrage, .env support |
| CLI | **click + rich** | Ergonomic, belles progress bars |
