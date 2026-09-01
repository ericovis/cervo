"""Queuing background work and accounting for how it went.

The rules are deliberately few: a job is retried with a delay until it either
succeeds or spends its attempts, and a running job that outlives its timeout
is treated as a failed attempt — the worker holding it is presumed dead, so
this is also the crash recovery.
"""

import sqlite3
from collections.abc import Sequence
from typing import Any

from cervo.job import _dao
from cervo.job.types import Job

_MAX_ATTEMPTS = 3
_RETRY_DELAY = 30  # seconds before a failed job is due again
_DEFAULT_TIMEOUT = 300  # seconds a job may run before it is presumed dead

# Kinds declared one-at-a-time (see serialize). Filled at import time by the
# domains that own the kinds, so every claimer enforces the same rule.
_SERIALIZED: set[str] = set()


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


def serialize(kind: str) -> None:
    """Have jobs of ``kind`` run one at a time, even across many workers.

    For work every job of the kind shares — a file they all rewrite, an API
    they all reload — where two at once would trample each other. A due job
    of a serialized kind stays pending while another of the same kind runs;
    every other kind keeps flowing around it.
    """
    _SERIALIZED.add(kind)


def claim_due(conn: sqlite3.Connection) -> Job | None:
    """Take one due job and mark it running. None means nothing is due."""
    return _dao.claim_due(conn, _SERIALIZED)


def succeed(conn: sqlite3.Connection, job_id: int) -> Job:
    """Record that the job finished."""
    return _dao.mark_done(conn, job_id)


def fail(conn: sqlite3.Connection, job_id: int, error: str) -> Job:
    """Record a failed attempt; the job retries later or ends up failed."""
    return _dao.record_failure(conn, job_id, error, _MAX_ATTEMPTS, _RETRY_DELAY)


def fail_permanently(conn: sqlite3.Connection, job_id: int, error: str) -> Job:
    """Fail the job for good — for failures retrying cannot help."""
    return _dao.mark_failed(conn, job_id, error)


def reap(conn: sqlite3.Connection) -> int:
    """Reclaim timed-out running jobs. Returns how many were reclaimed."""
    return _dao.reap(conn, _MAX_ATTEMPTS, _RETRY_DELAY)


def latest_of(
    conn: sqlite3.Connection, kinds: Sequence[str], payload: dict[str, Any]
) -> Job | None:
    """The newest job among ``kinds`` for this payload, if any was queued.

    For work that runs as a chain of jobs: the newest row says how far the
    chain has come.
    """
    return _dao.latest_of(conn, kinds, payload)


def newest_id(
    conn: sqlite3.Connection, kinds: Sequence[str], match: dict[str, Any]
) -> int | None:
    """The id of the newest job among ``kinds`` matching these payload fields.

    For deciding whether a claimed job has been superseded: a later
    submission for the same target has a higher id.
    """
    return _dao.newest_id(conn, kinds, match)


def latest_of_grouped(
    conn: sqlite3.Connection, kinds: Sequence[str], field: str, values: Sequence[str]
) -> dict[str, Job]:
    """The newest job among ``kinds`` for each ``field`` value, in one query.

    For attaching chain state to a whole list at once (every site's latest
    deployment) without an N+1.
    """
    return _dao.latest_of_grouped(conn, kinds, field, values)
