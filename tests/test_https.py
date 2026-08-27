"""SCHEME=https flips the whole stack: URLs and the Caddyfile."""

import pytest

from cervo import config
from cervo.db import connect
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
