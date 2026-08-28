"""The transaction rule in `cervo.db.connect`.

A refusal keeps what it wrote; a crash keeps nothing. The wrong-code counter
depends on this, so it is worth pinning down directly.
"""

import pytest

from cervo import db, user, website
from cervo.db import connect
from cervo.errors import AppError
from tests.conftest import OWNER


def test_work_is_committed_on_success():
    with connect() as conn:
        website.create(conn, "kept", user.ensure(conn, OWNER))

    with connect() as conn:
        assert website.exists(conn, "kept")


def test_a_refusal_still_commits_what_it_recorded():
    with pytest.raises(AppError), connect() as conn:
        website.create(conn, "refused", user.ensure(conn, OWNER))
        raise AppError("no")

    with connect() as conn:
        assert website.exists(conn, "refused"), "AppError must not discard bookkeeping"


def test_an_unexpected_error_rolls_everything_back():
    with pytest.raises(ZeroDivisionError), connect() as conn:
        website.create(conn, "rolled-back", user.ensure(conn, OWNER))
        raise ZeroDivisionError("something genuinely broke")

    with connect() as conn:
        assert not website.exists(conn, "rolled-back")


def test_connections_run_in_wal_mode():
    """Several processes share the file; WAL is what makes that livable."""
    with connect() as conn:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    assert mode == "wal"


async def test_transact_commits_off_the_event_loop():
    """The async wrapper runs the whole transaction and hands back the result."""
    site = await db.transact(
        lambda conn: website.create(conn, "threaded", user.ensure(conn, OWNER))
    )
    assert site.slug == "threaded"
    with connect() as conn:
        assert website.exists(conn, "threaded")
