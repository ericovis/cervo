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
from time import monotonic
from typing import Any

from cervo import caddy, config, job, monitoring, web, website
from cervo.db import connect
from cervo.schema import create_tables

_POLL_INTERVAL = 2  # seconds

_log = logging.getLogger(__name__)


def main() -> None:
    """Start the worker. The compose service boots it before the server.

    One process, ``WORKER_CONCURRENCY`` polling threads — more workers
    without another container. Claiming (and the one-at-a-time rule for
    serialized kinds) is a single statement in the database, so threads,
    processes, and containers can mix freely without double-running a job.
    The threads are daemons on purpose: there is no shutdown protocol, and
    a killed process is recovered by the reaper, thread count included.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s"
    )
    monitoring.setup()  # once for the process; a no-op outside production
    create_tables()
    _heal()  # once, before any thread polls — never concurrently
    threading.current_thread().name = "worker-1"
    for n in range(config.WORKER_CONCURRENCY - 1):
        threading.Thread(
            target=run_forever, name=f"worker-{n + 2}", daemon=True
        ).start()
    run_forever()


def run_forever(stop: threading.Event | None = None) -> None:
    """Poll for due jobs until told to stop (in practice: until killed)."""
    stop = stop or threading.Event()
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

    started = monotonic()
    try:
        handler = _HANDLERS.get(claimed.kind)
        if handler is None:
            raise RuntimeError(f"no handler for job kind {claimed.kind!r}")
        handler(claimed.payload)
    except job.PermanentError as error:
        _log.warning("job %d (%s) failed for good: %s", claimed.id, claimed.kind, error)
        with connect() as conn:
            job.fail_permanently(conn, claimed.id, str(error))
        monitoring.report(error, permanent=True, **_job_context(claimed))
        _job_event(claimed, "failed", started)
    except Exception as error:  # noqa: BLE001 — recorded on the job, retried
        _log.warning("job %d (%s) failed: %s", claimed.id, claimed.kind, error)
        with connect() as conn:
            failed = job.fail(conn, claimed.id, str(error))
        spent = failed.status == "failed"  # attempts exhausted, no retry coming
        monitoring.report(error, permanent=spent, **_job_context(claimed))
        _job_event(claimed, "failed" if spent else "retrying", started)
    else:
        _log.info("job %d (%s) done", claimed.id, claimed.kind)
        with connect() as conn:  # one transaction: a done step and its successor
            job.succeed(conn, claimed.id)
            follow_up = _NEXT.get(claimed.kind)
            if follow_up:
                job.enqueue(conn, follow_up, claimed.payload)
        _job_event(claimed, "done", started)
    return True


def _job_context(claimed: job.Job) -> dict[str, Any]:
    """What Honeybadger should know about a failed job.

    The payload rides along minus any file content — its size tells the
    story at a millionth of the bytes — and its ``user_id`` (the submitting
    owner, in every file-chain payload) is a key Honeybadger aggregates
    who-is-affected by.
    """
    payload = {k: v for k, v in claimed.payload.items() if k != "content"}
    if "content" in claimed.payload:
        payload["content_bytes"] = len(claimed.payload["content"].encode())
    return {
        "component": "worker",
        "job_id": claimed.id,
        "kind": claimed.kind,
        "attempt": claimed.attempts + 1,
        **payload,
    }


def _job_event(claimed: job.Job, outcome: str, started: float) -> None:
    """One Insights event per processed job — the worker's request log."""
    monitoring.event(
        "job.processed",
        {
            "kind": claimed.kind,
            "job_id": claimed.id,
            "outcome": outcome,
            "attempt": claimed.attempts + 1,
            "duration_ms": round((monotonic() - started) * 1000),
            "slug": claimed.payload.get("slug"),
        },
    )


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

    if not (site_dir / "index.html").exists():
        _write_default_page(site)


def _write_default_page(site: website.Website) -> None:
    """Render the site's default landing page into its directory."""
    (config.DATA_DIR / site.slug / "index.html").write_text(
        web.default_page(
            slug=site.slug,
            url=site.url,
            deployed_at=site.created_at.strftime("%B %-d, %Y at %H:%M UTC"),
        )
    )


def _configure_website(payload: dict[str, Any]) -> None:
    """Render the Caddyfile from the database, covering every site."""
    with connect() as conn:
        sites = website.routes(conn)
    caddy.render(sites)


def _activate_website(payload: dict[str, Any]) -> None:
    """Reload caddy, so it serves what the rendered Caddyfile says."""
    caddy.reload()


def _validate_file(payload: dict[str, Any]) -> None:
    """Check a submitted file before anything touches the disk.

    Nothing in the payload is trusted, even though the server checked it
    once — the ownership, path, and size are re-checked here, in the
    process that will write. Every failure is a verdict, not an accident,
    so the job fails for good instead of retrying.
    """
    slug, path, content = payload["slug"], payload["path"], payload["content"]
    with connect() as conn:
        site = website.get(conn, slug)
    if site is None or site.user_id != payload["user_id"]:
        raise job.PermanentError(f"the site {slug!r} was deleted")
    try:
        website.file_target(slug, path)
        if len(content.encode("utf-8")) > website.MAX_FILE_BYTES:
            raise website.WebsiteError("Files are limited to 1 MiB.")
        website.check_content(path, content)
    except website.WebsiteError as error:
        raise job.PermanentError(str(error)) from error


def _write_file(payload: dict[str, Any]) -> None:
    """Write a validated file into its site's directory.

    The site is checked again right before writing — and against the
    submitting owner's id, because a freed slug may already belong to
    someone else, into whose site a stale write must never land. (The
    window between this check and the write is accepted.) Rewriting the
    same content makes a retried step safe; caddy's file_server picks the
    file up with no reload.
    """
    slug, path, content = payload["slug"], payload["path"], payload["content"]
    with connect() as conn:
        site = website.get(conn, slug)
    if site is None or site.user_id != payload["user_id"]:
        raise job.PermanentError(f"the site {slug!r} was deleted")
    try:
        target = website.file_target(slug, path)
    except website.WebsiteError as error:
        raise job.PermanentError(str(error)) from error
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _delete_file(payload: dict[str, Any]) -> None:
    """Delete a file from its site's directory.

    The site is checked again right before deleting — and against the
    submitting owner's id, because a freed slug may already belong to
    someone else, whose files must never be touched. A missing file makes
    a retried step safe; empty folders the deletion leaves behind are
    pruned, and a deleted index.html gets the default page back in its
    place — a site never loses its landing page. Caddy's file_server
    notices with no reload.
    """
    slug, path = payload["slug"], payload["path"]
    with connect() as conn:
        site = website.get(conn, slug)
    if site is None or site.user_id != payload["user_id"]:
        raise job.PermanentError(f"the site {slug!r} was deleted")
    try:
        target = website.file_target(slug, path)
    except website.WebsiteError as error:
        raise job.PermanentError(str(error)) from error
    target.unlink(missing_ok=True)
    root = (config.DATA_DIR / slug).resolve()
    folder = target.parent
    while folder != root and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
    if path == "index.html":
        _write_default_page(site)


def _delete_website(payload: dict[str, Any]) -> None:
    """Stop routing a deleted site and remove its files.

    The row is already gone, so rendering the Caddyfile from the database
    drops the route; the directory is deleted after routing stops. Both
    steps are idempotent, so a retried deletion is safe. The directory is
    removed only if the slug is still free — a slug reclaimed before this
    job runs (its cleanup delayed by a retry, say) keeps the new owner's
    files, the same guarantee delete_file makes.
    """
    slug = payload["slug"]
    with connect() as conn:
        sites = website.routes(conn)
        reclaimed = website.exists(conn, slug)
    caddy.render(sites)
    caddy.reload()

    site_dir = config.DATA_DIR / slug
    if not reclaimed and site_dir.exists():
        shutil.rmtree(site_dir)


_HANDLERS = {
    website.PROVISION_KIND: _provision_website,
    website.CONFIGURE_KIND: _configure_website,
    website.ACTIVATE_KIND: _activate_website,
    website.DELETE_KIND: _delete_website,
    website.DELETE_FILE_KIND: _delete_file,
    website.VALIDATE_FILE_KIND: _validate_file,
    website.WRITE_FILE_KIND: _write_file,
}

# The chains: finishing one step enqueues the next.
_NEXT = {
    **dict(zip(website.DEPLOY_CHAIN, website.DEPLOY_CHAIN[1:], strict=False)),
    **dict(zip(website.FILE_CHAIN, website.FILE_CHAIN[1:], strict=False)),
    **dict(zip(website.DELETE_FILE_CHAIN, website.DELETE_FILE_CHAIN[1:], strict=False)),
}


def _heal() -> None:
    """Bring caddy in line with the database at startup.

    Renders the Caddyfile even if no job is queued, so a fresh checkout or a
    restored data directory starts serving without waiting for a deployment.
    Failure is only logged — caddy may still be booting — and the next
    deployment retries the reload anyway.
    """
    try:
        with connect() as conn:
            sites = website.routes(conn)
        caddy.render(sites)
        caddy.reload()
    except Exception as error:  # noqa: BLE001 — startup must not die on caddy
        _log.warning("could not sync caddy at startup: %s", error)
