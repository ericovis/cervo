"""Queuing background work and accounting for how it went.

The rules are deliberately few: a job is retried with a delay until it either
succeeds or spends its attempts, and a running job that outlives its timeout
is treated as a failed attempt — the worker holding it is presumed dead, so
this is also the crash recovery.
"""

import sqlite3
from typing import Any

from cervo.job import _dao
from cervo.job.types import Job

_MAX_ATTEMPTS = 3
_RETRY_DELAY = 30  # seconds before a failed job is due again
_DEFAULT_TIMEOUT = 300  # seconds a job may run before it is presumed dead


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict[str, Any],
    timeout: int = _DEFAULT_TIMEOUT,
) -> Job:
    """Queue work for the worker process to pick up."""
    return _dao.insert(conn, kind, payload, timeout)


def claim_due(conn: sqlite3.Connection) -> Job | None:
    """Take one due job and mark it running. None means nothing is due."""
    return _dao.claim_due(conn)


def succeed(conn: sqlite3.Connection, job_id: int) -> Job:
    """Record that the job finished."""
    return _dao.mark_done(conn, job_id)


def fail(conn: sqlite3.Connection, job_id: int, error: str) -> Job:
    """Record a failed attempt; the job retries later or ends up failed."""
    return _dao.record_failure(conn, job_id, error, _MAX_ATTEMPTS, _RETRY_DELAY)


def reap(conn: sqlite3.Connection) -> int:
    """Reclaim timed-out running jobs. Returns how many were reclaimed."""
    return _dao.reap(conn, _MAX_ATTEMPTS, _RETRY_DELAY)


def latest(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> Job | None:
    """The current state of this piece of work, if it was ever queued."""
    return _dao.latest(conn, kind, payload)
