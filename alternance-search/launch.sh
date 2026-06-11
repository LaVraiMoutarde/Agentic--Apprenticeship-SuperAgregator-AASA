#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Alternance Search — Launch script (Linux / macOS)
# ═══════════════════════════════════════════════════════════════════
# Usage : ./launch.sh
#
# L'application sera disponible sur :
#   http://127.0.0.1:8000
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"
URL="http://127.0.0.1:${PORT}"

echo ""
echo "  ╔═══════════════════════════════════════════════════════════╗"
echo "  ║     Alternance Search — Super Agregator                 ║"
echo "  ║     Dashboard : ${URL}                   ║"
echo "  ║     API Docs  : ${URL}/docs              ║"
echo "  ╚═══════════════════════════════════════════════════════════╝"
echo ""

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERREUR] Environnement virtuel introuvable."
    echo ""
    echo "Créez-le avec :"
    echo "    python3 -m venv .venv"
    echo "    .venv/bin/pip install -e ."
    echo ""
    exit 1
fi

echo "[INFO] Démarrage du serveur..."
echo "[INFO] Appuyez sur Ctrl+C pour arrêter."
echo ""

.venv/bin/python -m uvicorn src.webapp.main:app --host 127.0.0.1 --port "${PORT}" --reload
