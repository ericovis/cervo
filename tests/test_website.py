"""Creating sites, and who is allowed to."""

import sqlite3

import pytest
from fastmcp.exceptions import ToolError

from cervo import user, website
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

    assert result.structured_content == {"slug": "alices-site", "user_id": alice.id}


async def test_a_taken_slug_is_refused(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="taken")
        with pytest.raises(ToolError, match="already taken"):
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


def test_the_service_refuses_a_duplicate_slug():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        someone_else = user.ensure(conn, "someone-else@example.com")
        website.create(conn, "once", owner)
        with pytest.raises(website.WebsiteError, match="already taken"):
            website.create(conn, "once", someone_else)


def test_a_site_cannot_point_at_a_user_who_does_not_exist():
    """The foreign key is enforced, not decorative."""
    with pytest.raises(sqlite3.IntegrityError), connect() as conn:
        conn.execute("INSERT INTO website VALUES (?, ?)", ("orphan", 9999))


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
