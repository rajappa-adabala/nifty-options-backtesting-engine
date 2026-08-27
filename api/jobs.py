"""
api/jobs.py
-----------
In-memory job store for backtest runs.

Why in-memory and not straight to Postgres/Mongo for job state:
  Job status needs frequent, low-latency reads (the client polls
  GET /backtest/{id} repeatedly while a job runs). An in-memory dict
  guarded by a lock serves that perfectly for a single-process API
  server. For durability across restarts, completed jobs are also
  flushed to MongoDB (storage.save_backtest_run) — so a server restart
  loses only currently-running jobs, not the historical record.

  In a real multi-instance deployment this would move to Redis so all
  API replicas see the same job state. Documented here as a known
  scaling boundary rather than silently ignored.
"""

import threading
import uuid
from datetime import datetime
from typing import Dict, Optional

from api.schemas import JobStatus, BacktestRequest

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}


def create_job(request: BacktestRequest) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "config": request,
            "summary": None,
            "trades": [],
            "error": None,
            "created_at": datetime.utcnow(),
            "completed_at": None,
        }
    return job_id


def update_job(job_id: str, **fields) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def mark_running(job_id: str) -> None:
    update_job(job_id, status=JobStatus.RUNNING)


def mark_completed(job_id: str, summary: dict, trades: list) -> None:
    update_job(
        job_id,
        status=JobStatus.COMPLETED,
        summary=summary,
        trades=trades,
        completed_at=datetime.utcnow(),
    )


def mark_failed(job_id: str, error: str) -> None:
    update_job(
        job_id,
        status=JobStatus.FAILED,
        error=error,
        completed_at=datetime.utcnow(),
    )