"""Background work: queued in the database, run by the worker process.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.job.service import (
    claim_due,
    create_tables,
    enqueue,
    fail,
    fail_permanently,
    latest_of,
    latest_of_grouped,
    newest_id,
    reap,
    serialize,
    succeed,
)
from cervo.job.types import Job, JobStatus, PermanentError

__all__ = [
    "Job",
    "JobStatus",
    "PermanentError",
    "claim_due",
    "create_tables",
    "enqueue",
    "fail",
    "fail_permanently",
    "latest_of",
    "latest_of_grouped",
    "newest_id",
    "reap",
    "serialize",
    "succeed",
]
