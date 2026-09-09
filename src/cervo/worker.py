"""The worker: runs the jobs the server queues.

Runs as its own process (`uv run cervo-worker`, the compose ``worker``
service), polling the database for due jobs and dispatching them by kind.
There is no shutdown protocol on purpose: a worker killed mid-job leaves the
job running until its timeout, at which point reaping turns it back into a
pending attempt.

It is also the only process that talks to caddy, and it only ever does one
thing there: write the whole config the database describes. Publishing a
site, deleting one, and reconciling — at startup, every few minutes, and on
demand (``cervo-sync``) — are all that same :func:`cervo.caddy.sync`.

Tests never start the loop — they call :func:`run_once` and get the same
behavior deterministically.
"""

import logging
import shutil
import sqlite3
import threading
from time import monotonic, sleep
from typing import Any

from cervo import caddy, config, job, monitoring, web, website
from cervo.db import connect
from cervo.schema import create_tables

_POLL_INTERVAL = 2  # seconds
_PRUNE_INTERVAL = 3600  # seconds between sweeps of old file jobs
_JOB_RETENTION = 86400  # keep a terminal file job at least a day before pruning
_SYNC_INTERVAL = 300  # seconds between reconciliations of caddy's config

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
    _request_sync()  # once, before any thread polls — never concurrently
    _prune_old_jobs()  # clear any backlog left by a previous run
    threading.current_thread().name = "worker-1"
    for n in range(config.WORKER_CONCURRENCY - 1):
        threading.Thread(
            target=run_forever, name=f"worker-{n + 2}", daemon=True
        ).start()
    run_forever()


def run_forever() -> None:
    """Poll for due jobs until the process is killed.

    Daemon threads plus reaper-based recovery are the whole shutdown story
    (see the module docstring), so there is deliberately no graceful-stop hook.
    """
    last_prune = last_sync = monotonic()
    while True:
        sleep(_POLL_INTERVAL)
        with connect() as conn:
            reaped = job.reap(conn)
            stalled = _sync_gave_up(conn)
        if reaped:
            _log.warning("reclaimed %d timed-out job(s)", reaped)
        if monotonic() - last_prune >= _PRUNE_INTERVAL:
            _prune_old_jobs()
            last_prune = monotonic()
        if stalled or monotonic() - last_sync >= _SYNC_INTERVAL:
            _request_sync()  # deduped, so every thread asking is harmless
            last_sync = monotonic()
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
        handler(claimed)
    except job.PermanentError as error:
        _log.warning("job %d (%s) failed for good: %s", claimed.id, claimed.kind, error)
        with connect() as conn:
            outcome = job.fail_permanently(conn, claimed, str(error))
        if outcome is None:
            _log.warning(
                "job %d (%s) lease lost; not recorded", claimed.id, claimed.kind
            )
        else:
            monitoring.report(error, permanent=True, **_job_context(claimed))
            _job_event(claimed, "failed", started)
    except Exception as error:  # noqa: BLE001 — recorded on the job, retried
        _log.warning("job %d (%s) failed: %s", claimed.id, claimed.kind, error)
        with connect() as conn:
            failed = job.fail(conn, claimed, str(error))
        if failed is None:
            _log.warning(
                "job %d (%s) lease lost; not recorded", claimed.id, claimed.kind
            )
        else:
            spent = failed.status == "failed"  # attempts exhausted, no retry
            monitoring.report(error, permanent=spent, **_job_context(claimed))
            _job_event(claimed, "failed" if spent else "retrying", started)
    else:
        _log.info("job %d (%s) done", claimed.id, claimed.kind)
        with connect() as conn:  # one transaction: a done step and its successor
            done = job.succeed(conn, claimed)
            if done is not None:
                follow_up = _NEXT.get(claimed.kind)
                if follow_up:
                    job.enqueue(conn, follow_up, claimed.payload)
        if done is None:
            _log.warning(
                "job %d (%s) lease lost; successor not enqueued",
                claimed.id,
                claimed.kind,
            )
        else:
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


def _provision_website(claimed: job.Job) -> None:
    """Create the site's directory and its default page.

    Idempotent, so a retried step is safe: the default page is written only
    if the owner has not replaced it with their own.
    """
    slug = claimed.payload["slug"]
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


def _publish_website(claimed: job.Job) -> None:
    """Make caddy serve what the database says, this site included.

    There is nothing site-specific to do: the config is written whole from
    the database, so publishing one site is the same call as reconciling
    every site. A retried step therefore costs caddy nothing (the config
    already matches, so nothing is written), and a site whose row vanished
    mid-deployment simply is not in what gets written — the step syncs and
    succeeds instead of failing for a row it no longer needs.
    """
    _sync_caddy_config()


def _sync_caddy(claimed: job.Job) -> None:
    """Reconcile caddy's running config with the database.

    The safety net behind every publish: a caddy that came back with an
    empty (or stale) autosave gets the whole config rewritten. Cheap when
    there is nothing to do, which is the normal case.
    """
    _sync_caddy_config()


def _sync_caddy_config() -> None:
    """Write caddy's config from the database — the one place that does.

    Read in a fresh connection at job time, so a publish, a deletion, and a
    reconciliation all act on what the database holds *now* rather than on
    whatever their payload was queued with.
    """
    with connect() as conn:
        sites = website.routes(conn)
    rewrote = caddy.sync(sites)
    _log.info(
        "caddy %s (%d site(s))",
        "rewritten" if rewrote else "already in step",
        len(sites),
    )


def _validate_file(claimed: job.Job) -> None:
    """Check a submitted file before anything touches the disk.

    Nothing in the payload is trusted, even though the server checked it
    once — the ownership, path, and size are re-checked here, in the
    process that will write. Every failure is a verdict, not an accident,
    so the job fails for good instead of retrying.
    """
    payload = claimed.payload
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


def _write_file(claimed: job.Job) -> None:
    """Write a validated file into its site's directory.

    The site is checked again right before writing — and against the
    submitting owner's id, because a freed slug may already belong to
    someone else, into whose site a stale write must never land. (The
    window between this check and the write is accepted.) A newer write or
    deletion of the same file supersedes this one, so an older retry never
    reverts it. Rewriting the same content makes a retried step safe;
    caddy's file_server picks the file up with no reload.
    """
    payload = claimed.payload
    slug, path, content = payload["slug"], payload["path"], payload["content"]
    with connect() as conn:
        site = website.get(conn, slug)
        superseded = website.file_job_superseded(conn, claimed)
    if superseded:
        raise job.PermanentError("a newer write or deletion of this file supersedes it")
    if site is None or site.user_id != payload["user_id"]:
        raise job.PermanentError(f"the site {slug!r} was deleted")
    try:
        target = website.file_target(slug, path)
    except website.WebsiteError as error:
        raise job.PermanentError(str(error)) from error
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _delete_file(claimed: job.Job) -> None:
    """Delete a file from its site's directory.

    The site is checked again right before deleting — and against the
    submitting owner's id, because a freed slug may already belong to
    someone else, whose files must never be touched. A newer write or
    deletion of the same file supersedes this one, so an older retry never
    removes a file a later write just published. A missing file makes a
    retried step safe; empty folders the deletion leaves behind are pruned,
    and a deleted index.html gets the default page back in its place — a
    site never loses its landing page. Caddy's file_server notices with no
    reload.
    """
    payload = claimed.payload
    slug, path = payload["slug"], payload["path"]
    with connect() as conn:
        site = website.get(conn, slug)
        superseded = website.file_job_superseded(conn, claimed)
    if superseded:
        raise job.PermanentError("a newer write or deletion of this file supersedes it")
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


def _delete_website(claimed: job.Job) -> None:
    """Stop routing a deleted site and remove its files.

    Caddy comes first — nothing is served from a directory being deleted —
    and it needs no argument: the sync reads the database, where the row is
    already gone, so the route goes with it. A slug freed and re-taken
    meanwhile is served again by the same call, since the new owner's row
    is in that same read.

    The files are the part that must be careful. Reclamation is read right
    before the removal, so a slug taken while caddy was being updated keeps
    its fresh files (that window is microseconds, the same accepted window
    the file jobs carry). Both steps are idempotent, so a retried deletion
    is safe.
    """
    slug = claimed.payload["slug"]
    site_dir = config.DATA_DIR / slug
    _sync_caddy_config()
    with connect() as conn:  # the slug may have been taken meanwhile
        reclaimed = website.exists(conn, slug)
    if not reclaimed and site_dir.exists():
        shutil.rmtree(site_dir)


_HANDLERS = {
    website.PROVISION_KIND: _provision_website,
    website.PUBLISH_KIND: _publish_website,
    website.SYNC_KIND: _sync_caddy,
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


_PRUNED_KINDS = (*website.FILE_CHAIN, website.SYNC_KIND)


def _prune_old_jobs() -> None:
    """Drop terminal jobs old enough that keeping them is only dead weight.

    The file-write chain, whose payload carries the file's content, and the
    reconciliations, which are queued around the clock — the deploy chain's
    rows are few and tiny and carry a site's status, so they stay.
    """
    try:
        with connect() as conn:
            removed = job.prune(conn, _PRUNED_KINDS, _JOB_RETENTION)
        if removed:
            _log.info("pruned %d old job(s)", removed)
    except Exception as error:  # noqa: BLE001 — housekeeping must not kill the loop
        _log.warning("could not prune old jobs: %s", error)


def _sync_gave_up(conn: sqlite3.Connection) -> bool:
    """Whether the last reconciliation spent its attempts without landing.

    Caddy holds no config of its own, so a sync that never ran is not one
    site's route missing — it is cervo's whole front door, its own hostname
    and sign-in pages included. A job's attempts are quickly spent (three,
    thirty seconds apart) against a caddy that is slow to listen, and
    waiting out ``_SYNC_INTERVAL`` for the next request would turn that into
    a five-minute outage. So the loop asks again as soon as it sees one
    failed: the gap is a poll, not the interval.
    """
    latest = job.latest_of(conn, (website.SYNC_KIND,), {})
    return latest is not None and latest.status == "failed"


def _request_sync() -> None:
    """Ask for caddy's config to be reconciled with the database.

    Queued rather than done here: at startup caddy may still be booting, and
    the queue's retries are a better answer than logging and hoping. The
    request is deduped, so the startup call, the periodic one, and every
    thread doing either add up to a single job.
    """
    try:
        with connect() as conn:
            website.request_sync(conn)
    except Exception as error:  # noqa: BLE001 — housekeeping must not kill the loop
        _log.warning("could not queue a caddy sync: %s", error)


def sync() -> None:
    """Reconcile caddy now — the ``cervo-sync`` command, run in a container.

    The operator's escape hatch, for when waiting for the next periodic
    sync is not an option. It only queues the job (deduped, like every
    other request); the worker runs it within a poll or two. The tables are
    created first, so the command works on a host where nothing has run yet
    instead of failing on a missing table.
    """
    create_tables()
    with connect() as conn:
        waiting = job.latest_of(conn, (website.SYNC_KIND,), {})
        queued = website.request_sync(conn)
    if waiting is not None and waiting.id == queued.id:
        print(f"a caddy sync is already queued (job {queued.id})")
    else:
        print(f"queued caddy sync as job {queued.id}")
