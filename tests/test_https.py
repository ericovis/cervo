"""SCHEME=https flips the whole stack: URLs, Caddyfile, provider mail."""

import smtplib
from typing import ClassVar

import pytest

from cervo import config
from cervo.db import connect

# Bound at import time, before the autouse mailbox fixture replaces
# cervo.mail.send with a capture — these tests exercise the real thing.
from cervo.mail import send as real_send
from cervo.user import ensure
from cervo.website import create
from tests.conftest import deploy


@pytest.fixture
def https(monkeypatch):
    monkeypatch.setattr(config, "SCHEME", "https")
    monkeypatch.setattr(config, "ACME_EMAIL", "certs@example.com")


def created(slug: str) -> None:
    with connect() as conn:
        create(conn, slug, ensure(conn, "owner@example.com"))


def test_site_urls_carry_the_scheme(https):
    with connect() as conn:
        site = create(conn, "secure", ensure(conn, "owner@example.com"))
    assert site.url == "https://secure.localhost"


def test_the_caddyfile_turns_on_automatic_https(https, data_dir, caddy_reloads):
    created("secure")
    deploy()

    caddyfile = (data_dir / "Caddyfile").read_text()
    assert "http://" not in caddyfile  # schemeless addresses = auto-HTTPS
    assert "\nlocalhost {" in caddyfile  # cervo itself, at the bare domain
    assert "secure.localhost {" in caddyfile
    assert "email certs@example.com" in caddyfile


def test_each_site_registers_its_owners_acme_email(https, data_dir, caddy_reloads):
    created("secure")
    deploy()

    caddyfile = (data_dir / "Caddyfile").read_text()
    assert "\ttls owner@example.com" in caddyfile  # inside the site's block
    assert caddyfile.count("tls ") == 1  # cervo itself keeps the global email


def test_plain_http_sets_no_acme_contact(data_dir, caddy_reloads):
    created("plain")
    deploy()

    assert "tls " not in (data_dir / "Caddyfile").read_text()


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
