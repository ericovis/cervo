"""Settings.

Values under "fixed by the compose environment" are constants on purpose: the
stack runs from docker-compose in development and production alike, so paths
and service addresses are the same everywhere and an environment variable
would only invite drift. Everything else is read from the environment (or a
``.env`` file) with defaults that are correct for development.
"""

from pathlib import Path

from decouple import config

# Fixed by the compose environment — identical shape in development and
# production.
DATA_DIR = Path("/mnt/data")  # the shared volume: app, worker, and caddy
DATABASE_PATH = DATA_DIR / "cervo.db"
MCP_HOST = "0.0.0.0"  # inside the container; only caddy reaches it
MCP_PORT = 8000
CADDY_ADMIN_URL = "http://caddy:2019"  # caddy's admin API, by service name
MCP_UPSTREAM = "app:8000"  # how caddy reaches the MCP server

# Environment-dependent.
DOMAIN = config("DOMAIN", default="localhost")
SCHEME = config("SCHEME", default="http")  # "https" in production; caddy
# then obtains a certificate per hostname and redirects plain http.
ACME_EMAIL = config("ACME_EMAIL", default="")  # contact for certificate issues


def origin(host: str | None = None) -> str:
    """The public origin of cervo itself, or of ``host`` — scheme included."""
    return f"{SCHEME}://{host or DOMAIN}"


EMAIL_HOST = config("EMAIL_HOST", default="mail")
EMAIL_PORT = config("EMAIL_PORT", default=1025, cast=int)
EMAIL_FROM = config("EMAIL_FROM", default="cervo@localhost")
EMAIL_USER = config("EMAIL_USER", default="")  # set for a real SMTP provider:
EMAIL_PASSWORD = config("EMAIL_PASSWORD", default="")  # enables STARTTLS+login

AUTH_CODE_TTL = config("AUTH_CODE_TTL", default=600, cast=int)
AUTH_SESSION_TTL = config("AUTH_SESSION_TTL", default=4 * 60 * 60, cast=int)
