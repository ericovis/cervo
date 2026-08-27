"""The worker: runs the jobs the server queues.

Runs as its own process (`uv run cervo-worker`, the compose ``worker``
service), polling the database for due jobs and dispatching them by kind.
There is no shutdown protocol on purpose: a worker killed mid-job leaves the
job running until its timeout, at which point reaping turns it back into a
pending attempt.

Tests never start the loop — they call :func:`run_once` and get the same
behavior deterministically.
"""

import logging
import shutil
import threading
from typing import Any

from cervo import caddy, config, job, web, website
from cervo.db import connect
from cervo.schema import create_tables

_POLL_INTERVAL = 2  # seconds

_log = logging.getLogger(__name__)


def main() -> None:
    """Start the worker. The compose service boots it before the server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    create_tables()
    run_forever()


def run_forever(stop: threading.Event | None = None) -> None:
    """Poll for due jobs until told to stop (in practice: until killed)."""
    stop = stop or threading.Event()
    _heal()
    while not stop.wait(_POLL_INTERVAL):
        with connect() as conn:
            reaped = job.reap(conn)
        if reaped:
            _log.warning("reclaimed %d timed-out job(s)", reaped)
        while run_once():
            pass


def run_once() -> bool:
    """Claim and run at most one due job. Returns whether there was work."""
    with connect() as conn:  # claiming commits before the slow work starts
        claimed = job.claim_due(conn)
    if claimed is None:
        return False

    try:
        handler = _HANDLERS.get(claimed.kind)
        if handler is None:
            raise RuntimeError(f"no handler for job kind {claimed.kind!r}")
        handler(claimed.payload)
    except Exception as error:  # noqa: BLE001 — recorded on the job, retried
        _log.warning("job %d (%s) failed: %s", claimed.id, claimed.kind, error)
        with connect() as conn:
            job.fail(conn, claimed.id, str(error))
    else:
        _log.info("job %d (%s) done", claimed.id, claimed.kind)
        with connect() as conn:  # one transaction: a done step and its successor
            job.succeed(conn, claimed.id)
            follow_up = _NEXT.get(claimed.kind)
            if follow_up:
                job.enqueue(conn, follow_up, claimed.payload)
    return True


def _provision_website(payload: dict[str, Any]) -> None:
    """Create the site's directory and its default page.

    Idempotent, so a retried step is safe: the default page is written only
    if the owner has not replaced it with their own.
    """
    slug = payload["slug"]
    with connect() as conn:
        site = website.get(conn, slug)
    if site is None:
        raise RuntimeError(f"no website row for slug {slug!r}")

    site_dir = config.DATA_DIR / slug
    site_dir.mkdir(parents=True, exist_ok=True)

    index = site_dir / "index.html"
    if not index.exists():
        index.write_text(
            web.default_page(
                slug=slug,
                url=site.url,
                deployed_at=site.created_at.strftime("%B %-d, %Y at %H:%M UTC"),
            )
        )


def _configure_website(payload: dict[str, Any]) -> None:
    """Render the Caddyfile from the database, covering every site."""
    with connect() as conn:
        sites = website.all_sites(conn)
    caddy.render(sites)


def _activate_website(payload: dict[str, Any]) -> None:
    """Reload caddy, so it serves what the rendered Caddyfile says."""
    caddy.reload()


def _deploy_website(payload: dict[str, Any]) -> None:
    """A whole deployment as one job — rows queued before the chain existed."""
    _provision_website(payload)
    _configure_website(payload)
    _activate_website(payload)


def _delete_website(payload: dict[str, Any]) -> None:
    """Stop routing a deleted site and remove its files.

    The row is already gone, so rendering the Caddyfile from the database
    drops the route; the directory is deleted after routing stops. Both
    steps are idempotent, so a retried deletion is safe.
    """
    slug = payload["slug"]
    with connect() as conn:
        sites = website.all_sites(conn)
    caddy.render(sites)
    caddy.reload()

    site_dir = config.DATA_DIR / slug
    if site_dir.exists():
        shutil.rmtree(site_dir)


_HANDLERS = {
    website.PROVISION_KIND: _provision_website,
    website.CONFIGURE_KIND: _configure_website,
    website.ACTIVATE_KIND: _activate_website,
    website.DEPLOY_KIND: _deploy_website,
    website.DELETE_KIND: _delete_website,
}

# The deployment chain: finishing one step enqueues the next.
_NEXT = dict(zip(website.DEPLOY_CHAIN, website.DEPLOY_CHAIN[1:], strict=False))


def _heal() -> None:
    """Bring caddy in line with the database at startup.

    Renders the Caddyfile even if no job is queued, so a fresh checkout or a
    restored data directory starts serving without waiting for a deployment.
    Failure is only logged — caddy may still be booting — and the next
    deployment retries the reload anyway.
    """
    try:
        with connect() as conn:
            sites = website.all_sites(conn)
        caddy.render(sites)
        caddy.reload()
    except Exception as error:  # noqa: BLE001 — startup must not die on caddy
        _log.warning("could not sync caddy at startup: %s", error)
