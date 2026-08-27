"""Caddy, the front door: config rendered from the database, loaded over its
admin API.

Caddy serves every site from ``DATA_DIR`` and reverse-proxies cervo itself,
reading its config from the ``Caddyfile`` rendered here — ``DATA_DIR`` is the
volume both share. Reloading is a POST to the admin API, which only the
compose network can reach, so the running container is never replaced.
"""

import os
import urllib.error
import urllib.request

from jinja2 import Environment, PackageLoader

from cervo import config
from cervo.website.types import Website

_env = Environment(loader=PackageLoader("cervo"), keep_trailing_newline=True)


def _caddyfile_path():
    return config.DATA_DIR / "Caddyfile"


def render(sites: list[Website]) -> None:
    """Write the whole Caddyfile for these sites. Idempotent."""
    text = _env.get_template("Caddyfile.j2").render(
        domain=config.DOMAIN,
        scheme=config.SCHEME,
        acme_email=config.ACME_EMAIL,
        data_dir=config.DATA_DIR,
        mcp_upstream=config.MCP_UPSTREAM,
        sites=sites,
    )
    caddyfile = _caddyfile_path()
    caddyfile.parent.mkdir(parents=True, exist_ok=True)
    # Swapped in whole, so caddy never reads a half-written file.
    scratch = caddyfile.with_name("Caddyfile.rendering")
    scratch.write_text(text)
    os.replace(scratch, caddyfile)


def reload() -> None:
    """Hand the rendered Caddyfile to the running caddy over its admin API."""
    request = urllib.request.Request(
        f"{config.CADDY_ADMIN_URL}/load",
        data=_caddyfile_path().read_bytes(),
        headers={"Content-Type": "text/caddyfile"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        # The body is caddy's own explanation, e.g. an adapter error.
        detail = error.read().decode(errors="replace").strip()
        raise RuntimeError(f"caddy rejected the config: {detail}") from error
