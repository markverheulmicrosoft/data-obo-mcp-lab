import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2

import deploy
from entra import (
    Graph, api_definition, bootstrap, ensure_app, ensure_grant,
    ensure_managed_identity_federation, ensure_role, ensure_web_client, stable_id,
    permission, canonical_permissions, SQL_APP_ID, SQL_SCOPE_ID,
)

TENANT = "11111111-1111-4111-8111-111111111111"
SUB = "22222222-2222-4222-8222-222222222222"
NORTH = "33333333-3333-4333-8333-333333333333"
FINANCE = "44444444-4444-4444-8444-444444444444"
GATEWAY = "55555555-5555-4555-8555-555555555555"
RIGHTS = "66666666-6666-4666-8666-666666666666"
PREFIX = "velora-mcp-dev-demo"


def values():
    return {
        "AZURE_SUBSCRIPTION_ID": SUB, "ENTRA_TENANT_ID": TENANT,
        "RESOURCE_PREFIX": PREFIX, "AZURE_LOCATION": "swedencentral",
        "TEST_NORTH_USER_ID": NORTH, "TEST_FINANCE_USER_ID": FINANCE,
    }


class DeploymentTests(unittest.TestCase):
    def test_apply_uses_private_parameters_and_preserves_live_apps_on_repeat(self):
        cfg = deploy.Settings.load(values())
        names = cfg.names()
        images, parameter_files, commands = [], [], []
        outputs = {
            "registryLoginServer": names["registryName"] + ".azurecr.io",
            "gatewayUrl": "https://gateway.example/mcp",
            "rightsUrl": "https://rights.internal.example/mcp",
            "gatewayActualFqdn": "gateway.example",
            "rightsActualFqdn": "rights.internal.example",
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = root / ".env"
            inputs = values()
            state_file = root / ".deploy" / "state.json"
            fake = Mock()

            def run(*args, **kwargs):
                commands.append(args)
                self.assertNotIn("test-only-secret", " ".join(args))
                if args[:2] == ("account", "show"):
                    return {"id": SUB, "tenantId": TENANT, "user": {"type": "user"}}
                if args[:2] == ("group", "exists"):
                    return bool(images)
                if args[:2] == ("group", "show"):
                    state = json.loads(state_file.read_text())
                    return {"location": cfg.location, "tags": {
                        "environment": cfg.prefix, "app-onboard-skill": "true",
                        "app-onboard-session-id": state["sessionId"],
                    }}
                if args[:2] == ("account", "get-access-token"):
                    return {"accessToken": "test-only-token"}
                if args[:2] == ("acr", "show"):
                    return outputs["registryLoginServer"]
                if args[:2] == ("acr", "build"):
                    self.assertEqual(Path(args[args.index("--file") + 1]), Path(args[-1]) / "Dockerfile")
                    return None
                if args[:2] == ("identity", "show"):
                    return {"tenantId": TENANT, "name": names["gatewayIdentityName"],
                            "principalId": "88888888-8888-4888-8888-888888888888"}
                if args[:3] == ("containerapp", "env", "show"):
                    return {"properties": {"defaultDomain": "example.swedencentral.azurecontainerapps.io"}}
                if args[:2] == ("deployment", "sub"):
                    path = Path(args[args.index("--parameters") + 1][1:])
                    parameter_files.append(path)
                    parameters = json.loads(path.read_text())["parameters"]
                    if args[2] == "what-if":
                        return {"status": "Succeeded", "changes": [{
                            "resourceId": f"/subscriptions/{SUB}/resourceGroups/{names['resourceGroupName']}",
                            "changeType": "Create",
                        }]}
                    images.append(parameters["containerImage"]["value"])
                    self.assertNotIn("gatewayClientSecret", parameters)
                    return {"properties": {"provisioningState": "Succeeded", "outputs": {
                        key: {"value": value} for key, value in outputs.items()
                    }}}
                raise AssertionError(f"Unexpected CLI operation: {args[:3]}")

            fake.run.side_effect = run
            graph = Mock()
            graph.items.return_value = [{"id": TENANT}]
            graph.request.return_value = {"id": "77777777-7777-4777-8777-777777777777", "displayName": "Example"}

            def identities(graph, prefix, north, finance, data, save, recover, *, owner_id, need_secret):
                self.assertEqual(owner_id, "77777777-7777-4777-8777-777777777777")
                self.assertFalse(need_secret)
                save({"GATEWAY_CLIENT_ID": GATEWAY, "RIGHTS_CLIENT_ID": RIGHTS,
                      "GATEWAY_CLIENT_SECRET": "test-only-secret"})
                return {"gateway": GATEWAY, "rights": RIGHTS, "gatewayObjectId": GATEWAY}

            with patch.object(deploy, "ROOT", root), patch.object(deploy, "Azure", return_value=fake), patch.object(
                deploy, "build_web"
            ), patch.object(deploy, "ensure_web_client", return_value=GATEWAY) as web_client, patch.object(
                deploy, "Graph", return_value=graph
            ), patch.object(deploy, "bootstrap", side_effect=identities), patch.object(
                deploy, "stage_build"
            ), patch.object(deploy, "seed_vault") as seed, patch.object(
                deploy, "ensure_managed_identity_federation"
            ) as federation, patch.object(
                deploy.webbrowser, "open", return_value=True
            ), patch.object(deploy, "smoke") as smoke, contextlib.redirect_stdout(io.StringIO()) as output:
                deploy.apply(cfg, inputs, env)
                deploy.apply(cfg, inputs, env)
            self.assertEqual(images.count(deploy.PLACEHOLDER), 1)
            self.assertEqual(len(images), 3)
            self.assertEqual(smoke.call_count, 2)
            self.assertEqual(seed.call_count, 0)
            self.assertEqual(federation.call_count, 2)
            self.assertEqual(web_client.call_count, 2)
            self.assertTrue(all(not path.exists() for path in parameter_files))
            self.assertNotIn("test-only-secret", state_file.read_text())
            self.assertNotIn("test-only-secret", output.getvalue())
            self.assertFalse(json.loads(state_file.read_text())["userOboVerified"])

    def test_settings_and_names(self):
        cfg = deploy.Settings.load(values())
        self.assertEqual(cfg.names()["registryName"], "crveloramcpdevdemo")
        self.assertLessEqual(len(cfg.names()["keyVaultName"]), 24)
        self.assertLessEqual(len(cfg.names()["gatewayAppName"]), 32)
        for update in [
            {"RESOURCE_PREFIX": "x;echo unsafe"},
            {"TEST_FINANCE_USER_ID": NORTH},
            {"ENTRA_TENANT_ID": ""},
            {"AZURE_LOCATION": "swedencentral & echo"},
        ]:
            with self.assertRaises(ValueError):
                deploy.Settings.load({**values(), **update})

    def test_build_context_is_a_strict_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            (root / "mcp_lab").mkdir(parents=True)
            (root / "infra").mkdir()
            (root / "mcp_lab" / "static").mkdir()
            for name in deploy.WEB_FILES:
                (root / "mcp_lab" / "static" / name).write_text("<html>Build output</html>", encoding="utf-8")
            (root / "mcp_lab" / "static" / "secret.json").write_text("DO_NOT_UPLOAD", encoding="utf-8")
            for name in deploy.SOURCE_FILES:
                (root / "mcp_lab" / name).write_text("# code", encoding="utf-8")
            (root / "mcp_lab" / "extra-secret.py").write_text("DO_NOT_UPLOAD", encoding="utf-8")
            (root / ".env").write_text("SECRET=DO_NOT_UPLOAD", encoding="utf-8")
            (root / "requirements.txt").write_text("mcp==2.3.0", encoding="utf-8")
            (root / "infra" / "Dockerfile").write_text("FROM python:3.11-slim", encoding="utf-8")
            (root / "infra" / ".dockerignore").write_text("**", encoding="utf-8")
            target = Path(folder) / "staged"
            with patch.object(deploy, "ROOT", root), patch.object(deploy, "INFRA", root / "infra"):
                deploy.stage_build(target)
            expected = {"requirements.txt", "Dockerfile", ".dockerignore"}
            expected |= {f"mcp_lab/{name}" for name in deploy.SOURCE_FILES}
            expected |= {f"mcp_lab/static/{name}" for name in deploy.WEB_FILES}
            self.assertEqual({p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}, expected)
            self.assertNotIn("DO_NOT_UPLOAD", "".join(p.read_text() for p in target.rglob("*") if p.is_file()))

    def test_secret_never_enters_arm_parameter_files(self):
        cfg = deploy.Settings.load(values())
        state = {"sessionId": TENANT, "createdAt": "2026-01-01T00:00:00Z"}
        operator = {"displayName": "Example operator", "id": NORTH}
        apps = {"gateway": GATEWAY, "rights": RIGHTS, "secret": "test-only-secret"}
        first = deploy.arm_parameters(cfg, state, operator, apps, deploy.PLACEHOLDER)
        final = deploy.arm_parameters(cfg, state, operator, apps, "example.azurecr.io/mcp-lab:1")
        self.assertNotIn("gatewayClientSecret", first["parameters"])
        self.assertNotIn("gatewayClientSecret", final["parameters"])
        self.assertNotIn("test-only-secret", json.dumps(final))
        self.assertEqual(final["parameters"]["gatewayClientId"]["value"], GATEWAY)

    def test_preview_blocks_deletes_and_other_resource_groups(self):
        scope = f"/subscriptions/{SUB}/resourceGroups/rg-example"
        valid = {"status": "Succeeded", "changes": [{"resourceId": scope, "changeType": "Create"}]}
        self.assertEqual(deploy.inspect_preview(valid, SUB, "rg-example"), {"Create": 1})
        for change in [{"resourceId": scope, "changeType": "Delete"},
                       {"resourceId": scope + "-other", "changeType": "Create"}]:
            with self.assertRaises(RuntimeError):
                deploy.inspect_preview({"status": "Succeeded", "changes": [change]}, SUB, "rg-example")
        with self.assertRaises(RuntimeError):
            deploy.inspect_preview({"status": "Failed", "changes": []}, SUB, "rg-example")

    def test_unresolved_rbac_id_is_allowed_only_inside_the_target_scope(self):
        scope = f"/subscriptions/{SUB}/resourceGroups/rg-example"
        def result(base):
            return {"status": "Succeeded", "changes": [{
                "resourceId": f"[extensionResourceId('{base}/providers/Microsoft.ContainerRegistry/registries/example', 'Microsoft.Authorization/roleAssignments', guid('pending-principal'))]",
                "changeType": "Unsupported",
            }]}
        self.assertEqual(deploy.inspect_preview(result(scope), SUB, "rg-example"), {"Unsupported": 1})
        with self.assertRaises(RuntimeError):
            deploy.inspect_preview(result(scope + "-other"), SUB, "rg-example")
        with self.assertRaises(RuntimeError):
            deploy.inspect_preview({"status": "Succeeded", "changes": [{
                "resourceId": "[concat('unknown')]", "changeType": "Unsupported",
            }]}, SUB, "rg-example")

    def test_default_plan_does_not_call_azure_or_graph(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("\n".join(f"{k}={v}" for k, v in values().items()), encoding="utf-8")
            with patch("sys.argv", ["deploy.py", "--env-file", str(env)]), patch.object(
                deploy, "apply"
            ) as apply, contextlib.redirect_stdout(io.StringIO()) as output:
                deploy.main()
            apply.assert_not_called()
            self.assertIn("Alleen plan", output.getvalue())

    def test_azure_failure_is_explicit_and_private(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(deploy, "azure_command", return_value=["az"]):
            az = deploy.Azure(Path(folder))
            with patch.object(deploy.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="", stderr="private diagnostic",
            )):
                with self.assertRaisesRegex(RuntimeError, "last-error.log"):
                    az.run("deployment", "sub", "create")
            self.assertEqual((Path(folder) / "last-error.log").read_text(), "private diagnostic")
            audit = (Path(folder) / "deploy-audit.log").read_text()
            self.assertIn("started", audit)
            self.assertIn("failed", audit)
            self.assertNotIn("private diagnostic", audit)

    def test_azure_timeout_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(deploy, "azure_command", return_value=["az"]):
            az = deploy.Azure(Path(folder))
            with patch.object(deploy.subprocess, "run", side_effect=subprocess.TimeoutExpired(["az"], 1)):
                with self.assertRaisesRegex(RuntimeError, "timeout"):
                    az.run("deployment", "sub", "create", timeout=1)
            self.assertIn("timeout", (Path(folder) / "deploy-audit.log").read_text())

    def test_placeholder_or_unprotected_site_fails_smoke(self):
        url = "https://gateway.example/mcp"
        def response(request):
            if ".well-known" in str(request.url):
                return httpx2.Response(200, json={
                    "resource": url, "scopes_supported": [f"api://{GATEWAY}/access_as_user"],
                    "authorization_servers": [f"https://login.microsoftonline.com/{TENANT}/v2.0"],
                })
            return httpx2.Response(200, text="Hello world")
        client = httpx2.Client(transport=httpx2.MockTransport(response))
        with patch.object(deploy.httpx2, "Client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "anonieme toegang"):
                deploy.smoke(url, TENANT, GATEWAY)

    def test_expected_metadata_and_challenge_pass_smoke(self):
        url = "https://gateway.example/mcp"
        def response(request):
            if ".well-known" in str(request.url):
                return httpx2.Response(200, json={
                    "resource": url, "scopes_supported": [f"api://{GATEWAY}/access_as_user"],
                    "authorization_servers": [f"https://login.microsoftonline.com/{TENANT}/v2.0"],
                })
            return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://gateway.example/.well-known/oauth-protected-resource/mcp"'})
        client = httpx2.Client(transport=httpx2.MockTransport(response))
        with patch.object(deploy.httpx2, "Client", return_value=client):
            deploy.smoke(url, TENANT, GATEWAY)

    def test_network_policy_block_is_not_retried_as_rbac_delay(self):
        requests = []
        def response(request):
            requests.append(request.method)
            return httpx2.Response(403, json={"error": {
                "code": "Forbidden", "innererror": {"code": "ForbiddenByConnection"},
            }})
        client = httpx2.Client(transport=httpx2.MockTransport(response))
        az = Mock()
        az.run.return_value = {"accessToken": "test-only-token"}
        with patch.object(deploy.httpx2, "Client", return_value=client), patch.object(deploy.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "ForbiddenByConnection"):
                deploy.seed_vault(az, deploy.Settings.load(values()), "test-only-secret")
        self.assertEqual(requests, ["GET"])


class IdentityTests(unittest.TestCase):
    def test_teams_client_uses_only_the_fixed_token_store_redirect(self):
        redirect = {"redirectUris": ["https://token.botframework.com/.auth/web/redirect"]}
        app = {
            **self.client_fixture(), "displayName": f"{PREFIX}-teams-client",
            "publicClient": {"redirectUris": []}, "web": redirect,
        }
        graph = Mock()
        graph.items.side_effect = [[app], [{"id": FINANCE}]]
        graph.request.return_value = app
        ensure_app(graph, PREFIX, "teams-client", {"web": redirect}, GATEWAY, owner_id=FINANCE)
        self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))
        with self.assertRaises(ValueError):
            ensure_app(graph, PREFIX, "teams-client", {"web": {"redirectUris": ["https://other.invalid"]}}, owner_id=FINANCE)

    def test_permission_comparison_ignores_order_but_not_extra_grants_or_duplicates(self):
        expected = permission(RIGHTS, NORTH) + permission(SQL_APP_ID, SQL_SCOPE_ID)
        self.assertEqual(canonical_permissions(expected), canonical_permissions(list(reversed(expected))))
        self.assertNotEqual(canonical_permissions(expected), canonical_permissions(expected + permission(GATEWAY, FINANCE)))
        self.assertNotEqual(canonical_permissions(expected), canonical_permissions(expected + expected[:1]))

    def test_sql_delegation_upgrade_requires_pinned_app_and_exclusive_owner(self):
        definition = api_definition(PREFIX, "gateway")
        previous = permission(RIGHTS, stable_id(PREFIX, "rights/access_as_user"))
        desired = {**definition, "requiredResourceAccess": previous + permission(SQL_APP_ID, SQL_SCOPE_ID)}
        app = {
            **self.client_fixture(), **definition, "displayName": f"{PREFIX}-gateway",
            "publicClient": {"redirectUris": []}, "requiredResourceAccess": previous,
            "identifierUris": [f"api://{GATEWAY}"],
        }
        for owners in ([{"id": FINANCE}], [{"id": FINANCE}, {"id": NORTH}]):
            graph = Mock()
            graph.items.side_effect = [[app], owners]
            graph.request.return_value = app
            if len(owners) == 1:
                ensure_app(graph, PREFIX, "gateway", desired, GATEWAY, owner_id=FINANCE, allow_sql_delegation=True)
                graph.request.assert_any_call("PATCH", f"applications/{NORTH}", body={
                    "requiredResourceAccess": desired["requiredResourceAccess"],
                })
            else:
                with self.assertRaisesRegex(RuntimeError, "eigenaarschap"):
                    ensure_app(graph, PREFIX, "gateway", desired, GATEWAY, owner_id=FINANCE, allow_sql_delegation=True)
                self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_spa_redirect_is_exact_and_public_client_has_no_credentials(self):
        definition = {
            "spa": {"redirectUris": ["https://lab.example/auth.html"]},
            "requiredResourceAccess": [],
        }
        app = {
            **self.client_fixture(),
            "displayName": f"{PREFIX}-web-client",
            "publicClient": {"redirectUris": []},
            "spa": definition["spa"],
        }
        graph = Mock()
        graph.items.side_effect = [[app], [{"id": FINANCE}]]
        graph.request.return_value = app
        self.assertEqual(
            ensure_app(graph, PREFIX, "web-client", definition, GATEWAY, owner_id=FINANCE)["appId"], GATEWAY,
        )
        for update in (
            {"spa": {"redirectUris": ["https://attacker.invalid/auth.html"]}},
            {"passwordCredentials": [{"keyId": RIGHTS}]},
            {"web": {"redirectUris": ["https://lab.example/auth.html"]}},
        ):
            graph = Mock()
            graph.items.return_value = [app]
            graph.request.return_value = {**app, **update}
            with self.assertRaises(RuntimeError):
                ensure_app(graph, PREFIX, "web-client", definition, GATEWAY, owner_id=FINANCE)
            self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_web_redirect_rejects_untrusted_forms_before_graph_calls(self):
        graph = Mock()
        for redirect in ("http://lab.example/auth.html", "https://lab.example/auth.html?x=1",
                         "https://lab.example/other", "https://user@lab.example/auth.html"):
            with self.assertRaises(ValueError):
                ensure_web_client(graph, PREFIX, GATEWAY, redirect, {}, Mock(), owner_id=FINANCE)
        graph.request.assert_not_called()

    def test_federation_binds_only_the_expected_managed_identity(self):
        graph = Mock()
        graph.request.return_value = {"appId": GATEWAY}
        graph.items.side_effect = [[{"id": FINANCE}], []]
        ensure_managed_identity_federation(graph, NORTH, GATEWAY, FINANCE, TENANT, RIGHTS)
        graph.request.assert_any_call("POST", f"applications/{NORTH}/federatedIdentityCredentials", body={
            "name": "gateway-managed-identity",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "subject": RIGHTS,
            "audiences": ["api://AzureADTokenExchange"],
        })

    def test_federation_does_not_overwrite_another_trust(self):
        graph = Mock()
        graph.request.return_value = {"appId": GATEWAY}
        graph.items.side_effect = [[{"id": FINANCE}], [{
            "name": "gateway-managed-identity", "issuer": "https://other.invalid",
            "subject": NORTH, "audiences": ["other"],
        }]]
        with self.assertRaisesRegex(RuntimeError, "federated credential wijkt af"):
            ensure_managed_identity_federation(graph, NORTH, GATEWAY, FINANCE, TENANT, RIGHTS)
        self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def client_fixture(self):
        return {
            "id": NORTH, "appId": GATEWAY, "displayName": f"{PREFIX}-client",
            "tags": [f"mcp-lab:{PREFIX}"], "signInAudience": "AzureADMyOrg",
            "publicClient": {"redirectUris": ["http://localhost"]},
            "web": {"redirectUris": []}, "spa": {"redirectUris": []},
            "passwordCredentials": [], "keyCredentials": [], "requiredResourceAccess": [],
        }

    def test_predictable_name_and_marker_are_not_proof_of_ownership(self):
        graph = Mock()
        graph.items.return_value = [self.client_fixture()]
        with self.assertRaisesRegex(RuntimeError, "vastgelegd app-ID"):
            ensure_app(graph, PREFIX, "client", {}, owner_id=FINANCE)
        graph.request.assert_not_called()

    def test_pinned_app_requires_exclusively_the_verified_deployer_as_owner(self):
        for owners in ([], [{"id": NORTH}], [{"id": FINANCE}, {"id": NORTH}]):
            graph = Mock()
            app = self.client_fixture()
            graph.items.side_effect = [[app], owners]
            graph.request.return_value = app
            with self.assertRaisesRegex(RuntimeError, "eigenaarschap"):
                ensure_app(graph, PREFIX, "client", {}, GATEWAY, owner_id=FINANCE)
            self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_extra_redirects_and_retained_client_credentials_are_rejected(self):
        for field, value in [
            ("publicClient", {"redirectUris": ["http://localhost", "https://other.invalid/callback"]}),
            ("web", {"redirectUris": ["https://other.invalid/callback"]}),
            ("spa", {"redirectUris": ["https://other.invalid/callback"]}),
            ("passwordCredentials", [{"keyId": RIGHTS}]),
            ("keyCredentials", [{"keyId": RIGHTS}]),
        ]:
            graph = Mock()
            app = {**self.client_fixture(), field: value}
            graph.items.side_effect = [[app], [{"id": FINANCE}]]
            graph.request.return_value = app
            with self.assertRaises(RuntimeError):
                ensure_app(graph, PREFIX, "client", {}, GATEWAY, owner_id=FINANCE)
            self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_pinned_owned_client_is_reused_without_mutation(self):
        graph = Mock()
        app = self.client_fixture()
        graph.items.side_effect = [[app], [{"id": FINANCE}]]
        graph.request.return_value = app
        self.assertEqual(ensure_app(graph, PREFIX, "client", {}, GATEWAY, owner_id=FINANCE)["appId"], GATEWAY)
        self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_new_application_explicitly_gets_the_deployer_as_owner(self):
        graph = Mock()
        app = self.client_fixture()
        graph.items.side_effect = [[], []]
        graph.request.side_effect = [app, {}]
        ensure_app(graph, PREFIX, "client", {"publicClient": {"redirectUris": ["http://localhost"]}}, owner_id=FINANCE)
        graph.request.assert_any_call("POST", f"applications/{NORTH}/owners/$ref", body={
            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{FINANCE}",
        })

    def test_user_roles_and_v2_scope_are_explicit(self):
        definition = api_definition(PREFIX, "gateway")
        self.assertEqual(definition["api"]["requestedAccessTokenVersion"], 2)
        self.assertEqual(definition["api"]["oauth2PermissionScopes"][0]["value"], "access_as_user")
        self.assertTrue(all(r["allowedMemberTypes"] == ["User"] for r in definition["appRoles"]))
        self.assertNotEqual(stable_id(PREFIX, "gateway/access_as_user"), stable_id(PREFIX, "rights/access_as_user"))

    def test_foreign_existing_application_is_never_adopted(self):
        graph = Mock()
        graph.items.return_value = [{"id": NORTH, "appId": GATEWAY, "displayName": f"{PREFIX}-gateway", "tags": []}]
        with self.assertRaisesRegex(RuntimeError, "vastgelegd app-ID"):
            ensure_app(graph, PREFIX, "gateway", api_definition(PREFIX, "gateway"), owner_id=FINANCE)
        graph.request.assert_not_called()

    def test_existing_different_role_is_not_overwritten_or_combined(self):
        graph = Mock()
        graph.items.return_value = [{"principalId": NORTH, "appRoleId": "old-role"}]
        with self.assertRaisesRegex(RuntimeError, "gebruikersrol wijkt af"):
            ensure_role(graph, {"id": RIGHTS}, NORTH, "new-role")
        graph.request.assert_not_called()

    def test_role_assignment_uses_resource_object_id(self):
        graph = Mock()
        graph.items.return_value = []
        ensure_role(graph, {"id": RIGHTS, "appId": GATEWAY}, NORTH, FINANCE)
        graph.request.assert_called_once_with("POST", f"servicePrincipals/{RIGHTS}/appRoleAssignedTo", body={
            "principalId": NORTH, "resourceId": RIGHTS, "appRoleId": FINANCE,
        })

    def test_existing_role_is_idempotent(self):
        graph = Mock()
        graph.items.return_value = [{"principalId": NORTH, "appRoleId": FINANCE}]
        ensure_role(graph, {"id": RIGHTS}, NORTH, FINANCE)
        graph.request.assert_not_called()

    def test_consent_is_only_the_custom_delegated_scope(self):
        graph = Mock()
        graph.items.return_value = []
        ensure_grant(graph, {"id": GATEWAY}, {"id": RIGHTS})
        graph.request.assert_called_once_with("POST", "oauth2PermissionGrants", body={
            "clientId": GATEWAY, "resourceId": RIGHTS, "consentType": "AllPrincipals", "scope": "access_as_user",
        })

    def test_unexpected_existing_consent_is_not_silently_changed(self):
        graph = Mock()
        graph.items.return_value = [{"consentType": "AllPrincipals", "scope": "access_as_user other_scope"}]
        with self.assertRaisesRegex(RuntimeError, "consent wijkt af"):
            ensure_grant(graph, {"id": GATEWAY}, {"id": RIGHTS})
        graph.request.assert_not_called()

    def test_orphan_secret_fails_before_any_directory_call(self):
        graph = Mock()
        with self.assertRaisesRegex(ValueError, "GATEWAY_CLIENT_ID"):
            bootstrap(
                graph, PREFIX, NORTH, FINANCE, {"GATEWAY_CLIENT_SECRET": "test-only"}, Mock(), Mock(),
                owner_id=GATEWAY,
            )
        graph.request.assert_not_called()

    def test_graph_pagination_cannot_exfiltrate_bearer_to_another_host(self):
        graph = Graph("test-only-token")
        graph.http.close()
        calls = []
        def response(request):
            calls.append(str(request.url))
            return httpx2.Response(200, json={"value": [], "@odata.nextLink": "https://other.invalid/steal"})
        graph.http = httpx2.Client(transport=httpx2.MockTransport(response))
        try:
            with self.assertRaisesRegex(RuntimeError, "buiten Microsoft Graph"):
                graph.items("applications")
            self.assertEqual(calls, ["https://graph.microsoft.com/v1.0/applications"])
        finally:
            graph.close()


if __name__ == "__main__":
    unittest.main()
