"""Shapes for the OAuth authorization server.

cervo is its own authorization server: connecting the claude.ai connector
runs a browser flow where the user proves control of an email address, and
every MCP request afterwards carries a Bearer token minted here. A
:class:`Transaction` is one in-flight authorize request — everything from the
``/authorize`` query string that the verification pages and the final
redirect need, plus the state of the email challenge.
"""

import time

from pydantic import BaseModel, EmailStr


class Transaction(BaseModel):
    """One authorize request, parked while the user verifies their email."""

    txn_id: str
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    state: str | None = None
    scopes: list[str] = []
    code_challenge: str
    resource: str | None = None
    email: EmailStr | None = None
    code_hash: str | None = None
    attempts: int = 0
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at
