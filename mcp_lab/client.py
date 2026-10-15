"""Referentieclient: echte interactieve Entra-login, daarna MCP-discovery/calls."""

import argparse
import asyncio
import json

import httpx2
import msal
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mcp_lab.auth import Endpoint, required_env


async def run(tool: str | None) -> None:
    endpoint = Endpoint.from_env("GATEWAY")
    app = msal.PublicClientApplication(
        required_env("CLIENT_APP_ID"),
        authority=f"https://login.microsoftonline.com/{endpoint.tenant}",
    )
    result = await asyncio.to_thread(
        app.acquire_token_interactive,
        scopes=[endpoint.scope],
        prompt="select_account",
    )
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            f"Login mislukt: {result.get('error', 'missing_access_token')}; "
            f"correlation_id={result.get('correlation_id', 'none')}"
        )
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with Client(streamable_http_client(endpoint.url, http_client=http), cache=None) as client:
            if tool is None:
                print(json.dumps([item.name for item in (await client.list_tools()).tools], indent=2))
            else:
                response = await client.call_tool(tool, {})
                print(response.model_dump_json(indent=2, by_alias=True))
                if response.is_error:
                    raise SystemExit(1)


if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", nargs="?", help="Geen tool: tools/list. Of list_orders / read_margin.")
    asyncio.run(run(parser.parse_args().tool))
