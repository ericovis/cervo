"""The public website: pages, the catalog, and staying out of /mcp's way."""

import pytest
from starlette.testclient import TestClient

from cervo.server import app
from tests.conftest import call, chat, deploy

PAGES = ["/", "/docs", "/terms", "/privacy"]


@pytest.fixture
def client():
    with TestClient(app.http_app(stateless_http=True)) as client:
        yield client


@pytest.mark.parametrize("path", PAGES)
def test_every_page_is_served_on_the_design_system(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.lower().startswith("<!doctype")
    assert response.text.lower().count("<!doctype") == 1
    assert ">cervo</a>" in response.text  # the wordmark
    assert "--accent" in response.text  # the design system's token block


def test_the_homepage_points_at_the_mcp_endpoint(client):
    page = client.get("/").text
    assert "http://localhost/mcp" in page


def test_the_catalog_starts_empty(client):
    assert "No sites are live yet" in client.get("/").text


async def test_the_catalog_lists_only_live_sites(client):
    async with chat() as c:
        await call(c, "create_website", slug="ready")
        deploy()
        await call(c, "create_website", slug="waiting")

    page = client.get("/").text
    assert "ready" in page
    assert "http://ready.localhost" in page
    assert "waiting" not in page  # pending is not live


def test_the_docs_have_their_anchors(client):
    page = client.get("/docs").text
    for anchor in (
        "connecting-from-claude",
        "signing-in",
        "getting-started",
        "updating-your-site",
        "how-deployments-work",
    ):
        assert f'id="{anchor}"' in page


def test_the_docs_illustrate_the_setup(client):
    page = client.get("/docs").text

    # Three drawings, inline and self-contained: no external requests.
    assert page.count("<svg") == 3
    assert "<img" not in page
    assert "Add custom connector" in page
    # The dialog figure shows the address the reader has to paste.
    assert page.count("http://localhost/mcp") >= 2


def test_unknown_paths_get_the_styled_404(client):
    response = client.get("/nope/deeper")

    assert response.status_code == 404
    assert "NOT FOUND" in response.text
    assert "/nope/deeper" in response.text  # the requested path, shown back


def test_the_catch_all_does_not_shadow_the_mcp_endpoint(client):
    # Not a full MCP handshake — just proof the route is not our 404 page.
    response = client.post("/mcp", json={})
    assert response.status_code != 404
