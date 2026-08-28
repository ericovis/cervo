"""Smoke tests: the whole stack, driven through a real MCP client.

Deliberately not collected by a plain ``pytest`` run — the filename does not
match ``test_*.py`` — because these need the test stack up and reach it by
service name, so they only run from inside that network (``bin/smoke``).

Everything goes through the front door, exactly like Claude: OAuth discovery
and sign-in against ``http://{DOMAIN}`` (this runner shares caddy's network
namespace, so localhost is the front door), verification codes read from
mailcatcher's API the way a user reads their inbox, the MCP endpoint reached
with the minted Bearer token, and deployed sites fetched via caddy with a
Host header.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

DOMAIN = os.environ.get("DOMAIN", "localhost")
MAIL_API = "http://mail:1080"
CALLBACK = "http://localhost:33418/callback"

TOOLS = {
    "create_website",
    "write_file",
    "delete_file",
    "list_websites",
    "delete_website",
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
    """Wait out the first boot: caddy serves a stub until the worker's render."""

    def answers() -> None:
        assert "cervo" in _get(f"http://{DOMAIN}/")

    _wait_for(answers, "caddy to proxy cervo")


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def mail_to(email: str) -> list[dict]:
    """Everything mailcatcher holds for this recipient."""
    messages = json.loads(_get(f"{MAIL_API}/messages"))
    return [m for m in messages if f"<{email}>" in m["recipients"]]


def emailed_code(email: str) -> str:
    message = _wait_for(
        lambda: mail_to(email)[-1], f"the verification email for {email}", timeout=15
    )
    body = _get(f"{MAIL_API}/messages/{message['id']}.plain")
    return re.search(r"code is: (\d{6})", body).group(1)


class Flow:
    """The OAuth dance against the real front door, the way Claude runs it."""

    def __init__(self):
        self.web = httpx.Client(
            base_url=f"http://{DOMAIN}", follow_redirects=False, timeout=10
        )
        self.verifier = secrets.token_urlsafe(43)
        digest = hashlib.sha256(self.verifier.encode()).digest()
        self.challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self.state = secrets.token_urlsafe(8)
        self.client_id: str | None = None
        self.txn: str | None = None

    def register(self) -> str:
        response = self.web.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "smoke",
            },
        )
        assert response.status_code == 201, response.text
        self.client_id = response.json()["client_id"]
        return self.client_id

    def authorize(self) -> str:
        if self.client_id is None:
            self.register()
        response = self.web.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": CALLBACK,
                "state": self.state,
                "code_challenge": self.challenge,
                "code_challenge_method": "S256",
            },
        )
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        self.txn = parse_qs(urlparse(location).query)["txn"][0]
        return self.txn

    def submit_email(self, email: str) -> httpx.Response:
        return self.web.post("/verify/email", data={"txn": self.txn, "email": email})

    def submit_code(self, code: str) -> httpx.Response:
        return self.web.post("/verify/code", data={"txn": self.txn, "code": code})

    def sign_in(self, email: str) -> str:
        """The whole handshake; returns the access token."""
        self.authorize()
        assert self.submit_email(email).status_code == 303
        response = self.submit_code(emailed_code(email))
        assert response.status_code == 302, response.text
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["state"] == [self.state]
        response = self.web.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": CALLBACK,
                "client_id": self.client_id,
                "code_verifier": self.verifier,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]


def chat(email: str) -> Client:
    """One signed-in conversation against the real server."""
    return Client(f"http://{DOMAIN}/mcp", auth=Flow().sign_in(email))


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
    async with chat(f"{unique('tools')}@example.com") as client:
        tools = {tool.name for tool in await client.list_tools()}
    assert tools == TOOLS


def test_the_metadata_advertises_oauth_with_cimd():
    """What claude.ai reads before offering "hosted client metadata"."""
    metadata = json.loads(
        _get(f"http://{DOMAIN}/.well-known/oauth-authorization-server")
    )
    assert metadata["client_id_metadata_document_supported"] is True
    assert "none" in metadata["token_endpoint_auth_methods_supported"]

    resource = json.loads(
        _get(f"http://{DOMAIN}/.well-known/oauth-protected-resource/mcp")
    )
    assert resource["resource"] == f"http://{DOMAIN}/mcp"


def test_signing_in_takes_a_real_emailed_code():
    email = f"{unique('signin')}@example.com"
    flow = Flow()
    flow.authorize()
    assert flow.submit_email(email).status_code == 303

    (message,) = mail_to(email)
    assert message["sender"].startswith("<cervo@localhost>")
    assert message["subject"] == "Your cervo verification code"
    code = emailed_code(email)

    wrong = "000000" if code != "000000" else "111111"
    response = flow.submit_code(wrong)
    assert "attempts left" in response.text

    response = flow.submit_code(code)
    assert response.status_code == 302
    assert response.headers["location"].startswith(CALLBACK)


def test_the_mcp_endpoint_demands_a_token():
    response = httpx.post(f"http://{DOMAIN}/mcp", json={}, timeout=10)
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


async def test_bad_slugs_are_rejected():
    async with chat(f"{unique('slugs')}@example.com") as client:
        for slug in ("Not-Valid", "under_score", "-leading", ""):
            with pytest.raises(ToolError):
                await client.call_tool("create_website", {"slug": slug})
        with pytest.raises(ToolError, match="reserved"):
            await client.call_tool("create_website", {"slug": "caddyfile"})


async def test_a_site_is_created_deployed_and_served():
    email = f"{unique('owner')}@example.com"
    slug = unique("smoke")
    async with chat(email) as client:
        result = await client.call_tool("create_website", {"slug": slug})
        site = result.structured_content
        assert site["error"] is None
        assert site["url"] == f"http://{slug}.{DOMAIN}"

        # The client sends a progress token, so the tool follows the chain
        # and normally hands the site back already live; wait out the rare
        # case where it gave up before the worker finished.
        if site["status"] != "live":
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


async def test_a_written_file_is_served():
    """write_file puts an owner's page online; bad paths never get that far."""
    email = f"{unique('writer')}@example.com"
    slug = unique("written")
    content = "<!doctype html><title>smoke</title><h1>written by hand</h1>"
    async with chat(email) as client:
        site = await client.call_tool("create_website", {"slug": slug})
        if site.structured_content["status"] != "live":
            await wait_for_deployment(client, slug)

        result = await client.call_tool(
            "write_file",
            {"slug": slug, "path": "blog/post.html", "content": content},
        )
        write = result.structured_content
        assert write["status"] in ("done", "pending"), write
        assert write["url"] == f"http://{slug}.{DOMAIN}/blog/post.html"

        with pytest.raises(ToolError):
            await client.call_tool(
                "write_file",
                {"slug": slug, "path": "../escape.html", "content": content},
            )
        with pytest.raises(ToolError):
            await client.call_tool(
                "write_file",
                {"slug": slug, "path": "script.js", "content": content},
            )

        page = _wait_for(
            lambda: _get(f"http://{DOMAIN}/blog/post.html", host=f"{slug}.{DOMAIN}"),
            "the written file to be served",
            timeout=15,
        )
        assert "written by hand" in page

        result = await client.call_tool(
            "delete_file", {"slug": slug, "path": "blog/post.html"}
        )
        deletion = result.structured_content
        assert deletion["status"] in ("done", "pending"), deletion

    def file_gone() -> None:
        try:
            _get(f"http://{DOMAIN}/blog/post.html", host=f"{slug}.{DOMAIN}")
        except urllib.error.HTTPError as error:
            assert error.code == 404
            return
        raise AssertionError("the deleted file is still being served")

    _wait_for(file_gone, "the deleted file to stop being served", timeout=15)


async def test_a_followed_creation_reports_progress_and_returns_live():
    """A progress token makes create_website follow the deployment chain."""
    email = f"{unique('progress')}@example.com"
    slug = unique("followed")
    updates = []

    async def on_progress(progress, total, message):
        updates.append((progress, total, message))

    async with chat(email) as client:
        result = await client.call_tool(
            "create_website", {"slug": slug}, progress_handler=on_progress
        )

    site = result.structured_content
    assert site["status"] == "live", site
    assert updates, "no progress notifications arrived"
    steps = [progress for progress, _, _ in updates]
    assert steps[0] == 0 and steps[-1] == 3
    assert all(total == 3 for _, total, _ in updates)


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
    async with chat(f"{unique('owner')}@example.com") as owner:
        await owner.call_tool("create_website", {"slug": slug})
        await wait_for_deployment(owner, slug)

    page = _get(f"http://{DOMAIN}/")
    assert slug in page
    assert f"http://{slug}.{DOMAIN}" in page


async def test_the_progress_app_can_follow_a_deployment():
    slug = unique("watched")
    async with chat(f"{unique('owner')}@example.com") as owner:
        await owner.call_tool("create_website", {"slug": slug})
        await wait_for_deployment(owner, slug)

        # the progress app polls website_status from the owner's own session
        result = await owner.call_tool("website_status", {"slug": slug})
        site = json.loads(result.content[0].text)
        assert site["status"] == "live"
        assert site["url"] == f"http://{slug}.{DOMAIN}"

    # and website_status is owner-scoped: a stranger cannot read it
    async with chat(f"{unique('stranger')}@example.com") as stranger:
        with pytest.raises(ToolError, match="no site"):
            await stranger.call_tool("website_status", {"slug": slug})


async def test_a_slug_cannot_be_taken_from_its_owner():
    slug = unique("contested")
    async with chat(f"{unique('alice')}@example.com") as alice:
        await alice.call_tool("create_website", {"slug": slug})

    async with chat(f"{unique('bob')}@example.com") as bob:
        with pytest.raises(ToolError, match="already taken"):
            await bob.call_tool("create_website", {"slug": slug})


async def test_each_owner_sees_only_their_own_sites():
    mine, theirs = unique("mine"), unique("theirs")
    async with chat(f"{unique('alice')}@example.com") as alice:
        await alice.call_tool("create_website", {"slug": mine})

    async with chat(f"{unique('bob')}@example.com") as bob:
        await bob.call_tool("create_website", {"slug": theirs})
        result = await bob.call_tool("list_websites")

    listed = [s["slug"] for s in result.structured_content["result"]]
    assert theirs in listed
    assert mine not in listed


async def test_a_deleted_site_stops_being_served():
    slug = unique("gone")
    async with chat(f"{unique('owner')}@example.com") as client:
        await client.call_tool("create_website", {"slug": slug})
        await wait_for_deployment(client, slug)

        result = await client.call_tool("delete_website", {"slug": slug})
        assert "deleted" in result.content[0].text

        listing = await client.call_tool("list_websites")
        assert slug not in [s["slug"] for s in listing.structured_content["result"]]

    def gone():
        try:
            body = _get(f"http://{DOMAIN}/", host=f"{slug}.{DOMAIN}")
        except urllib.error.HTTPError:
            return
        assert slug not in body, "the site is still being served"

    _wait_for(gone, "the deleted site to stop being served", timeout=30)


async def test_an_unknown_subdomain_serves_no_site():
    try:
        body = _get(f"http://{DOMAIN}/", host=f"{unique('ghost')}.{DOMAIN}")
    except urllib.error.HTTPError:
        return
    assert "live on cervo" not in body
