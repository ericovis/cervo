"""How long a sign-in lasts, and how far it reaches."""

import pytest
from fastmcp.exceptions import ToolError

from cervo import config
from tests.conftest import OWNER, call, chat, sign_in


async def test_a_session_covers_later_calls():
    async with chat() as c:
        await sign_in(c)
        await call(c, "create_website", slug="one")
        await call(c, "create_website", slug="two")


async def test_a_new_conversation_starts_signed_out():
    """The same person in a new chat has to confirm their email again."""
    async with chat() as first:
        await sign_in(first)

    async with chat() as second:
        assert "not signed in" in await call(second, "authentication_status")
        with pytest.raises(ToolError, match="not authenticated"):
            await call(second, "create_website", slug="anything")


async def test_two_conversations_hold_separate_identities():
    async with chat() as alice, chat() as bob:
        await sign_in(alice, "alice@example.com")
        await sign_in(bob, "bob@example.com")

        assert "alice@example.com" in await call(alice, "authentication_status")
        assert "bob@example.com" in await call(bob, "authentication_status")


async def test_an_expired_session_stops_working(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SESSION_TTL", 0)
    async with chat() as c:
        await sign_in(c)
        assert "not signed in" in await call(c, "authentication_status")
        with pytest.raises(ToolError, match="expired"):
            await call(c, "create_website", slug="too-late")


async def test_the_error_tells_the_agent_how_to_recover(monkeypatch):
    """The message is the agent's instructions, so assert on what it says."""
    monkeypatch.setattr(config, "AUTH_SESSION_TTL", 0)
    async with chat() as c:
        await sign_in(c)
        with pytest.raises(ToolError) as caught:
            await call(c, "create_website", slug="too-late")

    message = str(caught.value)
    assert "authenticate" in message
    assert "Claude account" in message


async def test_a_chat_can_sign_in_again_after_expiry(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SESSION_TTL", 0)
    async with chat() as c:
        await sign_in(c)
        with pytest.raises(ToolError):
            await call(c, "create_website", slug="retried")

        monkeypatch.setattr(config, "AUTH_SESSION_TTL", 3600)
        await sign_in(c)
        assert "retried" in await call(c, "create_website", slug="retried")


async def test_signing_in_as_someone_else_replaces_the_session():
    async with chat() as c:
        await sign_in(c, OWNER)
        await sign_in(c, "second@example.com")

        status = await call(c, "authentication_status")
        assert "second@example.com" in status
        assert OWNER not in status
