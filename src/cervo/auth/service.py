"""Confirming the Claude account's email as the one way to say who you are.

Cervo only talks to Claude, and Claude already knows the user's email — so
signing in is the human confirming that address in the chat, no code mailed
anywhere. From then on the chat carries an :class:`~cervo.auth.AuthSession`
that stands in for the email until it expires. Sessions are keyed by MCP
session id, so a new conversation starts unauthenticated even for the same
person.
"""

import math
import sqlite3
from datetime import UTC, datetime, timedelta

from cervo import config
from cervo.auth import _dao
from cervo.auth.types import AuthSession
from cervo.errors import AppError


class AuthError(AppError):
    """Raised when a chat cannot be authenticated."""


class NotAuthenticated(AuthError):
    """Raised when an action needs an email that this chat has not confirmed."""


def minutes_until(moment: datetime) -> int:
    """Whole minutes from now until ``moment``, rounded up, never below one."""
    seconds = (moment - datetime.now(UTC)).total_seconds()
    return max(1, math.ceil(seconds / 60))


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def sign_in(conn: sqlite3.Connection, session_id: str, email: str) -> AuthSession:
    """Authenticate this chat as ``email``, replacing any earlier session.

    The caller has already had the human confirm the address; signing in
    again simply refreshes the session's expiry.
    """
    return _dao.upsert_session(
        conn,
        AuthSession(
            session_id=session_id,
            email=email,
            expires_at=datetime.now(UTC) + timedelta(seconds=config.AUTH_SESSION_TTL),
        ),
    )


def current(conn: sqlite3.Connection, session_id: str) -> AuthSession | None:
    """This chat's live session, or None. Expired sessions are cleaned up."""
    session = _dao.get_session(conn, session_id)
    if session is None:
        return None
    if session.is_expired():
        _dao.delete_session(conn, session_id)
        return None
    return session


def require(conn: sqlite3.Connection, session_id: str) -> AuthSession:
    """This chat's live session, or raise telling the caller how to get one."""
    session = current(conn, session_id)
    if session is None:
        raise NotAuthenticated(
            "This chat is not authenticated, or its session has expired. Call "
            "authenticate with the email address on the user's Claude account "
            "and have them confirm it, then retry."
        )
    return session
