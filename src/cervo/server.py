import asyncio
from time import monotonic

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.exceptions import ToolError
from jinja2 import Environment, PackageLoader
from mcp.types import ClientCapabilities, ElicitationCapability
from pydantic import BaseModel, EmailStr, Field, create_model

from cervo import auth, user, web, website
from cervo.db import connect
from cervo.errors import AppError

app = FastMCP("cervo")

_env = Environment(loader=PackageLoader("cervo"), autoescape=True)

# The deployment-progress app: clients that support MCP apps render this UI
# for create_website's result, and it polls website_status until the site
# settles. Clients that do not simply read the tool results as usual.
_DEPLOYMENT_URI = "ui://cervo/deployment.html"

# The websites-overview app: the same idea for list_websites — every site the
# signed-in user owns, with unsettled deployments followed live.
_WEBSITES_URI = "ui://cervo/websites.html"

# Following a deployment from create_website: how often to look, and for how
# long before handing off to list_websites (the deployment runs on either way).
_FOLLOW_POLL = 0.5  # seconds
_FOLLOW_FOR = 30  # seconds

_CLAUDE_ONLY = (
    "cervo only works through Claude. Connect from a Claude client — Claude "
    "Code, the Claude apps, or claude.ai — to sign in."
)

_ELICITATION_REQUIRED = (
    "This client cannot ask the user to confirm an email address, and cervo "
    "will not sign in as an unconfirmed one. Use a client that supports "
    "MCP elicitation."
)


def _is_claude(ctx: Context) -> bool:
    """Whether the connected client introduced itself as Claude.

    The client's name arrives in the MCP initialize handshake; cervo serves
    Claude only, where the account's email address is at hand.
    """
    params = ctx.session.client_params
    return params is not None and "claude" in params.clientInfo.name.lower()


def _confirmation_model(proposed: str) -> type[BaseModel]:
    """A one-field form pre-filled with the address the caller proposed.

    Built per call so the client can render the proposal as an editable
    default rather than an empty box.
    """
    return create_model(
        "ConfirmEmail",
        email=(
            EmailStr,
            Field(
                default=proposed,
                title="Email",
                description="The address that owns your sites.",
            ),
        ),
    )


def _remaining(session: auth.AuthSession) -> str:
    """A human phrase for how much longer a session lasts."""
    minutes = auth.minutes_until(session.expires_at)
    if minutes < 90:
        return f"{minutes} minutes" if minutes != 1 else "1 minute"
    hours = round(minutes / 60)
    return f"{hours} hours" if hours != 1 else "1 hour"


async def _confirm_email(ctx: Context, proposed: str) -> str:
    """Ask the human to confirm or correct the address. Returns theirs."""
    if not ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    ):
        raise ToolError(_ELICITATION_REQUIRED)

    result = await ctx.elicit(
        "Confirm the email address to sign in with — this address owns any "
        "site you create.",
        response_type=_confirmation_model(proposed),
    )
    if result.action != "accept":
        raise ToolError(
            "The user did not confirm an email address, so this chat is "
            "still unauthenticated."
        )
    return result.data.email


@app.tool
async def authenticate(email: EmailStr, ctx: Context) -> str:
    """Sign in with the email address on the user's Claude account.

    Cervo only works through Claude, and the Claude account's email is the
    identity: pass the address Claude knows the user by rather than
    interrogating them first. The user is asked to confirm it — they may
    correct it — and the chat is signed in the moment they do; there is no
    code to wait for.

    Calling this while already signed in just refreshes the session.
    """
    if not _is_claude(ctx):
        raise ToolError(_CLAUDE_ONLY)

    confirmed = await _confirm_email(ctx, email)

    with connect() as conn:
        session = auth.sign_in(conn, ctx.session_id, confirmed)

    return (
        f"Signed in as {session.email}. This chat stays signed in for "
        f"{_remaining(session)}."
    )


@app.tool
def authentication_status(ctx: Context) -> str:
    """Report whether this chat is signed in, and as whom.

    Use this to answer "am I signed in?" and to check before a run of work,
    rather than triggering a needless confirmation dialog.
    """
    with connect() as conn:
        session = auth.current(conn, ctx.session_id)

    if session is None:
        return (
            "This chat is not signed in. Call authenticate with the email "
            "address on the user's Claude account to start."
        )
    return f"Signed in as {session.email} for another {_remaining(session)}."


def _wants_progress(ctx: Context) -> bool:
    """Whether the client sent a progress token with this call."""
    meta = ctx.request_context.meta if ctx.request_context else None
    return meta is not None and meta.progressToken is not None


def _progress_message(site: website.Website) -> str:
    """One line saying where the deployment is, for a progress notification."""
    if site.status == "live":
        return f"live at {site.url}"
    if site.status == "failed":
        return f"deployment failed: {site.error}"
    if site.status == "pending":
        return "queued, waiting for a worker"
    return site.step or "deploying"


async def _follow_deployment(ctx: Context, site: website.Website) -> website.Website:
    """Follow the deployment chain, reporting each step as progress.

    Only when a progress token came with the call (most clients send one by
    default) — without it the reports would vanish, so the tool returns
    immediately and the deployment app (or list_websites) follows instead.
    Either way the worker keeps going: this only watches, for at most
    ``_FOLLOW_FOR`` seconds, then hands back whatever state the site is in.
    """
    if not _wants_progress(ctx):
        return site
    deadline = monotonic() + _FOLLOW_FOR
    reported = None
    while True:
        state = (site.steps_done, site.status)
        if state != reported:
            reported = state
            await ctx.report_progress(
                progress=site.steps_done,
                total=site.steps_total or None,
                message=_progress_message(site),
            )
        if site.status in ("live", "failed") or monotonic() >= deadline:
            return site
        await asyncio.sleep(_FOLLOW_POLL)
        with connect() as conn:
            site = website.get(conn, site.slug) or site  # deleted mid-watch


@app.tool(app=AppConfig(resource_uri=_DEPLOYMENT_URI))
async def create_website(slug: website.Slug, ctx: Context) -> website.Website:
    """Create a static site owned by the signed-in user.

    Requires this chat to be signed in — the owner is taken from the session,
    never from an argument. If it is not, or the session has expired, this
    fails with instructions to call authenticate; do that and then retry.

    Creation queues a deployment that a worker runs step by step in the
    background. A call that asked for progress follows along — each step is
    reported as it happens — and normally returns the site already "live" at
    its url. Otherwise the site is returned as status "pending" right away;
    check list_websites for progress. Report a "failed" status's error to
    the user. Deployments retry on their own, but calling this again with
    the slug of your own failed site queues a fresh deployment.
    """
    try:
        with connect() as conn:
            session = auth.require(conn, ctx.session_id)
            owner = user.ensure(conn, session.email)
            site = website.create(conn, slug, owner)
    except AppError as error:
        raise ToolError(str(error)) from error
    return await _follow_deployment(ctx, site)


@app.tool(app=AppConfig(resource_uri=_WEBSITES_URI))
def list_websites(ctx: Context) -> list[website.Website]:
    """List every site the signed-in user owns.

    Requires this chat to be signed in. An empty list means they have not
    created any yet, which is not an error. Each site carries its url and the
    state of its deployment: status (pending, deploying, live, or failed)
    and, when it failed, the error.
    """
    try:
        with connect() as conn:
            session = auth.require(conn, ctx.session_id)
            owner = user.ensure(conn, session.email)
            return website.for_user(conn, owner)
    except AppError as error:
        raise ToolError(str(error)) from error


@app.tool
def delete_website(slug: website.Slug, ctx: Context) -> str:
    """Delete a site the signed-in user owns.

    Requires this chat to be signed in, and the site must belong to the
    signed-in user. Deletion is permanent — the site's files are removed
    and its slug is freed for anyone to take — so only call this once the
    user has clearly asked for this specific site to be deleted.

    The site disappears from list_websites immediately; a background job
    then stops routing the subdomain and deletes the site's files.
    """
    try:
        with connect() as conn:
            session = auth.require(conn, ctx.session_id)
            owner = user.ensure(conn, session.email)
            website.delete(conn, slug, owner)
    except AppError as error:
        raise ToolError(str(error)) from error
    return (
        f"The site {slug!r} was deleted. Its files and routing are being "
        "removed in the background."
    )


@app.tool(app=AppConfig(visibility=["app"]))
def website_status(slug: website.Slug) -> website.Website:
    """Report a site's deployment state, for the progress UI.

    Only the deployment app calls this — it polls while the page is open.
    Agents should use list_websites instead, which also proves ownership.
    """
    with connect() as conn:
        site = website.get(conn, slug)
    if site is None:
        raise ToolError(f"There is no site with the slug {slug!r}.")
    return site


@app.resource(
    _DEPLOYMENT_URI,
    app=AppConfig(csp=ResourceCSP(resource_domains=["https://unpkg.com"])),
)
def deployment_view() -> str:
    """The deployment-progress UI, rendered on cervo's design system.

    All of its data arrives at runtime: the create_website result seeds the
    page, then it follows the deployment through website_status.
    """
    return _env.get_template("deployment.html.j2").render()


@app.resource(
    _WEBSITES_URI,
    app=AppConfig(csp=ResourceCSP(resource_domains=["https://unpkg.com"])),
)
def websites_view() -> str:
    """The websites-overview UI, rendered on cervo's design system.

    All of its data arrives at runtime: the list_websites result fills the
    page, and unsettled deployments are followed through website_status.
    """
    return _env.get_template("websites.html.j2").render()


web.register(app)
