# Het live Fabric-rechtenlab

Dit is een echte webclient voor de MCP-gateway, geen persona-simulator.
De gateway vraagt via OBO een SQL-token voor de ingelogde gebruiker aan.
Fabric Warehouse voert vaste, read-only SQL uit en beslist zelf over tabellen
en rijen. Er zit nog geen LLM of Fabric data agent in deze route.

De bron bevat **zes mockorders die echt in Fabric zijn opgeslagen**. Dit is
geen productiedata. De twee oude in-memory orders van de Rights Test MCP zijn
een afzonderlijke controlegroep; ze worden nooit als Fabric-resultaat gebruikt.
Databricks is op verzoek uitgesteld tot na deze Fabric-proef.

## Openen en aanmelden

Open de root-URL van je uitgerolde gateway, dus zonder `/mcp`. Kies **Aanmelden**
en meld aan met een van de twee toegewezen testaccounts, niet met de beheerder.
De browser gebruikt Entra authorization code + PKCE. De aparte SPA-app heeft
alleen een exacte HTTPS `/auth.html`-redirect, geen clientsecret.

De webapp begint met drie stappen: **aanmelden, orders of marges kiezen, ander
account proberen**. Account en tools worden automatisch na aanmelden gecontroleerd.
Je ziet de verwachting naast het echte resultaat. Techniek, tijdstip, duur,
bronidentiteit uit SQL `USER_NAME()` en JSON staan achter **Waarom dit resultaat?**.
De twee aanvullende proeven en uitleg staan achter **Extra controles en uitleg**.
Een architectuurpijl betekent niet dat die stap succesvol is uitgevoerd.
Alleen een ontvangen resultaat of expliciete fout telt als waarneming.
Bij afmelden/accountwisseling worden eerdere resultaten gewist.

## De vier experimenten

Orders en marges zijn de twee hoofdknoppen. Identiteit en de extra gatewaycheck
blijven beschikbaar onder de ingeklapte extra controles.

| Experiment | Noord-account | Finance-account | Wie beslist? |
|---|---|---|---|
| Identiteit en tools | `North.Reader` | `Finance.Reader` | Gateway, uit gevalideerde JWT |
| `fabric_orders` | 3 Noord-orders | Alle 6 orders | Fabric SQL RLS |
| `fabric_margins` | `SOURCE_FORBIDDEN` | 6 margeregels | Fabric SQL SELECT/DENY |
| `fabric_finance_only` | `TOOL_FORBIDDEN`, geen broncall | 6 margeregels | Eerst gatewayrol, daarna Fabric |

Dit zijn verwachtingen voor de **begintoestand**, geen alvast geslaagde tests.
Een timeout, ontbrekende consent of onbereikbare bron is nooit bewijs van een
correcte rechtenweigering. Een gebruiker zonder labrol kan identiteit inspecteren,
maar geen datatool uitvoeren. De gateway-app vereist daarnaast een toegewezen
Entra-applicatierol voordat Entra een gebruikerstoken uitgeeft.

## Wat voegt een Teams-agent toe?

De webclient test nu rechtstreeks de MCP en bronrechten, zonder LLM. Een
Teams-agent is de volgende gebruikersproef, niet een vervanging van bronautorisatie:
persoonlijke Teams-chat -> agent met gebruikersaanmelding -> dezelfde MCP ->
OBO -> Fabric. De botidentiteit alleen is geen bewijs van de eindgebruiker.
De agent moet een delegated token voor de gateway verkrijgen, geen Teams-token
met een verkeerde audience doorgeven en geen gebruikers-ID uit prompttekst vertrouwen.

Voor meerdere kanalen past de Microsoft 365 Agents SDK; voor Teams-only is
de Teams SDK een alternatief. De SDK is geen gehoste AI-dienst: een eigen agent
wordt gehost in Azure en kan een Foundry-model gebruiken. De oude Bot Framework
SDK wordt niet meer onderhouden; nieuwe code moet daarop niet gebaseerd worden.
Deze Teams-koppeling is nog niet gebouwd of getest.

Begin met twee afzonderlijke **1-op-1-chats** en dezelfde vragen: orders, marges,
en een expliciet verzoek om toch verboden data te tonen. Vergelijk de ontvangen
bronresultaten/rechten, niet de exacte antwoordformulering van het model.
Gesprekscontext en tokens moeten per gebruiker gescheiden zijn. Een kanaalantwoord
is leesbaar voor anderen en is dus geen veilige eerste proef voor persoonlijke data.

Bronnen: [Agents SDK](https://learn.microsoft.com/microsoft-365/agents-sdk/agents-sdk-overview),
[Teams-gebruikersauthenticatie](https://learn.microsoft.com/microsoftteams/platform/teams-sdk/in-depth-guides/user-authentication),
[Bot Framework lifecycle](https://learn.microsoft.com/azure/bot-service/bot-builder-howto-add-media-attachments?view=azure-bot-service-4.0).

## Data rechtstreeks in Fabric bekijken

Gebruik na aanmelden de bronlink in de webapp. Het aparte Warehouse heet
`velora_rights`, in de eigen labworkspace. Er zijn twee tabellen:
`dbo.Orders` en `dbo.Margins`. De SQL-editor kan bijvoorbeeld uitvoeren:

```sql
SELECT USER_NAME() AS source_principal;
SELECT id, region, product, revenue FROM dbo.Orders ORDER BY id;
SELECT id, region, margin FROM dbo.Margins ORDER BY id;
```

Meld ook hier aan als dezelfde testgebruiker. Een beheerder is geen vervanging
voor een gebruikersproef. De bron-RLS geldt voor SQL-toegang, niet als algemene
garantie voor OneLake/Spark of andere toegangspaden. Daarom krijgen testers
geen ReadAll, workspace-rol of andere brede bronrechten.

## De vier afzonderlijke rechtenlagen

1. **Entra/gateway:** de approl geeft toegang tot specifieke MCP-tools.
2. **Fabric item Read:** de gebruiker mag met dit Warehouse verbinden.
3. **SQL SELECT/DENY:** de gebruiker mag de specifieke tabel lezen.
4. **SQL RLS:** `Security.RegionFilter` filtert beide tabellen met
   `Security.UserScope` en de door SQL vastgestelde `USER_NAME()`.

De gateway filtert deze Fabric-rijen niet nogmaals op een meegegeven rol of regio.
Een request kan geen willekeurige SQL, bronhost, account of regio kiezen.
Elke SQL-call opent en sluit een eigen verbinding; pooling en antwoordcaching
zijn uitgeschakeld. Een managed identity bewijst alleen de gateway-app bij
de OBO-uitwisseling; zij is niet de data-identiteit.

## Zelf met bronrechten spelen

Voer deze wijzigingen uitsluitend als labbeheerder uit op **dit aparte Warehouse**.
Vervang `<noord-upn>` door het eigen testaccount, niet een echte productiegebruiker.
Verander een laag per keer en herhaal daarna de webproef.

Verplaats het Noord-account naar Zuid zonder de gatewayrol te wijzigen:

```sql
UPDATE Security.UserScope SET region = 'Zuid'
WHERE principal_name = '<noord-upn>';
```

Dezelfde `fabric_orders`-call hoort nu Zuid te tonen. De beginverwachting van de
webapp wijkt dan bewust af. Herstel met `region = 'Noord'`.

Weiger vervolgens de ordertabel, terwijl de gateway de tool blijft toestaan:

```sql
DENY SELECT ON OBJECT::dbo.Orders TO [<noord-upn>];
-- Herstellen:
GRANT SELECT ON OBJECT::dbo.Orders TO [<noord-upn>];
```

Voor de itemlaag kun je in Fabric **Manage permissions** alleen het item Read
intrekken en later Read-only opnieuw delen. Geef niet als snelle oplossing
Viewer: dat geeft ook ReadData op andere items. Rol-/itemwijzigingen kunnen
vertraagd doorwerken; Microsoft noemt voor delen tot twee uur.
Bij Entra-rolwijzigingen kunnen reeds uitgegeven tokens blijven gelden:
meld af, meld opnieuw aan en vergelijk het nieuwe gedrag.

## Inrichten en herhalen

De `.env` blijft lokaal. Gebruik een bestaande actieve Fabric-capacity:

```powershell
.\.venv\Scripts\python.exe infra\fabric_setup.py --capacity <capacity-id>
.\.venv\Scripts\python.exe infra\fabric_setup.py --capacity <capacity-id> --apply
```

Het script maakt alleen een nieuwe eigen workspace en Warehouse, zaait de
zes rijen, zet RLS en SQL-grants, en slaat de concrete bron-ID's lokaal op.
Het neemt geen bestaande workspace op naam over. Het geeft geen workspace-rollen
aan testers en reset bij een gewone herhaling geen rechtenexperimenten.

Andere testaccounts instellen: werk de twee `TEST_*_USER_ID`-velden in je private
`.env` bij en gebruik expliciet `--apply --configure-users` bij `fabric_setup.py`.
Dit stelt de gekozen Noord-/Finance SQL-grants in zonder andere accounts te
verwijderen; afwijkende bestaande rijtoewijzingen worden geweigerd. De gewone
gatewaydeploy verzorgt hun Entra-labrollen. Deel daarnaast het Warehouse
Read-only met de nieuwe accounts via de portal.

**Eenmalige portalstap:** deel dit Warehouse met beide testaccounts met alleen
Read. Laat ReadData, ReadAll, Build, Monitor, Audit en Reshare uit; e-mail is niet
nodig. De publieke Warehouse-API ondersteunt dit delen niet. Controleer daarna
Manage permissions. Een SQL GRANT alleen geeft geen item Read.

Webclient en gateway herdeployen:

```powershell
npm ci
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe infra\deploy.py --apply
```

Dit bouwt de browserbundels lokaal en neemt alleen de expliciete broncode/assets
mee naar ACR. Geen `.env`, admin-token, deploymentstate of bronresultaten.
De Azure-container gebruikt MI-federatie en delegated SQL-consent; nooit de
beheerlogin of een SQL-serviceprincipal als fallback.

Zie `live-bronnen.md` voor primaire bronnen en productbeperkingen. Een geslaagde
uitrol en broninrichting bewijzen nog niet de volledige browser/OBO-keten:
voer daarvoor beide echte gebruikersproeven uit.
