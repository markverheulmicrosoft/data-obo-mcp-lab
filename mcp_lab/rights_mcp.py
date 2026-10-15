"""De testbron: eigen authenticatie en dataselectie, zonder LLM of database."""

import logging
import os

import uvicorn
from dotenv import load_dotenv
from mcp.server.auth.provider import TokenVerifier
from mcp.types import ToolAnnotations

from mcp_lab.auth import Endpoint, EntraVerifier, ProtectedMCP, caller

ORDERS = (
    {"id": "N-001", "region": "Noord", "revenue": 1200, "margin": 240},
    {"id": "Z-001", "region": "Zuid", "revenue": 1800, "margin": 540},
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def create_rights_mcp(endpoint: Endpoint, verifier: TokenVerifier) -> ProtectedMCP:
    mcp = ProtectedMCP("Rights Test MCP", endpoint, verifier)

    @mcp.tool(annotations=READ_ONLY)
    def list_orders() -> dict[str, object]:
        """Lees toegestane orders. Regiomanagers zien Noord; finance ziet beide regio's."""
        finance = "Finance.Reader" in (caller().claims or {}).get("roles", [])
        rows = [
            {key: value for key, value in row.items() if key != "margin"}
            for row in ORDERS
            if finance or row["region"] == "Noord"
        ]
        return {"source": "rights-test", "orders": rows}

    @mcp.tool(annotations=READ_ONLY)
    def read_margin() -> dict[str, object]:
        """Lees marges; uitsluitend voor Finance.Reader op deze testbron."""
        return {
            "source": "rights-test",
            "margins": [
                {"id": row["id"], "region": row["region"], "margin": row["margin"]}
                for row in ORDERS
            ],
        }

    return mcp


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("velora").setLevel(logging.INFO)
    settings = Endpoint.from_env("RIGHTS")
    uvicorn.run(
        create_rights_mcp(settings, EntraVerifier(settings)).http_app(),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8001")),
    )
