"""Creating and deleting sites, and who is allowed to."""

import sqlite3
from datetime import UTC, datetime

import pytest
from fastmcp.exceptions import ToolError

from cervo import job, user, website
from cervo.db import connect
from tests.conftest import OWNER, call, chat, sign_in


async def test_creating_a_site_needs_a_signed_in_chat():
    async with chat() as c:
        with pytest.raises(ToolError, match="not authenticated"):
            await call(c, "create_website", slug="mine")


async def test_nothing_is_written_when_the_chat_is_not_signed_in():
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "create_website", slug="mine")

    with connect() as conn:
        assert not website.exists(conn, "mine")


async def test_the_owner_comes_from_the_session(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox, "alice@example.com")
        result = await c.call_tool("create_website", {"slug": "alices-site"})

    with connect() as conn:
        alice = user.by_email(conn, "alice@example.com")

    content = dict(result.structured_content)
    assert content.pop("created_at") == content.pop("updated_at")
    assert content == {
        "slug": "alices-site",
        "user_id": alice.id,
        "status": "pending",
        "error": None,
        "url": "http://alices-site.localhost",
    }


async def test_recreating_your_own_site_mid_deployment_is_refused(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="taken")
        with pytest.raises(ToolError, match="in progress"):
            await call(c, "create_website", slug="taken")


async def test_someone_else_cannot_take_a_slug_that_exists(mailbox):
    async with chat() as alice:
        await sign_in(alice, mailbox, "alice@example.com")
        await call(alice, "create_website", slug="contested")

    async with chat() as bob:
        await sign_in(bob, mailbox, "bob@example.com")
        with pytest.raises(ToolError, match="already taken"):
            await call(bob, "create_website", slug="contested")

    with connect() as conn:
        alice = user.by_email(conn, "alice@example.com")
        row = conn.execute(
            "SELECT * FROM website WHERE slug = ?", ("contested",)
        ).fetchone()
        assert row["user_id"] == alice.id


@pytest.mark.parametrize(
    "slug", ["Upper", "under_score", "-leading", "trailing-", "has space", ""]
)
async def test_a_slug_that_is_not_dns_safe_is_rejected(slug, mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        with pytest.raises(ToolError):
            await call(c, "create_website", slug=slug)


@pytest.mark.parametrize("slug", ["a", "site", "my-site", "a1-b2-c3"])
async def test_dns_safe_slugs_are_accepted(slug, mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        assert slug in await call(c, "create_website", slug=slug)


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


def test_a_reserved_slug_is_refused():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        with pytest.raises(website.WebsiteError, match="reserved"):
            website.create(conn, "caddyfile", owner)
        assert not website.exists(conn, "caddyfile")


def test_creating_a_site_queues_its_deployment():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        site = website.create(conn, "queued", owner)
        deployment = job.latest(conn, website.DEPLOY_KIND, {"slug": "queued"})

    assert site.status == "pending"
    assert deployment is not None
    assert deployment.status == "pending"


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
        conn.execute("UPDATE job SET status = 'done'")

    with connect() as conn:
        with pytest.raises(website.WebsiteError, match="live"):
            website.create(conn, "settled", owner)
        assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == 1


def test_a_site_cannot_point_at_a_user_who_does_not_exist():
    """The foreign key is enforced, not decorative."""
    with pytest.raises(sqlite3.IntegrityError), connect() as conn:
        conn.execute("INSERT INTO website VALUES (?, ?, 0, 0)", ("orphan", 9999))


async def test_listing_needs_a_signed_in_chat():
    async with chat() as c:
        with pytest.raises(ToolError, match="not authenticated"):
            await call(c, "list_websites")


async def test_listing_is_empty_before_anything_is_created(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        result = await c.call_tool("list_websites")

    assert result.structured_content["result"] == []


async def test_listing_returns_every_site_the_user_owns(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="alpha")
        await call(c, "create_website", slug="beta")
        result = await c.call_tool("list_websites")

    assert [site["slug"] for site in result.structured_content["result"]] == [
        "alpha",
        "beta",
    ]


async def test_listing_shows_only_your_own_sites(mailbox):
    async with chat() as alice:
        await sign_in(alice, mailbox, "alice@example.com")
        await call(alice, "create_website", slug="alices-place")

    async with chat() as bob:
        await sign_in(bob, mailbox, "bob@example.com")
        await call(bob, "create_website", slug="bobs-place")
        result = await bob.call_tool("list_websites")

    assert [site["slug"] for site in result.structured_content["result"]] == [
        "bobs-place"
    ]


async def test_deleting_a_site_needs_a_signed_in_chat():
    async with chat() as c:
        with pytest.raises(ToolError, match="not authenticated"):
            await call(c, "delete_website", slug="mine")


async def test_the_owner_deletes_their_own_site(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="doomed")
        assert "deleted" in await call(c, "delete_website", slug="doomed")
        result = await c.call_tool("list_websites")

    assert result.structured_content["result"] == []
    with connect() as conn:
        assert not website.exists(conn, "doomed")


async def test_a_site_cannot_be_deleted_by_someone_else(mailbox):
    async with chat() as alice:
        await sign_in(alice, mailbox, "alice@example.com")
        await call(alice, "create_website", slug="alices-only")

    async with chat() as bob:
        await sign_in(bob, mailbox, "bob@example.com")
        with pytest.raises(ToolError, match="someone else"):
            await call(bob, "delete_website", slug="alices-only")

    with connect() as conn:
        assert website.exists(conn, "alices-only")


async def test_deleting_a_site_that_does_not_exist_is_refused(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        with pytest.raises(ToolError, match="no site"):
            await call(c, "delete_website", slug="ghost")


def test_the_service_deletes_the_row_and_queues_the_cleanup():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.create(conn, "cleaned", owner)
        website.delete(conn, "cleaned", owner)
        cleanup = job.latest(conn, website.DELETE_KIND, {"slug": "cleaned"})
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
