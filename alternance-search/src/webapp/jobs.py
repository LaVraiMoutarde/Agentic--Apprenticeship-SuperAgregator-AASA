"""
Job System — gestion simple de jobs asynchrones.

Stocke les jobs dans un dict en mémoire (ou SQLite plus tard).
Chaque job a un ID unique, un type, un statut, des logs et un résultat.

Usage:
    from src.webapp.jobs import create_job, update_job, get_job

    job_id = create_job("scrape")
    update_job(job_id, status="running")
    # ... work ...
    update_job(job_id, status="done", result={"count": 42})
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    """Représente un job asynchrone."""
    id: str
    type: str                          # "scrape", "llm", "export"
    status: JobStatus = JobStatus.PENDING
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "logs": self.logs[-50:],           # last 50 lines
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ═══════════════════════════════════════════════════════════════════════
# In-memory store (thread-safe)
# ═══════════════════════════════════════════════════════════════════════

_jobs: dict[str, Job] = {}
_lock = threading.Lock()

# Maximum number of completed jobs to keep
MAX_JOBS = 100


def create_job(job_type: str) -> str:
    """Crée un nouveau job et retourne son ID."""
    job_id = uuid.uuid4().hex[:12]
    job = Job(id=job_id, type=job_type)
    with _lock:
        _jobs[job_id] = job
        _cleanup()
    return job_id


def get_job(job_id: str) -> Job | None:
    """Récupère un job par son ID."""
    with _lock:
        return _jobs.get(job_id)


def update_job(
    job_id: str,
    status: str | None = None,
    result: dict | None = None,
    error: str | None = None,
    log_line: str | None = None,
) -> Job | None:
    """Met à jour un job existant."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None

        if status:
            job.status = JobStatus(status)
            now = datetime.now(timezone.utc).isoformat()
            if status == "running" and job.started_at is None:
                job.started_at = now
            elif status in ("done", "failed"):
                job.finished_at = now

        if result is not None:
            job.result = result
        if error is not None:
            job.error = error
        if log_line:
            job.logs.append(log_line)

        return job


def list_jobs(job_type: str | None = None, limit: int = 20) -> list[dict]:
    """Liste les derniers jobs (optionnellement filtrés par type)."""
    with _lock:
        jobs = list(_jobs.values())
        if job_type:
            jobs = [j for j in jobs if j.type == job_type]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


def _cleanup() -> None:
    """Supprime les vieux jobs terminés si plus de MAX_JOBS."""
    if len(_jobs) <= MAX_JOBS:
        return
    # Remove oldest done/failed jobs first
    done_jobs = [
        (jid, j) for jid, j in _jobs.items()
        if j.status in (JobStatus.DONE, JobStatus.FAILED)
    ]
    done_jobs.sort(key=lambda x: x[1].created_at)
    excess = len(_jobs) - MAX_JOBS
    for jid, _ in done_jobs[:excess]:
        del _jobs[jid]
