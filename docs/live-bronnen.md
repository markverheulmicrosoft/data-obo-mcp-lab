# Live bronnen: Fabric Warehouse-lab en gateway-OBO

Status: research only. Geen cloudoperaties, geen credentials, geen infrastructuurwijzigingen.
Alle claims verwijzen naar primaire Microsoft Learn-pagina's, de Model Context Protocol-specificatie of primaire driver-repositories. Wat niet op een primaire bron is gevonden, staat expliciet onder **Onbekend**.

## 0. Correcties op eerder onderzoek (`docs/mcp-onderzoek.md`)

- De Fabric externe Python-clientpagina gebruikt de Assistants-API, die per 26 augustus 2026 wordt uitgefaseerd. Die pagina is geen basislijn; de MCP-endpointpagina is leidend. https://learn.microsoft.com/en-us/fabric/data-science/consume-data-agent-python (Important-blok bovenaan)
- Databricks Model Serving OBO (`ModelServingUserCredentials`) is een Databricks-interne flow in Public Preview, geen Entra-OBO. https://learn.microsoft.com/en-us/azure/databricks/agents/custom-agents/model-serving/agent-authentication-model-serving ("On-behalf-of-user authentication", Public Preview)
- Power BI `executeQueries`: de groeps-variant vereist dat de aanroeper toegang heeft tot de werkruimte; de pagina zonder groep noemt alleen dataset Read en Build. Dit spreekt de Data-agent-sharingpagina tegen (Read op semantic model is voldoende). Testen, niet aannemen. https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group ; https://learn.microsoft.com/en-us/fabric/data-science/data-agent-sharing

---

## 1. Scope: Fabric Warehouse-lab op bestaande F2

Aanbevolen: een nieuw **Warehouse-item** met synthetische orders en een aparte margetabel, opgeslagen in Fabric, op de bestaande F2-capaciteit. Dit is geen Databricks-pad (later) en geen wijziging aan de Coffee-demo.

Isolatie-optie: een **aparte werkruimte** op dezelfde capaciteit, zodat de ContosoCoffeeIQ-werkruimte en de rollen daar niet worden geraakt. Dit is een ontwerpkeuze; de Learn-pagina's verbieden het niet en maken het ook niet verplicht.

---

## 2. Vastgestelde feiten

### A. Warehouse aanmaken via Fabric REST
- `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/warehouses`. Body: `displayName` (verplicht), optioneel `creationPayload.collationType`. Creatie met `definition` is niet ondersteund. https://learn.microsoft.com/en-us/rest/api/fabric/warehouse/items/create-warehouse
- Vereist: aanroeper heeft de **contributor**-werkruimterol. Delegated scope: `Warehouse.ReadWrite.All` of `Item.ReadWrite.All`. Gebruikers en service principals worden ondersteund. https://learn.microsoft.com/en-us/rest/api/fabric/warehouse/items/create-warehouse ("Permissions", "Required Delegated Scopes")
- Antwoord is `201` of `202` met LRO (`Location`, `Retry-After`). Zelfde pagina.
- Collatie: default is `Latin1_General_100_BIN2_UTF8` (case-sensitive). Alternatief: `Latin1_General_100_CI_AS_KS_WS_SC_UTF8`. Zelfde pagina, sectie CollationType. **Dit is relevant voor RLS op UPN-strings** (zie D).
- Warehouse-creatie vereist een ondersteunde Fabric-capaciteit. Zelfde pagina, "Limitations".

### B. F2-constraints
- F2 = **2 Capacity Units**. https://learn.microsoft.com/en-us/fabric/enterprise/licenses (tabel SKU/CU)
- Fabric-features: pauzeren/hervatten en on-demand resizing zijn beschikbaar voor F-SKU's. Een **trial**-capaciteit ondersteunt de meeste van deze features niet. https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features
- Operationeel risico: twee CU delen met de Coffee-demo. Zware testqueries kunnen die demo vertragen. **Onbekend:** concrete F2-concurrency- en throttlinglimieten (niet opgehaald).

### C. Tenant-settings en identiteit
- De tenantsetting **Service principals can use Fabric APIs** geldt alleen voor service-principal-toegang tot Fabric/SQL. Voor de user-OBO-route is die niet nodig. https://learn.microsoft.com/en-us/fabric/data-warehouse/entra-id-authentication ("Tenant setting")
- Fabric REST-scopes gelden **alleen voor delegated toegang**; directe SP/MI-toegang wordt door adminbeheer en itempermissies beperkt. https://learn.microsoft.com/en-us/rest/api/fabric/articles/scopes ("Note")
- **Onbekend:** of tenantsetting "Users can create Fabric items" vereist is voor de creator. Niet opgehaald in deze sessie.

### D. Gebruikers, rechten en CREATE USER (kernbeperking)
- **`CREATE USER` wordt niet ondersteund** in Warehouse en SQL analytics endpoint. `GRANT` of `DENY` maakt de databasegebruiker automatisch aan. https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions ("CREATE USER limitation"); https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area ("Limitations")
- Aanbevolen volgorde volgens de pagina: eerst SQL-rechten definiëren, daarna itempermissie geven. https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions ("Configure user granular permissions")
- Verbinden vereist minimaal **Fabric Read** op het item, of een werkruimterol. https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions
- Itempermissie **Read zonder extra rechten** geeft alleen connect (CONNECT-equivalent). Er is geen tabel-leesrecht totdat er een T-SQL `GRANT` is. **ReadData** is db_datareader-equivalent en geeft leesrechten op alle tabellen. Voor deze lab-opzet: **alleen Read**, daarna granulaire `GRANT SELECT`. https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions ("Fabric security roles")
- Het delen via de UI vereist dat de deler admin of member is in de werkruimte. https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions (Note)
- Propagatie van itempermissies duurt tot **2 uur**; testgebruikers moeten opnieuw inloggen. Zelfde pagina, "Limitations".
- Beperking: gedeelde ontvangers gebruiken de **identiteit van de eigenaar** (delegated mode). De eigenaar mag dus niet uit de werkruimte worden verwijderd. Zelfde pagina, "Limitations".
- Permissies op Entra-**groepen** worden in de security-overzichtspagina genoemd. Of T-SQL `GRANT` naar een groepsprincipal werkt in Warehouse, is **niet** op een primaire pagina bevestigd. Voor de lab: per gebruiker. https://learn.microsoft.com/en-us/fabric/data-warehouse/security ("Item permissions")

### E. Row-level security (RLS)
- Via `CREATE SECURITY POLICY` met een inline table-valued function als predicate. Ondersteund in Warehouse én SQL analytics endpoint. https://learn.microsoft.com/en-us/fabric/data-warehouse/row-level-security ("Restrict access...", "Examples")
- Officieel voorbeeld gebruikt `USER_NAME()` en `USER` in het predicate, met UPN-achtige waarden (`Sales1@contoso.com`). https://learn.microsoft.com/en-us/fabric/data-warehouse/row-level-security (sectie Examples)
- Predicate mag een join met een andere tabel bevatten; met `SCHEMABINDING = ON` (default) hoeft de aanroeper daarvoor geen extra rechten. Zelfde pagina, "Predicate-based row-level security".
- Vereiste rechten: `ALTER ANY SECURITY POLICY` om policy te maken; `ALTER` op schema; `SELECT`+`REFERENCES` op de functie; `REFERENCES` op de doeltabel en op de gebruikte kolommen. Zelfde pagina, "Permissions".
- **Kritiek:** policies gelden ook voor **dbo**, db_owner en tabeleigenaar. Leden van Admin/Member/Contributor zien alleen alle rijen als het predicate dat expliciet toestaat. Zelfde pagina, "Permissions".
- **Kritiek:** tabellen **zonder** policy zijn voor elke gebruiker met SELECT onbeperkt zichtbaar. Margetabel en orders moeten dus expliciet worden beschermd of afgeschermd via GRANT. Zelfde pagina, "Predicate-based".
- **Onbekend:** `SUSER_SNAME()` staat niet in de opgehaalde Fabric-RLS-pagina's. Gebruik `USER_NAME()` (gedocumenteerd). Test de exacte UPN-string en hoofdlettergedrag tegen de tokenclaim.

### F. Warehouse (schrijven) versus SQL analytics endpoint (lezen)
- Warehouse ondersteunt tabellen maken/wijzigen/verwijderen en INSERT/UPDATE/DELETE. Dit is wat nodig is om synthetische data in Fabric te zetten. https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area (eerste sectie)
- Het **SQL analytics endpoint van een Lakehouse** is read-only voor tabellen: geen create/alter/drop, geen insert/update/delete. Dit geldt ook voor ContosoCoffeeIQ. Zelfde pagina.
- Warehouse-ondersteuning voor `CREATE SECURITY POLICY` en `GRANT`: zie D en E.
- Warehouse-beperkingen die voor de lab relevant zijn: geen `CREATE USER`, geen synonyms, geen triggers, geen materialized views. Zelfde pagina, "Limitations".

### G. Entra OBO naar Fabric SQL (database.windows.net)
- Middle-tier OBO: het inkomende token moet `aud` = de gateway-app hebben. Downstream-scope via `scope=https://database.windows.net/.default`. Applicaties mogen geen token voor een andere app inwisselen. https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow ("Middle-tier access token request")
- Alleen **user principals**: OBO werkt niet voor app-only tokens. Zelfde pagina, "Client limitations".
- Client-assertion-variant: het OBO-request accepteert `client_assertion_type`+`client_assertion` naast `client_secret`. De pagina toont het voorbeeld met een certificaat. **Onbekend:** of een federated MI-JWT (`api://AzureADTokenExchange`) hier geldig is. Test als go/no-go.
- Consent: `.default` mag **niet** gecombineerd worden met andere delegated scopes (AADSTS70011). Admin consent voor de middle-tier-app of preauthorized clients is de betrouwbare route. Zelfde pagina, "Gaining consent for the middle-tier application".
- Gateway mag het inkomende token **niet** doorgeven (token passthrough verboden). Het moet een eigen token voor de SQL-resource verkrijgen. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization (Token audience / resource-sectie; geciteerd via secundaire samenvatting, primaire tekst niet volledig opgehaald).
- **Onbekend:** de exacte delegated permissie-naam voor het Azure SQL/database.windows.net-resource in app registraties. Niet op primaire pagina gevonden. Vastlegging via test.
- SQL-verbinding vereist dat de gebruiker minimaal **Fabric Read** op het item heeft. Zie D.

### H. Python-drivers

**Driver 1: `mssql-python` (Microsoft, GA volgens README)**
- Bron: https://github.com/microsoft/mssql-python (README, sectie "Support for Microsoft Entra ID Authentication").
- Python >= 3.10. ODBC-binaries via companion-pakket `mssql-python-odbc` (pip installeert die automatisch).
- Entra-methoden: ActiveDirectoryInteractive, ActiveDirectoryServicePrincipal, ActiveDirectoryMSI, enz. Zelfde README-tabel.
- **Bring-your-own token**: via `token_provider` of ruwe token in `attrs_before`. Levensduur is de verantwoordelijkheid van de app. Gepoolde verbindingen met verlopen token falen bij reconnect. Zelfde README.
- **Waarschuwing:** Interactive/device-code-auth deelt één account per proces; niet gebruiken voor meerdere eindgebruikers in één proces. Zelfde README.
- Broncode: `mssql_python/auth.py` (`_get_token_from_credential`, `acquire_token_from_credential`) en `mssql_python/db_connection.py` (`connect(..., token_provider=..., attrs_before=...)`) bevestigen: scope is hard-coded op `https://database.windows.net/.default` (commercial cloud). Sovereign clouds zijn buiten scope. Commit `9424705251baf8ded41a8a4968216b1fd9b00929`, bestanden: `mssql_python/auth.py:751-767`, `:862-883`; `mssql_python/db_connection.py:69-81`.
- `token_provider` vereist een object met `get_token(scope)` dat `.token` teruggeeft. Mixed `token_provider` + `attrs_before[SQL_COPT_SS_ACCESS_TOKEN]` wordt afgewezen. Testbestand `tests/test_008_auth.py:1455-1512`.

**Driver 2: `pytds` / PyPI `python-tds` (pure Python TDS)**
- Bron: https://github.com/denisenkom/pytds (README, token-authenticatie). Commit geïndexeerd: `66b17945ae9c6524badf19fab42ffc0d099d26d9`.
- `pytds.connect(server=..., database=..., access_token_callable=fn, cafile=...)`. Scope in voorbeeld: `https://database.windows.net/.default`. README.rst:71-126.
- Broncode `src/pytds/__init__.py:443-453`: de callable wordt bij elke **fysieke** verbinding aangeroepen (`login.access_token = login.access_token_callable()`). Dat maakt per-gebruikersisolatie eenvoudig: één callable per gebruikerstoken.
- **Onbekend:** actieve onderhoudsstatus en release-cadans van pytds niet gecontroleerd. Microsoft levert deze driver niet.

**Advies:** `mssql-python` als primaire driver, omdat het Microsoft-GA is en `token_provider` precies de OBO-vorm biedt. Gebruik een per-gebruiker-tokenprovider en **geen gedeelde pool** tussen gebruikers. `pytds` is de eenvoudige fallback met `access_token_callable`.

### I. Entra-identiteit en Fabric REST-scope voor de creator
- De creator (lab-eigenaar) heeft contributor-rol en `Warehouse.ReadWrite.All` of `Item.ReadWrite.All` nodig voor de REST-creatie. Testgebruikers hebben **geen** REST-scope of workspacerol nodig voor het SQL-pad.

---

## 3. Voorgestelde minimale lab-opzet (interfaces)

```
Workspace (bestaande F2 of aparte workspace op F2)
  Warehouse: <lab-warehouse-naam>                 -- POST /v1/workspaces/{id}/warehouses
    schema sales:    Orders (synthetisch, in Fabric opgeslagen)
    schema finance:  MarginByProduct (aparte margetabel)
    schema security: UserScope(UserUpn, StoreId), tvf_OrderFilter (inline TVF, SCHEMABINDING=ON)
    SECURITY POLICY OrdersFilter ON sales.Orders (filter predicate tvf_OrderFilter)
    GRANT SELECT ON sales.Orders TO [testuser-a@...]     -- per gebruiker
    GRANT SELECT ON finance.MarginByProduct TO [finance-testuser@...]
    (geen GRANT op security.* voor testgebruikers; geen GRANT ... db-brede rollen)
  Item-permissies testgebruikers: alleen "Read" (geen ReadData, geen ReadAll, geen werkruimterol)
```

Predicate-principe (concept, te testen):
`WHERE UserUpn = USER_NAME() AND StoreId = <rij>.StoreId` via een join op `security.UserScope`. Collatie: CI-collatie of `LOWER()` gebruiken; zie A en E.

Gateway-flow (per MCP-tool-call, geen app-fallback):
1. Valideer het inkomende token: `aud` = gateway-app, issuer, tenant. Afwijzen bij fout.
2. OBO naar `https://database.windows.net/.default` met `requested_token_use=on_behalf_of` en client-assertion (MI-federated JWT, **te testen**).
3. Verbind met `mssql-python` (`token_provider` per gebruiker) of `pytds` (`access_token_callable`). Geen gedeelde pool.
4. Faal gesloten bij elk OBO- of SQL-token-probleem. Geen app-only SQL-verbinding.

---

## 4. Onbekend / blokkers (volgorde van risico)

1. **MI-federated client assertion in OBO**: niet als combinatie in primaire docs bevestigd. Go/no-go-test.
2. **Exacte consent-permissie voor database.windows.net** in de gateway-app-registratie: naam en admin-consent-vereiste niet op primaire pagina gevonden.
3. **`SUSER_SNAME()` in Fabric-RLS**: niet gedocumenteerd op opgehaalde pagina's. Gebruik `USER_NAME()`.
4. **Hoofdletter/collatie van UPN-vergelijking**: default collatie is case-sensitive. Test met de echte tokenclaim.
5. **GRANT naar Entra-groepen**: niet bevestigd. Lab: per gebruiker.
6. **F2-capaciteitsconcurrency** en throttling-limieten: niet opgehaald. Risico voor de Coffee-demo.
7. **Tenantsetting "Users can create Fabric items"** voor de creator: niet geverifieerd.
8. **Owner-identiteit (delegated mode)** voor gedeelde warehouse-ontvangers: testen dat verwijderen van de eigenaar de toegang niet breekt, of documenteren als beperking.
9. **mssql-python BYO-token-struct**: als we `attrs_before` gebruiken i.p.v. `token_provider`, is de exacte bytes-struct niet op de README vastgelegd. Gebruik `token_provider` of lees de code.
10. **pytds-onderhoud**: niet gecontroleerd.
11. **Power BI `executeQueries` workspace-eis**: conflict tussen twee Learn-pagina's; testen als dit pad later wordt overwogen.
12. **Databricks**: uitgesteld. Bekend: Entra-token voor Databricks is documentair beschreven (resource `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`, delegated `user_impersonation`), maar Databricks raadt handmatige Entra-tokens af; account-wide federation vertrouwt een hele IdP. Genie One MCP en `system.ai.dbsql` (beta) zijn Unity Gateway-securables; of een Entra-OBO-token door dat endpoint wordt geaccepteerd, is onbekend.

---

## 5. Gebruikte primaire bronnen

- Fabric Warehouse Create: https://learn.microsoft.com/en-us/rest/api/fabric/warehouse/items/create-warehouse
- Fabric Warehouse T-SQL surface area: https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area
- Fabric SQL granular permissions: https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions
- Fabric Warehouse sharing en permissies: https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions
- Fabric Warehouse security-overzicht: https://learn.microsoft.com/en-us/fabric/data-warehouse/security
- Fabric RLS: https://learn.microsoft.com/en-us/fabric/data-warehouse/row-level-security
- Fabric Entra-authenticatie voor Warehouse: https://learn.microsoft.com/en-us/fabric/data-warehouse/entra-id-authentication
- Fabric REST-scopes: https://learn.microsoft.com/en-us/rest/api/fabric/articles/scopes
- Fabric licenties/SKU: https://learn.microsoft.com/en-us/fabric/enterprise/licenses
- Fabric features per SKU: https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features
- Entra OBO: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow
- MCP authorization 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- mssql-python: https://github.com/microsoft/mssql-python (README + `mssql_python/auth.py`, `mssql_python/db_connection.py`)
- pytds: https://github.com/denisenkom/pytds (README.rst, `src/pytds/__init__.py`)
- Power BI executeQueries (groep): https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries-in-group

---

## 6. Follow-up: alleen item Read verlenen op een Warehouse (2026-10-16)

### Vraag 1: programmatische grant van alleen Read

**Conclusie: geen gedocumenteerde publieke REST-API om een Warehouse-itempermissie (Read, ReadData, ReadAll) te verlenen.** Nog niet op een primaire pagina gevonden: een POST/PUT op item-permissies voor Warehouse.

- Sharing van een Warehouse is volgens de primaire pagina **alleen via de gebruikersinterface**. Citaat (sectie "Limitations"): "Currently, sharing a Warehouse is only available through the user experience." https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions
- Het enige itemgerichte API-eindpunt dat gevonden is, is **GET** en alleen voor admins (Preview): `GET https://api.fabric.microsoft.com/v1/admin/workspaces/{workspaceId}/items/{itemId}/users`. Vereist `Tenant.Read.All` of `Tenant.ReadWrite.All`, Fabric-admin of SP, max 200 requests/uur. Het **leest** permissies; het verleent niets. https://learn.microsoft.com/en-us/rest/api/fabric/admin/items/list-item-access-details
  - Bruikbaar voor verificatie na de UI-share. Let op: het voorbeeld in de docs toont een Notebook, niet een Warehouse. Verwacht voor een Read-only share: `permissions: ["Read"]` en `additionalPermissions: []`.
- **Niet gebruiken:** `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/roleAssignments` met body `{"principal":{"id":"<user-object-id>","type":"User"},"role":"Viewer"}`. Dit bestaat wel (Workspace.ReadWrite.All, caller member+), maar het is een **werkruimterol** en geldt voor alle items. Viewer geeft volgens de standaardtabel op de share-pagina **Read én ReadData** op alle Warehouses. https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/add-workspace-role-assignment ; https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions ("Manage permissions" tabel)

**Exacte portalstappen (Read-only share):**
1. Log in met een account dat **Admin of Member** is in de werkruimte (vereist voor delen).
2. Open de werkruimte. Zoek het Warehouse. Kies **Share** via de snelle actie in de rij, of via **More options → Share**.
3. Vul het UPN van de testuser in. Laat alle extra permissies **uit**: ReadData, ReadAll en "Subscribe to events". Zonder extra permissies krijgt de ontvanger alleen **Read** ("connect"). Optioneel: notificatiemail uit.
4. Klik **Grant access**.
5. Controle: Warehouse → **More options → Manage permissions**. De testuser moet alleen item-Read tonen. Met de knoppen **Remove ReadData** of **Remove ReadAll** verwijder je een per ongeluk meegegeven extra recht. Zie dezelfde pagina.
6. Wacht tot 2 uur voor propagatie (volgens de pagina; nieuwe permissies zijn direct zichtbaar in Manage permissions). De testuser moet opnieuw inloggen.
Bronnen: https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions ("Share a warehouse", "Fabric security roles", "Manage permissions", "Limitations")

### Vraag 2: geeft GRANT CONNECT/GRANT SELECT automatisch item Read?

**Nee.** Een T-SQL-`GRANT` verleent geen Fabric-itempermissie. Het delen (of een werkruimterol) is verplicht.

- "For a user to connect to a Fabric warehouse or SQL analytics endpoint, you must assign them to a workspace role or grant them the item **Read** permission. Without **Read** permission at a minimum, the connection fails." https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions ("Configure user granular permissions")
- Warehouse-connectiviteit vereist minimaal item-Read. https://learn.microsoft.com/en-us/fabric/data-warehouse/security ("Warehouse access model")
- `GRANT`/`DENY` maakt de databasegebruiker automatisch aan, maar "The user can't connect until they also have sufficient workspace-level rights." https://learn.microsoft.com/en-us/fabric/data-warehouse/sql-granular-permissions ("CREATE USER limitation")
- Read op het item is de CONNECT-equivalent. De share-pagina zegt: "Read ... equivalent of CONNECT permissions in SQL Server." Het is geen T-SQL-recht. https://learn.microsoft.com/en-us/fabric/data-warehouse/share-warehouse-manage-permissions ("Fabric security roles")
- Item-Read geeft volgens de Fabric-permissiepagina alleen metadata en connect, **geen toegang tot onderliggende data in SQL of OneLake**. Data komt van de T-SQL-GRANT's. https://learn.microsoft.com/en-us/fabric/security/permission-model ("Item permissions")
- **Onbekend:** of `GRANT CONNECT` als T-SQL-statement bestaat in Fabric Warehouse. Niet gevonden in de opgehaalde pagina's. Niet gebruiken.

**Volgorde voor de lab:** (a) T-SQL `GRANT SELECT` op de specifieke objecten, (b) portal-Share met alleen Read, (c) testuser opnieuw laten inloggen. De volgorde van (a) en (b) is niet kritisch, maar de pagina raadt aan eerst SQL te definiëren.

**Impact op het ontwerp:** een gebruiker met alleen Read en zonder GRANT kan verbinden maar niets lezen. Een gebruiker met alleen GRANT maar zonder Read kan niet verbinden. Beide zijn nodig.

**Te vermijden:** Viewer-werkruimterol (geeft ReadData op alle tabellen). Member/Contributor (geeft meer). ReadData-share (alle tabellen leesbaar).
