from pathlib import Path

from decouple import config

DATA_DIR = config(
    "DATA_DIR", default=Path(__file__).resolve().parents[2] / ".data", cast=Path
)

DATABASE_PATH = config("DATABASE_PATH", default=DATA_DIR / "cervo.db", cast=Path)

EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=1025, cast=int)
EMAIL_FROM = config("EMAIL_FROM", default="cervo@localhost")

MCP_HOST = config("MCP_HOST", default="127.0.0.1")
MCP_PORT = config("MCP_PORT", default=8000, cast=int)

SITES_DOMAIN = config("SITES_DOMAIN", default="localhost")

AUTH_CODE_TTL = config("AUTH_CODE_TTL", default=600, cast=int)
AUTH_SESSION_TTL = config("AUTH_SESSION_TTL", default=4 * 60 * 60, cast=int)
