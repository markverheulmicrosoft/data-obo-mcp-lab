"""Echte HTTP/MCP en JWT-validatie; alleen JWKS-ophalen en Entra OBO zijn testdoubles."""

import asyncio
import socket
import time
import unittest
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError

from mcp_lab.auth import Endpoint, EntraVerifier
from mcp_lab.gateway import EntraOBO, create_gateway
from mcp_lab.rights_mcp import create_rights_mcp

TENANT = "11111111-1111-4111-8111-111111111111"
GATEWAY_ID = "22222222-2222-4222-8222-222222222222"
RIGHTS_ID = "33333333-3333-4333-8333-333333333333"
CLIENT_ID = "44444444-4444-4444-8444-444444444444"
NOOR = "55555555-5555-4555-8555-555555555555"
FINANCE = "66666666-6666-4666-8666-666666666666"


def local_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


@asynccontextmanager
async def serve(app, sock):
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Testserver stopte voor het starten.")
                await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()


class MCPNetworkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, endpoint, user=NOOR, roles=None, **overrides):
        now = int(time.time())
        claims = {
            "iss": endpoint.issuer, "tid": TENANT, "aud": endpoint.client_id,
            "oid": user, "azp": CLIENT_ID, "ver": "2.0",
            "scp": "access_as_user", "roles": ["North.Reader"] if roles is None else roles,
            "iat": now, "nbf": now - 5, "exp": now + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        gateway_socket, rights_socket = local_socket(), local_socket()
        self.gateway_endpoint = Endpoint(TENANT, GATEWAY_ID, f"http://127.0.0.1:{gateway_socket.getsockname()[1]}/mcp")
        self.rights_endpoint = Endpoint(TENANT, RIGHTS_ID, f"http://127.0.0.1:{rights_socket.getsockname()[1]}/mcp")
        self.gateway_verifier = EntraVerifier(self.gateway_endpoint)
        self.rights_verifier = EntraVerifier(self.rights_endpoint)
        for verifier in (self.gateway_verifier, self.rights_verifier):
            self.stack.enter_context(patch.object(
                verifier.jwks, "get_signing_key_from_jwt",
                return_value=SimpleNamespace(key=self.key.public_key()),
            ))
        self.source_roles = {NOOR: ["North.Reader"], FINANCE: ["Finance.Reader"]}
        self.exchanges = []
        self.obo_failure = False
        self.wrong_downstream_audience = False

        async def exchange(assertion):
            self.exchanges.append(assertion)
            if self.obo_failure:
                raise ToolError("OBO_FAILED: consent ontbreekt (testdouble).")
            user = await self.gateway_verifier.verify_token(assertion)
            self.assertIsNotNone(user)
            endpoint = self.gateway_endpoint if self.wrong_downstream_audience else self.rights_endpoint
            return self.token(endpoint, user.subject, self.source_roles[user.subject], azp=GATEWAY_ID)

        self.rights = create_rights_mcp(self.rights_endpoint, self.rights_verifier)
        self.rights_calls = AsyncMock(wraps=self.rights.call_tool)
        self.rights.call_tool = self.rights_calls
        gateway = create_gateway(self.gateway_endpoint, self.gateway_verifier, self.rights_endpoint, exchange)
        await self.stack.enter_async_context(serve(self.rights.http_app(), rights_socket))
        await self.stack.enter_async_context(serve(gateway.http_app(), gateway_socket))

    @asynccontextmanager
    async def client(self, token, endpoint=None):
        target = endpoint or self.gateway_endpoint
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=5) as http:
            async with Client(streamable_http_client(target.url, http_client=http), cache=None) as client:
                yield client

    async def call(self, token, tool="list_orders", endpoint=None, arguments=None):
        async with self.client(token, endpoint) as client:
            return await client.call_tool(tool, arguments or {})

    async def raw(self, token=None, endpoint=None, headers=None):
        headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx2.AsyncClient(timeout=5) as http:
            return await http.post(
                (endpoint or self.gateway_endpoint).url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )

    async def test_noor_only_sees_north_and_no_margin(self):
        result = await self.call(self.token(self.gateway_endpoint))
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["orders"], [
            {"id": "N-001", "region": "Noord", "revenue": 1200},
        ])
        self.assertNotIn("margin", result.model_dump_json())
        self.assertEqual(len(self.exchanges), 1)
        self.assertEqual(self.rights_calls.await_count, 1)

    async def test_finance_sees_all_orders_and_margins(self):
        token = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        orders = await self.call(token)
        margin = await self.call(token, "read_margin")
        self.assertEqual(len(orders.structured_content["orders"]), 2)
        self.assertEqual([row["margin"] for row in margin.structured_content["margins"]], [240, 540])
        self.assertNotIn("margin", orders.model_dump_json())

    async def test_list_tools_is_filtered_per_user(self):
        for user, roles, expected in [
            (FINANCE, ["Finance.Reader"], {"list_orders", "read_margin"}),
            (NOOR, ["North.Reader"], {"list_orders"}),
            (NOOR, [], set()),
        ]:
            async with self.client(self.token(self.gateway_endpoint, user, roles)) as client:
                self.assertEqual({tool.name for tool in (await client.list_tools()).tools}, expected)
        self.assertEqual(self.exchanges, [])

    async def test_hidden_tool_direct_call_stops_before_obo_or_source(self):
        result = await self.call(self.token(self.gateway_endpoint), "read_margin")
        self.assertTrue(result.is_error)
        self.assertIn("TOOL_FORBIDDEN", result.model_dump_json())
        self.assertEqual(self.exchanges, [])
        self.assertEqual(self.rights_calls.await_count, 0)

    async def test_gateway_allows_but_source_independently_denies(self):
        token = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        self.source_roles[FINANCE] = ["North.Reader"]
        result = await self.call(token, "read_margin")
        self.assertTrue(result.is_error)
        self.assertIn("TOOL_FORBIDDEN", result.model_dump_json())
        self.assertNotIn("540", result.model_dump_json())
        self.assertEqual(len(self.exchanges), 1)
        self.assertEqual(self.rights_calls.await_count, 1)

    async def test_source_rows_do_not_follow_gateway_roles(self):
        token = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        self.source_roles[FINANCE] = ["North.Reader"]
        result = await self.call(token)
        self.assertEqual([row["region"] for row in result.structured_content["orders"]], ["Noord"])

    async def test_source_is_also_protected_on_direct_calls(self):
        wrong_token = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        self.assertEqual((await self.raw(wrong_token, self.rights_endpoint)).status_code, 401)
        direct = self.token(self.rights_endpoint)
        result = await self.call(direct, "read_margin", self.rights_endpoint)
        self.assertTrue(result.is_error)
        self.assertEqual(self.exchanges, [])

    async def test_impersonation_arguments_are_rejected(self):
        result = await self.call(
            self.token(self.gateway_endpoint), arguments={"userId": FINANCE, "roles": ["Finance.Reader"]},
        )
        self.assertTrue(result.is_error)
        self.assertEqual(self.exchanges, [])
        self.assertEqual(self.rights_calls.await_count, 0)

    async def test_unknown_tool_is_not_forwarded(self):
        result = await self.call(self.token(self.gateway_endpoint), "call_any_url")
        self.assertTrue(result.is_error)
        self.assertEqual(self.exchanges, [])

    async def test_missing_and_invalid_tokens_never_reach_a_tool(self):
        normal = self.token(self.gateway_endpoint)
        claims = jwt.decode(normal, options={"verify_signature": False})
        bad_signature = jwt.encode(claims, self.other_key, algorithm="RS256", headers={"kid": "test-key"})
        tokens = [
            None, "not-a-token", bad_signature,
            self.token(self.rights_endpoint),
            self.token(self.gateway_endpoint, exp=int(time.time()) - 10),
            self.token(self.gateway_endpoint, iss="https://wrong.invalid/"),
            self.token(self.gateway_endpoint, tid=CLIENT_ID),
            self.token(self.gateway_endpoint, scp=""),
            self.token(self.gateway_endpoint, ver="1.0"),
            self.token(self.gateway_endpoint, nbf=int(time.time()) + 600),
            self.token(self.gateway_endpoint, azp="not-a-guid"),
            self.token(self.gateway_endpoint, oid=123),
            self.token(self.gateway_endpoint, roles="Finance.Reader"),
        ]
        for token in tokens:
            with self.subTest(token_case=tokens.index(token)):
                response = await self.raw(token)
                self.assertEqual(response.status_code, 401)
                self.assertIn("resource_metadata=", response.headers["www-authenticate"])
        self.assertEqual(self.exchanges, [])
        self.assertEqual(self.rights_calls.await_count, 0)

    async def test_app_only_token_without_scp_is_rejected(self):
        claims = jwt.decode(self.token(self.gateway_endpoint), options={"verify_signature": False})
        del claims["scp"]
        token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
        self.assertEqual((await self.raw(token)).status_code, 401)

    async def test_valid_token_with_wrong_scope_is_forbidden(self):
        response = await self.raw(self.token(self.gateway_endpoint, scp="other_scope"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.exchanges, [])

    async def test_metadata_describes_each_distinct_resource(self):
        async with httpx2.AsyncClient() as http:
            for endpoint in (self.gateway_endpoint, self.rights_endpoint):
                url = endpoint.url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
                response = await http.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["resource"], endpoint.url)
                self.assertEqual(response.json()["scopes_supported"], [endpoint.scope])
                self.assertEqual(response.json()["authorization_servers"], [endpoint.issuer])

    async def test_no_fallback_when_obo_fails(self):
        self.obo_failure = True
        result = await self.call(self.token(self.gateway_endpoint))
        self.assertTrue(result.is_error)
        self.assertIn("OBO_FAILED", result.model_dump_json())
        self.assertEqual(self.rights_calls.await_count, 0)

    async def test_bad_downstream_audience_cannot_return_data(self):
        self.wrong_downstream_audience = True
        result = await self.call(self.token(self.gateway_endpoint))
        self.assertTrue(result.is_error)
        self.assertIn("DOWNSTREAM_FAILED", result.model_dump_json())
        self.assertNotIn("N-001", result.model_dump_json())
        self.assertEqual(self.rights_calls.await_count, 0)

    async def test_repeated_and_concurrent_clients_do_not_share_results(self):
        noor = self.token(self.gateway_endpoint)
        finance = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        results = await asyncio.gather(*(self.call(token) for token in [finance, noor, finance, noor]))
        self.assertEqual([len(result.structured_content["orders"]) for result in results], [2, 1, 2, 1])
        self.assertEqual(results[1].structured_content, results[3].structured_content)

    async def test_new_source_permissions_are_not_hidden_by_an_answer_cache(self):
        token = self.token(self.gateway_endpoint, FINANCE, ["Finance.Reader"])
        self.assertFalse((await self.call(token, "read_margin")).is_error)
        self.source_roles[FINANCE] = []
        result = await self.call(token, "read_margin")
        self.assertTrue(result.is_error)
        self.assertEqual(len(self.exchanges), 2)

    async def test_untrusted_origin_is_rejected(self):
        response = await self.raw(
            self.token(self.gateway_endpoint), headers={"Origin": "https://untrusted.invalid"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.exchanges, [])


class ConfigurationAndOBOTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = Endpoint(TENANT, GATEWAY_ID, "http://127.0.0.1:8000/mcp")
        self.rights = Endpoint(TENANT, RIGHTS_ID, "http://127.0.0.1:8001/mcp")

    def test_remote_http_and_credential_urls_are_not_allowed(self):
        for url in ["http://remote.example/mcp", "https://user:password@host/mcp", "https://host/mcp?token=x"]:
            with self.assertRaises(ValueError):
                Endpoint(TENANT, GATEWAY_ID, url)

    async def test_msal_uses_incoming_assertion_and_only_downstream_scope(self):
        with patch("mcp_lab.gateway.msal.ConfidentialClientApplication") as factory:
            factory.return_value.acquire_token_on_behalf_of.return_value = {"access_token": "test-downstream-token"}
            exchange = EntraOBO(self.gateway, self.rights, "test-only-secret")
            self.assertEqual(await exchange("test-incoming-assertion"), "test-downstream-token")
            factory.return_value.acquire_token_on_behalf_of.assert_called_once_with(
                user_assertion="test-incoming-assertion", scopes=[f"api://{RIGHTS_ID}/.default"],
            )
            factory.return_value.acquire_token_for_client.assert_not_called()

    async def test_msal_consent_error_is_explicit_and_never_falls_back(self):
        with patch("mcp_lab.gateway.msal.ConfidentialClientApplication") as factory:
            factory.return_value.acquire_token_on_behalf_of.return_value = {
                "error": "interaction_required", "correlation_id": "test-correlation",
            }
            exchange = EntraOBO(self.gateway, self.rights, "test-only-secret")
            with self.assertRaisesRegex(ToolError, "OBO_FAILED"):
                await exchange("test-incoming-assertion")
            factory.return_value.acquire_token_for_client.assert_not_called()

    def test_same_audience_is_a_configuration_error(self):
        with self.assertRaises(ValueError):
            EntraOBO(self.gateway, self.gateway, "test-only-secret")


if __name__ == "__main__":
    unittest.main()
