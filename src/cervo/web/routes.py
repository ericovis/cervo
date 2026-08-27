"""The website's routes, registered on the MCP server's HTTP app."""

from fasthtml.common import A, P
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response

from cervo.web import docs, home, layout, legal


def register(app: FastMCP) -> None:
    """Attach every page as a custom route on ``app``.

    FastMCP appends custom routes after its own ``/mcp`` route, so nothing
    here can shadow the MCP endpoint. Order still matters among the pages:
    routes match first-match-wins, so the catch-all 404 must stay last.
    """

    @app.custom_route("/", methods=["GET"])
    async def home_route(request: Request) -> Response:
        return home.home_page()

    @app.custom_route("/docs", methods=["GET"])
    async def docs_route(request: Request) -> Response:
        return docs.docs_page()

    @app.custom_route("/terms", methods=["GET"])
    async def terms_route(request: Request) -> Response:
        return legal.terms_page()

    @app.custom_route("/privacy", methods=["GET"])
    async def privacy_route(request: Request) -> Response:
        return legal.privacy_page()

    # The catch-all: keep this registered last, or it eats the pages above.
    @app.custom_route("/{path:path}", methods=["GET"])
    async def not_found_route(request: Request) -> Response:
        return _not_found_page(request.url.path)


def _not_found_page(path: str) -> Response:
    return layout.page(
        "404 — cervo",
        *layout.hero(
            "● NOT FOUND",
            "404",
            "There is no page here. The address may be misspelled, or the "
            "page may never have existed.",
        ),
        layout.receipt(layout.receipt_row("requested", path)),
        layout.section(
            "WHERE TO GO",
            P(
                "Back to ",
                A("the homepage", href="/"),
                ", or ",
                A("the docs", href="/docs"),
                ".",
            ),
        ),
        status=404,
    )
