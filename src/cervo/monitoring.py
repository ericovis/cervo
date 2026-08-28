"""Reporting to Honeybadger: errors with their context, and Insights events.

Dark outside production on purpose: without ``HONEYBADGER_API_KEY`` — set
only by production's environment file — nothing is configured and every
helper returns without side effects, so development and tests run the same
code paths as production minus the reporting. The pieces:

- :func:`setup` points the client at the project, once per process.
- :func:`wrap` reports unhandled web errors with their whole request, and
  emits an ``asgi.request`` Insights event per request served.
- :class:`ReportMCPErrors` does the same for MCP operations, whose failures
  FastMCP turns into protocol responses before the ASGI layer ever sees them.
- :func:`report` and :func:`event` are for code that handles its own
  failures — the worker's job loop.

Log shipping is deliberately not here: in production every container's
output already lands in journald, which vector tails and forwards to
Honeybadger Insights (see deploy/templates/vector.yaml.j2).
"""

import logging
from typing import Any

from fastmcp.exceptions import (
    DisabledError,
    FastMCPError,
    NotFoundError,
    PromptError,
    ResourceError,
    ToolError,
)
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from honeybadger import contrib, honeybadger
from mcp import McpError

from cervo import config
from cervo.errors import AppError

_log = logging.getLogger(__name__)

# Replaces honeybadger's default filter list, so the usual suspects are
# restated. Applied to every key, however nested, in params, headers, and
# request bodies: credentials in all the shapes OAuth passes them around,
# and the sign-in codes.
_PARAMS_FILTERS = [
    "password",
    "authorization",
    "cookie",
    "code",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "code_verifier",
]


def enabled() -> bool:
    """Whether reporting is on — that is, whether production set the key."""
    return bool(config.HONEYBADGER_API_KEY)


def setup() -> None:
    """Configure the client for this process; a no-op without an API key.

    Called once per serving process: by ``cervo.asgi`` as each uvicorn
    worker imports it, and by the job worker's ``main``. Insights is on, so
    the ASGI wrap also emits one ``asgi.request`` event per request.
    """
    if not enabled():
        return
    honeybadger.configure(
        api_key=config.HONEYBADGER_API_KEY,
        environment=config.HONEYBADGER_ENVIRONMENT,
        project_root="/app",  # where the image puts the checkout
        params_filters=_PARAMS_FILTERS,
        insights_enabled=True,
    )


def wrap(application: Any) -> Any:
    """The ASGI app wrapped so unhandled errors are reported — or as it was.

    The middleware attaches the whole request — url, method, headers, query
    string, body, client address — filtered through the params filters, and
    clears any context when the request ends.
    """
    if not enabled():
        return application
    return contrib.ASGIHoneybadger(application)


def report(error: BaseException, **context: Any) -> None:
    """Report one handled error, with everything the caller can name.

    The error's own traceback is sent explicitly: for an error dug out of a
    wrapper it holds the frames that raised it, where the ambient
    ``sys.exc_info`` would only show the wrapper's. ``user_id`` and
    ``user_email`` are context keys Honeybadger understands — they
    aggregate who is affected across occurrences.
    """
    if not enabled():
        return
    try:
        honeybadger.notify(
            exception=error, context=context, exc_traceback=error.__traceback__
        )
    except Exception:  # noqa: BLE001 — reporting must never add a failure
        _log.warning("could not report to honeybadger", exc_info=True)


def event(event_type: str, data: dict[str, Any]) -> None:
    """Send one Insights event; the client queues and batches them."""
    if not enabled():
        return
    try:
        honeybadger.event(event_type, data)
    except Exception:  # noqa: BLE001 — reporting must never add a failure
        _log.warning("could not send an event to honeybadger", exc_info=True)


class ReportMCPErrors(Middleware):
    """FastMCP middleware reporting failures the transport never surfaces.

    An exception inside an MCP operation becomes a protocol error response,
    not an ASGI-level crash, so the wrapped app alone would miss every one.
    Only defects are reported — errors that are answers are not — each with
    the operation, its arguments, and who ran it attached.
    """

    async def on_message(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        try:
            return await call_next(context)
        except Exception as error:
            defect = _defect(error)
            if defect is not None:
                report(defect, **_mcp_context(context))
            raise


# Errors that are answers, not defects: protocol errors, unknown names, bad
# arguments, and every deliberate refusal (the AppError → ToolError path).
_REFUSALS = (McpError, FastMCPError, NotFoundError, DisabledError)

# What FastMCP masks a tool's, resource's, or prompt's own exception into
# before it travels back through the middleware chain — chaining the
# original as the cause.
_MASKED = (ToolError, ResourceError, PromptError)


def _defect(error: Exception) -> BaseException | None:
    """The unexpected failure inside ``error`` — None when it is an answer.

    For a masked error the distinction lives in its cause: none means it
    was raised deliberately, an AppError is a refusal written to be read by
    the user, and anything else is the actual bug, returned so it is
    reported with its own class and frames.
    """
    cause = error.__cause__
    if isinstance(error, _MASKED) and cause is not None:
        return None if isinstance(cause, (AppError, *_REFUSALS)) else cause
    if isinstance(error, _REFUSALS):
        return None
    return error


def _mcp_context(context: MiddlewareContext[Any]) -> dict[str, Any]:
    """What was being done, with what arguments, and by whom."""
    data: dict[str, Any] = {"component": "mcp", "method": context.method}
    for field in ("name", "uri", "arguments"):
        value = getattr(context.message, field, None)
        if value is not None:
            data[field] = value
    arguments = data.get("arguments")
    if isinstance(arguments, dict) and "content" in arguments:
        # A megabyte of page bytes is bulk, not context; its size tells the
        # story.
        arguments = dict(arguments)
        arguments["content_bytes"] = len(str(arguments.pop("content")).encode())
        data["arguments"] = arguments
    token = get_access_token()
    if token is not None:
        data["user_id"] = token.subject
        data["user_email"] = (token.claims or {}).get("email")
    return data
