"""Alleen de drie eigen lab-apps beheren; geen wachtwoorden van gebruikers wijzigen."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx2

GRAPH = "https://graph.microsoft.com/v1.0/"
SECRET_LABEL = "mcp-lab-deployment"


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
    graph: Graph, prefix: str, kind: str, extra: dict, expected_id: str = "", *, owner_id: str
) -> dict:
    owner_id = str(UUID(owner_id))
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
        if kind != "client":
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
            if (app.get("publicClient") or {}).get("redirectUris", []) != ["http://localhost"]:
                raise RuntimeError("Referentieclient moet uitsluitend de loopback-redirect hebben.")
            if app.get("passwordCredentials") or app.get("keyCredentials"):
                raise RuntimeError("De public client mag geen achtergebleven clientcredentials hebben.")
        if any((app.get(surface) or {}).get("redirectUris", []) for surface in ("web", "spa")):
            raise RuntimeError(f"{kind}: onverwachte web/SPA-redirects; geen consent verlenen.")
        if kind != "client" and (app.get("publicClient") or {}).get("redirectUris", []):
            raise RuntimeError(f"{kind}: een API-app mag geen public-client-redirect hebben.")
        if app.get("requiredResourceAccess", []) != extra.get("requiredResourceAccess", []):
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
    if kind != "client":
        uri = f"api://{app['appId']}"
        existing = app.get("identifierUris", [])
        if existing and existing != [uri]:
            raise RuntimeError(f"{kind}: onverwachte API-identifier.")
        if not existing:
            graph.request("PATCH", f"applications/{app['id']}", body={"identifierUris": [uri]})
    return app


def ensure_principal(graph: Graph, app: dict, api: bool) -> dict:
    matches = graph.items("servicePrincipals", **{"$filter": f"appId eq '{app['appId']}'"})
    if len(matches) > 1:
        raise RuntimeError("Onverwacht meerdere service principals voor dezelfde app.")
    principal = matches[0] if matches else graph.request("POST", "servicePrincipals", body={"appId": app["appId"]})
    if api and not principal.get("appRoleAssignmentRequired"):
        graph.request("PATCH", f"servicePrincipals/{principal['id']}", body={"appRoleAssignmentRequired": True})
    return principal


def ensure_grant(graph: Graph, client: dict, resource: dict) -> None:
    grants = graph.items("oauth2PermissionGrants", **{
        "$filter": f"clientId eq '{client['id']}' and resourceId eq '{resource['id']}'",
    })
    shared = [grant for grant in grants if grant["consentType"] == "AllPrincipals"]
    if shared:
        if len(shared) != 1 or set(shared[0]["scope"].split()) != {"access_as_user"}:
            raise RuntimeError("Bestaande consent wijkt af; geen automatische verbreding.")
        return
    graph.request("POST", "oauth2PermissionGrants", body={
        "clientId": client["id"], "resourceId": resource["id"],
        "consentType": "AllPrincipals", "scope": "access_as_user",
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
    graph: Graph, prefix: str, north_id: str, finance_id: str, values: dict, save, recover_secret, *, owner_id: str
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
    gateway = ensure_app(
        graph, prefix, "gateway", gateway_definition, values.get("GATEWAY_CLIENT_ID", ""), owner_id=owner_id,
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
