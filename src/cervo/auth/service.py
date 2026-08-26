"""Email confirmation as the one way to prove who you are.

A chat asks to authenticate, gets a code mailed to the address, and pastes it
back; from then on the chat carries an :class:`~cervo.auth.AuthSession` that
stands in for the email until it expires. Sessions are keyed by MCP session id,
so a new conversation starts unauthenticated even for the same person.
"""

import hashlib
import math
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from cervo import config, mail
from cervo.auth import _dao
from cervo.auth.types import AuthChallenge, AuthSession
from cervo.errors import AppError

_CODE_DIGITS = 6
_MAX_ATTEMPTS = 5

_SUBJECT = "Your cervo confirmation code"
_BODY = """\
Someone is signing in to cervo with this email address.

Your confirmation code is: {code}

Paste it back in the conversation to finish signing in. The code expires in
{minutes} minutes. If this wasn't you, ignore this email — nothing has been
created and nobody has access to your sites.
"""


class AuthError(AppError):
    """Raised when a chat cannot be authenticated."""


class NotAuthenticated(AuthError):
    """Raised when an action needs an email that this chat has not confirmed."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def minutes_until(moment: datetime) -> int:
    """Whole minutes from now until ``moment``, rounded up, never below one."""
    seconds = (moment - datetime.now(UTC)).total_seconds()
    return max(1, math.ceil(seconds / 60))


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def start(conn: sqlite3.Connection, session_id: str, email: str) -> AuthChallenge:
    """Mail a fresh code to ``email`` and park the challenge for this chat.

    Asking again simply reissues the code, which invalidates the previous one.
    """
    code = "".join(secrets.choice("0123456789") for _ in range(_CODE_DIGITS))
    challenge = AuthChallenge(
        session_id=session_id,
        email=email,
        code_hash=_hash(code),
        expires_at=datetime.now(UTC) + timedelta(seconds=config.AUTH_CODE_TTL),
    )

    # Mail first: a send failure must not leave a challenge nobody can answer.
    mail.send(
        to=challenge.email,
        subject=_SUBJECT,
        body=_BODY.format(code=code, minutes=config.AUTH_CODE_TTL // 60),
    )
    return _dao.upsert_challenge(conn, challenge)


def confirm(conn: sqlite3.Connection, session_id: str, code: str) -> AuthSession:
    """Check the code and authenticate this chat. Raises on any mismatch."""
    challenge = _dao.get_challenge(conn, session_id)
    if challenge is None:
        raise AuthError("Nothing to confirm in this chat. Start with authenticate.")
    if challenge.is_expired():
        _dao.delete_challenge(conn, session_id)
        raise AuthError("That code has expired. Start with authenticate again.")

    if not secrets.compare_digest(challenge.code_hash, _hash(code.strip())):
        attempts = _dao.record_attempt(conn, session_id)
        if attempts >= _MAX_ATTEMPTS:
            _dao.delete_challenge(conn, session_id)
            raise AuthError("Too many wrong codes. Start with authenticate again.")
        raise AuthError(
            f"That code is not right. {_MAX_ATTEMPTS - attempts} attempts left."
        )

    _dao.delete_challenge(conn, session_id)
    return _dao.upsert_session(
        conn,
        AuthSession(
            session_id=session_id,
            email=challenge.email,
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
            "This chat is not authenticated, or its session has expired. Ask "
            "the user which email owns the site and call authenticate with it, "
            "then confirm_authentication with the code they receive."
        )
    return session
