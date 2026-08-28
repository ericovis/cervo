"""The public website, served beside the MCP endpoint on the same process.

Pages are built from FastHTML fasttags on the "deploy receipt" design
system and registered on the FastMCP instance as custom HTTP routes —
``server.py`` calls :func:`register` and caddy proxies the whole apex
domain, so the site and ``/mcp`` share one server.
"""

from cervo.web.routes import register
from cervo.web.site import default_page

__all__ = ["default_page", "register"]
