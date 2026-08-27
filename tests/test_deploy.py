"""The worker deploying and deleting sites: files, Caddyfile, reloads, retries.

A deployment is a chain of three jobs — provision, configure, activate — so
``deploy()`` runs three jobs for a fresh site, and a failed step retries
alone.
"""

import asyncio

from cervo import caddy, job, server, user, website, worker
from cervo.db import connect
from tests.conftest import OWNER, call, chat, deploy


def created(slug: str, email: str = OWNER) -> website.Website:
    with connect() as conn:
        owner = user.ensure(conn, email)
        return website.create(conn, slug, owner)


def status_of(slug: str) -> tuple[str, str | None]:
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        (site,) = [s for s in website.for_user(conn, owner) if s.slug == slug]
    return site.status, site.error


def test_the_worker_is_idle_with_nothing_queued():
    assert deploy() == 0


def site_of(slug: str) -> website.Website:
    with connect() as conn:
        return website.get(conn, slug)


def test_a_deployment_provisions_the_site(data_dir, caddy_reloads):
    created("mysite")
    assert deploy() == 3  # provision, configure, activate

    page = (data_dir / "mysite" / "index.html").read_text()
    assert "mysite" in page
    assert "http://mysite.localhost" in page
    # The page comes from the website's own components: shared design
    # tokens, footer links absolute to the apex domain.
    assert "--accent" in page
    assert "http://localhost/docs" in page

    caddyfile = (data_dir / "Caddyfile").read_text()
    assert "admin 0.0.0.0:2019" in caddyfile
    assert "http://localhost {" in caddyfile  # cervo itself, at the bare domain
    assert "http://mysite.localhost {" in caddyfile
    assert f"root * {data_dir}/mysite" in caddyfile

    assert caddy_reloads == [True]
    assert status_of("mysite") == ("live", None)


def test_a_deployment_advances_one_step_at_a_time(data_dir, caddy_reloads):
    created("stepwise")
    site = site_of("stepwise")
    assert (site.status, site.steps_done, site.steps_total) == ("pending", 0, 3)
    assert site.step == "writing the site's files"

    assert worker.run_once()  # provision
    site = site_of("stepwise")
    assert (site.status, site.steps_done) == ("deploying", 1)
    assert site.step == "updating the web server config"
    assert (data_dir / "stepwise" / "index.html").exists()
    assert not (data_dir / "Caddyfile").exists()

    assert worker.run_once()  # configure
    site = site_of("stepwise")
    assert (site.status, site.steps_done) == ("deploying", 2)
    assert site.step == "routing traffic to the site"
    assert (data_dir / "Caddyfile").exists()
    assert caddy_reloads == []

    assert worker.run_once()  # activate
    site = site_of("stepwise")
    assert (site.status, site.step, site.steps_done) == ("live", None, 3)
    assert caddy_reloads == [True]
    assert not worker.run_once()


def test_the_caddyfile_covers_every_site(data_dir):
    created("alpha")
    created("beta")
    deploy()

    caddyfile = (data_dir / "Caddyfile").read_text()
    assert "http://alpha.localhost {" in caddyfile
    assert "http://beta.localhost {" in caddyfile


def test_a_redeployment_keeps_the_owners_files(data_dir):
    created("kept")
    deploy()
    (data_dir / "kept" / "index.html").write_text("the owner's own page")

    with connect() as conn:  # force the failed-deployment path, then redeploy
        conn.execute("UPDATE job SET status = 'failed'")
    created("kept")
    assert deploy() == 3

    assert (data_dir / "kept" / "index.html").read_text() == "the owner's own page"
    assert status_of("kept") == ("live", None)


def test_a_failed_deployment_records_the_error_and_retries(monkeypatch):
    def refuse() -> None:
        raise RuntimeError("caddy is down")

    monkeypatch.setattr(caddy, "reload", refuse)
    created("unlucky")
    assert deploy() == 3  # only the last step fails

    status, error = status_of("unlucky")
    assert status == "deploying"  # the failed step is queued for another attempt
    assert error == "caddy is down"
    assert site_of("unlucky").step == "routing traffic to the site"

    assert deploy() == 0  # the retry delay has not passed yet


def test_a_recovered_deployment_goes_live_on_retry(monkeypatch, caddy_reloads):
    monkeypatch.setattr(caddy, "reload", _refuse)
    created("recovers")
    deploy()

    monkeypatch.setattr(caddy, "reload", lambda: caddy_reloads.append(True))
    with connect() as conn:  # skip the retry delay, the way waiting would
        conn.execute("UPDATE job SET next_attempt_at = 0")
    assert deploy() == 1  # only the failed step reruns, not the whole chain
    assert status_of("recovers") == ("live", None)


def _refuse() -> None:
    raise RuntimeError("caddy is down")


def test_a_job_with_no_handler_fails_cleanly():
    from cervo import job

    with connect() as conn:
        job.enqueue(conn, "website.destroy", {"slug": "x"})
    assert deploy() == 1

    with connect() as conn:
        row = conn.execute("SELECT * FROM job").fetchone()
    assert row["status"] == "pending"  # retried like any failure
    assert "no handler" in row["error"]


def test_healing_renders_and_reloads_without_jobs(data_dir, caddy_reloads):
    created("already-there")
    with connect() as conn:  # pretend it was deployed long ago
        conn.execute("UPDATE job SET status = 'done'")

    worker._heal()

    assert "http://already-there.localhost {" in (data_dir / "Caddyfile").read_text()
    assert caddy_reloads == [True]


async def test_a_followed_creation_streams_progress(monkeypatch):
    """A client that sends a progress token sees each step and gets 'live'."""
    monkeypatch.setattr(server, "_FOLLOW_POLL", 0.02)
    monkeypatch.setattr(server, "_FOLLOW_FOR", 30)
    updates = []

    async def on_progress(progress, total, message):
        updates.append((progress, total, message))

    async def pump():  # the worker service, one job at a time
        while True:
            await asyncio.sleep(0.05)
            await asyncio.to_thread(worker.run_once)

    async with chat() as c:
        pumping = asyncio.create_task(pump())
        try:
            result = await c.call_tool(
                "create_website", {"slug": "followed"}, progress_handler=on_progress
            )
        finally:
            pumping.cancel()

    site = result.structured_content
    assert site["status"] == "live"
    steps = [progress for progress, _, _ in updates]
    assert steps[0] == 0 and steps[-1] == 3
    assert steps == sorted(steps)
    assert all(total == 3 for _, total, _ in updates)
    assert updates[-1][2] == "live at http://followed.localhost"


async def test_a_creation_that_outlasts_the_follow_window_hands_off():
    """The tool never waits past its window: the site comes back pending."""
    async with chat() as c:  # conftest pins the window to zero
        result = await c.call_tool("create_website", {"slug": "unwatched"})
    assert result.structured_content["status"] == "pending"


async def test_an_agent_watches_a_site_go_live():
    async with chat() as c:
        await call(c, "create_website", slug="watched")

        deploy()  # the worker service, doing its thing

        result = await c.call_tool("list_websites")

    (site,) = result.structured_content["result"]
    assert site["slug"] == "watched"
    assert site["status"] == "live"
    assert site["error"] is None
    assert site["url"] == "http://watched.localhost"


def _deleted(slug: str, email: str = OWNER) -> None:
    with connect() as conn:
        owner = user.ensure(conn, email)
        website.delete(conn, slug, owner)


def test_a_deletion_removes_the_files_and_the_route(data_dir, caddy_reloads):
    created("doomed")
    deploy()
    assert (data_dir / "doomed" / "index.html").exists()

    _deleted("doomed")
    assert deploy() == 1

    assert not (data_dir / "doomed").exists()
    caddyfile = (data_dir / "Caddyfile").read_text()
    assert "http://doomed.localhost {" not in caddyfile
    assert "http://localhost {" in caddyfile  # cervo itself is still served
    assert caddy_reloads == [True, True]


def test_deleting_mid_deployment_leaves_nothing_behind(data_dir):
    created("halfway")  # deployment queued but never run
    _deleted("halfway")
    deploy()  # the orphaned deployment fails, the cleanup still runs

    assert not (data_dir / "halfway").exists()
    assert "halfway" not in (data_dir / "Caddyfile").read_text()


def test_a_failed_deletion_retries_and_recovers(monkeypatch, data_dir, caddy_reloads):
    created("stubborn")
    deploy()

    _deleted("stubborn")
    monkeypatch.setattr(caddy, "reload", _refuse)
    deploy()
    assert (data_dir / "stubborn").exists()  # files survive until routing stops

    monkeypatch.setattr(caddy, "reload", lambda: caddy_reloads.append(True))
    with connect() as conn:  # skip the retry delay, the way waiting would
        conn.execute("UPDATE job SET next_attempt_at = 0")
    assert deploy() == 1


def test_a_stale_site_deletion_spares_a_reclaimed_slug(
    monkeypatch, data_dir, caddy_reloads
):
    """A delayed site-deletion must not wipe the files of the slug's new owner.

    A freed slug can be re-taken before its cleanup job runs. When it is, the
    stale deletion must leave the new owner's directory alone — the same
    guarantee delete_file already makes.
    """
    created("shared")
    deploy()

    _deleted("shared")  # row gone, cleanup queued
    monkeypatch.setattr(caddy, "reload", _refuse)
    deploy()  # the cleanup fails its reload and is held for a retry
    assert (data_dir / "shared").exists()

    monkeypatch.setattr(caddy, "reload", lambda: caddy_reloads.append(True))
    with connect() as conn:  # the freed slug is taken and provisioned by someone else
        newcomer = user.ensure(conn, "newcomer@example.com")
        website.create(conn, "shared", newcomer)
    deploy()
    (data_dir / "shared" / "index.html").write_text("newcomer's page")

    with connect() as conn:  # the held cleanup finally retries
        conn.execute("UPDATE job SET next_attempt_at = 0")
    deploy()

    marker = data_dir / "shared" / "index.html"
    assert marker.exists()  # the newcomer's files are untouched
    assert marker.read_text() == "newcomer's page"
    assert not (data_dir / "stubborn").exists()


def test_caddyfile_updates_run_one_at_a_time():
    """The kinds that rewrite or reload the shared Caddyfile are serialized."""
    with connect() as conn:
        first = job.enqueue(conn, website.CONFIGURE_KIND, {"slug": "one"})
        job.enqueue(conn, website.CONFIGURE_KIND, {"slug": "two"})

    with connect() as conn:
        claimed = job.claim_due(conn)
    assert claimed is not None and claimed.id == first.id
    with connect() as conn:
        assert job.claim_due(conn) is None, "a second Caddyfile rewrite must wait"
