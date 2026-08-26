"""SQLite queries for the auth challenge and the session it becomes.

Private to the package, hence the underscore — reach these through
``cervo.auth``'s service. Both
tables are keyed by MCP session id, so a chat has at most one pending
challenge and at most one active session at a time.
"""

import sqlite3

from cervo.auth.types import AuthChallenge, AuthSession

_CREATE_CHALLENGE_TABLE = """
CREATE TABLE IF NOT EXISTS auth_challenge (
    session_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS auth_session (
    session_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
"""

_UPSERT_CHALLENGE = """
INSERT INTO auth_challenge (session_id, email, code_hash, expires_at, attempts)
VALUES (:session_id, :email, :code_hash, :expires_at, :attempts)
ON CONFLICT (session_id) DO UPDATE SET
    email = excluded.email,
    code_hash = excluded.code_hash,
    expires_at = excluded.expires_at,
    attempts = excluded.attempts
"""

_GET_CHALLENGE = "SELECT * FROM auth_challenge WHERE session_id = ?"

_DELETE_CHALLENGE = "DELETE FROM auth_challenge WHERE session_id = ?"

_RECORD_ATTEMPT = """
UPDATE auth_challenge SET attempts = attempts + 1 WHERE session_id = ?
RETURNING attempts
"""

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
    """Create both auth tables if they do not exist yet."""
    conn.execute(_CREATE_CHALLENGE_TABLE)
    conn.execute(_CREATE_SESSION_TABLE)


def upsert_challenge(
    conn: sqlite3.Connection, challenge: AuthChallenge
) -> AuthChallenge:
    """Store the challenge, replacing any earlier one for the same chat."""
    conn.execute(_UPSERT_CHALLENGE, challenge.model_dump(mode="json"))
    return challenge


def get_challenge(conn: sqlite3.Connection, session_id: str) -> AuthChallenge | None:
    """Return this chat's pending challenge, if there is one."""
    row = conn.execute(_GET_CHALLENGE, (session_id,)).fetchone()
    return AuthChallenge(**row) if row else None


def record_attempt(conn: sqlite3.Connection, session_id: str) -> int:
    """Count a failed code and return the new total for this chat."""
    row = conn.execute(_RECORD_ATTEMPT, (session_id,)).fetchone()
    return row["attempts"] if row else 0


def delete_challenge(conn: sqlite3.Connection, session_id: str) -> bool:
    """Drop this chat's pending challenge. Returns whether a row was removed."""
    return conn.execute(_DELETE_CHALLENGE, (session_id,)).rowcount > 0


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
