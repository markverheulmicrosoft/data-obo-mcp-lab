"""Kleine lokale deployroute. Zonder --apply worden geen cloudresources gewijzigd."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from urllib.parse import quote

import httpx2
from dotenv import dotenv_values, set_key

from entra import Graph, bootstrap, ensure_managed_identity_federation, ensure_web_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
INFRA = ROOT / "infra"
PLACEHOLDER = "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
SOURCE_FILES = ("__init__.py", "auth.py", "gateway.py", "rights_mcp.py", "client.py", "web.py", "fabric.py")
WEB_FILES = ("index.html", "auth.html")


def build_web() -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js ontbreekt; nodig voor de lokale webbuild.")
    result = subprocess.run([node, str(ROOT / "web" / "build.mjs")], cwd=ROOT, timeout=120)
    if result.returncode:
        raise RuntimeError("Webbuild mislukt. Installeer de vastgelegde dependencies met npm ci.")


def private_write(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError("Schrijven via een symlink is niet toegestaan.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


@dataclass(frozen=True)
class Settings:
    subscription: str
    tenant: str
    prefix: str
    location: str
    north_user: str
    finance_user: str

    @classmethod
    def load(cls, values: dict) -> "Settings":
        def required(name):
            value = values.get(name, "")
            if not value:
                raise ValueError(f"{name} ontbreekt; zie infra/.env.example.")
            return value
        prefix = required("RESOURCE_PREFIX")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,18}[a-z0-9]", prefix):
            raise ValueError("RESOURCE_PREFIX: 4-20 kleine letters/cijfers/streepjes.")
        location = required("AZURE_LOCATION")
        if not re.fullmatch(r"[a-z0-9]+", location):
            raise ValueError("Gebruik de ARM-regionaam, bijvoorbeeld swedencentral.")
        result = cls(
            str(UUID(required("AZURE_SUBSCRIPTION_ID"))), str(UUID(required("ENTRA_TENANT_ID"))),
            prefix, location, str(UUID(required("TEST_NORTH_USER_ID"))), str(UUID(required("TEST_FINANCE_USER_ID"))),
        )
        if result.north_user == result.finance_user:
            raise ValueError("De proef vereist twee verschillende testgebruikers.")
        return result

    def names(self) -> dict:
        p = self.prefix
        return {
            "resourceGroupName": f"rg-{p}", "containerEnvironmentName": f"cae-{p}",
            "gatewayAppName": f"ca-{p}-gateway", "rightsAppName": f"ca-{p}-rights",
            "registryName": ("cr" + p).replace("-", ""), "keyVaultName": f"kv-{p}",
            "logWorkspaceName": f"log-{p}", "appInsightsName": f"appi-{p}",
            "gatewayIdentityName": f"mi-{p}-gateway", "rightsIdentityName": f"mi-{p}-rights",
        }


def azure_command() -> list[str]:
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI ontbreekt.")
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        python = Path(executable).parent.parent / "python.exe"
        if not python.is_file():
            raise RuntimeError("Azure CLI Python niet gevonden naast de Windows-wrapper.")
        return [str(python), "-I", "-m", "azure.cli"]
    return [executable]


class Azure:
    def __init__(self, state_dir: Path):
        self.command = azure_command()
        self.state_dir = state_dir

    def run(self, *args: str, json_output=True, timeout=1800):
        operation = " ".join(args[:3])
        self.audit(operation, "started")
        try:
            result = subprocess.run(
                [*self.command, *args, "--only-show-errors", "--output", "json" if json_output else "none"],
                capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.audit(operation, "timeout")
            raise RuntimeError(f"az {operation}: timeout; controleer de Azure-operatie voor een retry.") from None
        self.audit(operation, "succeeded" if result.returncode == 0 else "failed")
        if result.returncode:
            private_write(self.state_dir / "last-error.log", result.stderr)
            raise RuntimeError(f"az {operation} mislukt; details staan lokaal in .deploy/last-error.log.")
        return json.loads(result.stdout) if json_output else None

    def audit(self, operation: str, result: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "deploy-audit.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now(timezone.utc).isoformat()} | {operation} | {result}\n")


def stage_build(destination: Path) -> None:
    (destination / "mcp_lab").mkdir(parents=True)
    for name in SOURCE_FILES:
        source = ROOT / "mcp_lab" / name
        if source.is_symlink():
            raise ValueError("Geen symlinks in het buildpakket.")
        shutil.copyfile(source, destination / "mcp_lab" / name)
    (destination / "mcp_lab" / "static").mkdir()
    for name in WEB_FILES:
        source = ROOT / "mcp_lab" / "static" / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Webbuild ontbreekt of is een symlink; voer eerst npm run build:web uit.")
        shutil.copyfile(source, destination / "mcp_lab" / "static" / name)
    for source, name in [(ROOT / "requirements.txt", "requirements.txt"),
                         (INFRA / "Dockerfile", "Dockerfile"), (INFRA / ".dockerignore", ".dockerignore")]:
        if source.is_symlink():
            raise ValueError("Geen symlinks in het buildpakket.")
        shutil.copyfile(source, destination / name)


def arm_parameters(cfg: Settings, state: dict, operator: dict, apps: dict, image: str) -> dict:
    values = {
        **cfg.names(), "environmentName": cfg.prefix, "location": cfg.location,
        "sessionId": state["sessionId"], "createdAt": state["createdAt"],
        "deployedBy": operator["displayName"], "deployerObjectId": operator["id"],
        "tenantId": cfg.tenant, "gatewayClientId": apps["gateway"], "rightsClientId": apps["rights"],
        "containerImage": image,
        "webClientId": apps.get("web", ""),
        "fabricServer": apps.get("fabricServer", ""),
        "fabricDatabase": apps.get("fabricDatabase", ""),
        "fabricWorkspaceId": apps.get("fabricWorkspaceId", ""),
        "fabricWarehouseId": apps.get("fabricWarehouseId", ""),
    }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {key: {"value": value} for key, value in values.items()},
    }


def inspect_preview(preview: dict, subscription: str, resource_group: str) -> dict:
    if preview.get("status") != "Succeeded":
        raise RuntimeError("What-if is niet geslaagd; geen deployment uitvoeren.")
    scope = f"/subscriptions/{subscription}/resourceGroups/{resource_group}".casefold()
    changes = preview["changes"]
    for change in changes:
        resource = change["resourceId"].casefold()
        if change["changeType"] == "Unsupported":
            # Nieuwe managed identities hebben nog geen principalId; de RBAC-naam blijft dan een ARM-expressie.
            assignment = re.match(
                r"^\[extensionresourceid\('([^']+)',\s*'microsoft\.authorization/roleassignments',\s*guid\(",
                resource,
            )
            if assignment is not None:
                resource = assignment.group(1)
        if resource != scope and not resource.startswith(scope + "/"):
            raise RuntimeError("What-if bevat een resource buiten de bedoelde resourcegroep.")
        if change["changeType"] == "Delete":
            raise RuntimeError("What-if bevat een Delete; eerst expliciet beoordelen.")
    return dict(Counter(change["changeType"] for change in changes))


def seed_vault(az: Azure, cfg: Settings, secret: str) -> None:
    def rbac_pending(response):
        if response.status_code != 403:
            return False
        error = response.json().get("error", {})
        code = (error.get("innererror") or {}).get("code", error.get("code", "Forbidden"))
        if code != "ForbiddenByRbac":
            raise RuntimeError(f"Key Vault-toegang geblokkeerd ({code}); netwerk/policy niet automatisch wijzigen.")
        return True

    token = az.run("account", "get-access-token", "--subscription", cfg.subscription,
                   "--resource", "https://vault.azure.net")["accessToken"]
    url = f"https://{cfg.names()['keyVaultName']}.vault.azure.net/secrets/gateway-client-secret"
    with httpx2.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=False) as http:
        for delay in (0, 10, 20):
            time.sleep(delay)
            result = http.get(url, params={"api-version": "7.4"})
            if rbac_pending(result):
                continue
            if result.status_code == 200:
                if result.json()["value"] != secret:
                    raise RuntimeError("Key Vault en .env hebben verschillende secrets; geen stille rotatie.")
                return
            if result.status_code != 404:
                raise RuntimeError(f"Key Vault secretcontrole mislukt: HTTP {result.status_code}.")
            created = http.put(url, params={"api-version": "7.4"}, json={"value": secret})
            if rbac_pending(created):
                continue
            if created.status_code not in {200, 201}:
                raise RuntimeError(f"Key Vault secretopslag mislukt: HTTP {created.status_code}.")
            return
    raise RuntimeError("Key Vault-RBAC is nog niet beschikbaar; wacht en herhaal zonder credentials te regenereren.")


def smoke(gateway_url: str, tenant: str, client_id: str) -> None:
    origin = gateway_url.removesuffix("/mcp")
    with httpx2.Client(timeout=30, follow_redirects=False) as http:
        metadata = http.get(origin + "/.well-known/oauth-protected-resource/mcp")
        metadata.raise_for_status()
        body = metadata.json()
        if body.get("resource") != gateway_url or body.get("scopes_supported") != [f"api://{client_id}/access_as_user"]:
            raise RuntimeError("Live discoverymetadata komt niet overeen met de bedoelde gateway.")
        if body.get("authorization_servers") != [f"https://login.microsoftonline.com/{tenant}/v2.0"]:
            raise RuntimeError("Live gateway verwijst naar een andere tenant.")
        denied = http.get(gateway_url)
        if denied.status_code != 401 or "resource_metadata=" not in denied.headers.get("www-authenticate", ""):
            raise RuntimeError("De live gateway weigert anonieme toegang niet zoals verwacht.")


def apply(cfg: Settings, values: dict, env_file: Path) -> None:
    build_web()
    state_dir = ROOT / ".deploy"
    if state_dir.is_symlink():
        raise ValueError(".deploy mag geen symlink zijn.")
    az = Azure(state_dir)
    account = az.run("account", "show", "--subscription", cfg.subscription)
    if account["tenantId"] != cfg.tenant or account["id"] != cfg.subscription or account["user"]["type"] != "user":
        raise RuntimeError("De lokale Azure-aanmelding past niet bij de gekozen tenant/subscription/gebruiker.")
    names = cfg.names()
    exists = az.run("group", "exists", "--name", names["resourceGroupName"], "--subscription", cfg.subscription)
    group = None
    if exists:
        group = az.run("group", "show", "--name", names["resourceGroupName"], "--subscription", cfg.subscription)
        tags = group.get("tags") or {}
        if (tags.get("environment") != cfg.prefix
                or tags.get("app-onboard-skill") != "true"
                or group["location"] != cfg.location):
            raise RuntimeError("Bestaande resourcegroep heeft een andere eigenaar/configuratie; niet overschrijven.")

    state_file = state_dir / "state.json"
    if state_file.is_symlink():
        raise ValueError("Deploymentstate mag geen symlink zijn.")
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {
        "tenant": cfg.tenant, "subscription": cfg.subscription, "prefix": cfg.prefix, "location": cfg.location,
        "createdAt": datetime.now(timezone.utc).isoformat(), "sessionId": values.get("DEPLOY_SESSION_ID") or str(uuid4()),
    }
    if any(state[key] != getattr(cfg, key) for key in ("tenant", "subscription", "prefix", "location")):
        raise RuntimeError(".deploy/state.json hoort bij een ander doel; geen stille wijziging of migratie.")
    if group and group.get("tags", {}).get("app-onboard-session-id") != state["sessionId"]:
        raise RuntimeError("Resourcegroep hoort bij een andere sessie; bevestig herstel via DEPLOY_SESSION_ID.")
    private_write(state_file, json.dumps(state, indent=2))

    def save(updates):
        if not env_file.exists():
            private_write(env_file, "")
        for key, value in updates.items():
            set_key(str(env_file), key, value, quote_mode="always")
            values[key] = value

    def recover_secret():
        return az.run("keyvault", "secret", "show", "--vault-name", names["keyVaultName"],
                      "--name", "gateway-client-secret", "--query", "value", "--subscription", cfg.subscription)

    print("1/5 Entra-apps, delegated consent en twee onafhankelijke roltoewijzingen.")
    token = az.run("account", "get-access-token", "--subscription", cfg.subscription,
                   "--resource-type", "ms-graph")["accessToken"]
    graph = Graph(token)
    try:
        organizations = graph.items("organization", **{"$select": "id"})
        if [item["id"] for item in organizations] != [cfg.tenant]:
            raise RuntimeError("De Graph-aanmelding hoort niet bij de bedoelde tenant.")
        operator = graph.request("GET", "me", params={"$select": "id,displayName"})
        if operator["id"] in {cfg.north_user, cfg.finance_user}:
            raise RuntimeError("Gebruik voor deployment de beheerlogin, niet een testaccount.")
        apps = bootstrap(
            graph, cfg.prefix, cfg.north_user, cfg.finance_user, values, save, recover_secret,
            owner_id=operator["id"], need_secret=False,
        )
    finally:
        graph.close()

    def deploy(image, stage):
        params = arm_parameters(cfg, state, operator, apps, image)
        with tempfile.TemporaryDirectory(prefix="params-", dir=state_dir) as folder:
            path = Path(folder) / "parameters.json"
            private_write(path, json.dumps(params))
            name = f"mcp-{stage}-{int(time.time())}"
            preview = az.run(
                "deployment", "sub", "what-if", "--name", name, "--location", cfg.location,
                "--subscription", cfg.subscription, "--template-file", str(INFRA / "main.bicep"),
                "--parameters", f"@{path}", "--result-format", "FullResourcePayloads", "--no-pretty-print",
            )
            print("What-if:", inspect_preview(preview, cfg.subscription, names["resourceGroupName"]))
            resource_id = f"/subscriptions/{cfg.subscription}/providers/Microsoft.Resources/deployments/{name}"
            portal = "https://portal.azure.com/#view/Microsoft_Azure_Resources/DeploymentDetails.MenuView/~/overview/id/" + quote(resource_id, safe="")
            print(portal, flush=True)
            if not webbrowser.open(portal):
                print("Portal kon niet automatisch openen; gebruik de getoonde link.")
            state.setdefault("deploymentNames", []).append(name)
            private_write(state_file, json.dumps(state, indent=2))
            result = az.run("deployment", "sub", "create", "--name", name, "--location", cfg.location,
                            "--subscription", cfg.subscription, "--template-file", str(INFRA / "main.bicep"),
                            "--parameters", f"@{path}")
            if result["properties"].get("provisioningState") != "Succeeded":
                raise RuntimeError("Azure rapporteert geen geslaagde deployment.")
            return {key: item["value"] for key, item in result["properties"]["outputs"].items()}

    if state.get("builtImage") and exists:
        print("2/5 Bestaande apps blijven draaien; geen terugval naar een placeholder.")
        outputs = {"registryLoginServer": az.run(
            "acr", "show", "--name", names["registryName"],
            "--subscription", cfg.subscription, "--query", "loginServer",
        )}
    else:
        print("2/5 Azure-infrastructuur en RBAC; tijdelijke apps blijven intern.")
        outputs = deploy(PLACEHOLDER, "bootstrap")
    environment = az.run(
        "containerapp", "env", "show", "--name", names["containerEnvironmentName"],
        "--resource-group", names["resourceGroupName"], "--subscription", cfg.subscription,
    )
    domain = environment["properties"]["defaultDomain"]
    if not re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", domain):
        raise RuntimeError("Container Apps retourneert een onverwacht domein.")
    redirect_uri = f"https://{names['gatewayAppName']}.{domain}/auth.html"
    identity = az.run(
        "identity", "show", "--name", names["gatewayIdentityName"],
        "--resource-group", names["resourceGroupName"], "--subscription", cfg.subscription,
    )
    if identity["tenantId"] != cfg.tenant or identity["name"] != names["gatewayIdentityName"]:
        raise RuntimeError("De gateway-managed identity hoort niet bij het bedoelde doel.")
    token = az.run("account", "get-access-token", "--subscription", cfg.subscription,
                   "--resource-type", "ms-graph")["accessToken"]
    graph = Graph(token)
    try:
        ensure_managed_identity_federation(
            graph, apps["gatewayObjectId"], apps["gateway"], operator["id"], cfg.tenant, identity["principalId"],
        )
        apps["web"] = ensure_web_client(
            graph, cfg.prefix, apps["gateway"], redirect_uri, values, save, owner_id=operator["id"],
        )
    finally:
        graph.close()
    for parameter, key in (
        ("fabricServer", "FABRIC_SQL_SERVER"), ("fabricDatabase", "FABRIC_SQL_DATABASE"),
        ("fabricWorkspaceId", "FABRIC_WORKSPACE_ID"), ("fabricWarehouseId", "FABRIC_WAREHOUSE_ID"),
    ):
        apps[parameter] = values.get(key, "")
    if any(apps[key] for key in ("fabricServer", "fabricDatabase", "fabricWorkspaceId", "fabricWarehouseId")):
        from mcp_lab.fabric import FabricSettings
        FabricSettings(apps["fabricServer"], apps["fabricDatabase"], apps["fabricWorkspaceId"], apps["fabricWarehouseId"])
    print("3/5 Alleen Python-broncode bouwen in ACR; geen .env of credentials in het pakket.")
    tag = f"mcp-lab:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    with tempfile.TemporaryDirectory(prefix="build-", dir=state_dir) as folder:
        stage_build(Path(folder))
        az.run("acr", "build", "--registry", names["registryName"], "--image", tag,
               "--file", str(Path(folder) / "Dockerfile"), "--no-logs", "--subscription", cfg.subscription,
               folder, json_output=False)
    image = f"{outputs['registryLoginServer']}/{tag}"
    state["builtImage"] = image
    private_write(state_file, json.dumps(state, indent=2))
    print("4/5 Echt image en secretloze gateway-clientauthenticatie activeren.")
    outputs = deploy(image, "apps")
    expected_gateway = "https://" + outputs["gatewayActualFqdn"] + "/mcp"
    expected_rights = "https://" + outputs["rightsActualFqdn"] + "/mcp"
    if outputs["gatewayUrl"] != expected_gateway or outputs["rightsUrl"] != expected_rights:
        raise RuntimeError("De berekende MCP-URL's verschillen van Azure's werkelijke FQDN's.")
    save({"GATEWAY_URL": outputs["gatewayUrl"], "RIGHTS_URL": outputs["rightsUrl"]})
    state.update({"image": image, "gatewayUrl": outputs["gatewayUrl"], "rightsUrl": outputs["rightsUrl"],
                  "health": "pending", "userOboVerified": False})
    private_write(state_file, json.dumps(state, indent=2))
    print("5/5 Publieke metadata en weigering zonder token controleren.")
    smoke(outputs["gatewayUrl"], cfg.tenant, apps["gateway"])
    state["health"] = "unauthenticated-probes-passed"
    private_write(state_file, json.dumps(state, indent=2))
    print(f"Gateway: {outputs['gatewayUrl']}\nNog te bewijzen: echte gebruikerslogin en OBO met beide accounts.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--apply", action="store_true", help="Maak/wijzig de expliciet gekozen labresources.")
    args = parser.parse_args()
    if args.env_file.is_symlink() or not (args.env_file.name == ".env" or args.env_file.name.endswith(".env")):
        parser.error("Gebruik een private .env-file, niet .env.example of een symlink.")
    values = {key: value or "" for key, value in dotenv_values(args.env_file).items()}
    try:
        cfg = Settings.load(values)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"location": cfg.location, "resources": cfg.names(), "apply": args.apply}, indent=2))
    if args.apply:
        apply(cfg, values, args.env_file)
    else:
        print("Alleen plan: geen Azure/Entra-wijzigingen. Gebruik --apply pas na beoordeling.")


if __name__ == "__main__":
    main()
