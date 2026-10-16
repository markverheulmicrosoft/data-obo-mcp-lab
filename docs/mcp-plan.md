# MCP als centrale toegangslaag - ontwerp en echte proef

**Status: gateway, Rights Test MCP en referentieclient zijn geimplementeerd.**
Zie [bouwen.md](bouwen.md) voor de werkende lokale proef en de nog benodigde
echte Entra-proef. Beide MCP-apps draaien in Azure; discovery en anonieme weigering
zijn live gecontroleerd. De twee-gebruikers/OBO-proef staat nog open.
De 3P-routes blijven ontwerp.
De offline browserdemo is geen bewijs voor deze opzet en wordt hiervoor niet uitgebreid.
Productondersteuning en primaire bronnen staan in [mcp-onderzoek.md](mcp-onderzoek.md).
Dit document legt vast wat onze eigen implementatie moet afdwingen.

## Bewust klein houden

De gateway en bronadapters in de tekening zijn **logische onderdelen van hetzelfde
Python-proces**, geen voorstel voor losse microservices. De eventuele webapp staat
erbuiten en is niet nodig om MCP-routing en rechten te bewijzen.

**Naamgeving in het diagram:** `MCP-clients / adapters` zijn onze uitgaande
clientcode binnen de eigen gateway. `Fabric data agent MCP` en `Databricks managed
MCP` zijn de externe, door de bronleverancier aangeboden **3P MCP-servers**.
De adapters roepen die servers aan; ze zijn niet zelf de 3P MCP's. Die externe
servers benaderen vervolgens de onderliggende data/API's. Er komt dus geen
extra adapterservice bij.

Daarnaast voegen we in het ontwerp **een custom Rights Test MCP** toe: een
tweede kleine Python-server met een eigen beschermd MCP-endpoint. Dat is geen
extra adapter, maar een gecontroleerde downstream-bron om rechten te testen.
Fabric en Databricks blijven de twee echte productintegraties.

Eerste rechtenproef: een gateway-endpoint, twee expliciete read-only tools op
de custom test-MCP, een kleine vaste routingtabel en twee testaccounts.
Authenticatie gebruikt ondersteunde libraries;
we schrijven geen eigen OAuth-server of JWT-cryptografie. Geen registrydatabase,
autodiscovery, dynamische toolgenerator, cache, Kubernetes of uitgebreide portal.

De voorkeur is een Python MCP-SDK voor MCP-server en upstream MCP-client.
FastAPI is optioneel voor gewone HTTP-routes of het samen hosten van endpoints,
niet een vervanging voor MCP-protocolafhandeling. De precieze library/API kiezen
we aan de hand van de geverifieerde bronnen, niet via handgeschreven JSON-RPC.
Een gewone downstream REST-call vereist op zichzelf geen extra FastAPI-server.

Een kleine voorgestelde indeling is `gateway.py` voor toelating/policy/toolrouting,
`backends.py` voor de twee concrete bronaanroepen en een testbestand voor negatieve
gevallen. Splits authenticatie alleen uit wanneer dat de werking leesbaarder maakt.
Voor de gecontroleerde downstream-proef komt er alleen `rights_mcp.py` bij,
met een paar synthetische records in het geheugen; geen aparte database of LLM.
De daadwerkelijke kleine indeling gebruikt `auth.py`, `gateway.py`,
`rights_mcp.py` en `client.py`; een aparte `backends.py` was nog niet nodig.

## 3P en custom zijn verschillende testdoelen

| Onderdeel | Wie maakt/beheert het? | Wat testen we ermee? |
|---|---|---|
| Eigen MCP-gateway | Wij | Inkomende identiteit, toolpolicy en routing |
| Custom Rights Test MCP | Wij, apart beschermd endpoint | Eigen bron-audience, gebruikersidentiteit en onafhankelijke data-/toolpolicy |
| Fabric/Databricks MCP | Bronleverancier | Dezelfde keten met echte productauthenticatie en bronrechten |

3P versus custom gaat over eigenaarschap en beheer. Het zegt op zichzelf niet
of een server user OAuth, app-only, RLS of kolombeveiliging gebruikt.
Voor alle drie gelden expliciete auth-/autorisatiecontroles.
Een geslaagde custom proef bewijst onze keten, niet automatisch de OBO-mogelijkheden
of beveiligingsgaranties van Fabric/Databricks.

## Wat we van Uber overnemen - en juist niet

Het [Uber-artikel over MCP Gateway](https://www.uber.com/us/en/blog/designing-mcp-gateway/)
beschrijft twee bruikbare runtime-paden: bestaande API's als MCP-tools aanbieden,
en native MCP-servers via een centrale gateway ontsluiten. Het beschrijft bovendien
tool-level autorisatie en apart token exchange voor externe integraties.
Dat past bij deze proef.

Wij nemen alleen de kern over: **authenticeer, autoriseer, routeer, gebruik het
juiste broncredential en log het besluit**. Met twee bronnen is een vaste
allowlist voldoende. Ubers Registry, AutoCrawler, distributed workflows,
Omni MCP-discovery, code mode en service mesh zijn schaaloplossingen die we niet
nodig hebben om onze vraag te beantwoorden.

Geef testgebruikers uitsluitend de benodigde leesrechten in de bron. Een
MCP-tool die zichzelf als read-only beschrijft, is geen vervanging voor die
bronrechten. Publiceer niet automatisch alle tools van een upstream-server.

De passage waarin Uber interne user-tokens doorstuurt naar een eigen
third-party-integratieservice beschrijft hun interne vertrouwensmodel. Dat is
geen bewijs dat ons MCP-access-token rechtstreeks als Fabric-/Databricks-token
mag worden gebruikt. Resource- en audiencegrenzen blijven expliciet.
Response projection of redaction kan resultaten verkleinen, maar vervangt
geen toolautorisatie of bronbeveiliging.

## De verduidelijkte vraag

Verschillende agents/clients, ieder met een eigen ingelogde gebruiker, verbinden met
**hetzelfde remote MCP-endpoint**. De MCP-server bepaalt welke tools beschikbaar
zijn, controleert iedere aanroep en benadert Fabric of Azure Databricks via een
ondersteund identiteitsmechanisme. De gebruiker mag alleen toegestane brondata ontvangen.

Foundry is hierbij eventueel een van de clients/orchestrators of een tussenliggende
dienst voor een specifieke tool, niet noodzakelijk het centrale component.
MCP levert een gemeenschappelijke toolinterface, geen automatisch federatief
databasesysteem en geen kant-en-klaar rechtenmodel.

## Wat we precies willen afdwingen

| Grens | Eigenaar van het besluit | Voorbeeld |
|---|---|---|
| Aanmelden | Identiteitsprovider + client | Dit is gebruiker A in tenant T |
| MCP-toegang | Onze server | Token is geldig, voor onze API en heeft vereiste scope |
| Tooltoegang | Onze server-side policy | A mag sales opvragen, B alleen onderhoud |
| Toolparameters | Onze server-side policy | A mag geen verboden regio of willekeurig bronendpoint kiezen |
| Bronaccount | Credential-/bronadapter | De bron ziet A via een ondersteunde flow, niet stilzwijgend een beheerapp |
| Rijen/kolommen | Beveiliging van het gekozen brontype | A ziet Noord zonder marge |
| Antwoord/context | Onze server en ontvangende client | Geen verboden data in MCP-resultaat, citaten of gedeelde context |

**Authenticatie** zegt wie de aanvrager is. **Autorisatie** zegt wat die mag.
Een geldige aanmelding geeft dus niet automatisch iedere tool of iedere rij vrij.

## De drie delen van de architectuur

### Links: clients met hun eigen login

Voorlopige clients: een Copilot-agent, Scout en een eenvoudige referentie-MCP-client.
De exacte Copilot-variant en het Scout-product zijn nog niet bevestigd. Daarom is
hun daadwerkelijke OAuth-compatibiliteit een open ingangstest, geen voldongen feit.

Een client moet via zijn ondersteunde aanmeldflow een access token voor **onze
MCP-resource** verkrijgen. Een login op de client zelf, een tenant-id in de
configuratie of een token voor een andere API is niet voldoende.
Een gedeelde API-key kan de client/app identificeren, maar bewijst niet welke
eindgebruiker de vraag stelt.

Gebruik voor de eerste proef vooraf bekende, expliciet toegestane clients.
Ga niet uit van automatische clientregistratie bij Entra. Controleer per client
transport, resource-discovery, OAuth-clientregistratie, redirects, gevraagde scopes
en accountwissel. Een werkende referentieclient bewijst niet dat Scout hetzelfde kan.

### Midden: onze remote MCP-server

Het endpoint gebruikt `/mcp`; de eigen URL volgt uit de deploymentconfiguratie.
De implementatie krijgt vier verantwoordelijkheden:

1. **Valideren:** handtekening via vertrouwde sleutels, issuer/tenant, audience,
   geldigheid, relevante delegated scope en waar nodig toegestane client.
2. **Autoriseren:** server-side policy op de geverifieerde principal en tool.
   Gebruikers- of groepsclaims uit modelargumenten zijn nooit gezaghebbend.
3. **Routeren:** vaste catalogus van read-only tools naar expliciet geconfigureerde
   bronadapters; geen door de gebruiker aangeleverde backend-URL.
4. **Verantwoorden:** gecorreleerde beslis-/bronlogs zonder credentials of onnodige data.

Gebruik `(tenant-id, object-id)` als interne Entra-gebruikerssleutel wanneer het
gekozen gevalideerde tokentype die claims levert. Niet de veranderlijke displaynaam
of e-mail. Ontbrekende vereiste claims geven een expliciete fout; geen standaardadmin.
Maak onderscheid tussen een delegated gebruiker en een application-only principal.

`tools/list` mag per gebruiker verschillen, maar **verbergen is geen beveiliging**.
Een client kan de naam van een tool kennen of raden en direct `tools/call` doen.
Daarom moet dezelfde autorisatie ook op iedere daadwerkelijke toolaanroep gelden.
Als we later MCP resources of prompts publiceren, moeten die afzonderlijk worden
gecontroleerd; een beveiligde toolcatalogus beschermt die niet automatisch.

Vereenvoudig de eerste proef met korte, read-only requests en zo weinig mogelijk
sessiestaat. Bij sessies bind je de eigenaar aan de geverifieerde principal;
een MCP-session-id is op zichzelf geen authenticatiebewijs. Valideer iedere
relevante HTTP-aanvraag, niet alleen `initialize`.

### Van onze adapters naar de externe 3P MCP-servers

De MCP-server is aan de voorkant een beschermde resource en aan de achterkant
een client van andere API's. Dat zijn **twee verschillende tokenrelaties**.
Een token dat voor onze MCP bestemd is mag niet blind naar Fabric/Databricks.

| Strategie | Wat we kunnen bewijzen | Wat dit niet bewijst |
|---|---|---|
| Gedelegeerde bronaanroep | De bron ziet de eindgebruiker en past diens rechten toe | Niet iedere API ondersteunt dezelfde OBO-flow |
| Apart gekoppelde gebruikers-OAuth | De bron ziet het gekoppelde bronaccount | Entra- en bronaccount zijn niet vanzelf dezelfde persoon |
| App-identiteit + harde gatewaypolicy | Onze gateway kan toegang beperken binnen eigen appbevoegdheden | De bron past niet automatisch de rechten van de eindgebruiker toe |

Voor het hoofddoel willen we het eerste pad, of een expliciet gekoppeld bronaccount
met aantoonbare binding. Een app-only vergelijking kan later nuttig zijn, maar
mag niet als bewezen bron-native OBO worden gepresenteerd.

Bij gekoppelde accounts moet de backend de accountbinding beheren en auditen.
Een client mag niet eenvoudig een ander Databricks-account-id invullen.
Geen gedeeld refresh token; per gebruiker en resource gescheiden tokenopslag.

De bronadapter kan een gewone API aanroepen, of als MCP-client met een upstream
managed MCP-server spreken. De tweede variant heeft nog steeds eigen upstream
authenticatie nodig. Een MCP-proxy is geen token-doorgeefluik.
Azure API Management kan later een extra gatewaylaag zijn, maar is niet vereist
voor de eerste proef en vervangt onze bron-/toolautorisatie niet automatisch.

## Een kleine, echte proef in plaats van een nieuwe simulator

### Fase 1 - bewijs beide grenzen met een custom Rights Test MCP

Maak een werkelijke remote MCP-gateway en een aparte Rights Test MCP, met
Entra-authenticatie en twee testgebruikers. De test-MCP is een eigen beschermde
resource met een andere audience en relevante delegated scope. Onze gateway
moet daarvoor een passend downstream-token verkrijgen; het inkomende gateway-token
of een onbewezen `X-User`-header is niet voldoende.

De custom MCP krijgt twee voorgestelde tools: `list_orders` en `read_margin`.
Enkele records staan in het geheugen, verdeeld over Noord/Zuid. Gebruiker A
ziet orders van Noord en geen marge; gebruiker B ziet beide regio's en mag
marges opvragen. Geef de test-MCP een eigen kleine server-side policy,
afgeleid van gevalideerde identiteit en eigen configuratie, niet van door
de gateway meegestuurde willekeurige rollen of gebruikersnamen.

Bij uitvoering zijn **protocol, login, tokenwisseling en autorisatie echt**,
maar de data synthetisch. De test-MCP filtert zelf rijen en velden voor het
antwoord. Er zit geen upstream-LLM tussen: we kunnen het precieze resultaat en
de beslissingen inspecteren. Dit bewijst nog geen database-native RLS/OLS;
het is een gecontroleerde server-side datatoegangsproef.

Test zonder taalmodel met een referentieclient en daarna met de twee gekozen
gebruikersclients. Zo is zichtbaar of de server weigert, onafhankelijk van wat
een model besluit aan te roepen.

Test de grenzen ook los van elkaar:

| Gatewaypolicy | Test-MCP-policy | Verwachting |
|---|---|---|
| Toestaan | Toestaan | Alleen de toegestane rijen/velden |
| Weigeren | Toestaan | Geen downstream-aanroep |
| Toestaan | Weigeren | Downstream weigert; geen beschermde data terug |
| Weigeren | Weigeren | Gateway stopt de call |

Gebruik hiervoor expliciete testpolicy-configuratie, niet rolwaarden in de prompt.
Controleer de test-MCP daarnaast met geautoriseerde directe testcalls:
een token voor de gateway moet falen; een geldig test-MCP-token zonder marge-recht
moet `read_margin` weigeren. Zo is de gateway niet de enige beveiligingsgrens.

### Fase 2 - sluit een echte Fabric-bron aan

Het [directe Fabric data agent MCP-endpoint](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server)
is bevestigd. Begin dus met een expliciete toolwrapper naar die bestaande MCP,
niet met het nabouwen van een Fabric-agent. Onze gateway gebruikt daarvoor
een Fabric-gebruikerstoken; het inkomende gateway-token is niet het bron-token.
Voer eerst de OBO-/consent-go-no-go-proef uit en controleer de bronprincipal.

Een gedocumenteerde Foundry Fabric-tool of `AzureCliCredential`-voorbeeld bewijst
niet automatisch de externe gatewayroute. Gebruik de oude Fabric externe
Assistants-SDK-route niet als nieuwe baseline; de actuele documentatie verwijst
naar MCP. Een deterministische semantic-model-API kan daarnaast dienen om
RLS/OLS afzonderlijk te bewijzen; label dat dan niet als data agent.

Gebruik twee gebruikers met andere rijrechten, dezelfde tool en dezelfde input.
Bewijs zowel de zichtbare rijen/kolommen als de effectieve bronprincipal.
Beheerrollen die RLS omzeilen horen niet bij deze eindgebruikersproef.

### Fase 3 - voeg Azure Databricks toe

Begin met een kleine Unity Catalog-dataset en een gekozen Genie-/query-API.
Verifieer voor precies die API de ondersteunde tokenflow, bronprincipal,
compute-eisen en row/column-policies. OBO voor een Databricks-hosted agent is niet
zonder meer bewijs voor onze externe MCP-server.

Stop de betreffende tool met een duidelijke fout wanneer de gebruikersroute
onmogelijk of onbewezen is. Maak hem niet ongemerkt werkend met een bredere PAT,
developerlogin of serviceprincipal.

De [Azure Databricks MCP-documentatie](https://learn.microsoft.com/en-us/azure/databricks/agents/mcp-tools/built-in-mcp-services)
bevestigt bestaande managed MCP-services. Hergebruik bij voorkeur een passende
service achter onze tweede expliciete toolwrapper. Kies bewust tussen het
Unity Gateway-pad en legacy-workspace-endpoints; ze hebben niet dezelfde
MCP-level governance. Een vooraf geregistreerde Databricks OAuth-client met
apart gekoppelde gebruikerslogin is een gedocumenteerde richting; externe
Entra-OBO mag niet worden aangenomen op basis van hosted Model Serving-OBO.

### Fase 4 - complete multi-client/multi-account matrix

Twee accounts maal twee clients, eerst per bron en daarna voor gecombineerde
antwoorden. Hetzelfde account moet via verschillende toegestane clients dezelfde
bronrechten hebben, tenzij een expliciete extra clientpolicy toegang verder beperkt.
Verschillende accounts mogen met dezelfde vraag verschillende data krijgen.

## Minimale negatieve tests

| Proef | Verwachte uitkomst |
|---|---|
| Geen of ongeldig token | Authenticatiefout; geen tool of bron aangeroepen |
| Token geldig maar voor Graph/Fabric in plaats van onze MCP | Audience geweigerd |
| Geldig token, onvoldoende toolrechten | Autorisatiefout; geen downstream-call |
| Verborgen tool direct via `tools/call` | Dezelfde weigering als bij een zichtbare verboden tool |
| `userId`, `role=admin` of andere identiteit in argument | Geen nieuwe rechten; ongeldige argumenten geweigerd |
| Regiofilter probeert RLS te verbreden | Server/source-policy kan alleen beperken, niet verruimen |
| Zelfde account via tweede client | Dezelfde zichtbare rijen en kolommen |
| Ander account op dezelfde MCP-sessie | Weigering of nieuwe correct gebonden sessie; nooit gedeelde context |
| Rechten intrekken na succesvolle call | Meetbaar intrekkingsgedrag; geen onbegrensde cachetoegang |
| Eerder finance-antwoord gevolgd door manager | Geen finance-data uit toolcache, bronconversatie of clientcontext |
| Bron weigert gedelegeerd token | Expliciete fout; geen fallback naar app-credentials |
| Model vraagt om "alles" | Geen ruimer toolcontract of bronrecht |

Een exacte HTTP-/MCP-foutcode wordt bij implementatie aan het gekozen SDK/protocol
gekoppeld. Het harde criterium bij een geweigerde aanroep is: **geen ongeoorloofde
broncall en geen beschermde informatie in het MCP-resultaat**.

## Waar bewijs moet komen

Leg per aanvraag correlation-id, pseudonieme principalreferentie, client,
tool, autorisatiebesluit, authenticatiemodus en bron-request-id vast.
Vergelijk de verwachte principal met de daadwerkelijke principal in bronlogs.
Onze eigen logtekst "namens Noor" is geen bewijs dat Fabric/Databricks Noor zag.

Inspecteer toolresultaten voordat een taalmodel ze ontvangt. Een model dat
verboden data heeft gezien maar die toevallig niet citeert, slaagt niet.
Bij Fabric data agent en Genie zit echter al een agent achter de upstream-MCP:
ons gatewaypayload toont niet automatisch de context van die upstream-LLM.
Gebruik daarom ook bronquerybewijs en audit, of een deterministische adapter
voor de specifieke beveiligingstest. Claim anders alleen wat daadwerkelijk
waarneembaar is, zoals "geen verboden gegevens aan de client-LLM geleverd".
Isoleer ook downstream Genie/data-agent-conversaties per gebruiker en tenant.
Test propagatievertraging van groepswijzigingen, tokens, bronrechten en caches;
claim geen onmiddellijke intrekking zonder meting.

MCP kan toegang via zijn eigen grens afdwingen. Het kan geen reeds ontvangen
informatie uit een client/model wissen en blokkeert geen rechtstreekse bronroute
waarvoor dezelfde gebruiker elders nog rechten heeft.

## Nog nodig voor uitvoering

Bevestig de exacte Copilot-variant en het Scout-product, een testtenant,
appregistratie-/consentmogelijkheden, een bereikbaar HTTPS-MCP-endpoint en twee
testaccounts. Daarna de concrete Fabric-/Databricks-resources en toegestane kosten.
Er zijn in deze stap geen resources, appregistraties, secrets of rechten aangemaakt.

## Architectuurartefact

Open `.archify\architecture-mcp-federation-20261015-092111\mcp-federation.html`
vanuit de projectmap. Links staan de clients, in het midden MCP en bronadapters,
rechts de 3P Fabric-/Databricks-MCP-servers plus een aparte custom Rights Test MCP.
De custom MCP is een eigen testserver, geen onderdeel van de adaptercode. De adapters zijn
gelabeld als eigen code binnen de gateway. Alle verbindingen zijn een ontwerpdoel, geen claim
dat de producten al zijn gekoppeld. De te bevestigen client- en bronroutes
zijn ook in de tekening gemarkeerd.

Het diagram is type `architecture`. De bijbehorende `candidate.json` bewaart de
specificatie. `mcp-federation.finalize-summary.json` bevat het receipt met geslaagde
validate-, deliver-, check- en browser-check-gates. Er is geen aparte visuele
inspectie uitgevoerd; inhoud is Nederlands, ingebouwde viewerbediening Engels.
