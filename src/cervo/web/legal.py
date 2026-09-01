"""The small print: terms of service and privacy."""

from fasthtml.common import P
from starlette.responses import HTMLResponse

from cervo.web import layout


def terms_page() -> HTMLResponse:
    return layout.page(
        "terms of service — cervo",
        *layout.hero(
            "● TERMS OF SERVICE",
            "Terms of service",
            "The short version: cervo is a demonstration service. Enjoy "
            "it, and expect nothing from it.",
        ),
        layout.section(
            "THE SERVICE",
            P(
                "cervo is a demo of static website hosting managed through "
                "an AI conversation. It is provided as-is and as-available, "
                "with no warranty of any kind and no guarantees of uptime, "
                "durability, or backups. The service may change or shut "
                "down at any time, without notice."
            ),
        ),
        layout.section(
            "YOUR SITES",
            P(
                "You keep ownership of the content you deploy, and you are "
                "responsible for it. Do not publish content that is "
                "unlawful, malicious, or infringes someone else's rights. "
                "Sites and accounts may be removed at any time, for any "
                "reason — this is a demo, not a home."
            ),
        ),
    )


def privacy_page() -> HTMLResponse:
    return layout.page(
        "privacy — cervo",
        *layout.hero(
            "● PRIVACY",
            "Privacy",
            "What cervo stores, and what it does not.",
        ),
        layout.section(
            "WHAT IS STORED",
            P(
                "Your email address, verified when you connect, which owns "
                "your sites. Sign-in codes and the tokens your Claude "
                "connection holds are stored only as hashes. And the sites "
                "you deploy — their files are, by design, published to the "
                "public internet."
            ),
        ),
        layout.section(
            "WHAT IS NOT",
            P(
                "No analytics and no tracking on your sites, and no cookies: "
                "the only thing kept in your browser is your theme "
                "preference, stored locally and never sent anywhere. cervo "
                "emails you nothing but sign-in codes."
            ),
        ),
        layout.section(
            "WHAT LEAVES, TO RUN THE SERVICE",
            P(
                "Two things go elsewhere, both to keep the service running. "
                "In production your email is registered with the certificate "
                "authority that issues your sites' HTTPS certificates, and "
                "cervo's servers send operational telemetry — requests, "
                "background jobs, and errors, which can include your email "
                "and network address — to an error-monitoring service. "
                "Nothing else is shared."
            ),
        ),
    )
