"""The authorization server: the browser sign-in, and the tokens it mints."""

import pytest

from cervo.auth import CervoOAuthProvider, service
from cervo.db import connect
from tests.conftest import OWNER, Flow, call, chat, serving


async def test_the_metadata_advertises_cimd():
    """claude.ai only offers "hosted client metadata" when both flags show."""
    async with serving() as web:
        metadata = (await web.get("/.well-known/oauth-authorization-server")).json()

    assert metadata["client_id_metadata_document_supported"] is True
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]


async def test_the_mcp_endpoint_demands_a_token():
    async with serving() as web:
        response = await web.post("/mcp", json={})

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


async def test_only_claude_clients_may_register():
    """DCR is open, but only for Claude's callbacks (loopback or claude.ai).

    A self-registered client pointing at an attacker's own server is what
    turns the genuine sign-in into a confused-deputy phishing vector, so it is
    refused.
    """
    base = {
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "probe",
    }
    async with serving() as web:
        loopback = await web.post(
            "/register",
            json={**base, "redirect_uris": ["http://localhost:33418/callback"]},
        )
        hosted = await web.post(
            "/register",
            json={**base, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]},
        )
        attacker = await web.post(
            "/register", json={**base, "redirect_uris": ["https://attacker.example/cb"]}
        )

    assert loopback.status_code == 201  # Claude Code
    assert hosted.status_code == 201  # claude.ai / Desktop
    assert attacker.status_code == 400
    assert attacker.json()["error"] == "invalid_redirect_uri"


async def test_a_cimd_document_from_a_non_claude_host_is_refused():
    """A CIMD client_id is any https URL, so only Claude's host is honored."""
    provider = CervoOAuthProvider()
    assert await provider.get_client("https://attacker.example/cimd.json") is None


async def test_the_whole_flow_signs_a_chat_in(mailbox):
    async with chat() as c:
        result = await c.call_tool("list_websites")

    assert result.structured_content["result"] == []
    assert mailbox.last.to == OWNER
    assert "verification code" in mailbox.last.subject.lower()


async def test_the_emailed_code_is_never_stored_in_the_clear(data_dir, mailbox):
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email(OWNER)

    code = mailbox.last_code
    database = (data_dir / "cervo.db").read_bytes().decode("latin-1")
    assert code not in database


async def test_submitting_the_email_advances_to_the_code_form(mailbox):
    """The same URL serves each step, so caching it would freeze the flow."""
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()

        page = await web.get(f"/verify?txn={flow.txn}")
        assert 'name="email"' in page.text
        assert page.headers["cache-control"] == "no-store"
        # agreeing to the terms is a tick box on the form, not fine print
        assert 'name="accept"' in page.text
        assert 'href="/terms"' in page.text
        assert 'href="/privacy"' in page.text

        response = await flow.submit_email(OWNER)
        assert response.status_code == 303
        assert response.headers["cache-control"] == "no-store"

        page = await web.get(response.headers["location"])
        assert 'name="code"' in page.text, "the code form did not appear"
        assert page.headers["cache-control"] == "no-store"


async def test_no_code_is_sent_until_the_terms_are_accepted(mailbox):
    """The tick box is "required" in the markup, which only binds a browser."""
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()

        response = await flow.submit_email(OWNER, accept=False)

        assert response.status_code == 200  # the form again, not the code step
        assert "Please accept the terms" in response.text
        assert 'name="code"' not in response.text
        assert mailbox == [], "a code was mailed without consent"
        # the address survives the refusal, so it need not be typed again
        assert OWNER in response.text

        assert (await flow.submit_email(OWNER)).status_code == 303
        assert mailbox.last.to == OWNER


async def test_the_email_links_back_to_the_sign_in_page(mailbox):
    """Closing the tab must not strand the user — the mail holds the way back."""
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email(OWNER)

    assert f"http://localhost/verify?txn={flow.txn}" in mailbox.last.body


async def test_a_wrong_code_counts_down_the_attempts(mailbox):
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email(OWNER)

        response = await flow.submit_code("000000")
        assert "attempts left" in response.text

        # the real code still works while attempts remain
        response = await flow.submit_code(mailbox.last_code)
        assert response.status_code == 302


async def test_too_many_wrong_codes_ends_the_attempt(mailbox):
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email(OWNER)

        for _ in range(20):
            response = await flow.submit_code("000000")
            if "connect again" in response.text:
                break
        else:
            pytest.fail("the attempt limit never kicked in")

        # even the correct code is now useless
        response = await flow.submit_code(mailbox.last_code)
        assert response.status_code == 400


async def test_reissuing_a_code_invalidates_the_previous_one(mailbox):
    """Changing the address mid-flow leaves only the new code working."""
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email("first@example.com")
        stale = mailbox.last_code
        await flow.submit_email(OWNER)

        if stale != mailbox.last_code:
            response = await flow.submit_code(stale)
            assert "attempts left" in response.text

        response = await flow.submit_code(mailbox.last_code)
        assert response.status_code == 302
        assert mailbox.last.to == OWNER


async def test_codes_to_one_address_are_capped(mailbox):
    """The unauthenticated send endpoint cannot flood a chosen inbox.

    A recipient takes at most _CODE_SENDS_PER_WINDOW codes per window; the
    next request is refused and re-renders the email form rather than mailing.
    """
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        for _ in range(service._CODE_SENDS_PER_WINDOW):
            assert (await flow.submit_email(OWNER)).status_code == 303
        blocked = await flow.submit_email(OWNER)

    assert blocked.status_code == 200  # the email form, re-rendered with the reason
    assert "Too many codes" in blocked.text
    assert len(mailbox) == service._CODE_SENDS_PER_WINDOW  # the extra was not sent


async def test_an_expired_sign_in_is_refused(monkeypatch):
    monkeypatch.setattr(service, "_TXN_TTL", 0)
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        response = await flow.submit_email(OWNER)

    assert response.status_code == 400
    assert "expired" in response.text


async def test_an_authorization_code_is_single_use(mailbox):
    async with serving() as web:
        flow = Flow(web)
        tokens = await flow.sign_in(OWNER)
        assert tokens["access_token"]

        # sign_in already spent the code; replay it through a fresh flow
        flow2 = Flow(web)
        await flow2.authorize()
        await flow2.submit_email(OWNER)
        response = await flow2.submit_code(mailbox.last_code)
        code = response.headers["location"].split("code=")[1].split("&")[0]

        assert (await flow2.exchange(code)).status_code == 200
        replay = await flow2.exchange(code)
        assert replay.status_code in (400, 401)
        assert replay.json()["error"] == "invalid_grant"


async def test_the_token_exchange_verifies_pkce(mailbox):
    async with serving() as web:
        flow = Flow(web)
        await flow.authorize()
        await flow.submit_email(OWNER)
        response = await flow.submit_code(mailbox.last_code)
        code = response.headers["location"].split("code=")[1].split("&")[0]

        flow.verifier = "not-the-right-verifier-at-all-0000000000000"
        response = await flow.exchange(code)

    assert response.status_code in (400, 401)


async def test_a_refresh_token_rotates_on_use():
    async with serving() as web:
        flow = Flow(web)
        tokens = await flow.sign_in(OWNER)

        refreshed = await flow.refresh(tokens["refresh_token"])
        assert refreshed.status_code == 200
        assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

        # the spent refresh token is dead
        replay = await flow.refresh(tokens["refresh_token"])
        assert replay.status_code in (400, 401)
        assert replay.json()["error"] == "invalid_grant"


async def test_revoking_an_access_token_kills_the_whole_grant():
    """Revoking either token type ends the session (RFC 7009).

    The access token's never-expiring sibling refresh token must not outlive
    it — otherwise a client that revoked its access token believing the
    session was over could refresh straight back in.
    """
    async with serving() as web:
        tokens = await Flow(web).sign_in(OWNER)

    with connect() as conn:
        assert service.load_access(conn, tokens["access_token"]) is not None
        assert service.load_refresh(conn, tokens["refresh_token"]) is not None

        service.revoke(conn, tokens["access_token"])

        assert service.load_access(conn, tokens["access_token"]) is None
        assert service.load_refresh(conn, tokens["refresh_token"]) is None


async def test_an_expired_access_token_stops_working(monkeypatch):
    monkeypatch.setattr(service, "_ACCESS_TOKEN_TTL", 0)
    async with serving() as web:
        tokens = await Flow(web).sign_in(OWNER)
        response = await web.post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_two_connections_hold_separate_identities():
    async with chat("alice@example.com") as alice:
        await call(alice, "create_website", slug="alices")
    async with chat("bob@example.com") as bob:
        listing = await bob.call_tool("list_websites")

    assert listing.structured_content["result"] == []
