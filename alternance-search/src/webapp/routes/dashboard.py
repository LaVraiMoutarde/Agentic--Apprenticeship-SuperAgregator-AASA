"""
Route dashboard — Page principale de pilotage.

Route:
    GET / → dashboard HTML

Aucune logique métier ici. La page est rendue avec des
placeholders vides, prêts à être alimentés plus tard.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

router = APIRouter()

# ── Jinja2 environment ────────────────────────────────────────────────
_templates_dir = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_templates_dir)))


# ═══════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Page dashboard principale — interface de pilotage."""
    template = _env.get_template("dashboard.html")
    html = template.render(
        project_title="Alternance Search",
        system_status="⚫ Idle",
        profile=None,
        keywords=[],
        results=[],
        total_offers=0,
        last_scrape="—",
        logs="",
    )
    return HTMLResponse(html)
