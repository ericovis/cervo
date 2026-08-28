"""SQLite queries for the authorization server's state.

Private to the package, hence the underscore — reach these through
``cervo.auth``'s service. Four tables: registered OAuth clients, in-flight
authorize transactions, single-use authorization codes, and issued tokens.
Codes and tokens are stored only as hashes, so a leaked database cannot be
used to authenticate as somebody else. Loads filter out expired rows and the
sweeps delete them for good; the SQL never leaves this module.
"""

import sqlite3
import time

from cervo.auth.types import Transaction

_CREATE_CLIENT_TABLE = """
CREATE TABLE IF NOT EXISTS oauth_client (
    client_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
)
"""

_CREATE_TXN_TABLE = """
CREATE TABLE IF NOT EXISTS auth_txn (
    txn_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly INTEGER NOT NULL,
    state TEXT,
    scopes TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    email TEXT,
    code_hash TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL
)
"""

_CREATE_CODE_TABLE = """
CREATE TABLE IF NOT EXISTS auth_code (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES user (id),
    scopes TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_provided_explicitly INTEGER NOT NULL,
    resource TEXT,
    expires_at REAL NOT NULL
)
"""

_CREATE_TOKEN_TABLE = """
CREATE TABLE IF NOT EXISTS auth_token (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES user (id),
    scopes TEXT NOT NULL,
    expires_at REAL
)
"""

# From the in-chat sign-in eras; databases created back then still carry them.
_DROP_LEGACY = (
    "DROP TABLE IF EXISTS auth_session",
    "DROP TABLE IF EXISTS auth_challenge",
)

_UPSERT_CLIENT = """
INSERT INTO oauth_client (client_id, data) VALUES (:client_id, :data)
ON CONFLICT (client_id) DO UPDATE SET data = excluded.data
"""

_GET_CLIENT = "SELECT data FROM oauth_client WHERE client_id = ?"

_INSERT_TXN = """
INSERT INTO auth_txn (
    txn_id, client_id, redirect_uri, redirect_uri_provided_explicitly,
    state, scopes, code_challenge, resource, email, code_hash, attempts,
    expires_at
)
VALUES (
    :txn_id, :client_id, :redirect_uri, :redirect_uri_provided_explicitly,
    :state, :scopes, :code_challenge, :resource, :email, :code_hash,
    :attempts, :expires_at
)
"""

_GET_TXN = "SELECT * FROM auth_txn WHERE txn_id = ? AND expires_at > ?"

_SET_TXN_CHALLENGE = """
UPDATE auth_txn SET email = :email, code_hash = :code_hash, attempts = 0
WHERE txn_id = :txn_id
"""

_RECORD_TXN_ATTEMPT = """
UPDATE auth_txn SET attempts = attempts + 1 WHERE txn_id = ?
RETURNING attempts
"""

_DELETE_TXN = "DELETE FROM auth_txn WHERE txn_id = ?"

_SWEEP_TXNS = "DELETE FROM auth_txn WHERE expires_at <= ?"

_INSERT_CODE = """
INSERT INTO auth_code (
    code_hash, client_id, user_id, scopes, code_challenge, redirect_uri,
    redirect_uri_provided_explicitly, resource, expires_at
)
VALUES (
    :code_hash, :client_id, :user_id, :scopes, :code_challenge, :redirect_uri,
    :redirect_uri_provided_explicitly, :resource, :expires_at
)
"""

_GET_CODE = "SELECT * FROM auth_code WHERE code_hash = ? AND expires_at > ?"

_DELETE_CODE = "DELETE FROM auth_code WHERE code_hash = ?"

_SWEEP_CODES = "DELETE FROM auth_code WHERE expires_at <= ?"

_INSERT_TOKEN = """
INSERT INTO auth_token (token_hash, kind, client_id, user_id, scopes, expires_at)
VALUES (:token_hash, :kind, :client_id, :user_id, :scopes, :expires_at)
"""

_GET_TOKEN = """
SELECT * FROM auth_token
WHERE token_hash = ? AND kind = ?
AND (expires_at IS NULL OR expires_at > ?)
"""

_DELETE_TOKEN = "DELETE FROM auth_token WHERE token_hash = ?"

_DELETE_GRANT_TOKENS = """
DELETE FROM auth_token WHERE client_id = ? AND user_id = ?
"""

_SWEEP_TOKENS = """
DELETE FROM auth_token WHERE expires_at IS NOT NULL AND expires_at <= ?
"""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the authorization server's tables if they do not exist yet."""
    conn.execute(_CREATE_CLIENT_TABLE)
    conn.execute(_CREATE_TXN_TABLE)
    conn.execute(_CREATE_CODE_TABLE)
    conn.execute(_CREATE_TOKEN_TABLE)
    for statement in _DROP_LEGACY:
        conn.execute(statement)


def upsert_client(conn: sqlite3.Connection, client_id: str, data: str) -> None:
    """Store a registered client, replacing any earlier registration."""
    conn.execute(_UPSERT_CLIENT, {"client_id": client_id, "data": data})


def get_client(conn: sqlite3.Connection, client_id: str) -> str | None:
    """The stored registration JSON for this client, if any."""
    row = conn.execute(_GET_CLIENT, (client_id,)).fetchone()
    return row["data"] if row else None


def insert_txn(conn: sqlite3.Connection, txn: Transaction) -> Transaction:
    """Park an authorize request; also sweeps expired ones while here."""
    conn.execute(_SWEEP_TXNS, (time.time(),))
    row = txn.model_dump(mode="json")
    row["scopes"] = " ".join(txn.scopes)
    conn.execute(_INSERT_TXN, row)
    return txn


def get_txn(conn: sqlite3.Connection, txn_id: str) -> Transaction | None:
    """The live transaction with this id, if any."""
    row = conn.execute(_GET_TXN, (txn_id, time.time())).fetchone()
    if row is None:
        return None
    fields = dict(row)
    fields["scopes"] = fields["scopes"].split()
    return Transaction(**fields)


def set_txn_challenge(
    conn: sqlite3.Connection, txn_id: str, email: str, code_hash: str
) -> None:
    """Attach a fresh email challenge to the transaction."""
    conn.execute(
        _SET_TXN_CHALLENGE, {"txn_id": txn_id, "email": email, "code_hash": code_hash}
    )


def record_txn_attempt(conn: sqlite3.Connection, txn_id: str) -> int:
    """Count a failed code and return the new total for this transaction."""
    row = conn.execute(_RECORD_TXN_ATTEMPT, (txn_id,)).fetchone()
    return row["attempts"] if row else 0


def delete_txn(conn: sqlite3.Connection, txn_id: str) -> None:
    """Drop the transaction — finished or forfeited."""
    conn.execute(_DELETE_TXN, (txn_id,))


def insert_code(conn: sqlite3.Connection, fields: dict) -> None:
    """Store a (hashed) authorization code; sweeps expired ones while here."""
    conn.execute(_SWEEP_CODES, (time.time(),))
    conn.execute(_INSERT_CODE, fields)


def get_code(conn: sqlite3.Connection, code_hash: str) -> dict | None:
    """The live authorization-code row with this hash, if any."""
    row = conn.execute(_GET_CODE, (code_hash, time.time())).fetchone()
    return dict(row) if row else None


def delete_code(conn: sqlite3.Connection, code_hash: str) -> bool:
    """Consume the code. Returns False if it was already gone."""
    return conn.execute(_DELETE_CODE, (code_hash,)).rowcount > 0


def insert_token(conn: sqlite3.Connection, fields: dict) -> None:
    """Store a (hashed) token; sweeps expired ones while here."""
    conn.execute(_SWEEP_TOKENS, (time.time(),))
    conn.execute(_INSERT_TOKEN, fields)


def get_token(conn: sqlite3.Connection, token_hash: str, kind: str) -> dict | None:
    """The live token row with this hash and kind, if any."""
    row = conn.execute(_GET_TOKEN, (token_hash, kind, time.time())).fetchone()
    return dict(row) if row else None


def delete_token(conn: sqlite3.Connection, token_hash: str) -> bool:
    """Drop one token. Returns whether a row was removed."""
    return conn.execute(_DELETE_TOKEN, (token_hash,)).rowcount > 0


def delete_grant_tokens(conn: sqlite3.Connection, client_id: str, user_id: int) -> None:
    """Drop every token this client holds for this user — a full revocation."""
    conn.execute(_DELETE_GRANT_TOKENS, (client_id, user_id))
