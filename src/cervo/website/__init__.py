"""Static sites: what one is, and how one comes to exist.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.website.service import (
    DEPLOY_KIND,
    WebsiteError,
    all_sites,
    create,
    create_tables,
    exists,
    for_user,
    get,
)
from cervo.website.types import Slug, Website, WebsiteStatus

__all__ = [
    "DEPLOY_KIND",
    "Slug",
    "Website",
    "WebsiteError",
    "WebsiteStatus",
    "all_sites",
    "create",
    "create_tables",
    "exists",
    "for_user",
    "get",
]
