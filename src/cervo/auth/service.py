"""The rules of the authorization server.

Connecting the claude.ai connector opens a browser flow: the ``/authorize``
request is parked as a :class:`~cervo.auth.Transaction`, the user proves
control of an email address with a mailed six-digit code, and the flow ends
with a single-use authorization code redirected back to Claude. Claude then
exchanges it for a Bearer access token (plus a rotating refresh token) that
rides on every MCP request — the token's subject is the cervo user.

Codes and tokens are stored hashed. The verification-page errors are
:class:`AuthError`, readable by the person in the browser; the OAuth-protocol
errors are the SDK's, raised by the provider on top of this module.
"""

import hashlib
import secrets
import sqlite3
import time

from mcp.server.auth.provider import (
    AuthorizationCode,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthToken
from pydantic import AnyUrl

from cervo import mail, user
from cervo.auth import _dao
from cervo.auth.types import Transaction
from cervo.errors import AppError

# One shape everywhere, so the constants live here rather than in config: a
# sign-in attempt (and its emailed code) lasts ten minutes, the code Claude
# exchanges five, an access token an hour. Refresh tokens do not expire —
# they are single-use and rotate on every refresh.
_TXN_TTL = 600
_AUTH_CODE_TTL = 300
_ACCESS_TOKEN_TTL = 3600

_CODE_DIGITS = 6
_MAX_ATTEMPTS = 5

_SUBJECT = "Your cervo verification code"
_BODY = """\
Someone is connecting cervo with this email address.

Your verification code is: {code}

Type it into the sign-in page to finish connecting. The code expires in
{minutes} minutes. If this wasn't you, ignore this email — nothing has been
created and nobody has access to your sites.
"""


class AuthError(AppError):
    """Raised when the browser sign-in flow cannot continue."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def save_client(conn: sqlite3.Connection, client_id: str, data: str) -> None:
    """Store a dynamically registered client's metadata (as JSON)."""
    _dao.upsert_client(conn, client_id, data)


def get_client(conn: sqlite3.Connection, client_id: str) -> str | None:
    """The stored metadata JSON for this client, if it ever registered."""
    return _dao.get_client(conn, client_id)


def begin(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    state: str | None,
    scopes: list[str],
    code_challenge: str,
    resource: str | None,
) -> Transaction:
    """Park an authorize request and hand back the transaction to verify."""
    return _dao.insert_txn(
        conn,
        Transaction(
            txn_id=secrets.token_urlsafe(32),
            client_id=client_id,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            state=state,
            scopes=scopes,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=time.time() + _TXN_TTL,
        ),
    )


def transaction(conn: sqlite3.Connection, txn_id: str) -> Transaction | None:
    """The live transaction with this id, or None if unknown or expired."""
    return _dao.get_txn(conn, txn_id)


def send_code(conn: sqlite3.Connection, txn_id: str, email: str) -> Transaction:
    """Mail a fresh code to ``email`` and attach the challenge to the txn.

    Asking again (or with a corrected address) reissues the code, which
    invalidates the previous one.
    """
    txn = _dao.get_txn(conn, txn_id)
    if txn is None:
        raise AuthError(
            "This sign-in attempt has expired. Go back to Claude and connect again."
        )

    code = "".join(secrets.choice("0123456789") for _ in range(_CODE_DIGITS))
    # Mail first: a send failure must not leave a challenge nobody can answer.
    mail.send(
        to=email,
        subject=_SUBJECT,
        body=_BODY.format(code=code, minutes=_TXN_TTL // 60),
    )
    _dao.set_txn_challenge(conn, txn_id, email, _hash(code))
    return _dao.get_txn(conn, txn_id)


def confirm(conn: sqlite3.Connection, txn_id: str, code: str) -> str:
    """Check the code, mint the authorization code, and say where to go.

    Returns the URL of the client's callback, carrying ``code`` and
    ``state`` — the browser is redirected there and Claude takes over.
    """
    txn = _dao.get_txn(conn, txn_id)
    if txn is None:
        raise AuthError(
            "This sign-in attempt has expired. Go back to Claude and connect again."
        )
    if txn.email is None or txn.code_hash is None:
        raise AuthError("No code has been sent yet. Enter your email first.")

    if not secrets.compare_digest(txn.code_hash, _hash(code.strip())):
        attempts = _dao.record_txn_attempt(conn, txn.txn_id)
        if attempts >= _MAX_ATTEMPTS:
            _dao.delete_txn(conn, txn.txn_id)
            raise AuthError(
                "Too many wrong codes. Go back to Claude and connect again."
            )
        raise AuthError(
            f"That code is not right. {_MAX_ATTEMPTS - attempts} attempts left."
        )

    owner = user.ensure(conn, txn.email)
    value = secrets.token_urlsafe(32)
    _dao.insert_code(
        conn,
        {
            "code_hash": _hash(value),
            "client_id": txn.client_id,
            "user_id": owner.id,
            "scopes": " ".join(txn.scopes),
            "code_challenge": txn.code_challenge,
            "redirect_uri": txn.redirect_uri,
            "redirect_uri_provided_explicitly": txn.redirect_uri_provided_explicitly,
            "resource": txn.resource,
            "expires_at": time.time() + _AUTH_CODE_TTL,
        },
    )
    _dao.delete_txn(conn, txn.txn_id)
    return construct_redirect_uri(txn.redirect_uri, code=value, state=txn.state)


def load_code(conn: sqlite3.Connection, code: str) -> AuthorizationCode | None:
    """The authorization code behind this value, for the token exchange.

    The SDK verifies expiry, the redirect_uri match, and PKCE against what is
    returned here; the user rides along as the ``subject``.
    """
    row = _dao.get_code(conn, _hash(code))
    if row is None:
        return None
    return AuthorizationCode(
        code=code,
        client_id=row["client_id"],
        scopes=row["scopes"].split(),
        expires_at=row["expires_at"],
        code_challenge=row["code_challenge"],
        redirect_uri=AnyUrl(row["redirect_uri"]),
        redirect_uri_provided_explicitly=bool(row["redirect_uri_provided_explicitly"]),
        resource=row["resource"],
        subject=str(row["user_id"]),
    )


def exchange_code(
    conn: sqlite3.Connection, code: AuthorizationCode
) -> OAuthToken | None:
    """Consume the single-use code and issue the token pair.

    Returns None when the code was already spent — the caller turns that
    into the protocol's ``invalid_grant``.
    """
    if not _dao.delete_code(conn, _hash(code.code)):
        return None
    assert code.subject is not None  # confirm() always binds a user
    return _issue(conn, code.client_id, int(code.subject), code.scopes)


def load_refresh(conn: sqlite3.Connection, token: str) -> RefreshToken | None:
    """The refresh token behind this value, if it is live."""
    row = _dao.get_token(conn, _hash(token), "refresh")
    if row is None:
        return None
    return RefreshToken(
        token=token,
        client_id=row["client_id"],
        scopes=row["scopes"].split(),
        expires_at=row["expires_at"],
        subject=str(row["user_id"]),
    )


def exchange_refresh(
    conn: sqlite3.Connection, refresh: RefreshToken, scopes: list[str]
) -> OAuthToken | None:
    """Rotate the refresh token and issue a fresh pair.

    Single use: returns None when the token was already spent. The previous
    access token is left to expire on its own — Claude refreshes ahead of
    expiry, and killing it early would fail requests already in flight.
    """
    if not _dao.delete_token(conn, _hash(refresh.token)):
        return None
    assert refresh.subject is not None
    user_id = int(refresh.subject)
    return _issue(conn, refresh.client_id, user_id, scopes or refresh.scopes)


def load_access(conn: sqlite3.Connection, token: str) -> dict | None:
    """The identity behind this access token, if it is live.

    Returns the fields the provider builds its AccessToken from: the chat's
    user id as ``subject`` and their email as a claim.
    """
    row = _dao.get_token(conn, _hash(token), "access")
    if row is None:
        return None
    owner = user.by_id(conn, row["user_id"])
    if owner is None:  # the account was removed; the token dies with it
        _dao.delete_token(conn, _hash(token))
        return None
    return {
        "client_id": row["client_id"],
        "scopes": row["scopes"].split(),
        "expires_at": int(row["expires_at"]) if row["expires_at"] else None,
        "subject": str(owner.id),
        "email": owner.email,
    }


def revoke(conn: sqlite3.Connection, token: str) -> None:
    """Drop this token; a refresh token takes its whole grant with it."""
    token_hash = _hash(token)
    refresh_row = _dao.get_token(conn, token_hash, "refresh")
    if refresh_row is not None:
        _dao.delete_grant_tokens(conn, refresh_row["client_id"], refresh_row["user_id"])
        return
    _dao.delete_token(conn, token_hash)


def _issue(
    conn: sqlite3.Connection, client_id: str, user_id: int, scopes: list[str]
) -> OAuthToken:
    """Mint and store an access + refresh token pair for this grant."""
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    _dao.insert_token(
        conn,
        {
            "token_hash": _hash(access),
            "kind": "access",
            "client_id": client_id,
            "user_id": user_id,
            "scopes": " ".join(scopes),
            "expires_at": time.time() + _ACCESS_TOKEN_TTL,
        },
    )
    _dao.insert_token(
        conn,
        {
            "token_hash": _hash(refresh),
            "kind": "refresh",
            "client_id": client_id,
            "user_id": user_id,
            "scopes": " ".join(scopes),
            "expires_at": None,
        },
    )
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=_ACCESS_TOKEN_TTL,
        scope=" ".join(scopes) or None,
        refresh_token=refresh,
    )
