import asyncio
import unittest
from contextlib import AsyncExitStack
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx2
import mssql_python
from mcp.server.mcpserver.exceptions import ToolError

from mcp_lab.auth import Endpoint, EntraVerifier, allowed
from mcp_lab.fabric import DelegatedToken, FabricSettings, FabricWarehouse, SQL_SCOPE
from mcp_lab.gateway import create_gateway
import test_mcp_lab as network
from test_mcp_lab import (
    CLIENT_ID, FINANCE, GATEWAY_ID, NOOR, RIGHTS_ID, TENANT, local_socket, serve,
)


class FabricDriverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = FabricSettings(
            "test.datawarehouse.fabric.microsoft.com", "velora_rights", TENANT, RIGHTS_ID,
        )

    def test_settings_cannot_inject_a_server_or_connection_option(self):
        for server, database in (
            ("evil.invalid", "good"), ("test.datawarehouse.fabric.microsoft.com.evil.invalid", "good"),
            (self.settings.server, "good;TrustServerCertificate=yes"), (self.settings.server, ""),
        ):
            with self.assertRaises(ValueError):
                FabricSettings(server, database, TENANT, RIGHTS_ID)
        self.assertIn("Encrypt=yes;TrustServerCertificate=no", self.settings.connection_string)
        self.assertNotIn("Authentication=", self.settings.connection_string)

    def test_token_provider_accepts_only_sql_scope_without_exposing_token(self):
        provider = DelegatedToken("private-token")
        self.assertEqual(provider.get_token(SQL_SCOPE).token, "private-token")
        self.assertNotIn("private-token", repr(provider))
        self.assertNotIn("private-token", repr(provider.get_token(SQL_SCOPE)))
        with self.assertRaises(ValueError):
            provider.get_token("https://graph.microsoft.com/.default")

    def test_fixed_query_and_fresh_connection_preserve_source_result(self):
        backend = FabricWarehouse(self.settings, AsyncMock())
        with patch("mcp_lab.fabric.mssql_python.connect") as connect:
            connection = connect.return_value
            cursor = connection.cursor.return_value
            cursor.fetchone.return_value = ("source-user",)
            cursor.description = [("id",), ("region",), ("product",), ("revenue",)]
            cursor.fetchmany.return_value = [("N-001", "Noord", "Bike", Decimal("1200"))]
            for token in ("north-token", "finance-token"):
                result = backend.query("orders", token)
                self.assertEqual(result["source_principal"], "source-user")
                self.assertEqual(result["rows"][0]["revenue"], 1200)
                self.assertEqual(connect.call_args.kwargs["token_provider"].token, token)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(connection.close.call_count, 2)
            self.assertTrue(all("WHERE" not in call.args[0] for call in cursor.execute.call_args_list))

    def test_source_permission_denial_is_not_confused_with_connection_failure(self):
        backend = FabricWarehouse(self.settings, AsyncMock())
        for detail, expected in (
            ("SELECT permission denied (229)", "SOURCE_FORBIDDEN"),
            ("Login failed for token-identified principal (18456)", "SOURCE_FORBIDDEN"),
            ("Connection timeout HYT00", "SOURCE_UNAVAILABLE"),
        ):
            error = mssql_python.OperationalError("SQL request failed", detail)
            with patch("mcp_lab.fabric.mssql_python.connect", side_effect=error):
                with self.assertRaisesRegex(ToolError, expected):
                    backend.query("orders", "user-token")

    def test_gateway_and_source_gates_are_distinct(self):
        north = SimpleNamespace(claims={"roles": ["North.Reader"]})
        empty = SimpleNamespace(claims={"roles": []})
        self.assertTrue(allowed("fabric_margins", north))
        self.assertFalse(allowed("fabric_finance_only", north))
        self.assertFalse(allowed("fabric_orders", empty))
        self.assertTrue(allowed("describe_access", empty))


class LiveWebNetworkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        network.MCPNetworkTests.setUpClass()
        cls.key = network.MCPNetworkTests.key
        cls.token = network.MCPNetworkTests.token
        cls.client = network.MCPNetworkTests.client
        cls.call = network.MCPNetworkTests.call

    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        sock = local_socket()
        self.gateway_endpoint = Endpoint(TENANT, GATEWAY_ID, f"http://127.0.0.1:{sock.getsockname()[1]}/mcp")
        rights = Endpoint(TENANT, RIGHTS_ID, "http://127.0.0.1:8001/mcp")
        verifier = EntraVerifier(self.gateway_endpoint)
        self.stack.enter_context(patch.object(
            verifier.jwks, "get_signing_key_from_jwt", return_value=SimpleNamespace(key=self.key.public_key()),
        ))
        self.sql_exchange = AsyncMock(return_value="per-user-sql-token")
        self.backend = FabricWarehouse(
            FabricSettings("test.datawarehouse.fabric.microsoft.com", "velora_rights", TENANT, RIGHTS_ID),
            self.sql_exchange,
        )
        self.query = self.stack.enter_context(patch.object(self.backend, "query", return_value={
            "source": "fabric-warehouse", "columns": ["id"], "rows": [{"id": "source-row"}],
            "source_principal": "actual-source-user", "authentication": "delegated-obo",
        }))
        gateway = create_gateway(
            self.gateway_endpoint, verifier, rights, AsyncMock(),
            web_client_id=CLIENT_ID, fabric=self.backend,
        )
        await self.stack.enter_async_context(serve(gateway.http_app(), sock))

    async def test_anonymous_web_exposes_only_public_oauth_config(self):
        origin = self.gateway_endpoint.url.removesuffix("/mcp")
        async with httpx2.AsyncClient() as http:
            config = await http.get(origin + "/lab-config.json")
            self.assertEqual(config.json(), {
                "tenantId": TENANT, "clientId": CLIENT_ID, "gatewayUrl": self.gateway_endpoint.url,
                "scope": self.gateway_endpoint.scope,
            })
            self.assertEqual(config.headers["cache-control"], "no-store")
            page = await http.get(origin + "/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("'sha256-", page.headers["content-security-policy"])
            self.assertNotIn("'unsafe-eval'", page.headers["content-security-policy"])
            self.assertNotIn(self.backend.settings.url, page.text)
            self.assertEqual((await http.get(origin + "/.env")).status_code, 404)
            self.assertEqual((await http.get(self.gateway_endpoint.url)).status_code, 401)

    async def test_signed_roles_not_model_arguments_control_calls(self):
        token = self.token(self.gateway_endpoint)
        result = await self.call(token, "describe_access")
        self.assertEqual(result.structured_content["user"], {"id": NOOR, "roles": ["North.Reader"]})
        result = await self.call(token, "fabric_finance_only")
        self.assertTrue(result.is_error)
        self.assertIn("TOOL_FORBIDDEN", result.model_dump_json())
        self.sql_exchange.assert_not_awaited()
        result = await self.call(token, "fabric_orders", arguments={"user": FINANCE})
        self.assertTrue(result.is_error)
        self.sql_exchange.assert_not_awaited()

    async def test_real_source_path_uses_original_user_assertion_and_actual_result(self):
        token = self.token(self.gateway_endpoint)
        response = await self.call(token, "fabric_orders")
        self.assertFalse(response.is_error)
        self.assertEqual(response.structured_content["rows"], [{"id": "source-row"}])
        self.sql_exchange.assert_awaited_once_with(token)
        self.query.assert_called_once_with("orders", "per-user-sql-token")

    async def test_north_source_probe_reaches_source_but_denial_is_preserved(self):
        self.query.side_effect = ToolError("SOURCE_FORBIDDEN: denied by source")
        result = await self.call(self.token(self.gateway_endpoint), "fabric_margins")
        self.assertTrue(result.is_error)
        self.assertIn("SOURCE_FORBIDDEN", result.model_dump_json())
        self.assertNotIn("source-row", result.model_dump_json())
        self.sql_exchange.assert_awaited_once()

    async def test_obo_failure_never_opens_sql_connection(self):
        self.sql_exchange.side_effect = ToolError("OBO_FAILED")
        result = await self.call(self.token(self.gateway_endpoint), "fabric_orders")
        self.assertTrue(result.is_error)
        self.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
