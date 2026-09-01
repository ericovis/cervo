"""cervo as an OAuth authorization server, plugged into FastMCP.

This is the adapter between the MCP SDK's authorization-server protocol and
the service: FastMCP mounts the ``/.well-known`` metadata, ``/authorize``,
``/token``, ``/register``, and ``/revoke`` endpoints and enforces Bearer
tokens on ``/mcp``; every hook lands here and reads or writes through
``cervo.auth``'s service.

Clients arrive two ways: plain Dynamic Client Registration, and CIMD —
Claude's "hosted client metadata", where the client_id is an HTTPS URL to a
metadata document (Anthropic hosts Claude's). The metadata route is rebuilt
to advertise CIMD support, since claude.ai only tries it when both
``client_id_metadata_document_supported`` and a "none" token auth method are
announced.
"""

import sqlite3
from urllib.parse import urlsplit

from fastmcp.server.auth import AccessToken, OAuthProvider
from fastmcp.server.auth.cimd import CIMDClientManager
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.routing import Route

from cervo import config, db
from cervo.auth import service

# Only Claude may connect for now. Every Claude client reaches cervo through one
# of these callbacks, so any other is refused — closing the confused-deputy
# where someone registers a client (or hosts a metadata document) pointing at
# their own server and phishes a victim through the genuine sign-in. To admit
# another provider later (ChatGPT, Gemini), add its callback and CIMD hosts.
#   - Claude Code self-registers (DCR) with an ephemeral loopback callback; a
#     loopback code can only ever reach the user's own machine, never a remote
#     attacker.
#   - claude.ai / Desktop call back to https://claude.ai/api/mcp/auth_callback.
#   - CIMD metadata documents (e.g. Claude Code's) are served from claude.ai.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOWED_REDIRECT_HOSTS = frozenset({"claude.ai"})
_ALLOWED_CIMD_HOSTS = frozenset({"claude.ai"})


def _redirect_allowed(redirect_uri: object) -> bool:
    """Whether cervo will send an authorization code to this callback."""
    parts = urlsplit(str(redirect_uri))
    if parts.hostname in _LOOPBACK_HOSTS:
        return True  # only the user's own machine can receive a loopback code
    return parts.scheme == "https" and parts.hostname in _ALLOWED_REDIRECT_HOSTS


class CervoOAuthProvider(OAuthProvider):
    """The authorization server behind the claude.ai connector."""

    def __init__(self) -> None:
        super().__init__(
            base_url=config.origin(),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        # CIMD clients validate redirect URIs against their own document, so
        # no extra patterns are configured here.
        self._cimd = CIMDClientManager()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if self._cimd.is_cimd_client_id(client_id):
            # Any https URL is a CIMD client_id, so gate on the host: a metadata
            # document served from anywhere but Claude is refused, not fetched.
            if urlsplit(client_id).hostname not in _ALLOWED_CIMD_HOSTS:
                return None
            return await self._cimd.get_client(client_id)
        data = await db.transact(lambda conn: service.get_client(conn, client_id))
        if data is None:
            return None
        return OAuthClientInformationFull.model_validate_json(data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Refuse any self-registering client whose callback is not Claude's, so
        # a code can never be delivered to an attacker-controlled endpoint.
        uris = client_info.redirect_uris or []
        if not uris or not all(_redirect_allowed(uri) for uri in uris):
            raise RegistrationError(
                "invalid_redirect_uri",
                "cervo accepts only Claude clients: a loopback callback "
                "(Claude Code) or an https://claude.ai/ callback.",
            )
        await db.transact(
            lambda conn: service.save_client(
                conn, client_info.client_id, client_info.model_dump_json()
            )
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the verification pages."""

        def begin(conn: sqlite3.Connection):
            return service.begin(
                conn,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                state=params.state,
                scopes=params.scopes or [],
                code_challenge=params.code_challenge,
                resource=params.resource,
            )

        txn = await db.transact(begin)
        return f"{config.origin()}/verify?txn={txn.txn_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await db.transact(
            lambda conn: service.load_code(conn, authorization_code)
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        tokens = await db.transact(
            lambda conn: service.exchange_code(conn, authorization_code)
        )
        if tokens is None:
            raise TokenError(
                "invalid_grant", "authorization code was already used or has expired"
            )
        return tokens

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await db.transact(lambda conn: service.load_refresh(conn, refresh_token))

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        tokens = await db.transact(
            lambda conn: service.exchange_refresh(conn, refresh_token, scopes)
        )
        if tokens is None:
            raise TokenError("invalid_grant", "refresh token is no longer valid")
        return tokens

    async def load_access_token(self, token: str) -> AccessToken | None:
        identity = await db.transact(lambda conn: service.load_access(conn, token))
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity["client_id"],
            scopes=identity["scopes"],
            expires_at=identity["expires_at"],
            subject=identity["subject"],
            claims={"sub": identity["subject"], "email": identity["email"]},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await db.transact(lambda conn: service.revoke(conn, token.token))

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """The SDK's routes, with the metadata rebuilt to advertise CIMD."""
        routes: list[Route] = []
        for route in super().get_routes(mcp_path):
            if isinstance(route, Route) and route.path.startswith(
                "/.well-known/oauth-authorization-server"
            ):
                assert self.base_url is not None
                metadata = build_metadata(
                    self.base_url,
                    self.service_documentation_url,
                    self.client_registration_options or ClientRegistrationOptions(),
                    self.revocation_options or RevocationOptions(),
                )
                metadata.client_id_metadata_document_supported = True
                existing = metadata.token_endpoint_auth_methods_supported or []
                metadata.token_endpoint_auth_methods_supported = [*existing, "none"]
                routes.append(
                    Route(
                        path=route.path,
                        endpoint=cors_middleware(
                            MetadataHandler(metadata).handle, ["GET", "OPTIONS"]
                        ),
                        methods=route.methods or ["GET", "OPTIONS"],
                        name=route.name,
                        include_in_schema=route.include_in_schema,
                    )
                )
            else:
                routes.append(route)
        return routes
