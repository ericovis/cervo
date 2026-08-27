"""Signing in: the Claude account's email, confirmed by the user."""

import pytest
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from tests.conftest import OWNER, call, chat, sign_in


async def test_a_new_chat_is_signed_out():
    async with chat() as c:
        assert "not signed in" in await call(c, "authentication_status")


async def test_confirming_the_address_signs_the_chat_in():
    async with chat() as c:
        result = await sign_in(c)
        assert f"Signed in as {OWNER}" in result
        assert "4 hours" in result
        assert OWNER in await call(c, "authentication_status")


async def test_the_user_can_correct_the_address_the_agent_guessed():
    """The argument is only a proposal; the elicited answer is what counts."""
    async with chat(confirms="real@example.com") as c:
        result = await call(c, "authenticate", email="wrong-guess@example.com")

    assert "Signed in as real@example.com" in result


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_dismissing_the_form_leaves_the_chat_signed_out(action):
    async with chat(action=action) as c:
        with pytest.raises(ToolError, match="did not confirm"):
            await call(c, "authenticate", email=OWNER)
        assert "not signed in" in await call(c, "authentication_status")


async def test_a_client_that_is_not_claude_is_refused():
    stranger = Implementation(name="some-other-agent", version="1.0")
    async with chat(client_info=stranger) as c:
        with pytest.raises(ToolError, match="only works through Claude"):
            await call(c, "authenticate", email=OWNER)
        assert "not signed in" in await call(c, "authentication_status")


async def test_authenticating_again_refreshes_the_session():
    async with chat() as c:
        await sign_in(c)
        result = await call(c, "authenticate", email=OWNER)

    assert f"Signed in as {OWNER}" in result
