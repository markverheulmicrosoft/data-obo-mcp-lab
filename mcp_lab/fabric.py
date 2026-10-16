"""Fixed, read-only queries against Fabric using a fresh delegated SQL connection."""

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

import mssql_python
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from mcp_lab.auth import ProtectedMCP, caller

logger = logging.getLogger("velora")
SQL_SCOPE = "https://database.windows.net/.default"
# Never reuse a physical connection across delegated users or token lifetimes.
mssql_python.pooling(enabled=False)
QUERIES = {
    "orders": "SELECT TOP (100) id, region, product, revenue FROM dbo.Orders ORDER BY id",
    "margins": "SELECT TOP (100) id, region, margin FROM dbo.Margins ORDER BY id",
}


@dataclass(frozen=True)
class FabricSettings:
    server: str
    database: str
    workspace_id: str
    warehouse_id: str

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9-]+\.datawarehouse\.fabric\.microsoft\.com", self.server):
            raise ValueError("FABRIC_SQL_SERVER moet een Fabric Warehouse-hostnaam zijn.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.database):
            raise ValueError("FABRIC_SQL_DATABASE bevat ongeldige tekens.")
        UUID(self.workspace_id)
        UUID(self.warehouse_id)

    @classmethod
    def from_env(cls) -> "FabricSettings | None":
        values = [os.environ.get(key, "") for key in (
            "FABRIC_SQL_SERVER", "FABRIC_SQL_DATABASE", "FABRIC_WORKSPACE_ID", "FABRIC_WAREHOUSE_ID",
        )]
        return cls(*values) if any(values) else None

    @property
    def url(self) -> str:
        return f"https://app.fabric.microsoft.com/groups/{self.workspace_id}/warehouses/{self.warehouse_id}"

    @property
    def connection_string(self) -> str:
        return (
            f"Server={self.server},1433;Database={self.database};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )


@dataclass(frozen=True)
class SqlToken:
    token: str = field(repr=False)


@dataclass(frozen=True)
class DelegatedToken:
    token: str = field(repr=False)

    def get_token(self, scope: str) -> SqlToken:
        if scope != SQL_SCOPE or not self.token:
            raise ValueError("Alleen het expliciete gedelegeerde SQL-token is toegestaan.")
        return SqlToken(self.token)


class FabricWarehouse:
    def __init__(self, settings: FabricSettings, exchange: Callable[[str], Awaitable[str]]):
        self.settings = settings
        self.exchange = exchange

    def query(self, kind: str, token: str) -> dict:
        sql = QUERIES[kind]
        try:
            with closing(mssql_python.connect(
                self.settings.connection_string, autocommit=True, timeout=30,
                token_provider=DelegatedToken(token),
            )) as connection:
                with closing(connection.cursor()) as cursor:
                    cursor.execute("SELECT USER_NAME() AS source_principal")
                    principal = cursor.fetchone()[0]
                    cursor.execute(sql)
                    columns = [column[0] for column in cursor.description]
                    rows = [
                        dict(zip(columns, [
                            float(value) if isinstance(value, Decimal) else value for value in row
                        ], strict=True))
                        for row in cursor.fetchmany(100)
                    ]
        except mssql_python.Error as error:
            detail = str(error)
            numbered_denial = bool(re.search(r"\((229|230|916|18456)\)", detail))
            native_message = getattr(error, "ddbc_error", "")
            # DDBC can omit native error numbers; SQLSTATE 42000 alone also includes syntax errors.
            native_select_denial = (
                isinstance(error, mssql_python.ProgrammingError)
                and isinstance(native_message, str)
                and bool(re.match(
                    r"^(?:\[Microsoft\])?(?:\[ODBC Driver [^\]]+\])?(?:\[SQL Server\])?"
                    r"The SELECT permission(?: or external policy action '[^']+')? "
                    r"was denied on the (?:object|column)\b",
                    native_message,
                ))
            )
            denied = numbered_denial or native_select_denial
            code = "SOURCE_FORBIDDEN" if denied else "SOURCE_UNAVAILABLE"
            diagnostic = (
                "native_select_denied" if native_select_denial else
                "numbered_access_denied" if numbered_denial else "unclassified_sql_error"
            )
            logger.warning(
                "fabric=failed query=%s code=%s type=%s diagnostic=%s",
                kind, code, type(error).__name__, diagnostic,
            )
            message = (
                "Fabric SQL weigert deze toegang. Controleer item Read en SQL SELECT/DENY."
                if denied else
                "De Fabric SQL-aanroep is mislukt; de oorzaak is niet vastgesteld. "
                "Dit bewijst geen rechtenweigering of netwerkstoring."
            )
            raise ToolError(f"{code}: {message} Geen alternatieve identiteit gebruikt.") from None
        return {
            "source": "fabric-warehouse", "columns": columns, "rows": rows,
            "source_principal": principal, "authentication": "delegated-obo",
        }

    async def read(self, kind: str) -> dict:
        assertion = caller().token
        token = await self.exchange(assertion)
        return await asyncio.to_thread(self.query, kind, token)


def register_fabric(mcp: ProtectedMCP, backend: FabricWarehouse | None) -> None:
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

    @mcp.tool(annotations=annotations)
    def describe_access() -> dict[str, object]:
        """Toon geverifieerde gatewayrollen en bronconfiguratie; dit verleent geen bronrechten."""
        token = caller()
        return {
            "user": {"id": token.subject, "roles": (token.claims or {}).get("roles", [])},
            "sources": [{
                "id": "fabric", "status": "configured" if backend else "not_configured",
                "kind": "Fabric Warehouse", "dataKind": "persisted-demo",
                "url": backend.settings.url + f"?ctid={mcp.endpoint.tenant}" if backend else None,
                "enforcement": "Fabric SQL permissions and RLS",
            }],
        }

    async def read(kind: str) -> dict:
        if backend is None:
            raise ToolError("SOURCE_NOT_CONFIGURED: Fabric is niet geconfigureerd; geen fixturefallback.")
        return await backend.read(kind)

    @mcp.tool(annotations=annotations)
    async def fabric_orders() -> dict[str, object]:
        """Lees echte Fabric Orders; Fabric SQL past de rijrechten van de gebruiker toe."""
        return await read("orders")

    @mcp.tool(annotations=annotations)
    async def fabric_margins() -> dict[str, object]:
        """Probeer Margins; beide labrollen passeren de gateway, maar Fabric beslist over SELECT."""
        return await read("margins")

    @mcp.tool(annotations=annotations)
    async def fabric_finance_only() -> dict[str, object]:
        """Lees Margins met een extra Finance.Reader-controle op de gateway voor de bronaanroep."""
        return await read("margins")
