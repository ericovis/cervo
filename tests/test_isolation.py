"""The guarantees the rest of the suite leans on.

Every test runs against a throwaway database and a fake caddy admin API. If
these fail, treat results from the other files as suspect.
"""

from cervo import caddy, config
from cervo.db import connect
from tests.conftest import OWNER, call, chat


def test_the_database_is_a_throwaway(tmp_path):
    assert config.DATABASE_PATH.parent.parent == tmp_path
    assert config.DATA_DIR.parent == tmp_path


def test_the_development_database_is_never_touched():
    """The repo's own .data must be nowhere near the configured path."""
    development = (config.DATA_DIR.parents[-1] / "cervo" / ".data").resolve()
    assert not config.DATABASE_PATH.is_relative_to(development)
    assert "Code/cervo/.data" not in str(config.DATABASE_PATH)


def test_each_test_gets_an_empty_database():
    """State cannot leak between tests; the sibling below writes the same row."""
    with connect() as conn:
        assert conn.execute("SELECT count(*) c FROM user").fetchone()["c"] == 0
        conn.execute("INSERT INTO user (email) VALUES (?)", (OWNER,))


def test_each_test_gets_an_empty_database_again():
    with connect() as conn:
        assert conn.execute("SELECT count(*) c FROM user").fetchone()["c"] == 0


def test_caddy_is_never_reached(caddy_reloads):
    """`caddy.reload` is replaced, so nothing can reach the admin API."""
    assert caddy.reload.__name__ == "fake_reload"
    caddy.reload()
    assert caddy_reloads == [True]


async def test_the_tables_exist_before_a_test_body_runs():
    async with chat() as c:
        assert "not signed in" in await call(c, "authentication_status")
