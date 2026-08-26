"""Who a chat is, proved by confirming an email address.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.auth.service import (
    AuthError,
    NotAuthenticated,
    confirm,
    create_tables,
    current,
    minutes_until,
    require,
    start,
)
from cervo.auth.types import AuthChallenge, AuthSession

__all__ = [
    "AuthChallenge",
    "AuthError",
    "AuthSession",
    "NotAuthenticated",
    "confirm",
    "create_tables",
    "current",
    "minutes_until",
    "require",
    "start",
]
