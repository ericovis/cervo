"""The OAuth authorization server: who a request is, proved once in the browser.

``_dao`` is private to this package: its leading underscore says so, and
ruff's TID251 rule enforces it. The MCP server plugs in
:class:`CervoOAuthProvider`; the verification web pages go through the
service functions.
"""

from cervo.auth.provider import CervoOAuthProvider
from cervo.auth.service import AuthError, confirm, create_tables, send_code, transaction
from cervo.auth.types import Transaction

__all__ = [
    "AuthError",
    "CervoOAuthProvider",
    "Transaction",
    "confirm",
    "create_tables",
    "send_code",
    "transaction",
]
