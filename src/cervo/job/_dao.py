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
import time
from collections.abc import Mapping, Sequence
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
    claims INTEGER NOT NULL DEFAULT 0,
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
# stamped from the job's own timeout as it starts running. :groups is a JSON
# object mapping each serialized kind to its group; a due job of a serialized
# kind is claimable only while no job sharing its group is running — one
# statement is what makes "one at a time" hold across any number of workers,
# and grouping is what lets several kinds (the Caddyfile writers) take turns
# with each other rather than only with their own kind.
_CLAIM_DUE = """
UPDATE job
SET status = 'running', times_out_at = :now + timeout, claims = claims + 1
WHERE id = (
    SELECT id FROM job
    WHERE status = 'pending'
      AND (next_attempt_at IS NULL OR next_attempt_at <= :now)
      AND (
        kind NOT IN (SELECT key FROM json_each(:groups))
        OR NOT EXISTS (
            SELECT 1 FROM job AS other
            WHERE other.status = 'running'
              AND (SELECT value FROM json_each(:groups) WHERE key = other.kind)
                = (SELECT value FROM json_each(:groups) WHERE key = job.kind)
        )
      )
    ORDER BY id
    LIMIT 1
)
RETURNING *
"""

# Finalizers only touch a job the caller still holds — running, and the same
# claim generation it was handed. A job reaped and reclaimed meanwhile matches
# neither, so the previous (zombie) holder updates nothing and learns it lost
# the lease from the empty result.
_MARK_DONE = """
UPDATE job
SET status = 'done', error = NULL, times_out_at = NULL
WHERE id = :id AND status = 'running' AND claims = :claims
RETURNING *
"""

_MARK_FAILED = """
UPDATE job
SET status = 'failed', error = :error, times_out_at = NULL, next_attempt_at = NULL
WHERE id = :id AND status = 'running' AND claims = :claims
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
WHERE id = :id AND status = 'running' AND claims = :claims
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

# Drop terminal jobs of the given kinds past a retention horizon, so their
# payloads (a file write carries up to a megabyte) do not pile up forever.
_PRUNE = """
DELETE FROM job
WHERE kind IN ({placeholders})
  AND status IN ('done', 'failed')
  AND created_at < ?
"""

# The IN () placeholders are filled per call — the number of kinds varies.
_LATEST_OF = """
SELECT * FROM job
WHERE kind IN ({placeholders}) AND payload = ?
ORDER BY id DESC
LIMIT 1
"""

# Both the kind list and the payload-field conditions are filled per call. The
# json path and value are bound, never interpolated, so callers cannot inject.
_NEWEST_ID = """
SELECT MAX(id) AS newest FROM job
WHERE kind IN ({placeholders}){conditions}
"""


def _now() -> float:
    return time.time()


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _from_row(row: sqlite3.Row) -> Job:
    return Job(**{**dict(row), "payload": json.loads(row["payload"])})


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the job table and its index if they do not exist yet."""
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_WORK_INDEX)
    # Bring a table created before the claim-generation column up to date.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job)")}
    if "claims" not in columns:
        conn.execute("ALTER TABLE job ADD COLUMN claims INTEGER NOT NULL DEFAULT 0")


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


def claim_due(
    conn: sqlite3.Connection, groups: Mapping[str, str] | None = None
) -> Job | None:
    """Take the oldest due pending job and mark it running, atomically.

    ``groups`` maps each serialized kind to its group; a due job of a
    serialized kind is skipped while another job in the same group is running,
    while every non-serialized kind keeps flowing around it.
    """
    row = conn.execute(
        _CLAIM_DUE,
        {"now": _now(), "groups": json.dumps(dict(groups or {}), sort_keys=True)},
    ).fetchone()
    return _from_row(row) if row else None


def mark_done(conn: sqlite3.Connection, job_id: int, claims: int) -> Job | None:
    """Record that the job finished. None if the lease was already lost."""
    row = conn.execute(_MARK_DONE, {"id": job_id, "claims": claims}).fetchone()
    return _from_row(row) if row else None


def mark_failed(
    conn: sqlite3.Connection, job_id: int, claims: int, error: str
) -> Job | None:
    """Fail the job for good. None if the lease was already lost."""
    row = conn.execute(
        _MARK_FAILED, {"id": job_id, "claims": claims, "error": error}
    ).fetchone()
    return _from_row(row) if row else None


def record_failure(
    conn: sqlite3.Connection,
    job_id: int,
    claims: int,
    error: str,
    max_attempts: int,
    retry_delay: int,
) -> Job | None:
    """Count a failed attempt: schedule a retry, or give up at the limit.

    None if the lease was already lost to a reap-and-reclaim.
    """
    row = conn.execute(
        _RECORD_FAILURE,
        {
            "id": job_id,
            "claims": claims,
            "error": error,
            "max_attempts": max_attempts,
            "retry_delay": retry_delay,
            "now": _now(),
        },
    ).fetchone()
    return _from_row(row) if row else None


def reap(conn: sqlite3.Connection, max_attempts: int, retry_delay: int) -> int:
    """Reclaim running jobs whose deadline passed. Returns how many."""
    return conn.execute(
        _REAP,
        {"max_attempts": max_attempts, "retry_delay": retry_delay, "now": _now()},
    ).rowcount


def prune(conn: sqlite3.Connection, kinds: Sequence[str], older_than: float) -> int:
    """Delete terminal jobs of ``kinds`` older than ``older_than`` seconds."""
    query = _PRUNE.format(placeholders=",".join("?" * len(kinds)))
    return conn.execute(query, (*kinds, _now() - older_than)).rowcount


def latest_of(
    conn: sqlite3.Connection, kinds: Sequence[str], payload: dict[str, Any]
) -> Job | None:
    """The newest job among ``kinds`` for exactly this payload, if any."""
    query = _LATEST_OF.format(placeholders=",".join("?" * len(kinds)))
    row = conn.execute(query, (*kinds, _serialize(payload))).fetchone()
    return _from_row(row) if row else None


def latest_of_grouped(
    conn: sqlite3.Connection, kinds: Sequence[str], field: str, values: Sequence[str]
) -> dict[str, Job]:
    """The newest job among ``kinds`` for each value of payload ``field``.

    One query instead of one per value — the caller (e.g. listing every site
    with its latest deployment) would otherwise fan out into an N+1. ``field``
    is a payload key supplied by code, never user input.
    """
    if not values:
        return {}
    if not field.isidentifier():  # defensive: field is always a code constant
        raise ValueError(f"unsafe payload field {field!r}")
    path = f"$.{field}"
    kinds_ph = ",".join("?" * len(kinds))
    values_ph = ",".join("?" * len(values))
    query = f"""
        SELECT job.* FROM job JOIN (
            SELECT MAX(id) AS id FROM job
            WHERE kind IN ({kinds_ph})
              AND json_extract(payload, '{path}') IN ({values_ph})
            GROUP BY json_extract(payload, '{path}')
        ) newest ON job.id = newest.id
    """
    rows = conn.execute(query, (*kinds, *values)).fetchall()
    grouped = {}
    for row in rows:
        found = _from_row(row)
        grouped[found.payload[field]] = found
    return grouped


def newest_id(
    conn: sqlite3.Connection, kinds: Sequence[str], match: dict[str, Any]
) -> int | None:
    """The id of the newest job among ``kinds`` matching these payload fields.

    ``match`` is a subset of payload keys to equal (e.g. slug and path),
    compared through ``json_extract`` — so jobs whose payloads differ in other
    fields (a file's content, say) still match. None if there is no such job.
    """
    placeholders = ",".join("?" * len(kinds))
    conditions = " AND json_extract(payload, ?) = ?" * len(match)
    query = _NEWEST_ID.format(placeholders=placeholders, conditions=conditions)
    params: list[Any] = [*kinds]
    for key, value in match.items():
        params += [f"$.{key}", value]
    row = conn.execute(query, params).fetchone()
    return row["newest"] if row and row["newest"] is not None else None
