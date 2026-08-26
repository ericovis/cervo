"""SQLite queries for :class:`cervo.website.Website`.

Private to the package, hence the underscore — reach these through
``cervo.website``'s service.
"""

import sqlite3

from cervo.website.types import Website

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS website (
    slug TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id)
)
"""

# One lookup per owner is the common read, so it is worth an index.
_CREATE_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS website_user_id ON website (user_id)
"""

_UPSERT = """
INSERT INTO website (slug, user_id)
VALUES (:slug, :user_id)
ON CONFLICT (slug) DO UPDATE SET user_id = excluded.user_id
"""

_EXISTS = "SELECT 1 FROM website WHERE slug = ?"

_FOR_USER = "SELECT * FROM website WHERE user_id = ? ORDER BY slug"

_DELETE = "DELETE FROM website WHERE slug = ?"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the website table and its index if they do not exist yet."""
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_OWNER_INDEX)


def upsert(conn: sqlite3.Connection, website: Website) -> Website:
    """Insert the website, or hand it to a new owner if the slug is taken."""
    conn.execute(_UPSERT, website.model_dump(mode="json"))
    return website


def exists(conn: sqlite3.Connection, slug: str) -> bool:
    """Whether a website is already using this slug."""
    return conn.execute(_EXISTS, (slug,)).fetchone() is not None


def for_user(conn: sqlite3.Connection, user_id: int) -> list[Website]:
    """Every site this user owns, oldest slug first alphabetically."""
    rows = conn.execute(_FOR_USER, (user_id,)).fetchall()
    return [Website(**row) for row in rows]


def delete(conn: sqlite3.Connection, slug: str) -> bool:
    """Delete the website with this slug. Returns whether a row was removed."""
    return conn.execute(_DELETE, (slug,)).rowcount > 0
