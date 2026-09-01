"""cervo's brand: the antler mark, the icons, and the social preview card.

Every icon is one drawing: `bin/brand` generates the whole set from
`brand/mark.svg`, so the antler exists in exactly one place.

The mark is inlined into every page — it is drawn in ``currentColor``, so it
follows the theme like everything else and costs no request. The icons and
the preview card are the one place cervo serves real files: browsers and
social scrapers fetch those on their own terms, outside the page, which is
why they cannot be inline the way the figures in ``web/figures.py`` are.

The files themselves live in ``cervo/brand/``, the single source for the
whole set (including the sizes nothing links to yet).
"""

from functools import cache
from importlib import resources

from fasthtml.common import Link, Meta, NotStr
from starlette.requests import Request
from starlette.responses import Response

from cervo import config

# What a page says about itself when it has nothing more specific to say —
# the same sentence the preview card carries.
DESCRIPTION = (
    "Static hosting on a shared VPS. No dashboard, no server to log into — "
    "you create and update a site by talking to an AI."
)

# The files a browser or a scraper fetches, each served under its own
# delivered name. Keeping the filename in the URL is what makes a revision
# bustable: a scraper re-reads a card only on its own schedule (LinkedIn only
# through its Post Inspector), so a new card has to arrive at a new address.
_SERVED = {
    "favicon.svg": "image/svg+xml",
    "favicon-16.png": "image/png",
    "favicon-32.png": "image/png",
    "apple-touch-icon-180.png": "image/png",
    "og-image-1200x630.png": "image/png",
}
# The social card, and the size scrapers are told to expect.
_CARD = "og-image-1200x630.png"
_CARD_SIZE = ("1200", "630")
# Icons change only when the brand does, and a stale one is harmless.
_CACHE_CONTROL = "public, max-age=86400"

# The tile colour behind the mark, matched to each theme's --bg so a mobile
# browser's chrome continues the page.
_THEME_COLOR_DARK = "#1b1a16"
_THEME_COLOR_LIGHT = "#f7f3ea"


@cache
def _file(name: str) -> bytes:
    return resources.files("cervo").joinpath("brand", name).read_bytes()


def mark() -> NotStr:
    """The antler mark as inline SVG, drawn in the current text colour."""
    return NotStr(_file("mark.svg").decode().strip())


def head_tags(title: str, description: str, base: str):
    """The icons, the preview card, and the theme colours, for a ``<head>``.

    ``base`` is empty on cervo's own pages and the apex origin on a site's
    default page; the preview card needs an absolute URL either way, because
    a scraper resolves it against nothing.
    """
    origin = base or config.origin()
    width, height = _CARD_SIZE
    return (
        Meta(name="description", content=description),
        Link(rel="icon", href=f"{base}/favicon.svg", type="image/svg+xml"),
        Link(
            rel="icon", href=f"{base}/favicon-32.png", type="image/png", sizes="32x32"
        ),
        Link(
            rel="icon", href=f"{base}/favicon-16.png", type="image/png", sizes="16x16"
        ),
        Link(rel="apple-touch-icon", href=f"{base}/apple-touch-icon-180.png"),
        Meta(
            name="theme-color",
            content=_THEME_COLOR_DARK,
            media="(prefers-color-scheme: dark)",
        ),
        Meta(
            name="theme-color",
            content=_THEME_COLOR_LIGHT,
            media="(prefers-color-scheme: light)",
        ),
        Meta(property="og:type", content="website"),
        Meta(property="og:site_name", content="cervo"),
        Meta(property="og:title", content=title),
        Meta(property="og:description", content=description),
        Meta(property="og:image", content=f"{origin}/{_CARD}"),
        Meta(property="og:image:width", content=width),
        Meta(property="og:image:height", content=height),
        Meta(name="twitter:card", content="summary_large_image"),
    )


def register_assets(app) -> None:
    """Attach a route per served file. Called from ``web.routes.register``."""
    for name, media_type in _SERVED.items():
        app.custom_route(f"/{name}", methods=["GET"])(_asset_route(name, media_type))


def _asset_route(name: str, media_type: str):
    async def asset(request: Request) -> Response:
        return Response(
            _file(name),
            media_type=media_type,
            headers={"cache-control": _CACHE_CONTROL},
        )

    asset.__name__ = name.replace("-", "_").replace(".", "_")
    return asset
