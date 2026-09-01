"""The default page a fresh site serves until its owner publishes their own.

Built from the same components as cervo's own pages, so the two cannot
drift apart. The worker renders this once per deployment and writes the
HTML into the site's directory; links back into cervo are absolute,
because a relative link would stay on the site's subdomain.
"""

from fasthtml.common import A, P

from cervo import config
from cervo.web import layout


def default_page(slug: str, url: str, deployed_at: str) -> str:
    host = url.removeprefix("https://").removeprefix("http://")
    base = config.origin()
    return layout.document(
        f"{host} — live on cervo",
        *layout.hero(
            "● LIVE",
            host,
            "This is the default page for a new cervo site. Publish an "
            "index.html of your own to replace it.",
        ),
        layout.receipt(
            layout.receipt_row("site", slug),
            layout.receipt_row("address", A(url, href=url)),
            layout.receipt_row("deployed", deployed_at),
        ),
        layout.section(
            "UPDATING THIS SITE",
            P(
                "Everything here is managed by chatting with an AI — there "
                "is no dashboard and no server to log into. Point your AI "
                "tool of choice at cervo's MCP server:"
            ),
            layout.endpoint_chip(f"{base}/mcp"),
            P("Then just ask. Try prompts like:"),
            layout.prompts(
                f"Upload my files to {host}",
                f"Design my {host} website",
            ),
        ),
        base=base,
        description=(
            f"{host} is live on cervo. Its owner has not published a page yet."
        ),
    )
