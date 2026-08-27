"""Adversarial tests: cross-tenant access and token misuse.

These probe the app the way an attacker would — one account reaching for
another's sites, and tokens presented where they should not work — and pin
the boundaries that must hold. Input-validation attacks (paths, slugs,
content) live with their tools in test_write_file / test_website.
"""

import pytest
from fastmcp.exceptions import ToolError

from cervo.auth import service
from cervo.db import connect
from tests.conftest import OWNER, Flow, call, chat, serving

STRANGER = "mallory@example.com"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── one account must never reach another's sites ──────────────────────────


async def test_no_owner_scoped_tool_touches_a_foreign_site():
    """write/delete_file and delete_website all refuse a slug owned elsewhere,
    and the site never appears in the stranger's own listing."""
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices")

    async with chat(STRANGER) as mallory:
        with pytest.raises(ToolError, match="someone else"):
            await call(
                mallory, "write_file", slug="alices", path="x.html", content="<p>x"
            )
        with pytest.raises(ToolError, match="someone else"):
            await call(mallory, "delete_file", slug="alices", path="index.html")
        with pytest.raises(ToolError, match="someone else"):
            await call(mallory, "delete_website", slug="alices")
        listing = await mallory.call_tool("list_websites")

    assert listing.structured_content["result"] == []


async def test_a_stranger_cannot_recreate_or_redeploy_a_taken_slug():
    """create_website on someone else's slug is refused, live or mid-flight,
    and never re-queues a deployment they could observe."""
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices")

    async with chat(STRANGER) as mallory:
        with pytest.raises(ToolError, match="already taken"):
            await call(mallory, "create_website", slug="alices")

    with connect() as conn:
        # exactly one deploy chain was ever queued — Alice's own
        (count,) = conn.execute(
            "SELECT count(*) FROM job WHERE payload LIKE ?", ('%"slug":"alices"%',)
        ).fetchone()
    assert count == 1


async def test_website_status_is_scoped_to_the_owner():
    """website_status refuses a slug the caller does not own — with the very
    same 'no site' error as a slug that never existed, so a stranger cannot
    confirm the site is there, let alone read its owner's id or deploy error.
    The owner still reads their own."""
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices-site")
        mine = await alice.call_tool("website_status", {"slug": "alices-site"})
    assert mine.structured_content["slug"] == "alices-site"  # the owner sees it

    async with chat(STRANGER) as mallory:
        with pytest.raises(ToolError, match="no site"):  # a real, foreign slug...
            await call(mallory, "website_status", slug="alices-site")
        with pytest.raises(ToolError, match="no site"):  # ...reads like a fake one
            await call(mallory, "website_status", slug="never-existed")


# ── a token is good only for what it is ───────────────────────────────────


async def test_revoking_a_refresh_token_drops_the_whole_grant():
    """Revoking a refresh token drops every token of that grant, so the
    access token minted alongside it stops resolving too — a stolen access
    token cannot outlive the revocation of its refresh token."""
    async with serving() as web:
        tokens = await Flow(web).sign_in(OWNER)

    with connect() as conn:
        assert service.load_access(conn, tokens["access_token"]) is not None
        assert service.load_refresh(conn, tokens["refresh_token"]) is not None

        service.revoke(conn, tokens["refresh_token"])

        assert service.load_access(conn, tokens["access_token"]) is None
        assert service.load_refresh(conn, tokens["refresh_token"]) is None


async def test_an_access_token_cannot_be_spent_as_a_refresh_token():
    """The token kinds are stored and looked up separately: an access token
    presented to the refresh grant matches nothing."""
    async with serving() as web:
        flow = Flow(web)
        tokens = await flow.sign_in(OWNER)
        response = await flow.refresh(tokens["access_token"])

    assert response.status_code in (400, 401)
    assert response.json()["error"] == "invalid_grant"


async def test_a_refresh_token_is_not_a_bearer_token():
    """A refresh token in the Authorization header is not an access token,
    so the MCP endpoint refuses it."""
    async with serving() as web:
        tokens = await Flow(web).sign_in(OWNER)
        response = await web.post(
            "/mcp", json={}, headers=_bearer(tokens["refresh_token"])
        )

    assert response.status_code == 401


async def test_a_forged_bearer_token_is_refused():
    """A random string is not a live token — no lookup matches it."""
    async with serving() as web:
        await Flow(web).sign_in(OWNER)  # a real grant exists, but this isn't it
        response = await web.post(
            "/mcp", json={}, headers=_bearer("not-a-real-token-000000000000")
        )

    assert response.status_code == 401


async def test_the_domain_of_an_address_is_case_folded_to_one_account():
    """Owning happens per address; the email domain is normalized, so casing
    it differently cannot fork a second account (nor a way to dodge the
    owner it resolves to)."""
    async with chat("owner@EXAMPLE.COM") as c:
        await c.call_tool("list_websites")
    async with chat("owner@example.com") as c:
        await c.call_tool("list_websites")

    with connect() as conn:
        rows = conn.execute("SELECT email FROM user").fetchall()

    assert len(rows) == 1
