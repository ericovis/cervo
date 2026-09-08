"""The homepage: how to use cervo, and the catalog of live sites."""

from fasthtml.common import A, P
from starlette.responses import HTMLResponse

from cervo import config, db, website
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
        layout.section("FOR AN AI ASSISTANT", *_for_assistants()),
        layout.section("SITES ON CERVO", *_catalog(sites)),
    )


def _for_assistants():
    """The endpoint, in the body of the homepage rather than only the docs.

    The docs still do the explaining, for a person. This section exists
    because of what happens when someone pastes cervo's bare URL into a
    chat: the assistant reads this page and nothing else, and a page that
    only says "see the documentation" leaves it with nothing to act on.
    One address and one pointer are enough to change that.
    """
    return (
        P(
            "cervo is an MCP server, so the assistant does the work. Point "
            "yours at this address — it is the whole of the setup:"
        ),
        layout.endpoint_chip(f"{config.origin()}/mcp"),
        P(
            "How to connect, the tools, and the rules about what may be "
            "published are all in ",
            A("llms.txt", href="/llms.txt"),
            ", written for machines to read in one fetch.",
        ),
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
