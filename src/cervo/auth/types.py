"""Shapes for email-based authentication.

Confirming an email is the only way to prove who you are, so a challenge is
issued per MCP session (one chat) and, once answered, becomes a session that
stands in for the email until it expires.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, EmailStr


class _Expiring(BaseModel):
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class AuthChallenge(_Expiring):
    """A code mailed to an address, waiting to be pasted back.

    The code itself is never stored — only its hash — so a leaked database
    cannot be used to authenticate as somebody else.
    """

    session_id: str
    email: EmailStr
    code_hash: str
    attempts: int = 0


class AuthSession(_Expiring):
    """A chat that has proven it controls ``email``, until ``expires_at``."""

    session_id: str
    email: EmailStr
