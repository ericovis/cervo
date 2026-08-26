"""The transaction rule in `cervo.db.connect`.

A refusal keeps what it wrote; a crash keeps nothing. The wrong-code counter
depends on this, so it is worth pinning down directly.
"""

import pytest

from cervo import user, website
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
