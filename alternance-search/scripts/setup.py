"""Setup interactif — verifie et installe tous les prerequis."""

from __future__ import annotations

import os, shutil, subprocess, sys
from pathlib import Path

# Ajouter le dossier parent de src/ au PYTHONPATH
# (pour que "from src.store import init_db" fonctionne)
_SRC_PARENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SRC_PARENT))

# Correction turbovec (parent supplementaire car hors du projet)
_TV_ROOT = Path(__file__).resolve().parent.parent.parent  # "system alternance/"
_TV_DIR = _TV_ROOT / "turbovec-0.8.1" / "turbovec-python"

G, Y, R, C, B, N = "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[0m"

def ask(question: str, default: bool = True) -> bool:
    prompt = f"  [{G}O{R}/{R}n{G}]" if default else f"  [{R}o{G}/{G}N{R}]"
    r = input(f"{B}?{N} {question} {prompt} ").strip().lower()
    return r in ("o", "oui", "yes", "y") if default else r in ("", "o", "oui", "yes", "y")

def ok(msg: str): print(f"  {G}OK{N}  {msg}")
def wrn(msg: str): print(f"  {Y}WRN{N} {msg}")
def err(msg: str): print(f"  {R}ERR{N} {msg}")
def inf(msg: str): print(f"  {C}>>{N} {msg}")
def hdr(msg: str): print(f"\n{B}{msg}{N}\n{chr(9472)*50}")

def _imp_ok(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False

def _find_pip() -> str:
    scripts = str(Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin"))
    return shutil.which("pip", path=scripts) or "pip"

def _install_rust() -> bool:
    if os.name == "nt":
        import urllib.request
        dest = Path(sys.executable).parent / "rustup-init.exe"
        urllib.request.urlretrieve("https://win.rustup.rs/x86_64", dest)
        return subprocess.run([str(dest), "-y"], capture_output=True, text=True).returncode == 0
    return subprocess.run(
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
        shell=True, capture_output=True, text=True,
    ).returncode == 0

# ═══════════════════════════════════════════════════════════════════

def check_python() -> bool:
    hdr("1. Python")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor} ({sys.executable})")
    if sys.version_info < (3, 10):
        err("Python >= 3.10 requis")
        return False
    return True

def check_packages(pip: str) -> bool:
    hdr("2. Packages Python")
    REQ = {"requests":"requests","beautifulsoup4":"bs4","lxml":"lxml","sqlalchemy":"sqlalchemy",
           "pydantic":"pydantic","pydantic-settings":"pydantic_settings","click":"click","rich":"rich",
           "pandas":"pandas","openpyxl":"openpyxl","numpy":"numpy","playwright":"playwright",
           "sentence-transformers":"sentence_transformers","openai":"openai","scikit-learn":"sklearn"}
    missing = [p for p, m in REQ.items() if not _imp_ok(m)]
    if not missing:
        ok(f"Tous les {len(REQ)} packages installes")
        return True
    wrn(f"{len(missing)} manquant(s) : {', '.join(missing)}")
    if not ask("Installer automatiquement ?"):
        return False
    r = subprocess.run([pip, "install"] + missing, capture_output=True, text=True)
    if r.returncode == 0:
        ok("Packages installes")
        return True
    err(f"Echec : {r.stderr[:300]}")
    return False

def check_turbovec(pip: str) -> bool:
    hdr("3. turbovec (vector search)")
    if _imp_ok("turbovec"):
        ok("turbovec deja installe")
        return True
    if not _TV_DIR.exists():
        wrn("Dossier turbovec introuvable — numpy fallback")
        return True
    ok(f"Dossier : {_TV_DIR}")
    if not shutil.which("rustc"):
        wrn("Rust non trouve")
        if not ask("Installer Rust (rustup) ?"):
            inf("turbovec ignore — numpy sera utilise")
            return True
        if not _install_rust():
            err("Echec Rust")
            return False
        ok("Rust installe")
    else:
        ok("Rust deja installe")
    inf("Compilation turbovec...")
    r = subprocess.run([pip, "install", str(_TV_DIR)], capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        ok("turbovec installe")
        return True
    err(f"Echec : {r.stderr[:400]}")
    inf("numpy sera utilise comme fallback")
    return True

def check_browser() -> bool:
    hdr("4. Navigateur Playwright")
    for path, name in [(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe","Brave"),
                        (r"C:\Program Files\Google\Chrome\Application\chrome.exe","Chrome")]:
        if os.path.exists(path):
            ok(f"{name} detecte")
            return True
    wrn("Aucun navigateur Chromium")
    if not ask("Telecharger Chromium Playwright (~150MB) ?"):
        wrn("Scrapers Playwright inoperants")
        return False
    r = subprocess.run([sys.executable,"-m","playwright","install","chromium"],capture_output=True,text=True,timeout=120)
    if r.returncode == 0:
        ok("Chromium installe")
        return True
    err(f"Echec : {r.stderr[:300]}")
    return False

def check_db() -> bool:
    hdr("5. Base SQLite")
    os.chdir(_SRC_PARENT)
    sys.path.insert(0, str(_SRC_PARENT))
    from src.store import init_db
    init_db()
    from src.store.database import get_engine
    from sqlalchemy import inspect
    cols = [c["name"] for c in inspect(get_engine()).get_columns("offers")]
    ok(f"Table 'offers' : {len(cols)} colonnes")
    from src.store import OfferRepository
    s = OfferRepository().stats()
    inf(f"Offres : {s['total_offers']} (actives : {s['active_offers']})")
    return True

# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    print(f"\n{B}{C}   {'='*50}\n   Alternance Search — Setup\n   {'='*50}{N}\n")
    if not check_python():
        sys.exit(1)
    pip = _find_pip()
    check_packages(pip)
    check_turbovec(pip)
    check_browser()
    check_db()
    print(f"\n{B}{'='*50}{N}\n{G}Setup termine !{N}\n")
    print(f"  Scraper : {C}python -m scripts.pipeline --sources all --query \"informatique\"{N}")
    print(f"  Index   : {C}python -m scripts.index rebuild{N}")
    print(f"  Search  : {C}python -m scripts.index search \"data science\"{N}\n")

if __name__ == "__main__":
    main()
