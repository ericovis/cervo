"""The authorization server: the browser sign-in, and the tokens it mints."""

import pytest

from cervo.auth import service
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

        response = await flow.submit_email(OWNER)
        assert response.status_code == 303
        assert response.headers["cache-control"] == "no-store"

        page = await web.get(response.headers["location"])
        assert 'name="code"' in page.text, "the code form did not appear"
        assert page.headers["cache-control"] == "no-store"


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
