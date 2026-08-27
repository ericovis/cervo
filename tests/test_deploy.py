"""The worker deploying and deleting sites: files, Caddyfile, reloads, retries."""

from cervo import caddy, user, website, worker
from cervo.db import connect
from tests.conftest import OWNER, call, chat, deploy, sign_in


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


def test_a_deployment_provisions_the_site(data_dir, caddy_reloads):
    created("mysite")
    assert deploy() == 1

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
    assert deploy() == 1

    assert (data_dir / "kept" / "index.html").read_text() == "the owner's own page"
    assert status_of("kept") == ("live", None)


def test_a_failed_deployment_records_the_error_and_retries(monkeypatch):
    def refuse() -> None:
        raise RuntimeError("caddy is down")

    monkeypatch.setattr(caddy, "reload", refuse)
    created("unlucky")
    assert deploy() == 1

    status, error = status_of("unlucky")
    assert status == "pending"  # queued for another attempt
    assert error == "caddy is down"

    assert deploy() == 0  # the retry delay has not passed yet


def test_a_recovered_deployment_goes_live_on_retry(monkeypatch, caddy_reloads):
    monkeypatch.setattr(caddy, "reload", _refuse)
    created("recovers")
    deploy()

    monkeypatch.setattr(caddy, "reload", lambda: caddy_reloads.append(True))
    with connect() as conn:  # skip the retry delay, the way waiting would
        conn.execute("UPDATE job SET next_attempt_at = 0")
    assert deploy() == 1
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


async def test_an_agent_watches_a_site_go_live(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
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
    assert not (data_dir / "stubborn").exists()
