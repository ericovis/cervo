"""Turning a confirmed email address into the person who owns sites."""

import sqlite3

from cervo.user import _dao
from cervo.user.types import User


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def ensure(conn: sqlite3.Connection, email: str) -> User:
    """The user behind a confirmed address, created on first sight.

    Signing in is the only way an address is confirmed, so this is called with
    an address the caller has already proven control of.
    """
    return _dao.upsert(conn, email)


def by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    """The user with this id — how a token's subject becomes a person."""
    return _dao.get_by_id(conn, user_id)
