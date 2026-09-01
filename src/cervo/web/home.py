"""The homepage: how to use cervo, and the catalog of live sites."""

from fasthtml.common import A, P
from starlette.responses import HTMLResponse

from cervo import db, website
from cervo.web import layout


async def home_page() -> HTMLResponse:
    sites = await db.transact(website.live)

    return layout.page(
        "cervo — static hosting",
        *layout.hero(
            "● OPEN",
            "Host a site by asking for it",
            "cervo hosts static websites on a shared VPS. There is no "
            "dashboard and no server to log into — you create and update "
            "a site by talking to an AI.",
        ),
        layout.section(
            "GET STARTED",
            P(
                "You add cervo to Claude once, prove your email address, and "
                "from then on you make websites by asking for them. The ",
                A("documentation", href="/docs"),
                " walks through every step, with pictures.",
            ),
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
