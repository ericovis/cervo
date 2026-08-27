"""SQLite queries for :class:`cervo.job.Job`.

Private to the package, hence the underscore — reach these through
``cervo.job``'s service.

Timestamps (``created_at``, ``next_attempt_at``, ``times_out_at``) are unix
epoch seconds, written and compared only here, so the arithmetic — "due
yet?", "timed out?" — stays in SQL. The payload is stored as canonical JSON
(sorted keys) so equality is a meaningful match.
"""

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cervo.job.types import Job

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    timeout INTEGER NOT NULL,
    next_attempt_at REAL,
    times_out_at REAL,
    created_at REAL NOT NULL
)
"""

_CREATE_WORK_INDEX = """
CREATE INDEX IF NOT EXISTS job_kind_payload ON job (kind, payload)
"""

_INSERT = """
INSERT INTO job (kind, payload, timeout, created_at)
VALUES (:kind, :payload, :timeout, :now)
RETURNING *
"""

# A single statement, so a job can never be claimed twice; the deadline is
# stamped from the job's own timeout as it starts running.
_CLAIM_DUE = """
UPDATE job
SET status = 'running', times_out_at = :now + timeout
WHERE id = (
    SELECT id FROM job
    WHERE status = 'pending'
      AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
    ORDER BY id
    LIMIT 1
)
RETURNING *
"""

_MARK_DONE = """
UPDATE job
SET status = 'done', error = NULL, times_out_at = NULL
WHERE id = ?
RETURNING *
"""

_MARK_FAILED = """
UPDATE job
SET status = 'failed', error = :error, times_out_at = NULL, next_attempt_at = NULL
WHERE id = :id
RETURNING *
"""

_RECORD_FAILURE = """
UPDATE job
SET attempts = attempts + 1,
    error = :error,
    times_out_at = NULL,
    status = CASE
        WHEN attempts + 1 >= :max_attempts THEN 'failed' ELSE 'pending'
    END,
    next_attempt_at = CASE
        WHEN attempts + 1 >= :max_attempts THEN NULL ELSE :now + :retry_delay
    END
WHERE id = :id
RETURNING *
"""

# A failure's bookkeeping, applied to every running job whose deadline has
# passed — the worker that claimed it is presumed dead.
_REAP = """
UPDATE job
SET attempts = attempts + 1,
    error = 'timed out',
    times_out_at = NULL,
    status = CASE
        WHEN attempts + 1 >= :max_attempts THEN 'failed' ELSE 'pending'
    END,
    next_attempt_at = CASE
        WHEN attempts + 1 >= :max_attempts THEN NULL ELSE :now + :retry_delay
    END
WHERE status = 'running' AND times_out_at <= :now
"""

_LATEST = """
SELECT * FROM job
WHERE kind = ? AND payload = ?
ORDER BY id DESC
LIMIT 1
"""

# The IN () placeholders are filled per call — the number of kinds varies.
_LATEST_OF = """
SELECT * FROM job
WHERE kind IN ({placeholders}) AND payload = ?
ORDER BY id DESC
LIMIT 1
"""


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _from_row(row: sqlite3.Row) -> Job:
    return Job(**{**dict(row), "payload": json.loads(row["payload"])})


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the job table and its index if they do not exist yet."""
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_WORK_INDEX)


def insert(
    conn: sqlite3.Connection, kind: str, payload: dict[str, Any], timeout: int
) -> Job:
    """Queue a new pending job."""
    row = conn.execute(
        _INSERT,
        {
            "kind": kind,
            "payload": _serialize(payload),
            "timeout": timeout,
            "now": _now(),
        },
    ).fetchone()
    return _from_row(row)


def claim_due(conn: sqlite3.Connection) -> Job | None:
    """Take the oldest due pending job and mark it running, atomically."""
    row = conn.execute(_CLAIM_DUE, {"now": _now()}).fetchone()
    return _from_row(row) if row else None


def mark_done(conn: sqlite3.Connection, job_id: int) -> Job:
    """Record that the job finished successfully."""
    return _from_row(conn.execute(_MARK_DONE, (job_id,)).fetchone())


def mark_failed(conn: sqlite3.Connection, job_id: int, error: str) -> Job:
    """Fail the job for good, no matter how many attempts remain."""
    row = conn.execute(_MARK_FAILED, {"id": job_id, "error": error}).fetchone()
    return _from_row(row)


def record_failure(
    conn: sqlite3.Connection,
    job_id: int,
    error: str,
    max_attempts: int,
    retry_delay: int,
) -> Job:
    """Count a failed attempt: schedule a retry, or give up at the limit."""
    row = conn.execute(
        _RECORD_FAILURE,
        {
            "id": job_id,
            "error": error,
            "max_attempts": max_attempts,
            "retry_delay": retry_delay,
            "now": _now(),
        },
    ).fetchone()
    return _from_row(row)


def reap(conn: sqlite3.Connection, max_attempts: int, retry_delay: int) -> int:
    """Reclaim running jobs whose deadline passed. Returns how many."""
    return conn.execute(
        _REAP,
        {"max_attempts": max_attempts, "retry_delay": retry_delay, "now": _now()},
    ).rowcount


def latest(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> Job | None:
    """The newest job for exactly this kind and payload, if any."""
    row = conn.execute(_LATEST, (kind, _serialize(payload))).fetchone()
    return _from_row(row) if row else None


def latest_of(
    conn: sqlite3.Connection, kinds: Sequence[str], payload: dict[str, Any]
) -> Job | None:
    """The newest job among ``kinds`` for exactly this payload, if any."""
    query = _LATEST_OF.format(placeholders=",".join("?" * len(kinds)))
    row = conn.execute(query, (*kinds, _serialize(payload))).fetchone()
    return _from_row(row) if row else None
