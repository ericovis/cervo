"""SCHEME=https flips the whole stack: URLs, certificates, provider mail."""

import smtplib
from typing import ClassVar

import pytest

from cervo import config
from cervo.db import connect

# Bound at import time, before the autouse mailbox fixture replaces
# cervo.mail.send with a capture — these tests exercise the real thing.
from cervo.mail import send as real_send
from cervo.user import ensure
from cervo.website import create, delete, request_sync
from tests.conftest import ZEROSSL, deploy


@pytest.fixture
def https(monkeypatch, caddy_api):
    """Production's shape: the https scheme, and caddy booted for it.

    The static Caddyfile adapts differently under https — the server listens
    on :443 and the operator's ACME email becomes an automation policy for
    cervo's own hostname — so the fake is re-seeded to match.
    """
    monkeypatch.setattr(config, "SCHEME", "https")
    monkeypatch.setattr(config, "ACME_EMAIL", "certs@example.com")
    caddy_api.https()
    return caddy_api


def created(slug: str) -> None:
    with connect() as conn:
        create(conn, slug, ensure(conn, "owner@example.com"))


def created_again() -> None:
    """Queue the same site's deployment again, the way a retry would."""
    with connect() as conn:
        conn.execute("UPDATE job SET status = 'failed'")
        create(conn, "steady", ensure(conn, "owner@example.com"))


def test_site_urls_carry_the_scheme(https):
    with connect() as conn:
        site = create(conn, "secure", ensure(conn, "owner@example.com"))
    assert site.url == "https://secure.localhost"


def test_a_site_is_published_on_the_https_server(https, data_dir):
    """The server is found by the port it listens on, not by its name."""
    created("secure")
    deploy()

    route = https.object("site:secure")
    assert route["match"] == [{"host": ["secure.localhost"]}]
    assert route in https.routes
    assert https.server["listen"] == [":443"]


def test_each_site_registers_its_owners_acme_email(https):
    """The owner hears from the CA about their own site's certificate.

    Both issuers, as the Caddyfile adapter writes them for cervo's own
    hostname: a site whose certificate Let's Encrypt cannot issue falls
    back to the second CA, exactly as it did when a rendered Caddyfile
    carried a ``tls {owner}`` line.
    """
    created("secure")
    deploy()

    policy = https.object("tls:secure")
    assert policy["subjects"] == ["secure.localhost"]
    assert policy["issuers"] == [
        {"module": "acme", "email": "owner@example.com"},
        {"module": "acme", "ca": ZEROSSL, "email": "owner@example.com"},
    ]

    apex, site = https.policies  # cervo's own policy is left exactly as it was
    assert apex["subjects"] == ["localhost"]
    assert "@id" not in apex
    assert site is policy


def test_the_certificate_is_ordered_under_the_owners_policy(https):
    """The policy goes in before the route, and that order is the point.

    A hostname becomes caddy's business the moment its route appears, and
    automatic HTTPS orders the certificate on that very reload — with no
    policy yet, it would order the first one as a stranger.
    """
    created("early")
    deploy()

    writes = [path for _, path in https.writes]
    assert writes.index("/config/apps/tls/automation/policies") < writes.index(
        "/config/apps/http/servers/srv0/routes"
    )


def test_a_policy_brings_the_tls_app_with_it(https, caddy_api):
    """Nothing is assumed about caddy holding a tls app at all.

    Under https this Caddyfile always adapts to one, but a caddy booted
    from another config is one restart away — and caddy creates no
    intermediate objects when appending, so the policy has to bring the
    whole apps.tls → automation → policies path with it.
    """
    del caddy_api.config["apps"]["tls"]

    created("lonely")
    deploy()

    assert caddy_api.policies == [https.object("tls:lonely")]


def test_a_sync_puts_back_the_certificate_policies(https):
    """A restarted caddy loses the sites' policies with their routes."""
    created("restored")
    deploy()
    https.https()  # caddy restarted: the static Caddyfile alone again

    with connect() as conn:
        request_sync(conn)
    assert deploy() == 1

    apex, site = https.policies
    assert "@id" not in apex and apex["subjects"] == ["localhost"]
    assert site == https.object("tls:restored")
    assert site["issuers"][0]["email"] == "owner@example.com"


def test_a_sync_of_a_settled_stack_leaves_the_policies_alone(https):
    created("steady")
    deploy()
    writes = list(https.writes)

    with connect() as conn:
        request_sync(conn)
    assert deploy() == 1

    assert https.writes == writes  # three reads, no reload


def test_a_sync_survives_a_caddy_with_no_tls_app(https, caddy_api):
    """Reading the policies must not be the thing that breaks a sync.

    Caddy answers a GET *through* a missing key with a refusal, not with
    null, so the policies are read through the tls app itself.
    """
    created("hopeful")
    deploy()
    https.https()
    del caddy_api.config["apps"]["tls"]  # a caddy booted from another config

    with connect() as conn:
        request_sync(conn)
    assert deploy() == 1

    assert caddy_api.policies == [https.object("tls:hopeful")]


def test_a_republished_site_leaves_its_policy_alone(https):
    created("steady")
    deploy()
    writes = list(https.writes)

    created_again()
    deploy()

    assert https.writes == writes  # nothing differs, so caddy is not disturbed


def test_a_deleted_site_takes_its_policy_with_it(https):
    created("gone")
    deploy()

    with connect() as conn:
        delete(conn, "gone", ensure(conn, "owner@example.com"))
    deploy()

    assert https.object("tls:gone") is None
    assert len(https.policies) == 1  # cervo's own policy stays


def test_plain_http_registers_no_certificate(caddy_api):
    created("plain")
    deploy()

    assert "tls" not in caddy_api.config["apps"]
    assert caddy_api.object("tls:plain") is None


class _RecordingSMTP:
    """A fake smtplib.SMTP that records the calls the real one would get."""

    calls: ClassVar[list] = []

    def __init__(self, host, port, timeout=None):
        self.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"]))


@pytest.fixture
def smtp(monkeypatch):
    _RecordingSMTP.calls = []
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    return _RecordingSMTP.calls


def test_mail_stays_plain_for_mailcatcher(smtp):
    real_send("to@example.com", "hi", "body")

    assert [name for name, *_ in smtp] == ["connect", "send"]


def test_mail_logs_in_over_tls_for_a_provider(smtp, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_USER", "resend")
    monkeypatch.setattr(config, "EMAIL_PASSWORD", "secret")

    real_send("to@example.com", "hi", "body")

    assert [name for name, *_ in smtp] == ["connect", "starttls", "login", "send"]
    assert ("login", "resend", "secret") in smtp
    assert ("starttls", True) in smtp  # a real TLS context, not None
