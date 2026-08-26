"""Static sites: what one is, and how one comes to exist.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.website.service import (
    WebsiteError,
    create,
    create_tables,
    exists,
    for_user,
)
from cervo.website.types import Slug, Website

__all__ = [
    "Slug",
    "Website",
    "WebsiteError",
    "create",
    "create_tables",
    "exists",
    "for_user",
]
