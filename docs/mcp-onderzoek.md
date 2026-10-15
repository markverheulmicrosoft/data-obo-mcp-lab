# Onderzoek: een kleine MCP-gateway voor Fabric en Azure Databricks

Onderzocht op 15 oktober 2026 met een GPT-6 Astra-onderzoeksagent.
Dit document beschrijft opgehaalde primaire bronnen, geen geslaagde tenanttest.
Het uitvoeringsplan staat in [mcp-plan.md](mcp-plan.md).

## Conclusie

**Beide platformen hebben gedocumenteerde remote MCP-endpoints.**
Onze gateway kan daarom aan de voorkant MCP-server en aan de achterkant
MCP-client zijn. Foundry is niet verplicht in dit pad. Wat niet automatisch
meekomt, is de juiste downstream-gebruikersidentiteit: die vereist per bron
een ondersteunde tokenflow, toestemming en aantoonbare accountbinding.

Houd de eerste implementatie klein: een Python-proces, een vast MCP-endpoint,
een centrale auth/policy-controle en expliciete wrappers voor maximaal de twee
gekozen brontools. Geen generieke platformlaag of automatische discovery.

## 1. Fabric: direct MCP is beschikbaar

De Fabric-documentatie beschrijft dit endpoint voor een gepubliceerde data agent:

```text
https://api.fabric.microsoft.com/v1/mcp/workspaces/{WorkspaceId}/dataagents/{DataAgentId}/agent
```

Het endpoint exposeert een tool en ondersteunt een externe client via Streamable
HTTP, `initialize`, `tools/list` en `tools/call`. Iedere request heeft een
**Fabric-token** nodig. De beschreven resource voor tokenverwerving is
`https://api.fabric.microsoft.com/.default`. Het endpoint ondersteunt geen DCR
en geen client identity metadata. [1]

De bron formuleert het expliciet: "your client must use its own authentication
flow to obtain a Fabric token and attach it to each request". [1]

De directe MCP-documentatie noemt gebruikers en service principals als mogelijke
tokenidentiteiten, met vereiste toegang tot het doel. Voor onze eindgebruikersproef
kiezen we de gebruikersroute. Dit credentialcontract moet niet worden verward
met de afzonderlijke native Foundry Fabric-tool, waarvoor andere beperkingen
kunnen gelden. [1]

Het voorbeeld met `AzureCliCredential` bewijst de identiteit van de ingelogde
ontwikkelaar, niet automatisch die van de gebruiker van onze gehoste gateway.
Entra documenteert OBO, maar de opgehaalde Fabric MCP-pagina bewijst niet de
complete externe gatewayconfiguratie met exacte delegated permissions en consent.
**Onze gateway-OBO-route blijft daarom een go/no-go-experiment.** [1][3]

De eerdere externe Fabric Python-clientpagina waarschuwt inmiddels voor de
beëindigde Assistants-API-route en verwijst naar het MCP-endpoint. Kopieer de
oude externe SDK-demo daarom niet als nieuwe baseline. [2]

## 2. Azure Databricks: managed MCP plus eigen OAuth-voorwaarden

De Azure-documentatie noemt voor Databricks-provided MCP-services:

```text
https://<workspace-hostname>/ai-gateway/mcp-services/<mcp-name>
```

Voorbeelden van genoemde MCP-namen zijn `system.ai.genie_one_mcp` en
`system.ai.dbsql`. De documentatie behandelt deze als Unity Catalog-objecten
via Unity Gateway, met MCP-aanroeprechten en policies. De beschreven OAuth-scope
voor deze endpoints is `ai-gateway`. [4]

Daarnaast staat het legacy Genie-workspace-endpoint beschreven:

```text
https://<workspace-hostname>/api/2.0/mcp/genie/{genie_space_id}
```

Daarbij hoort scope `genie`. Dit legacy-pad omzeilt Unity Gateway en biedt niet
dezelfde MCP-level grants, policies en tracing. Onderliggende resourcepermissies
blijven relevant. Kies bewust een route; behandel deze endpoints niet als
uitwisselbaar. [4]

Azure Databricks documenteert een vooraf geregistreerde OAuth-app met exacte
redirect-URI voor custom MCP-clients. Een server-side confidential client kan
een veilig opgeslagen secret gebruiken. De gebruikersroute vraagt de passende
scope en eventueel `offline_access`; M2M gebruikt daarentegen een serviceprincipal.
De bron zegt: "Azure Databricks MCP endpoints don't support dynamic client
registration, so use a client that accepts a preconfigured client ID." [5]

Dat ondersteunt een **aparte Databricks-gebruikerslogin**, veilig gekoppeld aan
de Entra-gebruiker van onze gateway. Het bewijst niet dat een willekeurig
gateway-Entra-token rechtstreeks via OBO naar dezelfde gebruiker wordt vertaald.
De koppeling tussen beide accounts is een te beveiligen onderdeel, geen
vrij door de client te kiezen argument. [3][5]

Databricks documenteert ook OBO voor agents op **Model Serving**, met expliciete
inschakeling en `ModelServingUserCredentials()`. Dat is een hosted-runtimefunctie,
niet automatisch beschikbaar in onze externe Python-gateway. Dezelfde
documentatie kan met "automatic authentication passthrough" juist een beheerde
serviceprincipal bedoelen; die termen mogen niet worden gelijkgesteld. [7]

De Genie API biedt als alternatief REST/SDK-toegang voor eigen applicaties en
bespreekt OAuth U2M apart van M2M. Dit blijft een mogelijke directe
adapterroute waar nodig, maar een Genie-antwoord zelf kan nog agentgegenereerd
zijn. [8]

## 3. Wat MCP-authenticatie wel en niet regelt

De opgehaalde actuele MCP-authorization-specificatie verwijst naar versie
`2026-07-28`. Controleer de gebruikte SDK/clientversie bij implementatie. [6]

Een beveiligde remote MCP-server publiceert Protected Resource Metadata en
biedt resource-/authorization-server-discovery. Een ongeauthenticeerde aanvraag
krijgt een bruikbare `401`/`WWW-Authenticate`-challenge met `resource_metadata`.
Het bedoelde resultaat is een access token voor **onze gatewayresource**. [6]

Normatief: "MCP servers MUST validate that access tokens were issued specifically
for them as the intended audience". Een token voor Graph, Fabric of een andere
MCP-server wordt dus niet automatisch geaccepteerd bij onze gateway. Evenmin
mag ons gateway-token blind worden gebruikt bij een downstream API. Entra
beschrijft daarvoor een afzonderlijke token A/API A naar token B/API B-uitwisseling. [3][6]

De huidige specificatie behandelt preregistratie en CIMD; DCR is optioneel en
deprecated. Sommige productclients bieden ondertussen nog DCR-gebaseerde
configuratie. Kies voor deze kleine proef vooraf geregistreerde clientinformatie
en bewijs de combinatie van discovery, `resource`, scopes, audience en redirects
in de werkelijk gekozen clients. Een standaardmogelijkheid is niet automatisch
door iedere client of Entra-configuratie geïmplementeerd. [6][9]

Bedrijfsautorisatie blijft onze implementatieverantwoordelijkheid:

- Identiteit komt uit gevalideerde claims, niet uit model-/toolargumenten.
- `tools/list` filteren en **iedere `tools/call` opnieuw autoriseren**.
- Een bekende of geraden verborgen tool blijft verboden.
- Bronhost, tenant en credential komen uit vertrouwde serverconfiguratie.
- Geen app-only-, developer- of makerfallback bij ontbrekende user-consent.
- Sessies, tokens en downstream-conversaties worden per gebruiker gescheiden.

Dit zijn expliciete ontwerp-/testeisen, geen functies die een MCP-tooldecorator
automatisch toevoegt.

## 4. Clients: niet iedere "Copilot" is hetzelfde

Copilot Studio documenteert remote MCP en drie OAuth-configuratiepaden:
dynamic discovery met DCR, dynamic met handmatige endpoints, en een handmatige
configuratie met clientgegevens, OAuth-URL's en scopes. De bron beschrijft
individuele gebruikersauthenticatie en een consent card in de agentchat. [9]

Voor onze proef kiezen en testen we **eindgebruikersauthenticatie**. Het bestaan
van een werkende connector of eerder aangemaakte connection bewijst niet dat
de huidige runtime-gebruiker wordt doorgegeven. Controleer gatewayclaims en
bronaudit; een vaste makerconnection voldoet niet aan dit experiment.

De Microsoft 365 Agents SDK documenteert user OAuth en OBO los van de
agent-serviceverbinding. Dat zijn bouwstenen voor een custom-engine agent,
niet automatisch een volledig aangesloten MCP-discoveryclient. [10]

**Scout blijft onbekend:** product en versie zijn nog niet bevestigd. Daarom
zijn Streamable HTTP, OAuth, preregistratie en accountwissel daar niet bewezen.
We stellen Scout niet gelijk aan Copilot CLI of VS Code.

## 5. Python: een MCP-SDK is voldoende; FastAPI is optioneel

De officiële Python MCP SDK kan zelfstandig Streamable HTTP serveren en als
ASGI-app worden gemount, bijvoorbeeld in FastAPI. Bij mounting moet de host
de MCP-session-manager-lifecycle beheren. Een aparte FastAPI-laag is dus niet
noodzakelijk om twee downstream-tools aan te roepen. [11]

Let op versies: de opgehaalde v2-migratiedocumentatie meldt onder meer de
hernoeming `FastMCP` naar `MCPServer` en verwijdering van `streamablehttp_client`.
Oudere Fabric-snippets gebruiken nog oude clientnamen/veldnotatie. Kies en pin
bewust een SDK-versie; meng geen huidige installatie met oude snippets. [1][12]

Aanbevolen vorm: expliciete toolwrappers, één policyfunctie en per bron een
MCP-client met een correct verworven gebruikerstoken. Geen handgeschreven
JSON-RPC-router, geen generieke `invoke_any_url`-tool en geen eigen cryptografie.

## 6. Klein toepassen wat Uber beschrijft

Uber beschrijft een gateway met zowel API-naar-MCP-adapters als proxying van native
MCP-servers. Centrale tool-level autorisatie en een aparte tokenuitwisseling
voor third-party integraties passen bij deze proef. [13]

Registry, AutoCrawler, service mesh, code mode en dynamische tool-discovery lossen
Ubers schaalprobleem op; wij hebben twee bronnen en een vaste allowlist nodig.
Het intern doorgeven van een user-token naar hun integratieservice beschrijft
hun vertrouwensmodel, niet algemene toestemming om externe API-audiences te
negeren. Ook response projection en redaction vervangen geen toegangscontrole. [13]

## 7. Bewijs en aanbevolen volgorde

| Route | Vastgesteld | Nog te bewijzen |
|---|---|---|
| Studio naar gateway | Gedocumenteerde remote MCP-/user-OAuth-bouwstenen | Werkelijke eindgebruiker en gateway-audience |
| Scout naar gateway | Product niet bekend | Clientcompatibiliteit |
| Gateway naar Fabric MCP | Direct endpoint, Fabric-token per request | Externe gateway-OBO, permissions/consent, bronprincipal |
| Gateway naar Databricks MCP | Endpoints en custom user-OAuth gedocumenteerd | Veilige accountbinding en bronprincipal |
| Externe Entra-OBO naar Databricks | Niet bewezen met deze bronnen | Ondersteunde product-specifieke uitwisseling |

Eerst echte MCP-auth en toolautorisatie met synthetische resultaten; daarna één
Fabric-tool. Test de Fabric-OBO-route vóór UI-werk. Voeg vervolgens één
Databricks-tool toe via aantoonbare user OAuth, tenzij externe Entra-OBO eerder
daadwerkelijk is bevestigd. Twee echte accounts en een gecontroleerde
referentieclient vormen de baseline zolang Scout niet is geïdentificeerd.

**Belangrijke bewijslimiet:** Fabric data agent en Genie zijn zelf agents.
Hun MCP-resultaat kan al door een upstream-LLM zijn verwerkt. Inspectie van het
gatewayresultaat bewijst hooguit wat de client-LLM ontvangt, niet dat geen
enkele upstream-LLM ooit verboden data zag. Gebruik bronprincipal-audit en
bronquerybewijs; kies desnoods een deterministische bron-API voor die specifieke
RLS/OLS-test, of beperk eerlijk de claim.

Negatieve tests: geen token/verkeerde audience/verlopen token, expliciet verboden
tool, vervalste gebruiker in argumenten, verschillende rijen/kolommen per account,
dezelfde rechten via een tweede client, gebruikerswissel, ingetrokken rechten en
consentfouten. Bij verboden tooltoegang mag geen downstream-call plaatsvinden.
Beoordeel response-data en bronidentiteit, niet alleen nette chatbottekst.

De gateway controleert alleen verkeer dat erdoorheen loopt. Hij wist geen reeds
geopenbaarde gegevens en blokkeert geen andere rechtstreeks toegestane bronroutes.

## Primaire bronnen

1. [Fabric data agent MCP-endpoint](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
2. [Fabric externe Python-client en MCP-migratie](https://learn.microsoft.com/en-us/fabric/data-science/consume-data-agent-python)
3. [Entra OAuth OBO-flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
4. [Azure Databricks-provided MCP-services](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/built-in-mcp-services)
5. [Azure Databricks MCP-authenticatie en networking](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/authentication-networking)
6. [MCP authorization, specificatie 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
7. [Azure Databricks Model Serving-agentauthenticatie](https://learn.microsoft.com/en-us/azure/databricks/agents/custom-agents/model-serving/agent-authentication-model-serving)
8. [Azure Genie conversation API](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/conversation-api)
9. [Copilot Studio: bestaande MCP-server toevoegen](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent)
10. [Microsoft 365 Agents SDK: user OAuth](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/agent-oauth-configuration)
11. [Python MCP SDK: ASGI en zelfstandig hosten](https://py.sdk.modelcontextprotocol.io/run/asgi/)
12. [Python MCP SDK v2-migratie](https://py.sdk.modelcontextprotocol.io/migration/)
13. [Uber: Designing MCP Gateway](https://www.uber.com/us/en/blog/designing-mcp-gateway/)
