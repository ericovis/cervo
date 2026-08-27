"""The documentation: one page, three anchored sections."""

from fasthtml.common import A, Code, Li, Ol, P, Strong
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
                "connect", A("Connecting from Claude", href="#connecting-from-claude")
            ),
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
            "CONNECTING FROM CLAUDE",
            P(
                "Cervo works through Claude. Signing in is part of "
                "connecting — there is nothing to configure beyond the "
                "connector itself, whose MCP server lives at:"
            ),
            layout.endpoint_chip(f"{config.origin()}/mcp"),
            P(Strong("On claude.ai"), " (or the Claude desktop and mobile apps):"),
            Ol(
                Li(
                    "Open Settings, choose Connectors, and click ",
                    Strong("Add custom connector"),
                    ".",
                ),
                Li(
                    "Name it cervo and paste the MCP server URL above. Under "
                    "the advanced settings, keep ",
                    Strong("Use Anthropic's hosted client metadata"),
                    " selected — the recommended option, which cervo "
                    "supports — and require authentication.",
                ),
                Li(
                    "Click Connect. Your browser opens cervo's sign-in "
                    "page: enter your email and type back the six-digit "
                    "code that lands in your inbox."
                ),
                cls="steps",
            ),
            P(
                Strong("In Claude Code"),
                ": add the server with ",
                Code(f"claude mcp add --transport http cervo {config.origin()}/mcp"),
                ", then run ",
                Code("/mcp"),
                " to connect — the same browser sign-in opens.",
            ),
            P(
                "Either way the verified email owns everything you create, "
                "and the connection stays signed in on its own — no codes "
                "in the chat, ever. By connecting you agree to the ",
                A("terms of service", href="/terms"),
                " and the ",
                A("privacy policy", href="/privacy"),
                ".",
            ),
            anchor="connecting-from-claude",
        ),
        layout.section(
            "GETTING STARTED",
            P(
                "Once connected, just ask for a website. A site's name is "
                "its slug — lowercase letters, digits, and hyphens — and "
                "becomes its address:"
            ),
            layout.prompts(
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
