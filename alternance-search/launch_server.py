#!/usr/bin/env python3
"""Launch script for uvicorn server."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import uvicorn
uvicorn.run("src.webapp.main:app", host="127.0.0.1", port=8002, log_level="info")
