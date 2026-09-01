"""Creating and deleting sites, and who is allowed to."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest
from fastmcp.exceptions import ToolError

from cervo import job, user, website
from cervo.db import connect
from tests.conftest import OWNER, call, chat, deploy


async def test_the_owner_comes_from_the_session():
    async with chat("alice@example.com") as c:
        result = await c.call_tool("create_website", {"slug": "alices-site"})

    with connect() as conn:
        alice = user.ensure(conn, "alice@example.com")

    content = dict(result.structured_content)
    assert content.pop("created_at") == content.pop("updated_at")
    assert content == {
        "slug": "alices-site",
        "user_id": alice.id,
        "status": "pending",
        "error": None,
        "step": "writing the site's files",
        "steps_done": 0,
        "steps_total": 3,
        "url": "http://alices-site.localhost",
    }


async def test_recreating_your_own_site_mid_deployment_is_refused():
    async with chat() as c:
        await call(c, "create_website", slug="taken")
        with pytest.raises(ToolError, match="in progress"):
            await call(c, "create_website", slug="taken")


async def test_someone_else_cannot_take_a_slug_that_exists():
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="contested")

    async with chat("bob@example.com") as bob:
        with pytest.raises(ToolError, match="already taken"):
            await call(bob, "create_website", slug="contested")

    with connect() as conn:
        alice = user.ensure(conn, "alice@example.com")
        row = conn.execute(
            "SELECT * FROM website WHERE slug = ?", ("contested",)
        ).fetchone()
        assert row["user_id"] == alice.id


@pytest.mark.parametrize(
    "slug", ["Upper", "under_score", "-leading", "trailing-", "has space", ""]
)
async def test_a_slug_that_is_not_dns_safe_is_rejected(slug):
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "create_website", slug=slug)


@pytest.mark.parametrize("slug", ["a", "site", "my-site", "a1-b2-c3"])
async def test_dns_safe_slugs_are_accepted(slug):
    async with chat() as c:
        assert slug in await call(c, "create_website", slug=slug)


@pytest.mark.parametrize(
    "slug",
    [
        "a" * 64,  # one past the DNS label limit
        "a--b",  # consecutive hyphens
        "café",  # non-ascii latin
        "\u0430dmin",  # cyrillic a (U+0430), a homoglyph of ascii 'a'
        "site.com",  # a dot would spill into another subdomain label
        "site/../etc",  # path characters
        "site\nname",  # a newline that could smuggle into the Caddyfile
        "site name",
    ],
)
async def test_hostile_slugs_are_rejected(slug):
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "create_website", slug=slug)


async def test_a_slug_at_the_length_limit_is_accepted():
    async with chat() as c:
        assert "a" * 63 in await call(c, "create_website", slug="a" * 63)


def test_the_service_creates_and_reports_existence():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        assert not website.exists(conn, "direct")
        site = website.create(conn, "direct", owner)
        assert site.user_id == owner.id
        assert website.exists(conn, "direct")


def test_a_new_site_is_stamped_with_its_creation_time():
    before = datetime.now(UTC)
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        site = website.create(conn, "stamped", owner)
    assert before <= site.created_at <= datetime.now(UTC)
    assert site.updated_at == site.created_at


def test_the_service_refuses_a_duplicate_slug():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        someone_else = user.ensure(conn, "someone-else@example.com")
        website.create(conn, "once", owner)
        with pytest.raises(website.WebsiteError, match="already taken"):
            website.create(conn, "once", someone_else)


def _race_to_create(owner, slug, start, results, refusals):
    """One contender in the create() race: wait at the barrier, then try."""
    start.wait()
    try:
        with connect() as conn:
            results[owner.email] = website.create(conn, slug, owner).user_id
    except website.WebsiteError as error:
        refusals.append(str(error))


def test_racing_creators_never_share_ownership_of_a_slug():
    """Two accounts creating the same fresh slug at once: one wins cleanly.

    The insert is the atomic decision point, so however the two threads
    interleave, exactly one create() succeeds and the database row belongs
    to that winner — the loser is refused, never silently made co-owner or
    handed the slug. (With the old ownership-transferring upsert, the loser's
    write reassigned the row and both calls "succeeded".)
    """
    with connect() as conn:
        alice = user.ensure(conn, "alice@example.com")
        bob = user.ensure(conn, "bob@example.com")

    for _ in range(50):
        with connect() as conn:
            conn.execute("DELETE FROM website")
            conn.execute("DELETE FROM job")

        results: dict[str, int] = {}
        refusals: list[str] = []
        start = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=_race_to_create,
                args=(owner, "contested", start, results, refusals),
            )
            for owner in (alice, bob)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM website WHERE slug = ?", ("contested",)
            ).fetchone()

        assert len(results) == 1, "exactly one creator may succeed"
        assert len(refusals) == 1 and "already taken" in refusals[0]
        assert row["user_id"] == next(iter(results.values())), "winner owns the row"


def test_a_user_cannot_exceed_their_site_quota(monkeypatch):
    monkeypatch.setattr(website.service, "_MAX_SITES_PER_USER", 2)
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.create(conn, "one", owner)
        website.create(conn, "two", owner)
        with pytest.raises(website.WebsiteError, match="at most 2 sites"):
            website.create(conn, "three", owner)
        assert not website.exists(conn, "three")  # the over-quota row was rolled back
        # Re-deploying a site they already own is never blocked by the quota.
        conn.execute("UPDATE job SET status = 'failed' WHERE payload LIKE '%\"one\"%'")
        website.create(conn, "one", owner)


def test_a_reserved_slug_is_refused():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        with pytest.raises(website.WebsiteError, match="reserved"):
            website.create(conn, "caddyfile", owner)
        assert not website.exists(conn, "caddyfile")


def test_creating_a_site_queues_the_first_step_of_the_chain():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        site = website.create(conn, "queued", owner)
        deployment = job.latest_of(conn, (website.PROVISION_KIND,), {"slug": "queued"})
        queued = conn.execute("SELECT count(*) c FROM job").fetchone()["c"]

    assert site.status == "pending"
    assert site.step == "writing the site's files"
    assert (site.steps_done, site.steps_total) == (0, 3)
    assert deployment is not None
    assert deployment.status == "pending"
    assert queued == 1  # later steps are queued by the worker, one at a time


def test_a_failed_deployment_is_queued_again_by_its_owner():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.create(conn, "flaky", owner)
        conn.execute("UPDATE job SET status = 'failed', error = 'boom'")

    with connect() as conn:
        site = website.create(conn, "flaky", owner)
        deployments = conn.execute("SELECT status FROM job ORDER BY id").fetchall()

    assert site.status == "pending"
    assert [row["status"] for row in deployments] == ["failed", "pending"]


def test_a_live_site_is_not_deployed_again():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.create(conn, "settled", owner)
    deploy()

    with connect() as conn:
        with pytest.raises(website.WebsiteError, match="live"):
            website.create(conn, "settled", owner)
        assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == 3


def test_a_site_cannot_point_at_a_user_who_does_not_exist():
    """The foreign key is enforced, not decorative."""
    with pytest.raises(sqlite3.IntegrityError), connect() as conn:
        conn.execute("INSERT INTO website VALUES (?, ?, 0, 0)", ("orphan", 9999))


async def test_listing_is_empty_before_anything_is_created():
    async with chat() as c:
        result = await c.call_tool("list_websites")

    assert result.structured_content["result"] == []


async def test_listing_returns_every_site_the_user_owns():
    async with chat() as c:
        await call(c, "create_website", slug="alpha")
        await call(c, "create_website", slug="beta")
        result = await c.call_tool("list_websites")

    assert [site["slug"] for site in result.structured_content["result"]] == [
        "alpha",
        "beta",
    ]


async def test_listing_shows_only_your_own_sites():
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices-place")

    async with chat("bob@example.com") as bob:
        await call(bob, "create_website", slug="bobs-place")
        result = await bob.call_tool("list_websites")

    assert [site["slug"] for site in result.structured_content["result"]] == [
        "bobs-place"
    ]


async def test_the_owner_deletes_their_own_site():
    async with chat() as c:
        await call(c, "create_website", slug="doomed")
        assert "deleted" in await call(c, "delete_website", slug="doomed")
        result = await c.call_tool("list_websites")

    assert result.structured_content["result"] == []
    with connect() as conn:
        assert not website.exists(conn, "doomed")


async def test_a_site_cannot_be_deleted_by_someone_else():
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices-only")

    async with chat("bob@example.com") as bob:
        with pytest.raises(ToolError, match="someone else"):
            await call(bob, "delete_website", slug="alices-only")

    with connect() as conn:
        assert website.exists(conn, "alices-only")


async def test_deleting_a_site_that_does_not_exist_is_refused():
    async with chat() as c:
        with pytest.raises(ToolError, match="no site"):
            await call(c, "delete_website", slug="ghost")


def test_the_service_deletes_the_row_and_queues_the_cleanup():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.create(conn, "cleaned", owner)
        website.delete(conn, "cleaned", owner)
        cleanup = job.latest_of(conn, (website.DELETE_KIND,), {"slug": "cleaned"})
        assert not website.exists(conn, "cleaned")

    assert cleanup is not None
    assert cleanup.status == "pending"


def test_a_deleted_slug_is_free_to_take_again():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        someone_else = user.ensure(conn, "someone-else@example.com")
        website.create(conn, "recycled", owner)
        website.delete(conn, "recycled", owner)
        site = website.create(conn, "recycled", someone_else)

    assert site.user_id == someone_else.id
    assert site.status == "pending"
