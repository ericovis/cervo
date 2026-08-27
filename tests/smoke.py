"""Smoke tests: the whole stack, driven through a real MCP client.

Deliberately not collected by a plain ``pytest`` run — the filename does not
match ``test_*.py`` — because these need the test stack up and reach it by
service name, so they only run from inside that network (``bin/smoke``).

Everything goes through the front door, exactly like a real client: the MCP
server at ``http://{DOMAIN}/mcp``, sign-in codes read from mailcatcher's API
the way a user reads their inbox, and deployed sites fetched via caddy with a
Host header (only service names resolve inside the network).
"""

import asyncio
import contextlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

DOMAIN = os.environ.get("DOMAIN", "localhost")
MAIL_API = "http://mail:1080"

TOOLS = {
    "authenticate",
    "confirm_authentication",
    "authentication_status",
    "create_website",
    "list_websites",
    "website_status",
}


def _get(url: str, host: str | None = None) -> str:
    request = urllib.request.Request(url, headers={"Host": host} if host else {})
    return urllib.request.urlopen(request, timeout=5).read().decode()


def _wait_for(check, what: str, timeout: float = 60):
    """Retry until ``check`` stops raising; the stack may still be booting."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check()
        except Exception as error:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"gave up waiting for {what}: {error}") from error
            time.sleep(1)


@pytest.fixture(scope="module", autouse=True)
def front_door():
    """Wait out the first boot: caddy restarts until the worker's render."""

    def answers() -> None:
        with contextlib.suppress(urllib.error.HTTPError):
            _get(f"http://{DOMAIN}/")

    _wait_for(answers, "caddy to proxy cervo")


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def chat(*, action: str = "accept") -> Client:
    """One conversation against the real server, with a scripted human."""

    async def handler(message, response_type, params, context):
        if action != "accept":
            return ElicitResult(action=action)
        proposed = params.requestedSchema["properties"]["email"]["default"]
        return response_type(email=proposed)

    return Client(f"http://{DOMAIN}/mcp", elicitation_handler=handler)


def mail_to(email: str) -> list[dict]:
    """Everything mailcatcher holds for this recipient."""
    messages = json.loads(_get(f"{MAIL_API}/messages"))
    return [m for m in messages if f"<{email}>" in m["recipients"]]


def mail_body(message: dict) -> str:
    return _get(f"{MAIL_API}/messages/{message['id']}.plain")


def emailed_code(email: str) -> str:
    message = _wait_for(
        lambda: mail_to(email)[-1], f"the sign-in email for {email}", timeout=15
    )
    return re.search(r"code is: (\d{6})", mail_body(message)).group(1)


async def sign_in(client: Client, email: str) -> None:
    await client.call_tool("authenticate", {"email": email})
    result = await client.call_tool(
        "confirm_authentication", {"code": emailed_code(email)}
    )
    assert "Signed in" in result.content[0].text


async def wait_for_deployment(client: Client, slug: str) -> dict:
    """Poll through the tools, the way an agent would."""
    deadline = time.monotonic() + 60
    while True:
        result = await client.call_tool("list_websites")
        (site,) = [s for s in result.structured_content["result"] if s["slug"] == slug]
        if site["status"] in ("live", "failed") or time.monotonic() > deadline:
            return site
        await asyncio.sleep(1)


async def test_every_tool_is_published():
    async with chat() as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert tools == TOOLS


async def test_signing_in_takes_a_real_emailed_code():
    email = f"{unique('signin')}@example.com"
    async with chat() as client:
        status = await client.call_tool("authentication_status")
        assert "not signed in" in status.content[0].text

        await client.call_tool("authenticate", {"email": email})

        (message,) = mail_to(email)
        assert message["sender"].startswith("<cervo@localhost>")
        assert message["subject"] == "Your cervo confirmation code"
        body = mail_body(message)
        code = re.search(r"code is: (\d{6})", body).group(1)
        assert "expires" in body

        wrong = "000000" if code != "000000" else "111111"
        with pytest.raises(ToolError, match="not right"):
            await client.call_tool("confirm_authentication", {"code": wrong})

        result = await client.call_tool("confirm_authentication", {"code": code})
        assert "Signed in" in result.content[0].text

        status = await client.call_tool("authentication_status")
        assert email in status.content[0].text


async def test_a_sign_in_does_not_leak_into_other_conversations():
    email = f"{unique('private')}@example.com"
    async with chat() as client:
        await sign_in(client, email)

    async with chat() as fresh:
        status = await fresh.call_tool("authentication_status")
        assert "not signed in" in status.content[0].text


async def test_declining_the_confirmation_sends_nothing():
    email = f"{unique('declined')}@example.com"
    async with chat(action="decline") as client:
        with pytest.raises(ToolError, match="did not confirm"):
            await client.call_tool("authenticate", {"email": email})
    assert mail_to(email) == []


async def test_site_tools_demand_a_signed_in_chat():
    async with chat() as client:
        with pytest.raises(ToolError, match="not authenticated"):
            await client.call_tool("create_website", {"slug": unique("nope")})
        with pytest.raises(ToolError, match="not authenticated"):
            await client.call_tool("list_websites")


async def test_bad_slugs_are_rejected():
    async with chat() as client:
        await sign_in(client, f"{unique('slugs')}@example.com")
        for slug in ("Not-Valid", "under_score", "-leading", ""):
            with pytest.raises(ToolError):
                await client.call_tool("create_website", {"slug": slug})
        with pytest.raises(ToolError, match="reserved"):
            await client.call_tool("create_website", {"slug": "caddyfile"})


async def test_a_site_is_created_deployed_and_served():
    email = f"{unique('owner')}@example.com"
    slug = unique("smoke")
    async with chat() as client:
        await sign_in(client, email)

        result = await client.call_tool("create_website", {"slug": slug})
        site = result.structured_content
        assert site["status"] == "pending"
        assert site["error"] is None
        assert site["url"] == f"http://{slug}.{DOMAIN}"

        with pytest.raises(ToolError, match="already own"):
            await client.call_tool("create_website", {"slug": slug})

        site = await wait_for_deployment(client, slug)
        assert site["status"] == "live", site
        assert site["error"] is None

        with pytest.raises(ToolError, match="live"):
            await client.call_tool("create_website", {"slug": slug})

    page = _wait_for(
        lambda: _get(f"http://{DOMAIN}/", host=f"{slug}.{DOMAIN}"),
        "the site to be served",
        timeout=15,
    )
    assert slug in page
    assert "cervo" in page


def test_the_homepage_is_served():
    page = _get(f"http://{DOMAIN}/")
    assert ">cervo</a>" in page  # the wordmark
    assert f"http://{DOMAIN}/mcp" in page  # the endpoint chip
    assert 'href="/docs"' in page


def test_docs_terms_and_privacy_are_served():
    assert "HOW DEPLOYMENTS WORK" in _get(f"http://{DOMAIN}/docs")
    assert "TERMS OF SERVICE" in _get(f"http://{DOMAIN}/terms")
    assert "PRIVACY" in _get(f"http://{DOMAIN}/privacy")


def test_unknown_paths_get_the_styled_404():
    with pytest.raises(urllib.error.HTTPError) as error:
        _get(f"http://{DOMAIN}/no-such-page")
    assert error.value.code == 404
    assert "NOT FOUND" in error.value.read().decode()


async def test_the_homepage_catalogs_live_sites():
    slug = unique("public")
    async with chat() as owner:
        await sign_in(owner, f"{unique('owner')}@example.com")
        await owner.call_tool("create_website", {"slug": slug})
        await wait_for_deployment(owner, slug)

    page = _get(f"http://{DOMAIN}/")
    assert slug in page
    assert f"http://{slug}.{DOMAIN}" in page


async def test_the_progress_app_can_follow_a_deployment():
    slug = unique("watched")
    async with chat() as owner:
        await sign_in(owner, f"{unique('owner')}@example.com")
        await owner.call_tool("create_website", {"slug": slug})
        await wait_for_deployment(owner, slug)

    async with chat() as page:  # the app's poll needs no sign-in
        result = await page.call_tool("website_status", {"slug": slug})

    site = json.loads(result.content[0].text)
    assert site["status"] == "live"
    assert site["url"] == f"http://{slug}.{DOMAIN}"


async def test_a_slug_cannot_be_taken_from_its_owner():
    slug = unique("contested")
    async with chat() as alice:
        await sign_in(alice, f"{unique('alice')}@example.com")
        await alice.call_tool("create_website", {"slug": slug})

    async with chat() as bob:
        await sign_in(bob, f"{unique('bob')}@example.com")
        with pytest.raises(ToolError, match="already taken"):
            await bob.call_tool("create_website", {"slug": slug})


async def test_each_owner_sees_only_their_own_sites():
    mine, theirs = unique("mine"), unique("theirs")
    async with chat() as alice:
        await sign_in(alice, f"{unique('alice')}@example.com")
        await alice.call_tool("create_website", {"slug": mine})

    async with chat() as bob:
        await sign_in(bob, f"{unique('bob')}@example.com")
        await bob.call_tool("create_website", {"slug": theirs})
        result = await bob.call_tool("list_websites")

    listed = [s["slug"] for s in result.structured_content["result"]]
    assert theirs in listed
    assert mine not in listed


async def test_an_unknown_subdomain_serves_no_site():
    try:
        body = _get(f"http://{DOMAIN}/", host=f"{unique('ghost')}.{DOMAIN}")
    except urllib.error.HTTPError:
        return
    assert "live on cervo" not in body
