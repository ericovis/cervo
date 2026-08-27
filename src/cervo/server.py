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

_ELICITATION_REQUIRED = (
    "This client cannot ask the user to confirm an email address, and cervo "
    "will not mail a code to an unconfirmed one. Use a client that supports "
    "MCP elicitation."
)


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
                description="Where the confirmation code will be sent.",
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
        "Confirm the email address to sign in with — the confirmation code "
        "goes here, and this address owns any site you create.",
        response_type=_confirmation_model(proposed),
    )
    if result.action != "accept":
        raise ToolError(
            "The user did not confirm an email address. Nothing was sent and "
            "this chat is still unauthenticated."
        )
    return result.data.email


@app.tool
async def authenticate(email: EmailStr, ctx: Context) -> str:
    """Sign in by confirming control of an email address.

    Confirming an email is the only way to prove identity here. The user is
    asked to confirm the address before anything is sent — they may correct
    it, so pass your best guess rather than interrogating them first. A
    six-digit code is then emailed; ask the user to paste it back and call
    confirm_authentication with it. Never guess or make up the code — only the
    email contains it.

    If this chat is already signed in as the same address, this is a no-op.
    """
    confirmed = await _confirm_email(ctx, email)

    with connect() as conn:
        session = auth.current(conn, ctx.session_id)
        if session and session.email == confirmed:
            return (
                f"Already signed in as {session.email} for another "
                f"{_remaining(session)}. No code was sent — just carry on."
            )

        challenge = auth.start(conn, ctx.session_id, confirmed)

    return (
        f"A confirmation code was sent to {challenge.email}. Ask the user for "
        f"it and call confirm_authentication. It expires in "
        f"{auth.minutes_until(challenge.expires_at)} minutes."
    )


@app.tool
def confirm_authentication(code: str, ctx: Context) -> str:
    """Finish signing in using the code that was emailed.

    Pass the code exactly as the user gave it. Only call this once the user has
    supplied a code — a wrong one counts against a limited number of attempts.
    """
    # The connection closes before the error becomes a ToolError, so a refusal
    # still commits what it recorded on the way (see cervo.errors.AppError).
    try:
        with connect() as conn:
            session = auth.confirm(conn, ctx.session_id, code)
    except AppError as error:
        raise ToolError(str(error)) from error

    return (
        f"Signed in as {session.email}. This chat stays signed in for "
        f"{_remaining(session)}, so you will not need another code until then."
    )


@app.tool
def authentication_status(ctx: Context) -> str:
    """Report whether this chat is signed in, and as whom.

    Use this to answer "am I signed in?" and to check before a run of work,
    rather than triggering a needless code email.
    """
    with connect() as conn:
        session = auth.current(conn, ctx.session_id)

    if session is None:
        return (
            "This chat is not signed in. Call authenticate with the user's "
            "email to start."
        )
    return f"Signed in as {session.email} for another {_remaining(session)}."


@app.tool(app=AppConfig(resource_uri=_DEPLOYMENT_URI))
def create_website(slug: website.Slug, ctx: Context) -> website.Website:
    """Create a static site owned by the signed-in user.

    Requires this chat to be signed in — the owner is taken from the session,
    never from an argument. If it is not, or the session has expired, this
    fails with instructions to call authenticate; do that and then retry.

    Creation queues a deployment job that a worker runs in the background:
    the returned site starts as status "pending" and is normally "live" at
    its url within seconds — check list_websites for progress, and report a
    "failed" status's error to the user. Deployments retry on their own, but
    calling this again with the slug of your own failed site queues a fresh
    deployment.
    """
    try:
        with connect() as conn:
            session = auth.require(conn, ctx.session_id)
            owner = user.ensure(conn, session.email)
            return website.create(conn, slug, owner)
    except AppError as error:
        raise ToolError(str(error)) from error


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
