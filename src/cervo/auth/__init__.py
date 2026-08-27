"""Who a chat is, taken from the Claude account's email once the user confirms.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.auth.service import (
    AuthError,
    NotAuthenticated,
    create_tables,
    current,
    minutes_until,
    require,
    sign_in,
)
from cervo.auth.types import AuthSession

__all__ = [
    "AuthError",
    "AuthSession",
    "NotAuthenticated",
    "create_tables",
    "current",
    "minutes_until",
    "require",
    "sign_in",
]
