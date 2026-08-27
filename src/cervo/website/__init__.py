"""Static sites: what one is, and how one comes to exist.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. Callers go through the service.
"""

from cervo.website.service import (
    ACTIVATE_KIND,
    CONFIGURE_KIND,
    DELETE_KIND,
    DEPLOY_CHAIN,
    DEPLOY_KIND,
    PROVISION_KIND,
    WebsiteError,
    all_sites,
    create,
    create_tables,
    delete,
    exists,
    for_user,
    get,
    live,
)
from cervo.website.types import Slug, Website, WebsiteStatus

__all__ = [
    "ACTIVATE_KIND",
    "CONFIGURE_KIND",
    "DELETE_KIND",
    "DEPLOY_CHAIN",
    "DEPLOY_KIND",
    "PROVISION_KIND",
    "Slug",
    "Website",
    "WebsiteError",
    "WebsiteStatus",
    "all_sites",
    "create",
    "create_tables",
    "delete",
    "exists",
    "for_user",
    "get",
    "live",
]
