"""The homepage: how to use cervo, and the catalog of live sites."""

from fasthtml.common import A, P
from starlette.responses import HTMLResponse

from cervo import config, website
from cervo.db import connect
from cervo.web import layout


def home_page() -> HTMLResponse:
    with connect() as conn:
        sites = website.live(conn)

    mcp_url = f"{config.origin()}/mcp"
    return layout.page(
        "cervo — static hosting",
        *layout.hero(
            "● OPEN",
            "Host a site by asking for it",
            "Cervo hosts static websites on a shared VPS. There is no "
            "dashboard and no server to log into — you create and update "
            "a site by talking to an AI.",
        ),
        layout.section(
            "GET STARTED",
            P("Point your AI tool of choice at cervo's MCP server:"),
            layout.endpoint_chip(mcp_url),
            P(
                "Signing in is one confirmation — the email on your Claude "
                "account owns the site. Then just ask:"
            ),
            layout.prompts(
                "Authenticate with cervo",
                "Create a website called my-cool-site",
                f"Upload my files to my-cool-site.{config.DOMAIN}",
            ),
            P("Curious about the details? ", A("Read the docs", href="/docs"), "."),
        ),
        layout.section("SITES ON CERVO", *_catalog(sites)),
    )


def _catalog(sites: list[website.Website]):
    if not sites:
        return (P("No sites are live yet — yours could be the first.", cls="intro"),)
    return (
        P("Every site cervo is serving right now. Browse around."),
        layout.receipt(
            *(
                layout.receipt_row(site.slug, A(site.url, href=site.url))
                for site in sites
            )
        ),
    )
