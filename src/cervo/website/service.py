"""Creating and listing sites, on behalf of the user who owns them."""

import sqlite3

from cervo.errors import AppError
from cervo.user.types import User
from cervo.website import _dao
from cervo.website.types import Website


class WebsiteError(AppError):
    """Raised when a website cannot be created."""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def create(conn: sqlite3.Connection, slug: str, owner: User) -> Website:
    """Create a site owned by ``owner``. Raises if the slug is taken."""
    if _dao.exists(conn, slug):
        raise WebsiteError(f"The slug {slug!r} is already taken.")
    return _dao.upsert(conn, Website(slug=slug, user_id=owner.id))


def for_user(conn: sqlite3.Connection, owner: User) -> list[Website]:
    """Every site ``owner`` has created."""
    return _dao.for_user(conn, owner.id)


def exists(conn: sqlite3.Connection, slug: str) -> bool:
    """Whether a site with this slug has been created."""
    return _dao.exists(conn, slug)
