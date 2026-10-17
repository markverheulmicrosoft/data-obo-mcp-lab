"""Alleen de drie eigen lab-apps beheren; geen wachtwoorden van gebruikers wijzigen."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx2

GRAPH = "https://graph.microsoft.com/v1.0/"
SECRET_LABEL = "mcp-lab-deployment"
SQL_APP_ID = "022907d3-0f1b-48f7-badc-1ba6abab6d66"
SQL_SCOPE_ID = "c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9"


def stable_id(prefix: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://mcp-lab.invalid/{prefix}/{value}"))


def api_definition(prefix: str, kind: str) -> dict:
    return {
        "api": {
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [{
                "id": stable_id(prefix, f"{kind}/access_as_user"),
                "value": "access_as_user", "type": "Admin", "isEnabled": True,
                "adminConsentDisplayName": "Use this MCP API as the signed-in user",
                "adminConsentDescription": "Call the lab API within the signed-in user's assigned roles.",
            }],
        },
        "appRoles": [
            {
                "id": stable_id(prefix, f"{kind}/{role}"),
                "value": role, "displayName": role, "description": description,
                "allowedMemberTypes": ["User"], "isEnabled": True,
            }
            for role, description in [
                ("North.Reader", "Read synthetic orders for North; no margins."),
                ("Finance.Reader", "Read synthetic orders and margins for both regions."),
            ]
        ],
    }


def permission(resource_app_id: str, scope_id: str) -> list:
    return [{"resourceAppId": resource_app_id, "resourceAccess": [{"id": scope_id, "type": "Scope"}]}]


def canonical_permissions(items: list) -> list:
    # Graph's list and individual GET endpoints can return different array orders.
    return sorted([
        {**item, "resourceAccess": sorted(item["resourceAccess"], key=lambda entry: (entry["id"], entry["type"]))}
        for item in items
    ], key=lambda item: item["resourceAppId"])


class Graph:
    def __init__(self, token: str):
        self.http = httpx2.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=False,
        )

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, path: str, *, body=None, params=None) -> dict:
        url = path if path.startswith(GRAPH) else GRAPH + path
        if not url.startswith(GRAPH) or "://" in url[len(GRAPH):]:
            raise ValueError("Onverwachte Graph-bestemming.")
        response = self.http.request(method, url, json=body, params=params)
        if not response.is_success:
            code = response.json().get("error", {}).get("code", "unknown") if response.headers.get(
                "content-type", ""
            ).startswith("application/json") else "non_json_error"
            raise RuntimeError(f"Microsoft Graph {method} mislukt: HTTP {response.status_code}, {code}.")
        return response.json() if response.content else {}

    def items(self, path: str, **params) -> list:
        result = []
        for _ in range(20):
            page = self.request("GET", path, params=params)
            result.extend(page["value"])
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return result
            if not next_link.startswith(GRAPH):
                raise RuntimeError("Graph-paginering verwijst buiten Microsoft Graph.")
            path, params = next_link, {}
        raise RuntimeError("Onverwacht veel Graph-pagina's; afgebroken zonder onvolledige selectie.")


def ensure_app(
    graph: Graph, prefix: str, kind: str, extra: dict, expected_id: str = "", *, owner_id: str,
    allow_sql_delegation: bool = False,
) -> dict:
    owner_id = str(UUID(owner_id))
    if kind not in {"gateway", "rights", "client", "web-client", "teams-client"}:
        raise ValueError("Onbekend type lab-app.")
    is_api = kind in {"gateway", "rights"}
    expected_web = ["https://token.botframework.com/.auth/web/redirect"] if kind == "teams-client" else []
    if kind == "teams-client" and (extra.get("web") or {}).get("redirectUris") != expected_web:
        raise ValueError("Teams OAuth vereist de exacte Bot Token Store-redirect.")
    extend_sql_permission = False
    name, tag = f"{prefix}-{kind}", f"mcp-lab:{prefix}"
    query = f"appId eq '{UUID(expected_id)}'" if expected_id else f"displayName eq '{name}'"
    apps = graph.items("applications", **{"$filter": query})
    if len(apps) > 1:
        raise RuntimeError(f"Meerdere apps met de labnaam {name}; kies niet automatisch.")
    if expected_id and not apps:
        raise RuntimeError(f"De opgegeven {kind}-app bestaat niet in deze tenant.")
    if apps and not expected_id:
        raise RuntimeError(f"{kind}: naam bestaat al. Hergebruik vereist een expliciet vastgelegd app-ID.")
    if apps:
        app = graph.request("GET", f"applications/{apps[0]['id']}", params={
            "$select": "id,appId,displayName,tags,signInAudience,api,appRoles,requiredResourceAccess,"
                       "publicClient,web,spa,passwordCredentials,keyCredentials,identifierUris",
        })
        if str(UUID(app["appId"])) != str(UUID(expected_id)):
            raise RuntimeError(f"{kind}: Graph-resultaat wijkt af van het vastgelegde app-ID.")
        if app["displayName"] != name or tag not in app.get("tags", []):
            raise RuntimeError(f"{kind}: bestaande app is niet herkenbaar als deze lab-app.")
        if app.get("signInAudience") != "AzureADMyOrg":
            raise RuntimeError(f"{kind}: onverwachte tenantconfiguratie; niet automatisch overschrijven.")
        if is_api:
            definition = api_definition(prefix, kind)
            actual_roles = {r["value"]: r for r in app.get("appRoles", [])}
            for role in definition["appRoles"]:
                if any(actual_roles.get(role["value"], {}).get(key) != role[key]
                       for key in ("id", "isEnabled", "allowedMemberTypes")):
                    raise RuntimeError(f"{kind}: rolconfiguratie wijkt af; eerst handmatig beoordelen.")
            api = app.get("api") or {}
            expected_scope = definition["api"]["oauth2PermissionScopes"][0]
            scopes = api.get("oauth2PermissionScopes", [])
            if api.get("requestedAccessTokenVersion") != 2 or not any(
                all(scope.get(k) == expected_scope[k] for k in ("id", "value", "type", "isEnabled"))
                for scope in scopes
            ):
                raise RuntimeError(f"{kind}: v2/scopeconfiguratie wijkt af; niet stilzwijgend herstellen.")
        else:
            public_redirects = ["http://localhost"] if kind == "client" else []
            if (app.get("publicClient") or {}).get("redirectUris", []) != public_redirects:
                raise RuntimeError("Referentieclient moet uitsluitend de loopback-redirect hebben.")
            if app.get("passwordCredentials") or app.get("keyCredentials"):
                raise RuntimeError("De public client mag geen achtergebleven clientcredentials hebben.")
        expected_spa = (extra.get("spa") or {}).get("redirectUris", []) if kind == "web-client" else []
        if (app.get("web") or {}).get("redirectUris", []) != expected_web or (
            (app.get("spa") or {}).get("redirectUris", []) != expected_spa
        ):
            raise RuntimeError(f"{kind}: onverwachte web/SPA-redirects; geen consent verlenen.")
        if is_api and (app.get("publicClient") or {}).get("redirectUris", []):
            raise RuntimeError(f"{kind}: een API-app mag geen public-client-redirect hebben.")
        if canonical_permissions(app.get("requiredResourceAccess", [])) != canonical_permissions(extra.get("requiredResourceAccess", [])):
            desired = extra.get("requiredResourceAccess", [])
            if (
                allow_sql_delegation and kind == "gateway" and len(desired) == 2
                and desired[-1:] == permission(SQL_APP_ID, SQL_SCOPE_ID)
                and canonical_permissions(app.get("requiredResourceAccess", [])) == canonical_permissions(desired[:-1])
            ):
                extend_sql_permission = True
            else:
                raise RuntimeError(f"{kind}: gedelegeerde API-permissies wijken af; eerst beoordelen.")
    else:
        app = graph.request("POST", "applications", body={
            "displayName": name, "tags": [tag], "signInAudience": "AzureADMyOrg", **extra,
        })
    owners = {item["id"] for item in graph.items(f"applications/{app['id']}/owners", **{"$select": "id"})}
    if not apps and not owners:
        graph.request("POST", f"applications/{app['id']}/owners/$ref", body={
            "@odata.id": f"{GRAPH}directoryObjects/{owner_id}",
        })
    elif owners != {owner_id}:
        raise RuntimeError(f"{kind}: eigenaarschap is niet uitsluitend van de huidige deployer.")
    if extend_sql_permission:
        graph.request("PATCH", f"applications/{app['id']}", body={
            "requiredResourceAccess": extra["requiredResourceAccess"],
        })
    if is_api:
        uri = f"api://{app['appId']}"
        existing = app.get("identifierUris", [])
        if existing and existing != [uri]:
            raise RuntimeError(f"{kind}: onverwachte API-identifier.")
        if not existing:
            graph.request("PATCH", f"applications/{app['id']}", body={"identifierUris": [uri]})
    return app


def ensure_web_client(
    graph: Graph, prefix: str, gateway_app_id: str, redirect_uri: str, values: dict, save, *, owner_id: str,
) -> str:
    from urllib.parse import urlsplit

    uri = urlsplit(redirect_uri)
    if (
        uri.scheme != "https" or not uri.hostname or uri.port not in (None, 443)
        or uri.username or uri.password or uri.query or uri.fragment or uri.path != "/auth.html"
    ):
        raise ValueError("Webclient vereist een exacte HTTPS /auth.html redirect.")
    app = ensure_app(graph, prefix, "web-client", {
        "spa": {"redirectUris": [redirect_uri]},
        "requiredResourceAccess": permission(gateway_app_id, stable_id(prefix, "gateway/access_as_user")),
    }, values.get("WEB_CLIENT_ID", ""), owner_id=owner_id)
    save({"WEB_CLIENT_ID": app["appId"]})
    principal = ensure_principal(graph, app, False)
    gateways = graph.items("servicePrincipals", **{"$filter": f"appId eq '{UUID(gateway_app_id)}'"})
    if len(gateways) != 1:
        raise RuntimeError("Geen unieke gateway-serviceprincipal voor de webclient.")
    ensure_grant(graph, principal, gateways[0])
    return app["appId"]


def ensure_principal(graph: Graph, app: dict, api: bool) -> dict:
    matches = graph.items("servicePrincipals", **{"$filter": f"appId eq '{app['appId']}'"})
    if len(matches) > 1:
        raise RuntimeError("Onverwacht meerdere service principals voor dezelfde app.")
    principal = matches[0] if matches else graph.request("POST", "servicePrincipals", body={"appId": app["appId"]})
    if api and not principal.get("appRoleAssignmentRequired"):
        graph.request("PATCH", f"servicePrincipals/{principal['id']}", body={"appRoleAssignmentRequired": True})
    return principal


def ensure_grant(graph: Graph, client: dict, resource: dict, *, scope: str = "access_as_user") -> None:
    if scope not in {"access_as_user", "user_impersonation"}:
        raise ValueError("Onbekende gedelegeerde scope.")
    grants = graph.items("oauth2PermissionGrants", **{
        "$filter": f"clientId eq '{client['id']}' and resourceId eq '{resource['id']}'",
    })
    shared = [grant for grant in grants if grant["consentType"] == "AllPrincipals"]
    if shared:
        if len(shared) != 1 or set(shared[0]["scope"].split()) != {scope}:
            raise RuntimeError("Bestaande consent wijkt af; geen automatische verbreding.")
        return
    graph.request("POST", "oauth2PermissionGrants", body={
        "clientId": client["id"], "resourceId": resource["id"],
        "consentType": "AllPrincipals", "scope": scope,
    })


def ensure_role(graph: Graph, principal: dict, user_id: str, role_id: str) -> None:
    assignments = graph.items(f"servicePrincipals/{principal['id']}/appRoleAssignedTo")
    existing = [a for a in assignments if a["principalId"] == user_id]
    if existing:
        if any(a["appRoleId"] != role_id for a in existing):
            raise RuntimeError("Bestaande gebruikersrol wijkt af; deploy verandert die niet automatisch.")
        return
    graph.request("POST", f"servicePrincipals/{principal['id']}/appRoleAssignedTo", body={
        "principalId": user_id, "resourceId": principal["id"], "appRoleId": role_id,
    })


def bootstrap(
    graph: Graph, prefix: str, north_id: str, finance_id: str, values: dict, save, recover_secret, *,
    owner_id: str, need_secret: bool = True,
) -> dict:
    if values.get("GATEWAY_CLIENT_SECRET") and not values.get("GATEWAY_CLIENT_ID"):
        raise ValueError("Een bestaand secret vereist het bijbehorende GATEWAY_CLIENT_ID.")
    for user_id in (north_id, finance_id):
        user = graph.request("GET", f"users/{user_id}", params={"$select": "id,accountEnabled,userType"})
        if not user.get("accountEnabled") or user.get("userType") != "Member":
            raise RuntimeError("Testgebruikers moeten actieve members van deze tenant zijn.")

    rights = ensure_app(
        graph, prefix, "rights", api_definition(prefix, "rights"), values.get("RIGHTS_CLIENT_ID", ""),
        owner_id=owner_id,
    )
    save({"RIGHTS_CLIENT_ID": rights["appId"]})
    gateway_definition = api_definition(prefix, "gateway")
    gateway_definition["requiredResourceAccess"] = permission(rights["appId"], stable_id(prefix, "rights/access_as_user"))
    enable_sql = bool(values.get("FABRIC_SQL_SERVER"))
    if enable_sql:
        gateway_definition["requiredResourceAccess"] += permission(SQL_APP_ID, SQL_SCOPE_ID)
    gateway = ensure_app(
        graph, prefix, "gateway", gateway_definition, values.get("GATEWAY_CLIENT_ID", ""), owner_id=owner_id,
        allow_sql_delegation=enable_sql,
    )
    save({"GATEWAY_CLIENT_ID": gateway["appId"]})
    client = ensure_app(graph, prefix, "client", {
        "publicClient": {"redirectUris": ["http://localhost"]},
        "isFallbackPublicClient": True,
        "requiredResourceAccess": permission(gateway["appId"], stable_id(prefix, "gateway/access_as_user")),
    }, values.get("CLIENT_APP_ID", ""), owner_id=owner_id)
    save({"CLIENT_APP_ID": client["appId"]})

    rights_sp = ensure_principal(graph, rights, True)
    gateway_sp = ensure_principal(graph, gateway, True)
    client_sp = ensure_principal(graph, client, False)
    for kind, principal in (("rights", rights_sp), ("gateway", gateway_sp)):
        for user_id, role in ((north_id, "North.Reader"), (finance_id, "Finance.Reader")):
            ensure_role(graph, principal, user_id, stable_id(prefix, f"{kind}/{role}"))
    ensure_grant(graph, gateway_sp, rights_sp)
    ensure_grant(graph, client_sp, gateway_sp)
    if enable_sql:
        resources = graph.items("servicePrincipals", **{"$filter": f"appId eq '{SQL_APP_ID}'"})
        if len(resources) > 1:
            raise RuntimeError("Meerdere SQL-resourceprincipals gevonden.")
        resource = resources[0] if resources else graph.request("POST", "servicePrincipals", body={"appId": SQL_APP_ID})
        if "https://database.windows.net/" not in resource.get("servicePrincipalNames", []) or not any(
            scope.get("id") == SQL_SCOPE_ID and scope.get("value") == "user_impersonation" and scope.get("isEnabled")
            for scope in resource.get("oauth2PermissionScopes", [])
        ):
            raise RuntimeError("De SQL-resource heeft niet de verwachte delegated scope.")
        ensure_grant(graph, gateway_sp, resource, scope="user_impersonation")
    if not need_secret:
        return {
            "gateway": gateway["appId"], "rights": rights["appId"], "client": client["appId"],
            "gatewayObjectId": gateway["id"],
        }

    existing_secret = values.get("GATEWAY_CLIENT_SECRET", "")
    credentials = graph.request("GET", f"applications/{gateway['id']}", params={"$select": "passwordCredentials"}).get(
        "passwordCredentials", []
    )
    ours = [c for c in credentials if c.get("displayName") == SECRET_LABEL]
    if existing_secret and not credentials:
        raise RuntimeError("Een secret is opgegeven, maar deze gateway-app heeft geen password credential.")
    if ours and not any(
        datetime.fromisoformat(c["endDateTime"].replace("Z", "+00:00")) > datetime.now(timezone.utc) for c in ours
    ):
        raise RuntimeError("Het gateway-secret is verlopen. Expliciete rotatie nodig, geen stille vervanging.")
    if not existing_secret:
        if credentials:
            existing_secret = recover_secret()
            if not existing_secret:
                raise RuntimeError("Bestaand app-secret niet teruggevonden; herstel het lokaal of roteer expliciet.")
        else:
            credential = graph.request("POST", f"applications/{gateway['id']}/addPassword", body={
                "passwordCredential": {
                    "displayName": SECRET_LABEL,
                    "endDateTime": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
                },
            })
            existing_secret = credential["secretText"]
        save({"GATEWAY_CLIENT_SECRET": existing_secret})
    return {"gateway": gateway["appId"], "rights": rights["appId"], "client": client["appId"], "secret": existing_secret}


def ensure_managed_identity_federation(
    graph: Graph, app_object_id: str, app_id: str, owner_id: str, tenant: str, principal_id: str,
) -> None:
    for value in (app_object_id, app_id, owner_id, tenant, principal_id):
        UUID(value)
    app = graph.request("GET", f"applications/{app_object_id}", params={"$select": "appId"})
    owners = graph.items(f"applications/{app_object_id}/owners", **{"$select": "id"})
    if app["appId"] != app_id or {owner["id"] for owner in owners} != {owner_id}:
        raise RuntimeError("Federatie vereist het vastgelegde app-ID en uitsluitend de vertrouwde eigenaar.")
    desired = {
        "name": "gateway-managed-identity",
        "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "subject": principal_id,
        "audiences": ["api://AzureADTokenExchange"],
    }
    path = f"applications/{app_object_id}/federatedIdentityCredentials"
    existing = graph.items(path)
    if existing:
        if len(existing) != 1 or any(existing[0].get(key) != value for key, value in desired.items()):
            raise RuntimeError("Bestaande federated credential wijkt af; niet automatisch wijzigen.")
        return
    graph.request("POST", path, body=desired)
