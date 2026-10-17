"""Configure only this hosted bot's secretless user OAuth and two consumer roles."""

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import dotenv_values, set_key

PROJECT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra"))
from deploy import Azure, inspect_preview, private_write
from entra import Graph, ensure_app, ensure_grant, ensure_principal, permission, stable_id


def token_store_subject(tenant: str, unique_id: str) -> str:
    encoded = base64.urlsafe_b64encode(UUID(tenant).bytes_le).decode().rstrip("=")
    unique_id = str(UUID(unique_id))
    return f"/eid1/c/pub/t/{encoded}/a/9ExAW52n_ky4ZiS_jhpJIQ/{unique_id}"


def ensure_federation(graph, app, owner_id, tenant, unique_id):
    current = graph.request("GET", f"applications/{app['id']}", params={"$select": "appId"})
    owners = graph.items(f"applications/{app['id']}/owners", **{"$select": "id"})
    if current["appId"] != app["appId"] or {owner["id"] for owner in owners} != {owner_id}:
        raise RuntimeError("OAuth-federatie vereist het vastgelegde app-ID en exclusieve eigenaar.")
    desired = {
        "name": "bot-token-store",
        "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "subject": token_store_subject(tenant, unique_id),
        "audiences": ["api://AzureADTokenExchange"],
    }
    path = f"applications/{app['id']}/federatedIdentityCredentials"
    existing = graph.items(path)
    if existing:
        if len(existing) != 1 or any(existing[0].get(k) != v for k, v in desired.items()):
            raise RuntimeError("Onverwachte OAuth-federatie; geen stille wijziging.")
    else:
        graph.request("POST", path, body=desired)


def configure(apply: bool):
    command_env = os.environ.copy()
    command_env["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    result = subprocess.run(
        ["azd", "env", "get-values"], cwd=PROJECT, env=command_env,
        capture_output=True, text=True, check=True,
    )
    values = dict(dotenv_values(stream=io.StringIO(result.stdout)))
    lab = dict(dotenv_values(ROOT / ".env"))
    if any(values[key] != lab[key] for key in ("AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID") if key in lab):
        raise RuntimeError("Teams-project en MCP-configuratie hebben een ander doel.")
    tenant = str(UUID(values["AZURE_TENANT_ID"]))
    if tenant != lab["ENTRA_TENANT_ID"] or values["MCP_URL"] != lab["GATEWAY_URL"]:
        raise RuntimeError("Teams verwijst niet naar de bedoelde MCP/tenant.")
    subscription = str(UUID(values["AZURE_SUBSCRIPTION_ID"]))
    group = values["AZURE_RESOURCE_GROUP"]
    if group != f"rg-{lab['RESOURCE_PREFIX']}":
        raise RuntimeError("OAuth-deploy valt buiten de eigen labresourcegroep.")
    agent_name = "velora-teams-f9ba"
    bot_name = values["AGENT_VELORA_TEAMS_F9BA_BOT_NAME"]
    app_id = str(UUID(values["AGENT_VELORA_TEAMS_F9BA_INSTANCE_IDENTITY_CLIENT_ID"]))
    az = Azure(ROOT / ".deploy")
    bot = az.run("bot", "show", "--name", bot_name, "--resource-group", group, "--subscription", subscription)
    expected_endpoint = values["FOUNDRY_PROJECT_ENDPOINT"] + f"/agents/{agent_name}/endpoint/protocols/activityProtocol"
    if (
        bot["properties"]["msaAppId"] != app_id
        or bot["properties"]["msaAppTenantId"] != tenant
        or bot["properties"]["endpoint"].split("?")[0] != expected_endpoint
    ):
        raise RuntimeError("De botidentiteit of endpoint wijkt af; geen OAuth instellen.")
    if not apply:
        print("Plan: owned OAuth app + Bot Token Store federation + mcp-user connection + project consumer for the two selected lab users.")
        return
    state_path = PROJECT / ".azure" / "oauth.env"
    if state_path.is_symlink():
        raise ValueError("Geen symlink voor OAuth-state.")
    state = dict(dotenv_values(state_path)) if state_path.exists() else {}

    def save(key, value):
        if not state_path.exists():
            private_write(state_path, "")
        set_key(str(state_path), key, value, quote_mode="always")
        state[key] = value

    for key, value in {
        "TENANT_ID": tenant, "BOT_RESOURCE_ID": bot["id"], "GATEWAY_CLIENT_ID": lab["GATEWAY_CLIENT_ID"],
    }.items():
        if key in state and state[key] != value:
            raise RuntimeError("OAuth-state hoort bij een ander doel; geen credential hergebruiken.")
        save(key, value)
    unique_id = state.get("SUBJECT_ID") or str(uuid4())
    save("SUBJECT_ID", unique_id)
    graph = Graph(az.run("account", "get-access-token", "--resource-type", "ms-graph",
                         "--subscription", subscription)["accessToken"])
    try:
        operator = graph.request("GET", "me", params={"$select": "id"})
        app = ensure_app(graph, lab["RESOURCE_PREFIX"], "teams-client", {
            "web": {"redirectUris": ["https://token.botframework.com/.auth/web/redirect"]},
            "requiredResourceAccess": permission(
                lab["GATEWAY_CLIENT_ID"], stable_id(lab["RESOURCE_PREFIX"], "gateway/access_as_user"),
            ),
        }, state.get("CLIENT_ID", ""), owner_id=operator["id"])
        save("CLIENT_ID", app["appId"])
        ensure_federation(graph, app, operator["id"], tenant, unique_id)
        client = ensure_principal(graph, app, False)
        gateways = graph.items("servicePrincipals", **{"$filter": f"appId eq '{UUID(lab['GATEWAY_CLIENT_ID'])}'"})
        if len(gateways) != 1:
            raise RuntimeError("Geen unieke gateway-serviceprincipal.")
        ensure_grant(graph, client, gateways[0])
    finally:
        graph.close()
    parameters = {
        "botName": bot_name, "oauthClientId": app["appId"], "subjectId": unique_id, "tenantId": tenant,
        "gatewayClientId": lab["GATEWAY_CLIENT_ID"], "foundryAccountName": values["AZURE_AI_ACCOUNT_NAME"],
        "foundryProjectName": values["AZURE_AI_PROJECT_NAME"],
        "testUserIds": [str(UUID(lab["TEST_NORTH_USER_ID"])), str(UUID(lab["TEST_FINANCE_USER_ID"]))],
    }
    with tempfile.TemporaryDirectory(prefix="teams-oauth-", dir=ROOT / ".deploy") as folder:
        path = Path(folder) / "parameters.json"
        private_write(path, json.dumps({
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0", "parameters": {key: {"value": value} for key, value in parameters.items()},
        }))
        arguments = (
            "--resource-group", group, "--subscription", subscription,
            "--template-file", str(PROJECT / "infra" / "oauth.bicep"), "--parameters", f"@{path}",
        )
        preview = az.run("deployment", "group", "what-if", *arguments, "--result-format", "FullResourcePayloads", "--no-pretty-print")
        print("OAuth what-if:", inspect_preview(preview, subscription, group))
        deployed = az.run("deployment", "group", "create", "--name", "velora-teams-oauth", *arguments)
        if deployed["properties"]["provisioningState"] != "Succeeded":
            raise RuntimeError("OAuth-deploy is niet geslaagd.")
    print("Secretless OAuth configured; scope is only the MCP API. No Fabric data roles granted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    configure(parser.parse_args().apply)
