"""SQLite queries for the authenticated session.

Private to the package, hence the underscore — reach these through
``cervo.auth``'s service. The table is keyed by MCP session id, so a chat has
at most one active session at a time.
"""

import sqlite3

from cervo.auth.types import AuthSession

_CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS auth_session (
    session_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
"""

# From the emailed-code era; databases created back then still carry it.
_DROP_CHALLENGE_TABLE = "DROP TABLE IF EXISTS auth_challenge"

_UPSERT_SESSION = """
INSERT INTO auth_session (session_id, email, expires_at)
VALUES (:session_id, :email, :expires_at)
ON CONFLICT (session_id) DO UPDATE SET
    email = excluded.email,
    expires_at = excluded.expires_at
"""

_GET_SESSION = "SELECT * FROM auth_session WHERE session_id = ?"

_DELETE_SESSION = "DELETE FROM auth_session WHERE session_id = ?"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the session table if it does not exist yet."""
    conn.execute(_CREATE_SESSION_TABLE)
    conn.execute(_DROP_CHALLENGE_TABLE)


def upsert_session(conn: sqlite3.Connection, session: AuthSession) -> AuthSession:
    """Store the authenticated session, replacing any earlier one."""
    conn.execute(_UPSERT_SESSION, session.model_dump(mode="json"))
    return session


def get_session(conn: sqlite3.Connection, session_id: str) -> AuthSession | None:
    """Return this chat's session, expired or not."""
    row = conn.execute(_GET_SESSION, (session_id,)).fetchone()
    return AuthSession(**row) if row else None


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Drop this chat's session. Returns whether a row was removed."""
    return conn.execute(_DELETE_SESSION, (session_id,)).rowcount > 0
