"""The documentation: connecting cervo to Claude, then using it.

Written for someone who has never heard of MCP: the two sections that
matter are the connector setup and the email sign-in, both illustrated
(``figures.py``) rather than described in the abstract.
"""

from fasthtml.common import A, Code, Li, Ol, P, Strong
from starlette.responses import HTMLResponse

from cervo import config
from cervo.web import figures, layout


def docs_page() -> HTMLResponse:
    mcp_url = f"{config.origin()}/mcp"
    return layout.page(
        "documentation — cervo",
        *layout.hero(
            "● DOCUMENTATION",
            "How to use cervo",
            "Cervo has no dashboard and no password. You add it to Claude "
            "once, prove your email address, and from then on you make "
            "websites by asking for them.",
        ),
        layout.receipt(
            layout.receipt_row(
                "step one", A("Add cervo to Claude", href="#connecting-from-claude")
            ),
            layout.receipt_row("step two", A("Prove your email", href="#signing-in")),
            layout.receipt_row("then", A("Getting started", href="#getting-started")),
            layout.receipt_row(
                "your files", A("Updating your site", href="#updating-your-site")
            ),
            layout.receipt_row(
                "the rules", A("What cervo accepts", href="#limitations")
            ),
            layout.receipt_row(
                "behind it", A("How deployments work", href="#how-deployments-work")
            ),
        ),
        layout.section(
            "STEP ONE — ADD CERVO TO CLAUDE",
            P(
                "Cervo is a ",
                Strong("connector"),
                ": a tool you hand to Claude once, so that Claude can host "
                "websites on your behalf. Adding it takes about a minute, "
                "and you never have to do it again — once it is connected, "
                "cervo is there in every Claude conversation you start, on "
                "every device you use Claude on.",
            ),
            P(
                "Do it whichever way you already use Claude: through the ",
                A("claude.ai", href="https://claude.ai"),
                " website, or with the Claude Code command line tool. Either "
                "way you will need this address — it is cervo's front door:",
            ),
            layout.endpoint_chip(mcp_url),
            P(Strong("On the claude.ai website"), " — or the desktop app:"),
            Ol(
                Li(
                    "Open ",
                    A("claude.ai", href="https://claude.ai"),
                    " in your browser and sign in as usual.",
                ),
                Li(
                    "Click your name in the bottom-left corner, choose ",
                    Strong("Settings"),
                    ", then ",
                    Strong("Connectors"),
                    ". At the bottom of the list, click ",
                    Strong("Add custom connector"),
                    ".",
                    figures.connectors_screen(),
                ),
                Li(
                    "Type ",
                    Code("cervo"),
                    " as the name, and paste the address above into ",
                    Strong("Remote MCP server URL"),
                    ".",
                ),
                Li(
                    "Open ",
                    Strong("Advanced settings"),
                    ". Leave ",
                    Strong("Use Anthropic's hosted client metadata"),
                    " switched on — that is the recommended setting, and "
                    "cervo is built for it — and set authentication to ",
                    Strong("always required"),
                    ". Everything else can stay empty: cervo has no client "
                    "ID or secret for you to fill in.",
                    figures.connector_dialog(mcp_url),
                ),
                Li(
                    "Click ",
                    Strong("Connect"),
                    ". A cervo page opens in your browser — that is step two.",
                ),
                cls="steps",
            ),
            P(Strong("In Claude Code"), " — the command line tool:"),
            Ol(
                Li(
                    "Add cervo once, for every project you work in:",
                    layout.command(
                        f"claude mcp add --scope user --transport http cervo {mcp_url}"
                    ),
                ),
                Li(
                    "Start Claude Code and run ",
                    Code("/mcp"),
                    ", then choose cervo and authenticate. The same cervo "
                    "page opens in your browser — that is step two.",
                ),
                cls="steps",
            ),
            P(
                "The ",
                Code("--scope user"),
                " part is what makes it once and for all: cervo is then "
                "available in every Claude Code session, in any folder, "
                "rather than only the project you happened to be in.",
                cls="note",
            ),
            anchor="connecting-from-claude",
        ),
        layout.section(
            "STEP TWO — PROVE YOUR EMAIL",
            P(
                "There is no account to create and no password to choose. "
                "Cervo simply mails you a six-digit code and asks you to "
                "type it back. The address you verify is the one that owns "
                "your websites."
            ),
            figures.verification_flow(),
            Ol(
                Li(
                    "On the page Claude opened, type your email address and click ",
                    Strong("Send the code"),
                    ".",
                ),
                Li(
                    "Check your inbox for a mail from cervo with a "
                    "six-digit code. If it is not there within a minute, "
                    "look in your spam folder.",
                ),
                Li(
                    "Type the code back into the cervo page and click ",
                    Strong("Sign in"),
                    ". The page hands you back to Claude, and the connector "
                    "is ready to use.",
                ),
                cls="steps",
            ),
            P(
                "A code is good for ten minutes and five tries. If the page "
                "tells you the sign-in is over, nothing is broken — go back "
                "to Claude and click Connect again for a fresh code.",
                cls="note",
            ),
            P(
                "Cervo will never ask you for that code in the chat, only "
                "on its own page. From then on the connection keeps itself "
                "signed in — you will not be asked again. By connecting you "
                "agree to the ",
                A("terms of service", href="/terms"),
                " and the ",
                A("privacy policy", href="/privacy"),
                ".",
            ),
            anchor="signing-in",
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
            P(
                "You do not need files of your own to start. Have Claude "
                "design the page — in Claude Design, or just by describing "
                "what you want in the chat — and then ask for it to be "
                "published. The finished HTML and CSS go straight onto your "
                "site: nothing to download, nothing to upload by hand."
            ),
            anchor="updating-your-site",
        ),
        layout.section(
            "WHAT CERVO ACCEPTS",
            P(
                "Cervo hosts plain static pages, and it is strict about "
                "what goes on them: ",
                Strong("only .html and .css files can be published"),
                ". A file with any other extension is refused before it is "
                "ever written, and so is anything that does not actually "
                "read as HTML or CSS — the content is checked first.",
            ),
            layout.receipt(
                layout.receipt_row("file types", ".html and .css, nothing else"),
                layout.receipt_row("file size", "up to 1 MiB each"),
                layout.receipt_row("paths", "lowercase, relative, subfolders fine"),
                layout.receipt_row("site names", "lowercase letters, digits, hyphens"),
            ),
            P(
                "So there is no uploading images, fonts, videos, PDFs, or "
                "JavaScript files — and nothing runs on the server: no "
                "forms that submit back to cervo, no database, no logins. "
                "If you need a picture on a page, link to one hosted "
                "elsewhere, or embed it in the page itself as a data URI, "
                "within that 1 MiB."
            ),
            P(
                "Your site always has a home page: deleting ",
                Code("index.html"),
                " puts cervo's default page back rather than leaving the site empty.",
                cls="note",
            ),
            anchor="limitations",
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
