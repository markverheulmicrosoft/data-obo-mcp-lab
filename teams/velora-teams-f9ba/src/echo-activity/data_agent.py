"""A model chooses read-only tools; only the MCP supplies identity and data."""

import json
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

TOOLS = {
    "fabric_orders": {
        "description": "Lees de orders en omzet die deze gebruiker in Fabric mag zien.",
        "columns": ("id", "region", "product", "revenue"),
        "title": "Je toegestane orders",
    },
    "fabric_margins": {
        "description": "Vraag marges op. Fabric kan toegang weigeren op basis van de gebruikersrechten.",
        "columns": ("id", "region", "margin"),
        "title": "Je toegestane marges",
    },
}
HELP = "Ik kan je toegestane orders of marges uit Fabric opvragen. Vraag bijvoorbeeld: 'Welke orders mag ik zien?'"
DENIAL = "Geen toegang tot deze gegevens. Fabric heeft de aanvraag geweigerd (SOURCE_FORBIDDEN)."


class LabError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    tenant: str
    mcp_url: str
    project_endpoint: str
    model: str

    def __post_init__(self):
        UUID(self.tenant)
        mcp = urlsplit(self.mcp_url)
        project = urlsplit(self.project_endpoint)
        if (
            mcp.scheme != "https" or not mcp.hostname or mcp.path != "/mcp"
            or mcp.username or mcp.password or mcp.query or mcp.fragment
        ):
            raise ValueError("MCP_URL moet een vaste HTTPS /mcp-URL zijn.")
        if (
            project.scheme != "https" or not project.hostname
            or not project.hostname.endswith(".services.ai.azure.com")
            or not project.path.startswith("/api/projects/")
            or project.username or project.password or project.query or project.fragment or not self.model
        ):
            raise ValueError("Foundry-project/modelconfiguratie ontbreekt of is ongeldig.")


def model_tools(names: list[str]) -> list[dict]:
    return [{
        "type": "function", "name": name, "description": TOOLS[name]["description"],
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    } for name in names if name in TOOLS]


def render_result(name: str, result) -> str:
    if result.is_error:
        text = " ".join(part.text for part in result.content if part.type == "text")
        if "SOURCE_FORBIDDEN:" in text:
            return DENIAL
        if "TOOL_FORBIDDEN:" in text:
            return "De MCP-gateway staat deze tool niet toe voor je account (TOOL_FORBIDDEN)."
        raise LabError("MCP_CALL_FAILED")
    data = result.structured_content
    if (
        not isinstance(data, dict) or data.get("source") != "fabric-warehouse"
        or data.get("authentication") != "delegated-obo"
        or not isinstance(data.get("rows"), list) or len(data["rows"]) > 100
    ):
        raise LabError("MCP_RESULT_INVALID")
    columns = TOOLS[name]["columns"]
    rows = data["rows"]
    if any(not isinstance(row, dict) or any(key not in row for key in columns) for row in rows):
        raise LabError("MCP_RESULT_INVALID")

    def cell(value):
        text = html.escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")[:160]
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")

    lines = [
        f"**{TOOLS[name]['title']}** ({len(rows)} rijen)",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines += ["| " + " | ".join(cell(row[key]) for key in columns) + " |" for row in rows]
    lines.append("Bron: Fabric, namens jouw account. Opgeslagen demodata.")
    return "\n".join(lines)


async def answer_with_client(
    client, user_id: str, message: str,
    select: Callable[[str, list[dict]], Awaitable[list[tuple[str, str]]]],
) -> str:
    identity = await client.call_tool("describe_access", {})
    data = identity.structured_content
    user = data.get("user") if isinstance(data, dict) else None
    if identity.is_error or not isinstance(user, dict) or user.get("id") != user_id:
        raise LabError("USER_BINDING_MISMATCH")
    available = (await client.list_tools()).tools
    names = [tool.name for tool in available if tool.name in TOOLS]
    if not names:
        return "Je account heeft geen toegang tot de datatools van deze MCP."
    calls = await select(message, model_tools(names))
    if not calls:
        return HELP
    if len(calls) > 2 or len({name for name, _ in calls}) != len(calls):
        raise LabError("MODEL_TOOL_REQUEST_INVALID")
    output = []
    for name, arguments in calls:
        if name not in names:
            raise LabError("MODEL_TOOL_REQUEST_INVALID")
        try:
            args = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            raise LabError("MODEL_TOOL_REQUEST_INVALID") from None
        if args != {}:
            raise LabError("MODEL_TOOL_REQUEST_INVALID")
        result = await client.call_tool(name, {})
        output.append(render_result(name, result))
    return "\n\n".join(output)


async def answer(settings: Settings, user_id: str, message: str, token: str, select) -> str:
    UUID(user_id)
    if not isinstance(token, str) or not token:
        raise LabError("USER_TOKEN_REQUIRED")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise LabError("MESSAGE_INVALID")
    # Only user text and tool schemas reach the model. Rows and credentials do not.
    message = message.replace(token, "[credential verwijderd]")
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}, timeout=45, follow_redirects=False,
    ) as http:
        async with Client(streamable_http_client(settings.mcp_url, http_client=http), cache=None) as client:
            return await answer_with_client(client, user_id, message, select)
