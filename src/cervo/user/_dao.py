"""SQLite queries for :class:`cervo.user.User`.

Private to the package, hence the underscore — reach these through
``cervo.user``'s service.
"""

import sqlite3

from cervo.user.types import User

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
)
"""

# The no-op update is what makes RETURNING give back the existing row on a
# conflict; DO NOTHING would return nothing and need a second query.
_UPSERT = """
INSERT INTO user (email) VALUES (:email)
ON CONFLICT (email) DO UPDATE SET email = excluded.email
RETURNING id, email
"""

_GET_BY_EMAIL = "SELECT * FROM user WHERE email = ?"

_GET_BY_ID = "SELECT * FROM user WHERE id = ?"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the user table if it does not exist yet."""
    conn.execute(_CREATE_TABLE)


def upsert(conn: sqlite3.Connection, email: str) -> User:
    """Return the user for this address, creating them if they are new."""
    row = conn.execute(_UPSERT, {"email": email}).fetchone()
    return User(**row)


def get_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    """Return the user with this address, if they exist."""
    row = conn.execute(_GET_BY_EMAIL, (email,)).fetchone()
    return User(**row) if row else None


def get_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    """Return the user with this id, if they exist."""
    row = conn.execute(_GET_BY_ID, (user_id,)).fetchone()
    return User(**row) if row else None
