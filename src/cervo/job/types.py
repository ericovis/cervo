"""Shapes for background work."""

from typing import Any, Literal

from pydantic import BaseModel

JobStatus = Literal["pending", "running", "done", "failed"]


class PermanentError(Exception):
    """Raised by a handler to say retrying cannot help.

    The job is failed for good on the first raise — no retries — with the
    message as its error. For failures that are verdicts, not accidents:
    content that does not validate, a site that no longer exists.
    """


class Job(BaseModel):
    """One unit of background work, run by the worker process.

    A job is retried automatically until it succeeds or runs out of
    attempts, and reclaimed if the worker dies while running it — the
    bookkeeping for both lives on the row, not on the model.
    """

    id: int
    kind: str
    payload: dict[str, Any]
    status: JobStatus = "pending"
    error: str | None = None
    attempts: int = 0
    # Incremented each time the job is claimed; a worker may only finalize a
    # job it still holds this generation of, so a reaped-and-reclaimed job
    # cannot be mutated by the previous (zombie) holder.
    claims: int = 0
