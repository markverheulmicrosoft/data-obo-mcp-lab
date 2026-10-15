"""Gedeelde tokenvalidatie; iedere server heeft zijn eigen audience en rollen."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, Tool
from pydantic import AnyHttpUrl

logger = logging.getLogger("velora")
ROLES = {
    "list_orders": frozenset({"North.Reader", "Finance.Reader"}),
    "read_margin": frozenset({"Finance.Reader"}),
}


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} ontbreekt. Vul .env in volgens docs\\bouwen.md.")
    return value


@dataclass(frozen=True)
class Endpoint:
    tenant: str
    client_id: str
    url: str

    def __post_init__(self) -> None:
        UUID(self.tenant)
        UUID(self.client_id)
        url = urlsplit(self.url)
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != "/mcp"
            or not (
                url.scheme == "https"
                or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"})
            )
        ):
            raise ValueError("Gebruik HTTPS met pad /mcp; HTTP is alleen lokaal toegestaan.")

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/v2.0"

    @property
    def scope(self) -> str:
        return f"api://{self.client_id}/access_as_user"

    @classmethod
    def from_env(cls, prefix: str) -> "Endpoint":
        return cls(
            str(UUID(required_env("ENTRA_TENANT_ID"))),
            str(UUID(required_env(f"{prefix}_CLIENT_ID"))),
            required_env(f"{prefix}_URL"),
        )


class EntraVerifier(TokenVerifier):
    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self.jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{endpoint.tenant}/discovery/v2.0/keys",
            timeout=10,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.endpoint.issuer,
                audience=self.endpoint.client_id,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "azp", "scp", "ver"],
                    "strict_aud": True,
                },
            )
            roles = claims.get("roles", [])
            if (
                claims["tid"] != self.endpoint.tenant
                or claims["ver"] != "2.0"
                or not isinstance(claims["scp"], str)
                or not claims["scp"].split()
                or not isinstance(claims["oid"], str)
                or not isinstance(claims["azp"], str)
                or not isinstance(roles, list)
                or not all(isinstance(role, str) for role in roles)
            ):
                raise jwt.InvalidTokenError("Ongeldige gebruikersclaims")
            UUID(claims["oid"])
            UUID(claims["azp"])
            return AccessToken(
                token=token,
                client_id=claims["azp"],
                subject=claims["oid"],
                expires_at=claims["exp"],
                resource=claims["aud"],
                scopes=[f"api://{self.endpoint.client_id}/{scope}" for scope in claims["scp"].split()],
                claims={"tid": claims["tid"], "roles": roles},
            )
        except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, TypeError) as error:
            logger.warning("auth=denied audience=%s reason=%s", self.endpoint.client_id, type(error).__name__)
            return None


def caller() -> AccessToken:
    token = get_access_token()
    if token is None or token.claims is None or token.subject is None:
        raise MCPError(-32001, "USER_CONTEXT_REQUIRED")
    return token


def allowed(tool: str, token: AccessToken) -> bool:
    return bool(ROLES.get(tool, frozenset()).intersection((token.claims or {}).get("roles", [])))


class ProtectedMCP(MCPServer):
    """Dezelfde policy bij discovery en uitvoering; geen eigen MCP-protocolcode."""

    def __init__(self, name: str, endpoint: Endpoint, verifier: TokenVerifier):
        super().__init__(
            name,
            token_verifier=verifier,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(endpoint.issuer),
                resource_server_url=AnyHttpUrl(endpoint.url),
                required_scopes=[endpoint.scope],
                # Entra gebruikt het app-ID als aud; EntraVerifier controleert dit exact.
                validate_token_resource=False,
            ),
            subscriptions=False,
            log_level="WARNING",
        )
        self.layer = name
        self.endpoint = endpoint

    async def list_tools(self) -> list[Tool]:
        token = caller()
        return [tool for tool in await super().list_tools() if allowed(tool.name, token)]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> CallToolResult | InputRequiredResult:
        token = caller()
        granted = allowed(name, token)
        logger.info("layer=%s tool=%s user=%s allowed=%s", self.layer, name, token.subject, granted)
        if not granted:
            raise ToolError("TOOL_FORBIDDEN: deze identiteit mag deze tool niet gebruiken.")
        if arguments:
            raise ToolError("INVALID_ARGUMENTS: deze twee testtools accepteren geen argumenten.")
        return await super().call_tool(name, arguments, context)

    def http_app(self):
        url = urlsplit(self.endpoint.url)
        return self.streamable_http_app(
            stateless_http=True,
            json_response=True,
            max_request_body_size=32 * 1024,
            transport_security=TransportSecuritySettings(
                allowed_hosts=[url.netloc],
                allowed_origins=[f"{url.scheme}://{url.netloc}"],
            ),
        )
