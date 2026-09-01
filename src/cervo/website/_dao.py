"""SQLite queries for :class:`cervo.website.Website`.

Private to the package, hence the underscore — reach these through
``cervo.website``'s service.

Timestamps (``created_at``, ``updated_at``) are unix epoch seconds, written
only here; they leave as the model's datetimes.
"""

import sqlite3
from datetime import UTC, datetime

from cervo.website.types import Route, Website

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS website (
    slug TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_CREATE_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS website_user_id ON website (user_id)
"""

_INSERT_IF_ABSENT = """
INSERT INTO website (slug, user_id, created_at, updated_at)
VALUES (:slug, :user_id, :now, :now)
ON CONFLICT (slug) DO NOTHING
RETURNING *
"""

_GET = "SELECT * FROM website WHERE slug = ?"

_EXISTS = "SELECT 1 FROM website WHERE slug = ?"

_FOR_USER = "SELECT * FROM website WHERE user_id = ? ORDER BY slug"

_ALL = "SELECT * FROM website ORDER BY slug"

_ROUTES = """
SELECT website.slug, user.email AS owner_email
FROM website JOIN user ON user.id = website.user_id
ORDER BY website.slug
"""

_DELETE = "DELETE FROM website WHERE slug = ?"


def _now() -> float:
    return datetime.now(UTC).timestamp()


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the website table and its index if they do not exist yet."""
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_OWNER_INDEX)


def insert_if_absent(
    conn: sqlite3.Connection, slug: str, user_id: int
) -> Website | None:
    """Insert the website, or return None if the slug is already taken.

    The insert is the atomic point that decides who owns a fresh slug: it
    takes the write lock, so a second creator racing for the same new slug
    conflicts here and gets None rather than the row — ownership is never
    handed to a later creator. (A plain leading SELECT would not do: sqlite3
    opens the write transaction only at the first write, so two creators
    could both read "absent" before either wrote.)
    """
    row = conn.execute(
        _INSERT_IF_ABSENT, {"slug": slug, "user_id": user_id, "now": _now()}
    ).fetchone()
    return Website(**row) if row else None


def get(conn: sqlite3.Connection, slug: str) -> Website | None:
    """The website using this slug, if any."""
    row = conn.execute(_GET, (slug,)).fetchone()
    return Website(**row) if row else None


def exists(conn: sqlite3.Connection, slug: str) -> bool:
    """Whether a website is already using this slug."""
    return conn.execute(_EXISTS, (slug,)).fetchone() is not None


def for_user(conn: sqlite3.Connection, user_id: int) -> list[Website]:
    """Every site this user owns, oldest slug first alphabetically."""
    rows = conn.execute(_FOR_USER, (user_id,)).fetchall()
    return [Website(**row) for row in rows]


def all_sites(conn: sqlite3.Connection) -> list[Website]:
    """Every site there is."""
    rows = conn.execute(_ALL).fetchall()
    return [Website(**row) for row in rows]


def routes(conn: sqlite3.Connection) -> list[Route]:
    """Every site with its owner's email, for the web server's config."""
    rows = conn.execute(_ROUTES).fetchall()
    return [Route(**row) for row in rows]


def delete(conn: sqlite3.Connection, slug: str) -> bool:
    """Delete the website with this slug. Returns whether a row was removed."""
    return conn.execute(_DELETE, (slug,)).rowcount > 0
