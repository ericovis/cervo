"""Creating and listing sites, on behalf of the user who owns them.

Creating a site writes the row and queues a deployment job; the worker
process does the provisioning. A site's ``status`` and ``error`` therefore
come from its latest deployment job, attached here whenever a site is read.
"""

import sqlite3

from cervo import job
from cervo.errors import AppError
from cervo.user.types import User
from cervo.website import _dao
from cervo.website.types import Website, WebsiteStatus

DEPLOY_KIND = "website.deploy"

# DATA_DIR/caddyfile would collide with the rendered DATA_DIR/Caddyfile on a
# case-insensitive filesystem (macOS development).
_RESERVED = frozenset({"caddyfile"})

# How a deployment job's status reads as a site's status.
_STATUS: dict[str, WebsiteStatus] = {
    "pending": "pending",
    "running": "deploying",
    "done": "live",
    "failed": "failed",
}


class WebsiteError(AppError):
    """Raised when a website cannot be created."""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def create(conn: sqlite3.Connection, slug: str, owner: User) -> Website:
    """Create a site owned by ``owner`` and queue its deployment.

    Raises if the slug is reserved or belongs to someone else. Called on a
    site ``owner`` already has: a failed deployment is queued again, anything
    else is refused — it is either live or already on its way.
    """
    if slug in _RESERVED:
        raise WebsiteError(f"The slug {slug!r} is reserved.")

    existing = _dao.get(conn, slug)
    if existing is None:
        site = _dao.upsert(conn, slug, owner.id)
        job.enqueue(conn, DEPLOY_KIND, {"slug": slug})
        return _with_deployment(conn, site)
    if existing.user_id != owner.id:
        raise WebsiteError(f"The slug {slug!r} is already taken.")

    deployment = job.latest(conn, DEPLOY_KIND, {"slug": slug})
    if deployment is None or deployment.status == "failed":
        job.enqueue(conn, DEPLOY_KIND, {"slug": slug})
        return _with_deployment(conn, existing)
    if deployment.status == "done":
        raise WebsiteError(f"You already own {slug!r}, and it is live.")
    raise WebsiteError(f"You already own {slug!r}; its deployment is in progress.")


def get(conn: sqlite3.Connection, slug: str) -> Website | None:
    """The site using this slug, if any, with its deployment state."""
    site = _dao.get(conn, slug)
    return _with_deployment(conn, site) if site else None


def for_user(conn: sqlite3.Connection, owner: User) -> list[Website]:
    """Every site ``owner`` has created, with its deployment state."""
    return [_with_deployment(conn, site) for site in _dao.for_user(conn, owner.id)]


def all_sites(conn: sqlite3.Connection) -> list[Website]:
    """Every site there is, for rendering the web server's config."""
    return _dao.all_sites(conn)


def live(conn: sqlite3.Connection) -> list[Website]:
    """Every site whose latest deployment is live, for the public catalog."""
    sites = (_with_deployment(conn, site) for site in _dao.all_sites(conn))
    return [site for site in sites if site.status == "live"]


def exists(conn: sqlite3.Connection, slug: str) -> bool:
    """Whether a site with this slug has been created."""
    return _dao.exists(conn, slug)


def _with_deployment(conn: sqlite3.Connection, site: Website) -> Website:
    """The site with the status and error of its latest deployment job."""
    deployment = job.latest(conn, DEPLOY_KIND, {"slug": site.slug})
    if deployment is None:
        return site
    return site.model_copy(
        update={"status": _STATUS[deployment.status], "error": deployment.error}
    )
