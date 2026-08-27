"""People: created on first sign-in, owner of any number of sites.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.user.service import by_email, by_id, create_tables, ensure
from cervo.user.types import User

__all__ = ["User", "by_email", "by_id", "create_tables", "ensure"]
