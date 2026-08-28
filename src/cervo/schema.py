"""Bringing the database up to date.

Each domain owns its tables; this is the one place that knows the full list.
"""

from cervo import auth, job, user, website
from cervo.db import connect


def create_tables() -> None:
    """Create every table the app needs. Safe to call on every startup."""
    with connect() as conn:
        user.create_tables(conn)  # website references it, so it goes first
        website.create_tables(conn)
        auth.create_tables(conn)
        job.create_tables(conn)
