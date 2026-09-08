"""The public website: pages, the catalog, and staying out of /mcp's way."""

import pytest
from starlette.testclient import TestClient

from cervo.server import app
from cervo.web import agents, site
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


def test_the_homepage_sends_people_to_the_docs(client):
    # Setup lives in one place for a reader: the homepage points, the docs
    # explain. The endpoint below is the exception, and it is for machines.
    assert 'href="/docs"' in client.get("/").text


def test_the_homepage_gives_an_assistant_the_endpoint(client):
    # Someone pastes cervo's bare URL into a chat; the assistant reads this
    # page and nothing else. It has to leave with somewhere to connect.
    page = client.get("/").text

    assert "http://localhost/mcp" in page
    assert 'href="/llms.txt"' in page


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
        "limitations",
        "how-deployments-work",
    ):
        assert f'id="{anchor}"' in page


def test_the_docs_cover_both_ways_in(client):
    page = client.get("/docs").text

    assert "Add custom connector" in page  # the claude.ai route
    # The CLI route, at user scope: cervo in every session, not one project.
    assert "claude mcp add --scope user --transport http cervo" in page


def test_the_docs_state_what_can_be_published(client):
    page = client.get("/docs").text
    assert "only .html and .css files can be published" in page
    assert "1 MiB" in page


def test_the_docs_illustrate_the_setup(client):
    page = client.get("/docs").text

    # Three drawings, inline and self-contained: no external requests. The
    # fourth <svg> on the page is the brand mark in the header.
    assert page.count("<svg") == 4
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


@pytest.mark.parametrize("path", PAGES)
def test_every_page_wears_the_brand(client, path):
    page = client.get(path).text

    assert 'class="wordmark"' in page
    assert "<svg" in page  # the mark, inline beside the wordmark
    assert 'href="/favicon.svg"' in page
    assert 'href="/apple-touch-icon-180.png"' in page
    # The preview card's address is absolute: a scraper has nothing to
    # resolve a relative one against.
    assert 'content="http://localhost/og-image-1200x630.png"' in page
    assert 'name="description"' in page


def test_the_docs_describe_themselves_in_the_preview(client):
    page = client.get("/docs").text
    assert 'property="og:title" content="documentation — cervo"' in page
    assert "prove your email address" in page


BRAND_FILES = {
    "/favicon.svg": "image/svg+xml",
    "/favicon-16.png": "image/png",
    "/favicon-32.png": "image/png",
    "/apple-touch-icon-180.png": "image/png",
    "/og-image-1200x630.png": "image/png",
}


@pytest.mark.parametrize("path,media_type", BRAND_FILES.items())
def test_the_brand_files_are_served(client, path, media_type):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert len(response.content) > 0


ROBOTS_EXPECTATIONS = [
    "User-agent: *",
    "Allow: /",
    "Disallow: /verify",  # one browser, one ten-minute transaction
    "http://localhost/llms.txt",  # where an assistant should actually look
]


@pytest.mark.parametrize("expected", ROBOTS_EXPECTATIONS)
def test_robots_txt_opens_the_site_and_points_at_llms_txt(client, expected):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert expected in response.text


LLMS_EXPECTATIONS = [
    "http://localhost/mcp",  # the endpoint, before anything else
    "http://localhost/.well-known/oauth-authorization-server",
    "claude mcp add --scope user --transport http cervo",
    "Claude clients only",  # anything else is refused at registration
    "create_website(slug)",
    "write_file(slug, path, content)",
    "delete_file(slug, path)",
    "list_websites()",
    "delete_website(slug)",
    "^[a-z0-9]+(?:-[a-z0-9]+)*$",  # the slug rule, verbatim
    "1 MiB",
    "http://localhost/docs",
]


@pytest.mark.parametrize("expected", LLMS_EXPECTATIONS)
def test_llms_txt_is_enough_to_act_on_in_one_fetch(client, expected):
    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert expected in response.text


async def test_llms_txt_names_every_tool_the_server_exposes():
    # The file is hand-written prose, so it is the one thing here that can
    # quietly fall behind the server. Ask the server itself instead.
    text = agents.llms_txt()

    for tool in await app.list_tools():
        assert tool.name in text, f"llms.txt does not mention {tool.name}"


@pytest.mark.parametrize("path", PAGES)
def test_every_page_links_llms_txt_for_a_machine(client, path):
    assert 'href="/llms.txt"' in client.get(path).text


def test_a_site_page_points_its_llms_txt_at_the_apex():
    # Same reason as the brand files: an assistant handed a hosted site's
    # URL should find cervo's file, not look for one on the subdomain.
    page = site.default_page("demo", "http://demo.localhost", "2026-01-01")

    assert 'href="http://localhost/llms.txt"' in page


def test_a_site_page_points_its_brand_at_the_apex():
    # A relative icon would be looked for on the site's own subdomain, where
    # nothing serves it.
    page = site.default_page("demo", "http://demo.localhost", "2026-01-01")

    assert 'href="http://localhost/favicon.svg"' in page
    assert 'content="http://localhost/og-image-1200x630.png"' in page
    assert "demo.localhost is live on cervo" in page
