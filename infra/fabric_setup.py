"""Provision one isolated, persisted Fabric rights lab on an existing capacity."""

import argparse
import json
import time
from contextlib import closing
from pathlib import Path
from uuid import UUID

import httpx2
import mssql_python
from dotenv import dotenv_values, set_key

from deploy import Azure, ROOT, Settings, private_write
from entra import Graph
from mcp_lab.fabric import DelegatedToken, FabricSettings

API = "https://api.fabric.microsoft.com/v1/"
ORDERS = [
    ("N-001", "Noord", "Citybike", 1200, 240),
    ("N-002", "Noord", "E-bike", 2400, 600),
    ("N-003", "Noord", "Cargo", 3600, 900),
    ("Z-001", "Zuid", "Citybike", 1800, 540),
    ("Z-002", "Zuid", "E-bike", 2800, 700),
    ("Z-003", "Zuid", "Cargo", 4200, 1050),
]


class Fabric:
    def __init__(self, token):
        self.http = httpx2.Client(headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def request(self, method, path, **kwargs):
        url = path if path.startswith(API) else API + path
        if not url.startswith(API) or "://" in url[len(API):]:
            raise ValueError("Onverwachte Fabric-bestemming.")
        response = self.http.request(method, url, **kwargs)
        if not response.is_success:
            code = response.json().get("errorCode", "unknown")
            raise RuntimeError(f"Fabric {method} HTTP {response.status_code}: {code}.")
        return response

    def items(self, path):
        result = []
        for _ in range(20):
            body = self.request("GET", path).json()
            result.extend(body["value"])
            path = body.get("continuationUri")
            if not path:
                return result
        raise RuntimeError("Te veel Fabric-pagina's; geen onvolledige selectie gebruiken.")


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("Ongeldige SQL-principalnaam.")
    return "[" + value.replace("]", "]]") + "]"


def seed(settings, token, users):
    with closing(mssql_python.connect(
        settings.connection_string, token_provider=DelegatedToken(token), autocommit=True, timeout=60,
    )) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute("SELECT USER_NAME()")
            operator = cursor.fetchone()[0]
            for statement in (
                "IF SCHEMA_ID('Security') IS NULL EXEC('CREATE SCHEMA Security')",
                "IF OBJECT_ID('dbo.Orders') IS NULL CREATE TABLE dbo.Orders "
                "(id varchar(16) NOT NULL, region varchar(16) NOT NULL, product varchar(64) NOT NULL, revenue int NOT NULL)",
                "IF OBJECT_ID('dbo.Margins') IS NULL CREATE TABLE dbo.Margins "
                "(id varchar(16) NOT NULL, region varchar(16) NOT NULL, margin int NOT NULL)",
                "IF OBJECT_ID('Security.UserScope') IS NULL CREATE TABLE Security.UserScope "
                "(principal_name varchar(256) NOT NULL, region varchar(16) NOT NULL)",
            ):
                cursor.execute(statement)
            for order_id, region, product, revenue, margin in ORDERS:
                cursor.execute(
                    "INSERT INTO dbo.Orders SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM dbo.Orders WHERE id=?)",
                    (order_id, region, product, revenue, order_id),
                )
                cursor.execute(
                    "INSERT INTO dbo.Margins SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM dbo.Margins WHERE id=?)",
                    (order_id, region, margin, order_id),
                )
            for name, region in ((operator, "*"), (users["north"], "Noord"), (users["finance"], "*")):
                cursor.execute(
                    "INSERT INTO Security.UserScope SELECT ?,? WHERE NOT EXISTS "
                    "(SELECT 1 FROM Security.UserScope WHERE principal_name=?)", (name, region, name),
                )
            cursor.execute("SELECT OBJECT_ID('Security.region_filter')")
            if cursor.fetchone()[0] is None:
                cursor.execute(
                    "CREATE FUNCTION Security.region_filter(@region varchar(16)) RETURNS TABLE WITH SCHEMABINDING "
                    "AS RETURN SELECT 1 AS permitted FROM Security.UserScope AS s "
                    "WHERE LOWER(s.principal_name)=LOWER(USER_NAME()) AND (s.region=@region OR s.region='*')"
                )
            cursor.execute("SELECT COUNT(*) FROM sys.security_policies WHERE name='RegionFilter'")
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "CREATE SECURITY POLICY Security.RegionFilter "
                    "ADD FILTER PREDICATE Security.region_filter(region) ON dbo.Orders, "
                    "ADD FILTER PREDICATE Security.region_filter(region) ON dbo.Margins WITH (STATE=ON)"
                )
            north, finance = identifier(users["north"]), identifier(users["finance"])
            for statement in (
                f"GRANT SELECT ON OBJECT::dbo.Orders TO {north}",
                f"DENY SELECT ON OBJECT::dbo.Margins TO {north}",
                f"GRANT SELECT ON OBJECT::dbo.Orders TO {finance}",
                f"GRANT SELECT ON OBJECT::dbo.Margins TO {finance}",
            ):
                cursor.execute(statement)
            cursor.execute("SELECT COUNT(*) FROM dbo.Orders")
            if cursor.fetchone()[0] != 6:
                raise RuntimeError("De verse bron bevat niet precies de zes bedoelde laborders.")
            cursor.execute("SELECT is_enabled FROM sys.security_policies WHERE name='RegionFilter'")
            if not cursor.fetchone()[0]:
                raise RuntimeError("De bron-RLS-policy is niet actief.")
            print("Persisted: six orders, six margins, SQL grants and enabled RLS on both tables.")
            print("This is operator setup evidence, not a real-user/OBO result.")


def configure_users(settings, token, users):
    """Explicitly add the selected lab users without resetting existing RLS experiments."""
    with closing(mssql_python.connect(
        settings.connection_string, token_provider=DelegatedToken(token), autocommit=True, timeout=30,
    )) as connection:
        with closing(connection.cursor()) as cursor:
            for role, region in (("north", "Noord"), ("finance", "*")):
                upn = users[role]
                cursor.execute("SELECT region FROM Security.UserScope WHERE principal_name=?", (upn,))
                rows = cursor.fetchall()
                if rows and (len(rows) != 1 or rows[0][0] != region):
                    raise RuntimeError("Bestaande bronrechten wijken af; geen experiment automatisch resetten.")
                if not rows:
                    cursor.execute("INSERT INTO Security.UserScope VALUES (?,?)", (upn, region))
                name = identifier(upn)
                cursor.execute(f"GRANT SELECT ON OBJECT::dbo.Orders TO {name}")
                action = "DENY" if role == "north" else "GRANT"
                cursor.execute(f"{action} SELECT ON OBJECT::dbo.Margins TO {name}")
    print("Configured the selected users' SQL grants; previous lab accounts were not removed.")


def apply(cfg, values, env_file, capacity, *, add_users=False):
    state_file = ROOT / ".deploy" / "fabric-state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {
        "tenant": cfg.tenant, "prefix": cfg.prefix, "capacity": capacity,
    }
    if any(state.get(k) != v for k, v in {
        "tenant": cfg.tenant, "prefix": cfg.prefix, "capacity": capacity,
    }.items()):
        raise RuntimeError("Fabric-state hoort bij een ander doel.")

    def save():
        private_write(state_file, json.dumps(state, indent=2))

    az = Azure(ROOT / ".deploy")
    if az.run("account", "show")["tenantId"] != cfg.tenant:
        raise RuntimeError("Azure-login hoort bij een andere tenant.")
    graph = Graph(az.run("account", "get-access-token", "--resource-type", "ms-graph")["accessToken"])
    try:
        operator = graph.request("GET", "me")
        users = {}
        for role, user_id in (("north", cfg.north_user), ("finance", cfg.finance_user)):
            user = graph.request("GET", f"users/{UUID(user_id)}", params={"$select": "id,userPrincipalName,accountEnabled"})
            if not user.get("accountEnabled"):
                raise RuntimeError("Een testaccount is uitgeschakeld.")
            users[role] = user["userPrincipalName"]
    finally:
        graph.close()
    if state.get("owner", operator["id"]) != operator["id"]:
        raise RuntimeError("Fabric-state heeft een andere eigenaar.")
    state["owner"] = operator["id"]
    save()
    fabric = Fabric(az.run("account", "get-access-token", "--resource", "https://api.fabric.microsoft.com")["accessToken"])
    try:
        capacities = fabric.items("capacities")
        match = [c for c in capacities if c["id"] == capacity and c["state"] == "Active"]
        if len(match) != 1 or match[0]["region"] != "Sweden Central":
            raise RuntimeError("De gekozen bestaande capacity is niet actief in Sweden Central.")
        name, marker = cfg.prefix + "-data", "mcp-lab:" + cfg.prefix + "; isolated persisted demo"
        if not state.get("workspace"):
            if any(w["displayName"] == name for w in fabric.items("workspaces")):
                raise RuntimeError("Workspace-naam bestaat al zonder vastgelegd ID; niet overnemen.")
            body = fabric.request("POST", "workspaces", json={
                "displayName": name, "capacityId": capacity, "description": marker,
            }).json()
            state["workspace"] = body["id"]
            save()
        workspace = str(UUID(state["workspace"]))
        body = fabric.request("GET", f"workspaces/{workspace}").json()
        if body["displayName"] != name or body.get("description") != marker or body.get("capacityId") != capacity:
            raise RuntimeError("Workspace-configuratie wijkt af; stop zonder wijzigingen.")
        assignments = fabric.items(f"workspaces/{workspace}/roleAssignments")
        if {(a["principal"]["id"], a["role"]) for a in assignments} != {(operator["id"], "Admin")}:
            raise RuntimeError("De labworkspace heeft onverwachte workspace-rollen; eerst beoordelen.")
        if not state.get("warehouse"):
            items = fabric.items(f"workspaces/{workspace}/warehouses")
            if items:
                if len(items) != 1 or items[0]["displayName"] != "velora_rights" or items[0].get("description") != marker:
                    raise RuntimeError("Onverwachte warehouses in de eigen workspace.")
                state["warehouse"] = items[0]["id"]
            else:
                response = fabric.request("POST", f"workspaces/{workspace}/warehouses", json={
                    "displayName": "velora_rights", "description": marker,
                    "creationPayload": {"collationType": "Latin1_General_100_CI_AS_KS_WS_SC_UTF8"},
                })
                if response.status_code == 202:
                    operation = API + "operations/" + str(UUID(response.headers["x-ms-operation-id"]))
                    state["operation"] = operation
                    save()
                    for _ in range(30):
                        time.sleep(10)
                        result = fabric.request("GET", operation).json()
                        if result["status"] == "Succeeded":
                            break
                        if result["status"] in {"Failed", "Cancelled"}:
                            raise RuntimeError("Warehouse-aanmaak is mislukt; bekijk de Fabric-operatie.")
                    else:
                        raise RuntimeError("Warehouse-aanmaak loopt nog; geen tweede aanmaken.")
                    response = fabric.request("GET", operation + "/result")
                state["warehouse"] = response.json()["id"]
            save()
        warehouse = str(UUID(state["warehouse"]))
        body = fabric.request("GET", f"workspaces/{workspace}/warehouses/{warehouse}").json()
        if body["displayName"] != "velora_rights" or body.get("description") != marker or body["workspaceId"] != workspace:
            raise RuntimeError("Het vastgelegde Warehouse wijkt af van de eigen labbron.")
        server = body["properties"]["connectionString"]
        settings = FabricSettings(server, body["displayName"], workspace, warehouse)
        for key, value in {
            "FABRIC_CAPACITY_ID": capacity, "FABRIC_WORKSPACE_ID": workspace,
            "FABRIC_WAREHOUSE_ID": warehouse, "FABRIC_SQL_SERVER": server,
            "FABRIC_SQL_DATABASE": settings.database,
        }.items():
            set_key(str(env_file), key, value, quote_mode="always")
        if not state.get("seeded"):
            seed(settings, az.run("account", "get-access-token", "--resource", "https://database.windows.net/")["accessToken"], users)
            state["seeded"] = True
            state["configuredUsers"] = {"north": cfg.north_user, "finance": cfg.finance_user}
            save()
        elif add_users:
            configure_users(
                settings, az.run("account", "get-access-token", "--resource", "https://database.windows.net/")["accessToken"],
                users,
            )
            state["configuredUsers"] = {"north": cfg.north_user, "finance": cfg.finance_user}
            save()
        elif state.get("configuredUsers") != {"north": cfg.north_user, "finance": cfg.finance_user}:
            raise RuntimeError("Andere testaccounts geselecteerd; gebruik expliciet --configure-users na beoordeling.")
        print("Warehouse:", settings.url)
        print("Remaining: share this item Read-only with both testusers, without ReadData/ReadAll; no workspace roles.")
    finally:
        fabric.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--configure-users", action="store_true", help="Richt expliciet SQL-leesrechten in voor de gekozen accounts.")
    args = parser.parse_args()
    capacity = str(UUID(args.capacity))
    if args.env_file.is_symlink():
        parser.error("Geen symlink voor private configuratie.")
    values = dict(dotenv_values(args.env_file))
    cfg = Settings.load(values)
    if args.apply:
        apply(cfg, values, args.env_file, capacity, add_users=args.configure_users)
    else:
        print("Plan only: isolated Fabric workspace + Warehouse on existing capacity; six persisted mock orders and SQL RLS.")
