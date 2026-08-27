"""Shapes for background work."""

from typing import Any, Literal

from pydantic import BaseModel

JobStatus = Literal["pending", "running", "done", "failed"]


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
