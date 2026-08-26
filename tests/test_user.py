"""People, and the fact that one of them can own many sites."""

from cervo import user, website
from cervo.db import connect
from tests.conftest import OWNER, chat, sign_in


def test_a_user_is_created_on_first_sight():
    with connect() as conn:
        assert user.by_email(conn, OWNER) is None
        created = user.ensure(conn, OWNER)
        assert created.email == OWNER
        assert user.by_email(conn, OWNER) == created


def test_the_same_address_is_always_the_same_user():
    with connect() as conn:
        first = user.ensure(conn, OWNER)
        second = user.ensure(conn, OWNER)

    assert first.id == second.id


def test_different_addresses_are_different_users():
    with connect() as conn:
        alice = user.ensure(conn, "alice@example.com")
        bob = user.ensure(conn, "bob@example.com")

    assert alice.id != bob.id


def test_one_user_owns_many_sites():
    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        for slug in ("first", "second", "third"):
            website.create(conn, slug, owner)

        assert [site.slug for site in website.for_user(conn, owner)] == [
            "first",
            "second",
            "third",
        ]


def test_sites_are_not_visible_to_other_users():
    with connect() as conn:
        alice = user.ensure(conn, "alice@example.com")
        bob = user.ensure(conn, "bob@example.com")
        website.create(conn, "alices", alice)

        assert [site.slug for site in website.for_user(conn, alice)] == ["alices"]
        assert website.for_user(conn, bob) == []


async def test_signing_in_twice_does_not_duplicate_the_user(mailbox):
    """Two conversations, one person, one row in the user table."""
    async with chat() as first:
        await sign_in(first, mailbox)
        await first.call_tool("list_websites")

    async with chat() as second:
        await sign_in(second, mailbox)
        await second.call_tool("list_websites")

    with connect() as conn:
        rows = conn.execute("SELECT * FROM user WHERE email = ?", (OWNER,)).fetchall()

    assert len(rows) == 1
