import asyncio
import sqlite3
from collections.abc import Callable
from time import monotonic
from typing import Annotated

from fastmcp import Context, FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from jinja2 import Environment, PackageLoader
from pydantic import Field

from cervo import auth, user, web, website
from cervo.db import connect
from cervo.errors import AppError

# Bearer tokens on every MCP request: the provider mounts the OAuth
# endpoints (/.well-known metadata, /authorize, /token, /register, /revoke)
# and unauthenticated calls to /mcp are refused with a 401 before any tool
# runs. Signing in happens in the browser when the connector is added.
app = FastMCP("cervo", auth=auth.CervoOAuthProvider())

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

_NOT_AUTHENTICATED = (
    "This request carries no valid cervo sign-in. Ask the user to reconnect "
    "the cervo connector — signing in happens in the browser when it is "
    "added — and then try again."
)


def _owner(conn: sqlite3.Connection) -> user.User:
    """The user behind this request's Bearer token.

    The transport already refused requests without a valid token, so this is
    a lookup, not a check — the guard remains only for the token outliving
    its account.
    """
    token = get_access_token()
    if token is None or token.subject is None:
        raise ToolError(_NOT_AUTHENTICATED)
    owner = user.by_id(conn, int(token.subject))
    if owner is None:
        raise ToolError(_NOT_AUTHENTICATED)
    return owner


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


def _file_progress_message(state: website.FileWrite) -> str:
    """One line saying where the write is, for a progress notification."""
    if state.status == "done":
        return f"written to {state.url}"
    if state.status == "failed":
        return f"write failed: {state.error}"
    if state.status == "pending":
        return "queued, waiting for a worker"
    return state.step or "working"


def _file_deletion_progress_message(state: website.FileDeletion) -> str:
    """One line saying where the deletion is, for a progress notification."""
    if state.status == "done":
        return f"deleted {state.path} from the site"
    if state.status == "failed":
        return f"deletion failed: {state.error}"
    if state.status == "pending":
        return "queued, waiting for a worker"
    return state.step or "working"


async def _follow[S](
    ctx: Context,
    state: S,
    refresh: Callable[[S], S],
    message: Callable[[S], str],
    terminal: tuple[str, ...],
) -> S:
    """Follow a chain of background work, reporting each step as progress.

    Only when a progress token came with the call (most clients send one by
    default) — without it the reports would vanish, so the tool returns
    immediately and something else follows instead. Either way the worker
    keeps going: this only watches, for at most ``_FOLLOW_FOR`` seconds,
    then hands back whatever state the work is in.
    """
    if not _wants_progress(ctx):
        return state
    deadline = monotonic() + _FOLLOW_FOR
    reported = None
    while True:
        seen = (state.steps_done, state.status)
        if seen != reported:
            reported = seen
            await ctx.report_progress(
                progress=state.steps_done,
                total=state.steps_total or None,
                message=message(state),
            )
        if state.status in terminal or monotonic() >= deadline:
            return state
        await asyncio.sleep(_FOLLOW_POLL)
        state = refresh(state)


async def _follow_deployment(ctx: Context, site: website.Website) -> website.Website:
    """Follow the deployment chain, reporting each step as progress."""

    def refresh(current: website.Website) -> website.Website:
        with connect() as conn:
            return website.get(conn, current.slug) or current  # deleted mid-watch

    return await _follow(ctx, site, refresh, _progress_message, ("live", "failed"))


@app.tool(app=AppConfig(resource_uri=_DEPLOYMENT_URI))
async def create_website(slug: website.Slug, ctx: Context) -> website.Website:
    """Create a static site owned by the connected account.

    The owner is the account that connected this cervo connector — it is
    taken from the request's credentials, never from an argument.

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
            site = website.create(conn, slug, _owner(conn))
    except AppError as error:
        raise ToolError(str(error)) from error
    return await _follow_deployment(ctx, site)


@app.tool
async def write_file(
    slug: website.Slug,
    path: website.FilePath,
    content: Annotated[str, Field(max_length=1_048_576)],
    ctx: Context,
) -> website.FileWrite:
    """Write an HTML or CSS file into a site the connected account owns.

    The file goes to ``path`` inside the site — a relative path like
    "blog/post.html" or "css/main.css"; lowercase, no leading slash, no
    "..". Subfolders are created as needed, and writing to an existing
    path (index.html included) replaces that file. Only .html and .css
    files up to 1 MiB are accepted; anything else is refused immediately.

    The write runs in the background: the content is checked, then
    written. A call that asked for progress follows along — each step is
    reported as it happens — and normally returns the file already "done",
    served at its url. Otherwise it returns status "pending" right away.
    Report a "failed" status's error to the user: the content did not pass
    validation, or the site was deleted meanwhile.
    """
    try:
        with connect() as conn:
            state = website.submit_file(conn, slug, path, content, _owner(conn))
    except AppError as error:
        raise ToolError(str(error)) from error

    def refresh(current: website.FileWrite) -> website.FileWrite:
        with connect() as conn:
            return website.file_state(conn, slug, path, content) or current

    return await _follow(
        ctx, state, refresh, _file_progress_message, ("done", "failed")
    )


@app.tool
async def delete_file(
    slug: website.Slug,
    path: website.FilePath,
    ctx: Context,
) -> website.FileDeletion:
    """Delete a file from a site the connected account owns.

    The file at ``path`` — a relative path like "blog/post.html" — is
    removed and stops being served. Deletion is permanent, though any page
    can be written again with write_file; deleting index.html is allowed,
    and the site's default landing page is put back in its place. Only call
    this once the user has clearly asked for this specific file to be
    deleted.

    The deletion runs in the background. A call that asked for progress
    follows along and normally returns the file already "done" — gone from
    the site. Otherwise it returns status "pending" right away. Report a
    "failed" status's error to the user: the site was deleted meanwhile.
    """
    try:
        with connect() as conn:
            owner = _owner(conn)
            state = website.submit_file_deletion(conn, slug, path, owner)
    except AppError as error:
        raise ToolError(str(error)) from error

    def refresh(current: website.FileDeletion) -> website.FileDeletion:
        with connect() as conn:
            return website.file_deletion_state(conn, slug, path, owner.id) or current

    return await _follow(
        ctx, state, refresh, _file_deletion_progress_message, ("done", "failed")
    )


@app.tool(app=AppConfig(resource_uri=_WEBSITES_URI))
def list_websites() -> list[website.Website]:
    """List every site the connected account owns.

    An empty list means they have not created any yet, which is not an
    error. Each site carries its url and the state of its deployment:
    status (pending, deploying, live, or failed) and, when it failed, the
    error.
    """
    try:
        with connect() as conn:
            return website.for_user(conn, _owner(conn))
    except AppError as error:
        raise ToolError(str(error)) from error


@app.tool
def delete_website(slug: website.Slug) -> str:
    """Delete a site the connected account owns.

    The site must belong to the connected account. Deletion is permanent —
    the site's files are removed and its slug is freed for anyone to take —
    so only call this once the user has clearly asked for this specific
    site to be deleted.

    The site disappears from list_websites immediately; a background job
    then stops routing the subdomain and deletes the site's files.
    """
    try:
        with connect() as conn:
            website.delete(conn, slug, _owner(conn))
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
    Agents should use list_websites instead, which also scopes to the owner.
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
