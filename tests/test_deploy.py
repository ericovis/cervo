"""The worker deploying and deleting sites: files, caddy's config, retries.

A deployment is a chain of two jobs — provision, then publish — so
``deploy()`` runs two jobs for a fresh site, and a failed step retries alone.
Caddy is the in-memory fake from ``conftest``: its config is what the real
one would be holding.
"""

import asyncio

import pytest

from cervo import caddy, job, server, user, website, worker
from cervo.db import connect
from cervo.schema import create_tables
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


def hosts_of(route: dict) -> list[str]:
    return route["match"][0]["host"]


def test_a_deployment_provisions_the_site(data_dir, caddy_api):
    created("mysite")
    assert deploy() == 2  # provision, publish

    page = (data_dir / "mysite" / "index.html").read_text()
    assert "mysite" in page
    assert "http://mysite.localhost" in page
    # The page comes from the website's own components: shared design
    # tokens, footer links absolute to the apex domain.
    assert "--accent" in page
    assert "http://localhost/docs" in page

    route = caddy_api.object("site:mysite")
    assert hosts_of(route) == ["mysite.localhost"]
    assert route["handle"] == [{"handler": "file_server", "root": f"{data_dir}/mysite"}]
    assert route in caddy_api.routes  # appended to the server the apex uses
    assert status_of("mysite") == ("live", None)


def test_a_deployment_advances_one_step_at_a_time(data_dir, caddy_api):
    created("stepwise")
    site = site_of("stepwise")
    assert (site.status, site.steps_done, site.steps_total) == ("pending", 0, 2)
    assert site.step == "writing the site's files"

    assert worker.run_once()  # provision
    site = site_of("stepwise")
    assert (site.status, site.steps_done) == ("deploying", 1)
    assert site.step == "routing traffic to the site"
    assert (data_dir / "stepwise" / "index.html").exists()
    assert caddy_api.object("site:stepwise") is None

    assert worker.run_once()  # publish
    site = site_of("stepwise")
    assert (site.status, site.step, site.steps_done) == ("live", None, 2)
    assert caddy_api.object("site:stepwise") is not None
    assert not worker.run_once()


def test_every_site_gets_its_own_route(caddy_api):
    created("alpha")
    created("beta")
    deploy()

    assert hosts_of(caddy_api.object("site:alpha")) == ["alpha.localhost"]
    assert hosts_of(caddy_api.object("site:beta")) == ["beta.localhost"]
    assert len(caddy_api.routes) == 3  # the apex proxy is still there too


def test_the_apex_proxy_is_never_touched(caddy_api):
    apex = caddy_api.routes[0]
    created("newcomer")
    deploy()

    assert caddy_api.routes[0] == apex
    assert "@id" not in apex  # not cervo's to manage: it comes from the file


def test_a_republished_site_writes_nothing(caddy_api):
    """A retried publish must not cost caddy a config reload."""
    created("settled")
    deploy()
    writes = list(caddy_api.writes)

    with connect() as conn:  # the same step, queued again
        job.enqueue(conn, website.PUBLISH_KIND, {"slug": "settled"})
    assert deploy() == 1

    assert caddy_api.writes == writes  # nothing changed, so nothing was written


def test_a_redeployment_keeps_the_owners_files(data_dir):
    created("kept")
    deploy()
    (data_dir / "kept" / "index.html").write_text("the owner's own page")

    with connect() as conn:  # force the failed-deployment path, then redeploy
        conn.execute("UPDATE job SET status = 'failed'")
    created("kept")
    assert deploy() == 2

    assert (data_dir / "kept" / "index.html").read_text() == "the owner's own page"
    assert status_of("kept") == ("live", None)


def test_a_failed_deployment_records_the_error_and_retries(caddy_api):
    caddy_api.broken = True
    created("unlucky")
    assert deploy() == 2  # only the last step fails

    status, error = status_of("unlucky")
    assert status == "deploying"  # the failed step is queued for another attempt
    assert error == "caddy is down"
    assert site_of("unlucky").step == "routing traffic to the site"

    assert deploy() == 0  # the retry delay has not passed yet


def test_a_recovered_deployment_goes_live_on_retry(caddy_api):
    caddy_api.broken = True
    created("recovers")
    deploy()

    caddy_api.broken = False
    with connect() as conn:  # skip the retry delay, the way waiting would
        conn.execute("UPDATE job SET next_attempt_at = 0")
    assert deploy() == 1  # only the failed step reruns, not the whole chain
    assert status_of("recovers") == ("live", None)
    assert caddy_api.object("site:recovers") is not None


def test_a_publish_without_its_site_fails_and_retries():
    """A row deleted mid-deployment leaves the step nothing to publish."""
    with connect() as conn:
        job.enqueue(conn, website.PUBLISH_KIND, {"slug": "vanished"})
    assert deploy() == 1

    with connect() as conn:
        row = conn.execute("SELECT * FROM job").fetchone()
    assert row["status"] == "pending"
    assert "no website row" in row["error"]


def test_a_job_with_no_handler_fails_cleanly():
    with connect() as conn:
        job.enqueue(conn, "website.destroy", {"slug": "x"})
    assert deploy() == 1

    with connect() as conn:
        row = conn.execute("SELECT * FROM job").fetchone()
    assert row["status"] == "pending"  # retried like any failure
    assert "no handler" in row["error"]


def test_the_worker_asks_for_a_sync_at_startup(caddy_api):
    """Startup queues a reconciliation, so caddy is caught up with no jobs."""
    created("already-there")
    deploy()
    caddy_api.http()  # caddy restarted: back to the static Caddyfile alone

    worker._request_sync()
    assert deploy() == 1

    assert hosts_of(caddy_api.object("site:already-there")) == [
        "already-there.localhost"
    ]


def test_a_sync_republishes_what_caddy_lost(caddy_api):
    created("lost")
    deploy()
    caddy_api.http()

    with connect() as conn:
        website.request_sync(conn)
    assert deploy() == 1

    assert caddy_api.object("site:lost") is not None
    assert len(caddy_api.routes) == 2  # the apex proxy and the site


def test_a_sync_drops_a_route_no_site_owns(caddy_api):
    created("real")
    deploy()
    caddy_api.routes.append({"@id": "site:ghost", "match": [{"host": ["ghost"]}]})
    apex = caddy_api.routes[0]

    with connect() as conn:
        website.request_sync(conn)
    deploy()

    assert caddy_api.object("site:ghost") is None
    assert caddy_api.object("site:real") is not None
    assert caddy_api.routes[0] == apex  # what cervo does not own is left alone


def test_a_sync_of_a_settled_stack_writes_nothing(caddy_api):
    """Whatever order the sites were published in — a reload costs.

    ``publish`` appends, a sync lists the sites by slug: the two orders
    serve identically, so a stack in step must compare equal either way.
    """
    created("zeta")
    created("alpha")
    deploy()
    writes = list(caddy_api.writes)

    with connect() as conn:
        website.request_sync(conn)
    assert deploy() == 1

    assert caddy_api.writes == writes  # a couple of reads, no reload


def test_a_sync_replaces_a_route_an_older_cervo_left(caddy_api):
    """A rolled-back cervo publishes untagged routes: they are not the apex.

    Adopting one as part of the static front door would leave the site with
    two routes for good — and the stale one, being first, would keep
    serving a site after it was deleted.
    """
    created("veteran")
    deploy()
    caddy_api.routes[:] = [
        caddy_api.routes[0],
        {  # what a rendered Caddyfile POSTed to /load looks like
            "match": [{"host": ["veteran.localhost"]}],
            "handle": [{"handler": "file_server", "root": "/mnt/data/veteran"}],
            "terminal": True,
        },
    ]

    with connect() as conn:
        website.request_sync(conn)
    assert deploy() == 1

    assert len(caddy_api.routes) == 2  # the apex proxy and one route per site
    assert caddy_api.object("site:veteran") is not None
    assert "@id" not in caddy_api.routes[0]  # the apex, still untouched


def test_a_caddy_that_refuses_says_why(caddy_api):
    """The failed job carries caddy's own words, not a stack trace."""
    del caddy_api.config["apps"]["http"]  # a caddy booted from another config

    with pytest.raises(caddy.CaddyError) as refusal:
        caddy.sync([])

    assert "invalid traversal path" in str(refusal.value)


def test_a_sync_request_is_deduped():
    with connect() as conn:
        first = website.request_sync(conn)
        again = website.request_sync(conn)
    assert again.id == first.id

    assert deploy() == 1  # one job, not two
    with connect() as conn:  # once it has run, a fresh request queues a new one
        after = website.request_sync(conn)
    assert after.id != first.id


def test_the_sync_command_queues_one_job(capsys):
    """`cervo-sync`: the operator's way to reconcile caddy right now."""
    worker.sync()
    first = capsys.readouterr().out
    assert first.startswith("queued caddy sync as job ")

    worker.sync()
    assert "already queued" in capsys.readouterr().out

    assert deploy() == 1
    worker.sync()
    assert capsys.readouterr().out.startswith("queued caddy sync as job ")


def test_an_old_deployments_jobs_are_renamed(caddy_api):
    """A site deployed before the two-step chain still reads as live.

    Databases in production hold the kinds that rendered and reloaded the
    Caddyfile; ``create_tables`` renames them on every startup.
    """
    created("veteran")
    deploy()
    with connect() as conn:  # rewind to what the old chain left behind
        conn.execute(
            "UPDATE job SET kind = 'website.configure' WHERE kind = ?",
            (website.PUBLISH_KIND,),
        )
        conn.execute(
            "INSERT INTO job (kind, payload, status, timeout, created_at)"
            " VALUES ('website.activate', '{\"slug\":\"veteran\"}', 'done', 300, 0)"
        )
    # The renamed rows are invisible to the chain: the site reads as if its
    # deployment stopped after provisioning.
    assert status_of("veteran") == ("deploying", None)

    create_tables()  # the worker's startup

    assert status_of("veteran") == ("live", None)
    assert deploy() == 0  # nothing was re-queued


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
    assert steps[0] == 0 and steps[-1] == 2
    assert steps == sorted(steps)
    assert all(total == 2 for _, total, _ in updates)
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


def test_a_deletion_removes_the_files_and_the_route(data_dir, caddy_api):
    created("doomed")
    deploy()
    assert (data_dir / "doomed" / "index.html").exists()

    _deleted("doomed")
    assert deploy() == 1

    assert not (data_dir / "doomed").exists()
    assert caddy_api.object("site:doomed") is None
    assert len(caddy_api.routes) == 1  # cervo itself is still served


def test_deleting_mid_deployment_leaves_nothing_behind(data_dir, caddy_api):
    created("halfway")  # deployment queued but never run
    _deleted("halfway")
    deploy()  # the orphaned deployment fails, the cleanup still runs

    assert not (data_dir / "halfway").exists()
    assert caddy_api.object("site:halfway") is None


def test_a_failed_deletion_retries_and_recovers(data_dir, caddy_api):
    created("stubborn")
    deploy()

    _deleted("stubborn")
    caddy_api.broken = True
    deploy()
    assert (data_dir / "stubborn").exists()  # files survive until routing stops

    caddy_api.broken = False
    with connect() as conn:  # skip the retry delay, the way waiting would
        conn.execute("UPDATE job SET next_attempt_at = 0")
    assert deploy() == 1
    assert not (data_dir / "stubborn").exists()


def test_a_stale_site_deletion_spares_a_reclaimed_slug(data_dir, caddy_api):
    """A delayed site-deletion must not wipe the files of the slug's new owner.

    A freed slug can be re-taken before its cleanup job runs. When it is, the
    stale deletion must leave the new owner's directory alone — the same
    guarantee delete_file already makes.
    """
    created("shared")
    deploy()

    _deleted("shared")  # row gone, cleanup queued
    caddy_api.broken = True
    deploy()  # the cleanup fails at caddy and is held for a retry
    assert (data_dir / "shared").exists()

    caddy_api.broken = False
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


def test_caddy_updates_run_one_at_a_time():
    """Every kind that changes caddy's running config is serialized."""
    with connect() as conn:
        first = job.enqueue(conn, website.PUBLISH_KIND, {"slug": "one"})
        job.enqueue(conn, website.DELETE_KIND, {"slug": "two"})
        job.enqueue(conn, website.SYNC_KIND, {})

    with connect() as conn:
        claimed = job.claim_due(conn)
    assert claimed is not None and claimed.id == first.id
    with connect() as conn:
        assert job.claim_due(conn) is None, "a second caddy update must wait"
