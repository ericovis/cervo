"""Signing in: the emailed code, and the session it buys."""

import pytest
from fastmcp.exceptions import ToolError

from cervo import config
from tests.conftest import OWNER, call, chat, sign_in


async def test_a_new_chat_is_signed_out():
    async with chat() as c:
        assert "not signed in" in await call(c, "authentication_status")


async def test_authenticate_mails_a_six_digit_code(mailbox):
    async with chat() as c:
        result = await call(c, "authenticate", email=OWNER)

    assert OWNER in result
    assert len(mailbox) == 1
    assert mailbox.last.to == OWNER
    assert len(mailbox.last_code) == 6
    assert mailbox.last_code.isdigit()


async def test_the_code_is_never_stored_in_the_clear(mailbox):
    """Only the hash is persisted, so the mail is the only source of the code."""
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        code = mailbox.last_code

    assert code not in config.DATABASE_PATH.read_bytes().decode("latin-1")


async def test_authenticating_alone_does_not_sign_you_in():
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        assert "not signed in" in await call(c, "authentication_status")


async def test_confirming_the_code_signs_the_chat_in(mailbox):
    async with chat() as c:
        result = await sign_in(c, mailbox)
        assert f"Signed in as {OWNER}" in result
        assert "4 hours" in result
        assert OWNER in await call(c, "authentication_status")


async def test_the_user_can_correct_the_address_the_agent_guessed(mailbox):
    """The argument is only a proposal; the elicited answer is what counts."""
    async with chat(confirms="real@example.com") as c:
        await call(c, "authenticate", email="wrong-guess@example.com")

    assert mailbox.last.to == "real@example.com"


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_dismissing_the_form_sends_nothing(action, mailbox):
    async with chat(action=action) as c:
        with pytest.raises(ToolError, match="did not confirm"):
            await call(c, "authenticate", email=OWNER)
        assert mailbox == []
        assert "not signed in" in await call(c, "authentication_status")


async def test_a_wrong_code_counts_down_the_attempts(mailbox):
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        with pytest.raises(ToolError, match="4 attempts left"):
            await call(c, "confirm_authentication", code="000000")
        with pytest.raises(ToolError, match="3 attempts left"):
            await call(c, "confirm_authentication", code="000000")
        # the real code still works while attempts remain
        assert "Signed in" in await call(
            c, "confirm_authentication", code=mailbox.last_code
        )


async def test_too_many_wrong_codes_drops_the_challenge(mailbox):
    """Keep guessing until the lockout, without hardcoding how many that is."""
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        refusals = []
        for _ in range(20):
            with pytest.raises(ToolError) as caught:
                await call(c, "confirm_authentication", code="000000")
            refusals.append(str(caught.value))
            if "Too many wrong codes" in refusals[-1]:
                break
        else:
            pytest.fail(f"the attempt limit never kicked in: {refusals}")

        assert len(refusals) > 1, "one wrong code should not lock the chat out"
        assert all("attempts left" in refusal for refusal in refusals[:-1])

        # even the correct code is now useless
        with pytest.raises(ToolError, match="Nothing to confirm"):
            await call(c, "confirm_authentication", code=mailbox.last_code)


async def test_an_expired_code_is_refused(monkeypatch, mailbox):
    monkeypatch.setattr(config, "AUTH_CODE_TTL", 0)
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        with pytest.raises(ToolError, match="expired"):
            await call(c, "confirm_authentication", code=mailbox.last_code)


async def test_confirming_without_a_challenge_is_refused():
    async with chat() as c:
        with pytest.raises(ToolError, match="Nothing to confirm"):
            await call(c, "confirm_authentication", code="123456")


async def test_reissuing_a_code_invalidates_the_previous_one(mailbox):
    async with chat() as c:
        await call(c, "authenticate", email=OWNER)
        stale = mailbox.last_code
        await call(c, "authenticate", email="other@example.com")

        assert len(mailbox) == 2
        with pytest.raises(ToolError, match="not right"):
            await call(c, "confirm_authentication", code=stale)


async def test_authenticating_again_as_the_same_user_sends_no_mail(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        result = await call(c, "authenticate", email=OWNER)

    assert "Already signed in" in result
    assert len(mailbox) == 1, "a signed-in chat should not be mailed another code"
