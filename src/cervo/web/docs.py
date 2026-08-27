"""The documentation: one page, three anchored sections."""

from fasthtml.common import A, Code, P
from starlette.responses import HTMLResponse

from cervo import config
from cervo.web import layout


def docs_page() -> HTMLResponse:
    return layout.page(
        "documentation — cervo",
        *layout.hero(
            "● DOCUMENTATION",
            "How cervo works",
            "Everything on cervo happens by talking to an AI connected to "
            "its MCP server. These are the details worth knowing.",
        ),
        layout.receipt(
            layout.receipt_row(
                "start here", A("Getting started", href="#getting-started")
            ),
            layout.receipt_row(
                "your files", A("Updating your site", href="#updating-your-site")
            ),
            layout.receipt_row(
                "behind it", A("How deployments work", href="#how-deployments-work")
            ),
        ),
        layout.section(
            "GETTING STARTED",
            P(
                "Connect your AI tool — Claude Code, a desktop assistant, "
                "anything that speaks MCP — to cervo's server:"
            ),
            layout.endpoint_chip(f"{config.origin()}/mcp"),
            P(
                "Ask it to authenticate with cervo. You will be asked to "
                "confirm your email address, and a six-digit code lands in "
                "your inbox; paste it back into the chat. The confirmed "
                "address owns everything you create, and the chat stays "
                "signed in for four hours."
            ),
            P(
                "Then ask for a website. A site's name is its slug — "
                "lowercase letters, digits, and hyphens — and becomes its "
                "address:"
            ),
            layout.prompts(
                "Authenticate with cervo",
                "Create a website called my-cool-site",
            ),
            P(
                f"Within seconds the site is live at "
                f"{config.origin(f'my-cool-site.{config.DOMAIN}')}, serving a default "
                "page until you publish your own."
            ),
            anchor="getting-started",
        ),
        layout.section(
            "UPDATING YOUR SITE",
            P(
                "Every new site starts with cervo's default page. It is "
                "written only when your site has no ",
                Code("index.html"),
                " of its own — the moment you publish your files, they are "
                "yours, and no redeploy will ever overwrite them.",
            ),
            P("Updating is the same conversation as creating:"),
            layout.prompts(
                f"Upload my files to my-cool-site.{config.DOMAIN}",
                f"Design my my-cool-site.{config.DOMAIN} website",
            ),
            anchor="updating-your-site",
        ),
        layout.section(
            "HOW DEPLOYMENTS WORK",
            P(
                "Creating a website queues a deployment, which a background "
                "worker runs as a chain of steps the chat can follow in "
                "real time: create the site's directory and write the "
                "default page if none exists, regenerate the web server's "
                "configuration from the database, and reload it."
            ),
            P(
                "A deployment's status runs pending, then deploying, then "
                "live — or failed, with the error shown when you list your "
                "websites. Failures retry on their own, and asking to "
                "create your own failed site again queues a fresh "
                "deployment. Every step is idempotent, so retrying is "
                "always safe."
            ),
            anchor="how-deployments-work",
        ),
    )
