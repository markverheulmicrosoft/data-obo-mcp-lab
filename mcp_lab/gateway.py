"""Gateway: autoriseren -> OBO -> een vaste downstream-MCP -> resultaat."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import httpx2
import msal
import uvicorn
from dotenv import load_dotenv
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import TokenVerifier
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ToolAnnotations

from mcp_lab.auth import Endpoint, EntraVerifier, ProtectedMCP, caller, required_env

logger = logging.getLogger("velora")


class EntraOBO:
    def __init__(self, gateway: Endpoint, rights: Endpoint, secret: str):
        if gateway.client_id == rights.client_id:
            raise ValueError("Gateway en Rights Test MCP moeten verschillende app-ID's hebben.")
        if gateway.tenant != rights.tenant:
            raise ValueError("Deze kleine proef ondersteunt alleen dezelfde Entra-tenant.")
        self.scope = f"api://{rights.client_id}/.default"
        self.app = msal.ConfidentialClientApplication(
            gateway.client_id,
            authority=f"https://login.microsoftonline.com/{gateway.tenant}",
            client_credential=secret,
        )

    async def __call__(self, assertion: str) -> str:
        result = await asyncio.to_thread(
            self.app.acquire_token_on_behalf_of,
            user_assertion=assertion,
            scopes=[self.scope],
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            logger.warning(
                "obo=denied code=%s correlation=%s",
                result.get("error", "missing_access_token"),
                result.get("correlation_id", "none"),
            )
            raise ToolError("OBO_FAILED: controleer consent en de Entra-configuratie; geen app-only fallback.")
        return token


def create_gateway(
    endpoint: Endpoint,
    verifier: TokenVerifier,
    rights: Endpoint,
    exchange_token: Callable[[str], Awaitable[str]],
) -> ProtectedMCP:
    if endpoint.client_id == rights.client_id or endpoint.url == rights.url:
        raise ValueError("Gateway en bron moeten aparte audiences en endpoints hebben.")
    mcp = ProtectedMCP("MCP Gateway", endpoint, verifier)

    async def forward(tool: str) -> CallToolResult:
        # ProtectedMCP heeft deze call al geautoriseerd, voordat OBO of bronverkeer start.
        token = await exchange_token(caller().token)
        try:
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            ) as http:
                transport = streamable_http_client(rights.url, http_client=http)
                async with Client(transport, cache=None) as client:
                    return await client.call_tool(tool, {})
        # De MCP-client kan transportfouten in geneste ExceptionGroups verpakken.
        except* (httpx2.HTTPError, MCPError, TimeoutError):
            logger.warning("downstream=failed tool=%s reason=transport_or_protocol", tool)
            raise ToolError("DOWNSTREAM_FAILED: de bronaanroep is niet geslaagd.") from None

    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

    @mcp.tool(annotations=annotations)
    async def list_orders() -> CallToolResult:
        """Vraag toegestane orders op bij de Rights Test MCP namens de gebruiker."""
        return await forward("list_orders")

    @mcp.tool(annotations=annotations)
    async def read_margin() -> CallToolResult:
        """Vraag marges op; gateway EN testbron vereisen Finance.Reader."""
        return await forward("read_margin")

    return mcp


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logger.setLevel(logging.INFO)
    gateway = Endpoint.from_env("GATEWAY")
    rights = Endpoint.from_env("RIGHTS")
    app = create_gateway(
        gateway,
        EntraVerifier(gateway),
        rights,
        EntraOBO(gateway, rights, required_env("GATEWAY_CLIENT_SECRET")),
    ).http_app()
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
