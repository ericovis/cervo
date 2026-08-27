"""Shapes for authentication.

Cervo is used through Claude, and Claude already knows who the user is — so
signing in is the user confirming the email address on their Claude account.
Once confirmed, the chat carries a session that stands in for the email until
it expires.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, EmailStr


class AuthSession(BaseModel):
    """A chat whose user confirmed ``email``, until ``expires_at``."""

    session_id: str
    email: EmailStr
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at
