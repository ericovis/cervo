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

from fastmcp.server.auth import AccessToken, OAuthProvider
from fastmcp.server.auth.cimd import CIMDClientManager
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.routing import Route

from cervo import config, db
from cervo.auth import service


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
            return await self._cimd.get_client(client_id)
        data = await db.transact(lambda conn: service.get_client(conn, client_id))
        if data is None:
            return None
        return OAuthClientInformationFull.model_validate_json(data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
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
