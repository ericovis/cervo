"""Creating, listing, and deleting sites, on behalf of the user who owns them.

Creating a site writes the row and queues the first job of the deployment
chain; the worker process does the provisioning, one step per job. A site's
``status``, ``error``, and step fields therefore come from its latest
deployment job, attached here whenever a site is read.
"""

import sqlite3

from cervo import job
from cervo.errors import AppError
from cervo.user.types import User
from cervo.website import _dao
from cervo.website.types import Website, WebsiteStatus

DELETE_KIND = "website.delete"

# A deployment is a chain of jobs: each one's success has the worker enqueue
# the next, so a site's progress is visible one step at a time.
PROVISION_KIND = "website.provision"
CONFIGURE_KIND = "website.configure"
ACTIVATE_KIND = "website.activate"
DEPLOY_CHAIN = (PROVISION_KIND, CONFIGURE_KIND, ACTIVATE_KIND)

# Deployments queued before the chain existed ran as this single job; rows
# with this kind still exist, so reading them keeps old sites' status right.
DEPLOY_KIND = "website.deploy"

_ALL_DEPLOY_KINDS = (*DEPLOY_CHAIN, DEPLOY_KIND)

# What each step of the chain is doing, in words fit for a progress report.
_STEP_LABELS = {
    PROVISION_KIND: "writing the site's files",
    CONFIGURE_KIND: "updating the web server config",
    ACTIVATE_KIND: "routing traffic to the site",
}

# DATA_DIR/caddyfile would collide with the rendered DATA_DIR/Caddyfile on a
# case-insensitive filesystem (macOS development).
_RESERVED = frozenset({"caddyfile"})

# How a legacy single-job deployment's status reads as a site's status.
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
        job.enqueue(conn, DEPLOY_CHAIN[0], {"slug": slug})
        return _with_deployment(conn, site)
    if existing.user_id != owner.id:
        raise WebsiteError(f"The slug {slug!r} is already taken.")

    deployment = job.latest_of(conn, _ALL_DEPLOY_KINDS, {"slug": slug})
    if deployment is None or deployment.status == "failed":
        job.enqueue(conn, DEPLOY_CHAIN[0], {"slug": slug})
        return _with_deployment(conn, existing)
    site = existing.model_copy(update=_deployment_state(deployment))
    if site.status == "live":
        raise WebsiteError(f"You already own {slug!r}, and it is live.")
    raise WebsiteError(f"You already own {slug!r}; its deployment is in progress.")


def delete(conn: sqlite3.Connection, slug: str, owner: User) -> None:
    """Delete ``owner``'s site and queue the removal of its traces.

    The row goes immediately — the slug is free again and the site stops
    being listed — and a worker job then takes the route out of the
    Caddyfile and deletes the site's directory. Raises if there is no such
    site or it belongs to someone else.
    """
    site = _dao.get(conn, slug)
    if site is None:
        raise WebsiteError(f"There is no site with the slug {slug!r}.")
    if site.user_id != owner.id:
        raise WebsiteError(f"The site {slug!r} belongs to someone else.")
    _dao.delete(conn, slug)
    job.enqueue(conn, DELETE_KIND, {"slug": slug})


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
    """The site carrying the state of its latest deployment job."""
    deployment = job.latest_of(conn, _ALL_DEPLOY_KINDS, {"slug": site.slug})
    if deployment is None:
        return site
    return site.model_copy(update=_deployment_state(deployment))


def _deployment_state(deployment: job.Job) -> dict:
    """How a deployment job reads as a site's status, error, and step.

    A chain job also says how far along the pipeline the site is; a legacy
    single-job deployment is all-or-nothing. A chain job marked done is a
    moment the worker's transaction makes invisible — the next step is
    enqueued as the previous one succeeds — but it is still mapped, not
    trusted to never surface.
    """
    total = len(DEPLOY_CHAIN)
    state: dict = {"error": deployment.error, "steps_total": total, "step": None}
    if deployment.kind == DEPLOY_KIND:
        status = _STATUS[deployment.status]
        done = total if status == "live" else 0
        return {**state, "status": status, "steps_done": done}

    index = DEPLOY_CHAIN.index(deployment.kind)
    if deployment.status == "done":
        if index == total - 1:
            return {**state, "status": "live", "steps_done": total}
        next_kind = DEPLOY_CHAIN[index + 1]
        state |= {"step": _STEP_LABELS[next_kind], "steps_done": index + 1}
        return {**state, "status": "deploying"}

    state |= {"step": _STEP_LABELS[deployment.kind], "steps_done": index}
    if deployment.status == "failed":
        return {**state, "status": "failed"}
    if deployment.status == "pending" and index == 0:
        return {**state, "status": "pending"}
    return {**state, "status": "deploying"}
