# Onderzoek: één logische agent over Fabric + Databricks, ontsloten via Foundry Agent Service en M365 Copilot/Teams ("Bot Framework"), met on-behalf-of-identiteit

**Datum onderzoek:** 2026-10-14. Alle uitspraken hieronder zijn gebaseerd op bronnen die in deze sessie daadwerkelijk zijn opgehaald (datum van ophalen = vandaag); publicatie-/wijzigingsdata van de bron zelf staan erbij vermeld. Microsoft- en Databricks-documentatie worden continu bijgewerkt; preview-features kunnen tegen de tijd dat dit gelezen wordt GA zijn of andersom. Elke paragraaf markeert claims als **[VASTGESTELD]** (letterlijk zo gedocumenteerd), **[ARCHITECTUURADVIES]** (door leverancier aanbevolen patroon, geen harde garantie) of **[OPEN TESTAANNAME]** (nog te verifiëren met een eigen tenant, bij voorkeur via audit logs — niet alleen modeloutput).

---

## 0. Kernbevindingen in het kort

1. **[VASTGESTELD]** De Bot Framework SDK is in retirement: langetermijnondersteuning eindigt, supporttickets stoppen per 31-12-2025, archivering van de repo uiterlijk eind december 2025. Microsoft wijst door naar de **Microsoft 365 Agents SDK**, die GA is (C#, JavaScript, Python) [1].
2. **[VASTGESTELD]** Er zijn twee fundamenteel verschillende agent-vormen voor Copilot: **declaratieve agents** (draaien volledig op Copilot's eigen orchestrator/model/beveiliging, geen eigen hosting) en **custom engine agents** (eigen orchestrator/model/hosting, eigen verantwoordelijkheid voor compliance/RAI) [3][4].
3. **[VASTGESTELD]** De Fabric data agent-integratie in Foundry Agent Service is **preview**, vereist dezelfde tenant én dezelfde regio/capaciteit voor Fabric en Foundry, en gebruikt verplicht **user identity authentication** — de documentatie zegt expliciet dat **service-principal-authenticatie niet ondersteund wordt voor deze tool** [6][24].
4. **[VASTGESTELD, sterker dan voorheen]** Microsoft documenteert niet alleen de OBO-claim, maar ook fail-closed-gedrag: als een eindgebruiker geen toegang heeft tot de Fabric data agent of een onderliggende bron, **faalt de tool-aanroep**. Dit is sterker bewijs dan een losse marketingzin, maar blijft een documentatie-uitspraak — geen door ons gemeten gedrag. Empirische bevestiging via Fabric/OneLake-auditlogs blijft nodig, zeker voor het geval van een willekeurige externe webapp die zelf een OBO-keten opbouwt (zie §4 en §11).
5. **[VASTGESTELD]** Databricks Genie (nu "Genie Agents", voorheen "Genie Spaces") kent een eigen, Databricks-interne **on-behalf-of-user authentication**, die **standaard uitstaat**, door een workspace-admin aangezet moet worden, en alleen werkt voor **Databricks-gebruikersidentiteiten** — niet voor het blind doorsturen van een extern (Entra) token [15].
6. **[GENUANCEERD]** Unity Catalog-permissies op data-objecten gelden voor UC-beheerde resources, maar de *mate* van afdwinging hangt af van het pad: beheerde MCP's via Unity Gateway krijgen UC-grants/policies/guardrails, terwijl **legacy workspace-endpoints Unity Gateway omzeilen** en alleen op de onderliggende resource-permissies leunen [12]. Rijfilters/kolommaskers en ABAC-policies vereisen bovendien een specifiek compute-type en Runtime-versie — op te oude runtimes kan een query een ABAC-beveiligde tabel domweg niet benaderen [16][26].
7. **[VASTGESTELD]** Foundry IQ / Azure AI Search "agentic retrieval" kan queries uitvoeren "under the caller's Microsoft Entra identity" voor permissie-afdwinging, maar het onderliggende mechanisme voor documentniveau-beveiliging is **ACL-synchronisatie op indexeringstijd** plus **trimming op querytijd** — geen live doorverwijzing naar de brondatabron zelf [18][19][20].
8. **[GENUANCEERD]** De OAuth2 On-Behalf-Of-flow van Microsoft Entra ID gebruikt **delegated scopes**, niet de applicatie-eigen app-only rollen van de aanroepende app; de rollen die de **gebruiker zelf** op de resource heeft, blijven wél gewoon in diens claims zitten. Een service principal kan dit protocol niet native gebruiken (dat loopt via client-credentials) — maar dat is géén garantie dat eigen applicatiecode niet zelf, als bug, op een onveilige manier naar een app-only credential terugvalt wanneer OBO faalt [21].

---

## 1. Use case: Velora BikeOps (fictief)

Velora BikeOps verhuurt deelfietsen in meerdere Nederlandse steden. Drie databronnen zijn relevant:

- **Omzet & verhuur-analytics** in **Microsoft Fabric** (Lakehouse met ritdata, Warehouse met facturatie, een Power BI semantic model "Verhuuromzet" met RLS per regio).
- **Onderhoud & voertuigstatus** in **Databricks** (Unity Catalog-tabellen `maintenance.bikes`, `maintenance.work_orders`, ontsloten via een Genie Agent/space "Fleet Maintenance").
- **Beleidsdocumenten** (onderhoudsprotocollen, verhuurvoorwaarden, veiligheidsrichtlijnen) als ongestructureerde content, geïndexeerd in **Azure AI Search / Foundry IQ**.

Eindgebruikers (regiomanagers, monteurs, supportmedewerkers) stellen vragen in **Microsoft Teams** aan één logische "Velora-agent". Het doel van de gebruiker (los van dit document) is: een **lokale, offline identiteitssimulator** bouwen om te testen welke identiteit, rechten en scopes er in theorie door elke laag heen zouden moeten stromen — dus geen live cloud-integratie, maar een correct mentaal/test-model.

---

## 2. Logische architectuur (proza, geen diagram)

De voordeur is een gesprekskanaal in Teams/M365 Copilot. Architecturaal zijn er twee heel verschillende manieren om daar te komen, en de keuze bepaalt wie welke identiteit ziet:

- **Declaratieve agent**: volledig gehost en georkestreerd door Copilot zelf. Kennis en acties worden als configuratie (manifest, knowledge sources, API-plugins) aangeleverd; Copilot's eigen model en orchestrator draaien de beurten. Er is geen eigen backend-proces dat je zelf beheert, dus er is ook geen eigen punt waar je zelf OBO-logica implementeert — dat ligt bij Copilot/Graph-connectors.
- **Custom engine agent** (via **Microsoft 365 Agents SDK**, de opvolger van de Bot Framework SDK): een door jou gehoste service (Azure) met eigen orchestratie (bijv. Semantic Kernel, Azure AI Foundry Agent Service) die via een kanaal-abstractie wordt ontsloten in Teams, Copilot en eventueel andere kanalen. Dit is het pad waarlangs de gebruiker "Bot Framework" noemt: de Azure Bot-registratie/Bot Framework Token Service blijft (via de opvolger-SDK) de laag die de Teams-conversatie koppelt aan een Entra-identiteit en, via SSO/token-exchange, een gebruikerstoken (access token, geen ID-token) in een Bot Framework Token Store cachet per gebruiker+conversatie+connectie [5].

Vanaf het moment dat de custom engine agent een gebruikerstoken heeft, kan de agent-backend dat token (of een erop gebaseerde OBO-afgeleide token) meesturen naar **Foundry Agent Service**.

Foundry Agent Service kan op zijn beurt een **Fabric data agent** als "tool"/knowledge source aanroepen. Microsoft documenteert dat die aanroep verplicht met **user identity authentication** gebeurt (Identity Passthrough/OBO); service-principal-authenticatie is voor deze tool expliciet niet ondersteund [6][24]. In Foundry's eigen "Agent ID"-model wordt hierbij niet simpelweg het gebruikerstoken doorgegeven: Agent Service wisselt het om voor een token dat **zowel de agent-identiteit als de delegated permissions van de gebruiker** draagt [25].

Voor Databricks-gegevens loopt het pad anders. Er bestaat geen gedocumenteerde, directe Entra-OBO-brug naar Databricks. Databricks heeft zijn eigen **on-behalf-of-user authentication**-mechanisme, dat werkt op basis van een **Databricks-gebruikersidentiteit** (die zelf via SSO/SCIM aan Entra gekoppeld kan zijn, maar dat is identity-provisioning, geen tokenforwarding) [15].

Voor ongestructureerde content (beleidsdocumenten) loopt het pad via Foundry IQ/Azure AI Search. Daar wordt onderscheid gemaakt tussen de identiteit die de **index vult** (indexer/ingestion, vaak een breder bevoegde managed identity) en de identiteit waaronder op **querytijd** wordt getrimd (de "caller's Microsoft Entra identity", vergeleken met eerder gesynchroniseerde ACL-metadata) — geen van beide bevraagt de brondatastore live op het moment van de vraag [18][19].

De architecturale consequentie: er is **niet één OBO-keten** die van Teams tot Databricks doorloopt. Het zijn **minstens drie afzonderlijke delegatie-domeinen** (Entra/Agent-ID-OBO richting Foundry/Fabric, Databricks-eigen OBO/U2M richting Unity Catalog, Azure AI Search ACL-sync-plus-trimming richting het kennisarchief) die elk hun eigen bewijslast en beperkingen hebben. Een correcte simulator moet deze drie domeinen apart modelleren en mag ze niet laten "doorlekken" als wiskundig equivalent aan elkaar.

---

## 3. Bot Framework SDK → Microsoft 365 Agents SDK; declaratief vs. custom engine; kanaalidentiteit vs. delegated token

**[VASTGESTELD]**
- De Bot Framework SDK-repository stelt expliciet: *"final long term support ending December 2025"*, geen productupdates meer, geen dependency-updates, support tickets stoppen per 31-12-2025, en de repo wordt *"no later than end of December of 2025"* gearchiveerd. Doorverwezen wordt naar de **Microsoft 365 Agents SDK** (GA, C#/JS/Python) en naar een officiële migratiegids [1].
- De Microsoft 365 Agents SDK lost drie problemen op: (a) multi-kanaal abstractie (Teams, Copilot, website, Slack, Messenger …) zonder dat je per kanaal apart auth/berichtformaat hoeft te bouwen, (b) AI/orchestratie-agnostisch (geen vendor lock-in op model/orchestrator), (c) ingebouwd gespreksstatusbeheer per "turn" [2].
- **Declaratieve agent**: gebruikt Copilot's eigen orchestrator en foundation model; geen aparte hosting nodig; gebouwd met low-code (Copilot agent builder) of pro-code (VS/VS Code + Agents Toolkit); draait in M365 Copilot en apps als Teams/Word/Excel/Outlook [3][4].
- **Custom engine agent**: vereist eigen hosting (typisch Azure, extra kosten), eigen orchestratie/modellen, eigen verantwoordelijkheid voor security/compliance/Responsible AI-beleid [3].
- **SSO/token-uitwisseling in Teams-bots**: bij een eerste interactie slaat de **Bot Framework Token Service** een token op in de **Bot Framework Token Store**, gekoppeld aan gebruiker + OAuth-connectie. Bij SSO stuurt de Teams-client een `tokenExchange`-aanvraag; de bot vraagt de Token Service om het ontvangen Entra-token te wisselen; de gebruiker moet (eenmalig per app/endpoint) toestemming geven; bij consent-falen valt het terug op een expliciete sign-in-prompt [5].

**[ARCHITECTUURADVIES]**
- Voor Velora BikeOps is een **custom engine agent** op de Microsoft 365 Agents SDK het aangewezen pad wanneer je zelf orchestratie/identity-logica (Foundry Agent Service, eigen OBO-ketens) wilt aansturen; een **declaratieve agent** is te prefereren wanneer Copilot's ingebouwde connectors/kennisbronnen volstaan en je geen eigen backend wilt onderhouden.
- Migratie van bestaande Bot Framework SDK-bots naar de Agents SDK dient vóór eind 2025 serieus gepland te worden, gezien het expliciete end-of-support.

**[OPEN TESTAANNAME]**
- Of en hoe exact de "kanaalidentiteit" (het Microsoft App ID/Bot-registratie-identiteit die de Teams⇄Bot Framework-verbinding authenticeert) wordt gescheiden gehouden van het per-gebruiker delegated token in de nieuwste Agents SDK-documentatie, is in deze sessie niet met een letterlijk citaat bevestigd (alleen de oudere Bot Framework/Teams SSO-pagina is geciteerd). Dit moet bevestigd worden met de actuele Agents SDK auth-documentatie en, voor de simulator, met een expliciet onderscheid tussen "appid"-claim (bot-identiteit) en "oid"/"upn"-claims in het delegated token.
- Of custom engine agents altijd via Azure Bot Service-registratie lopen (zoals bij de klassieke Bot Framework SDK) is aannemelijk maar niet letterlijk herbevestigd voor de Agents SDK in de opgehaalde bronnen; behandel dit als aanname tot bevestigd.

---

## 4. Foundry Agent Service ↔ Fabric data agent

Er bestaan twee officiële documentatiepagina's over deze integratie die elkaar aanvullen: één aan de **Fabric-kant** (algemene preview-introductie) [6] en één aan de **Foundry-kant**, een recentere, gedetailleerdere how-to met expliciete identity- en permissietabellen (bijgewerkt 2026-10-12, dus actueler dan de Fabric-pagina) [24]. Beide beschrijven dezelfde preview-feature; de Foundry-pagina is specifieker en wordt hieronder als leidend genomen waar ze verschillen.

**[VASTGESTELD]**
- De integratie is expliciet gemarkeerd als **preview**, zonder SLA en niet aanbevolen voor productie [24].
- Vereisten: betaalde Fabric-capaciteit F2+ (of Power BI Premium P1+ met Fabric ingeschakeld); cross-geo-instellingen indien van toepassing; minimaal de **Foundry User**-RBAC-rol (recent hernoemd van "Azure AI User") voor developers/eindgebruikers [6][24].
- **Tenant- én regio-restrictie**: Fabric data agent en Foundry-project moeten in dezelfde tenant staan; daarnaast moeten data agent en zijn databronnen op capaciteiten in **dezelfde regio** staan — *"The data agent can't execute a query when a data source's workspace capacity is in a different region"* [24].
- **Verplicht user-identity-auth, geen SP-fallback**: *"Use user identity authentication. Service principal authentication isn't supported for the Fabric data agent."* [24]. Dit is sterker en preciezer dan de eerdere, meer algemene OBO-formulering uit de Fabric-pagina [6].
- **Fail-closed-gedrag gedocumenteerd**: *"Give each end user access to the Fabric data agent and its underlying data sources, or the tool call fails."* [24] — als iemand geen toegang heeft, faalt de aanroep expliciet in plaats van stilzwijgend te slagen of leeg terug te komen.
- **Minimumrechten per bron, inclusief RLS/CLS-bevestiging**: de Foundry-pagina geeft een expliciete tabel met minimale permissies per databrontype, met voor **Power BI semantic model** letterlijk: *"Read. Row-level security and column-level security continue to apply."* Voor Lakehouse: "Read on the lakehouse item (and table access, if enforced)"; voor Warehouse: "Read (SELECT on relevant tables)"; voor KQL database: Reader-rol; voor Microsoft Graph: "Delegated access to the Microsoft 365 content requested by the signed-in user" [24].
- **Agent-identiteit versus eindgebruikerstoken**: Foundry gebruikt een eigen "Agent ID"-platform in Microsoft Entra ID. Bij de "attended/OBO"-modus wisselt Agent Service het gebruikerstoken niet simpelweg door, maar om voor een nieuw token: *"Agent Service then exchanges that token for one that carries both the agent identity and the user's delegated permissions."* Bij de "unattended"-modus (client credentials) is er juist geen gebruiker betrokken en wordt toegang volledig bepaald door de eigen RBAC-rollen van de agent-identiteit [25].
- Je kan maar **één Fabric data agent** per Foundry-agent als knowledge source toevoegen; het Foundry-gekozen model is alleen voor orchestratie/antwoordgeneratie, niet voor de NL2SQL/NL2DAX-vertaling zelf [6].
- Compliance-kanttekening: zodra een Fabric data agent via Foundry (of Copilot Studio, M365 Copilot, of als MCP-server) wordt geconsumeerd, kan de respons **buiten Fabrics compliance-grens of regio** terechtkomen en wordt deze verwerkt volgens de voorwaarden van de consumerende dienst [7].

**[ARCHITECTUURADVIES]**
- Behandel deze integratie, zolang ze preview is, niet als production-ready pad voor Velora BikeOps zonder expliciete risicoacceptatie; plan een migratietoets zodra GA-aankondiging verschijnt.
- Omdat SP-authenticatie hier niet ondersteund is, kan een backend deze tool **niet** gebruiken als gedeelde/achtergrond-identiteit namens alle gebruikers; elke aanroep vereist een echte, actuele eindgebruikerscontext.
- Plan expliciet voor de regio-eis: als Velora-tenant meerdere Fabric-capaciteiten in verschillende regio's heeft, moet de gekozen Fabric data agent en zijn bronnen in dezelfde regio als het Foundry-project staan, anders faalt de query onafhankelijk van rechten.

**[OPEN TESTAANNAME — met name voor een willekeurige, zelf gebouwde webapp]**
- De hierboven geciteerde tekst geeft hard gedocumenteerd *bedoeld* gedrag (inclusief fail-closed), maar is nog steeds een documentatie-uitspraak, geen door ons gemeten resultaat. **Benodigd bewijs**: Fabric/OneLake-auditlogs (Microsoft Purview unified audit log) die tonen dat de uitvoerende identiteit bij de Fabric-side-query daadwerkelijk de aanmeldende eindgebruiker is, vergeleken met wie in Foundry was ingelogd.
- **Expliciet onbewezen voor een willekeurige externe webapp-gebruiker**: de voorbeeldcode authenticeert met `az account get-access-token --scope https://ai.azure.com/.default`, d.w.z. de interactief aangemelde CLI-/portaalgebruiker. Niet gedocumenteerd — en dus een open vraag — is of en hoe de identiteit correct blijft doorlopen wanneer een **eigen webapp** eerst zelf een eindgebruiker via zijn eigen Entra-app-registratie authenticeert, daarna zelf een OBO-uitwisseling doet naar het `https://ai.azure.com`-scope, en dát token vervolgens aan Foundry aanbiedt — waarna Foundry op zijn beurt weer een eigen Agent-ID-tokenwissel doet richting Fabric. Dit is een **drie-staps-OBO-keten** (webapp → Foundry Agent-ID-uitwisseling → Fabric) waarvan geen van de geraadpleegde bronnen het eind-tot-eind-gedrag (met name bij tokenintrekking, groepswijzigingen, of caching tussen agent-runs) expliciet beschrijft.
- Gedrag bij groepslidmaatschap-wijzigingen, tokenintrekking (revocation) en caching tussen Foundry-conversatiebeurten is niet gedocumenteerd in de opgehaalde pagina's; expliciet testen met een gebruiker wiens rechten tussentijds worden ingetrokken is nodig.

---

## 5. Fabric data agent: architectuur, databronnen, RLS/OLS, capaciteit, sharing

**[VASTGESTELD]**
- Ondersteunde databron-categorieën (max. 5 bronnen per data agent, elke combinatie toegestaan): **SQL** (Lakehouse, Data Warehouse, SQL Database, Mirrored Databases — via T-SQL/SQL Analytics Endpoint), **Eventhouse** (KQL Database via KQL), **Semantic Model** (Power BI, via DAX/XMLA-endpoint), **Graph (preview)** (GQL), **Ontology (preview)** (routeert naar onderliggende bron, voert native SQL/KQL/DAX uit), **Azure AI Search (preview)** (ongestructureerde content, met gebruikersidentiteit voor toegangscontrole meegestuurd) [8].
- Voor Ontology geldt expliciet: *"The underlying source executes the generated query by using the requesting user's permissions"* [8]. Voor de AI Search-bron: *"the agent sends the query (with the user's identity for access control) to the search index"* [8].
- **RLS/CLS-bevestiging per brontype**: de Foundry-how-to-pagina specificeert nu wél expliciet, per databrontype, welk minimumrecht nodig is en of rij-/kolombeveiliging blijft gelden: voor **Power BI semantic model** geldt *"Read. Row-level security and column-level security continue to apply."* Voor **Lakehouse**: "Read on the lakehouse item (and table access, if enforced)" — de formulering "if enforced" laat zien dat tabeltoegang op Lakehouse-niveau optioneel/configuratie-afhankelijk is, niet automatisch rij-granulair. Voor **Warehouse**: "Read (SELECT on relevant tables)" — dit is tabelniveau, geen rijniveau [24]. Met andere woorden: alleen het **semantic model**-pad heeft een expliciete, gedocumenteerde rijbeveiligingslaag; Lakehouse/Warehouse-rechten zijn item-/tabelniveau tenzij je zelf extra beveiliging (views, RLS-achtige constructies) toevoegt.
- Capaciteitsvereiste: betaalde F2+ Fabric-capaciteit, of Power BI Premium P1+ met Fabric ingeschakeld; cross-geo processing/storing voor AI moet tenant-breed aanstaan [6][7][9][10].
- Tenant-instellingen vereisen admin-rechten: *"Users can use Copilot and other features powered by Azure OpenAI"* moet aanstaan (tenant- en/of capaciteitsniveau), plus *"Capacities can be designated as Fabric Copilot capacities"*; wijzigingen kunnen tot een uur duren om door te werken [7].
- **Sharing ≠ data delen**: publiceren van een data agent genereert een read-only versie die je kan delen; dit document regelt wie de agent mag *gebruiken*, maar de leestoegang tot de onderliggende databron blijft een apart, al bestaand rechtensysteem (semantic model-rollen, workspace-rollen, item-permissies) [9]. Consumptie via M365 Copilot vereist bovendien een eigen Copilot-/Office 365-licentie per gebruiker, naast dezelfde tenant/account-eis als bij Foundry [10].

**[ARCHITECTUURADVIES]**
- Voor Velora BikeOps: zet de "Verhuuromzet"-vraag primair op het **Power BI semantic model** (waar RLS als rollen-concept gedocumenteerd en breed begrepen is) in plaats van rechtstreeks op het Lakehouse/Warehouse, zodat rijbeveiliging een bekend, auditbaar mechanisme gebruikt in plaats van impliciet te vertrouwen op workspace-brede leesrechten.
- Documenteer voor elke databron-categorie apart welk rechtenmodel eronder zit (workspace-rol, item-permissie, semantic-model-rol, OneLake-data-access-rol) — dit is per bron verschillend en niet uniform "RLS/OLS" zoals soms informeel wordt gesuggereerd.

**[OPEN TESTAANNAME]**
- De documentatie bevestigt nu expliciet dát RLS/CLS op het semantic model-pad "blijft gelden", maar niet **hoe volledig** (bijv. of alle RLS-rolconstructies, dynamic RLS met `USERNAME()`/`USERPRINCIPALNAME()`, en samengestelde rollen identiek werken wanneer de vraag via Foundry/NL2DAX binnenkomt in plaats van via een gewone Power BI-rapportsessie). Verifieer dit met twee testgebruikers met verschillende RLS-rollen op hetzelfde semantic model, zelfde vraag via de Fabric data agent, en vergelijk resultaten + Fabric-auditlog-items — niet alleen het antwoord van de agent.
- Of Lakehouse/Warehouse-tabeltoegang ("table access, if enforced") in de praktijk per-tabel afdwingbaar is of slechts een alles-of-niets item-leesrecht, is met de huidige citaten niet volledig scherp; test dit met een gebruiker die wel op het Lakehouse-item maar niet op specifieke tabellen rechten heeft.

---

## 6. Databricks Genie (Genie Agents) / Conversation API, Managed MCP, Unity Catalog row filters/column masks, OAuth U2M/M2M/OBO

> **Cloud-scope kanttekening**: alle Databricks-bronnen in deze sectie zijn opgehaald van `docs.databricks.com/aws/...` (AWS-documentatiepad). Databricks publiceert aparte documentatiebomen per cloud (AWS/Azure/GCP), en feature-naamgeving, GA-/preview-status, endpoint-URL's en sunset-/lifecycle-data kunnen per cloud verschillen of vertraagd uitrollen. Met name de naam **"Genie One MCP"**, de **GA-status**, en de **sunset-datum van 31 oktober 2026** voor het legacy-endpoint zijn hier uitsluitend bevestigd voor het AWS-pad. Voor een Azure Databricks-omgeving (`learn.microsoft.com/azure/databricks/...` of `docs.databricks.com/azure/...`) moeten naam, GA/preview-status en exacte datums apart herbevestigd worden voordat ze als vaststaand voor Azure Databricks worden aangenomen — presenteer dit dus niet automatisch als een bewezen Azure-claim.

**[VASTGESTELD]**
- Databricks hernoemde "Genie Spaces" naar **Genie Agents**; de Genie API biedt Chat-mode (stateful, multi-turn), Agent-mode (streaming redenering + SQL + citaties) en Management-API's (CI/CD) [11].
- Authenticatie-richtlijn van Databricks zelf: *"For production use cases where a user with access to a browser is present, use OAuth for users (OAuth U2M). In situations where browser-based authentication is not possible, use a service principal ... (OAuth M2M)"* [11]. Dit is een expliciete, binaire keuze — er wordt nergens gesuggereerd dat een extern (Entra) gebruikerstoken rechtstreeks kan worden "doorgegeven" aan de Genie API.
- **Managed MCP** (`system.ai.*`, via Unity Gateway): elke MCP is een Unity Catalog-securable; toegang wordt geregeld via UC-grants (wie mag aanroepen) en policies (wat mag per aanroep) [12]. De **Genie One MCP** (`system.ai.genie_one_mcp`) is **GA**; het oude beta-endpoint (`/api/2.0/mcp/genie`) is **deprecated en wordt op 31 oktober 2026 uitgefaseerd** [13] — een harde datum om in planning mee te nemen.
- Bij verbinden met **on-behalf-of user authentication** moet de `ai-gateway` OAuth-scope worden meegegeven; het verouderde endpoint gebruikt de `genie`-scope [13]. De Genie One MCP-pagina stelt dat *"Unity Catalog permissions are always enforced, so users and agents can only query data they're allowed to access"* [13] — dit geldt voor het **object-permissieniveau** (tabel/schema/catalog-grants) via de **nieuwe, door Unity Gateway beheerde MCP's**. Deze claim moet echter genuanceerd worden: **legacy workspace-MCP-endpoints omzeilen Unity Gateway volledig** en missen daardoor MCP-niveau-grants, -policies en -tracing; daar wordt toegang alleen nog geregeld door de kale permissies op de onderliggende resources (tabellen, functies, indexen, SQL warehouses) [12]. En rijfilters/kolommaskers/ABAC-policies zijn zelf weer **compute- en runtime-afhankelijk**: ABAC-beveiligde tabellen zijn op te oude runtimes niet eens toegankelijk — *"Standard and dedicated compute on Databricks Runtime versions earlier than 16.4 can't access ABAC-secured tables"* [26]. "Altijd afgedwongen" is dus correct voor de basis-UC-objectrechten op ondersteunde, UC-ingeschakelde compute, maar niet een universele garantie onafhankelijk van pad, compute-type en runtime-versie.
- **Databricks' eigen OBO-mechanisme** (voor agents op Model Serving) is expliciet **[VASTGESTELD]**: *"On-behalf-of-user (OBO) authentication lets an agent act as the Databricks user who runs the query"*; vereist **MLflow ≥ 2.22.1**; is **standaard uitgeschakeld** en moet door een **workspace-admin expliciet aangezet** worden; tokens worden "downscoped" tot enkel de gedeclareerde resource-scopes [15]. Dit is nadrukkelijk een Databricks-interne mechaniek op basis van een reeds geauthenticeerde Databricks-gebruikerssessie — geen gedocumenteerd pad waarbij een extern Entra-ID-access-token direct wordt "omgewisseld" voor Databricks-toegang.
- Daarnaast bestaat **automatic authentication passthrough**: hierbij draait de agent onder een systeem-gegenereerde **service principal** met least-privilege leesrechten op de bij het loggen opgegeven resources (vergelijkbaar met "Run as owner" voor dashboards) — dit is nadrukkelijk **geen** per-gebruiker/OBO-model, maar een gedeelde app-identiteit [15].
- **Unity Catalog row filters en column masks**: SQL-UDF's gebonden aan een tabel (`ALTER TABLE ... SET ROW FILTER` / `... SET MASK`), beheerd door de table owner; voor consistente afdwinging over veel tabellen heeft Databricks **ABAC-policies** (tag-gebaseerd, op catalog/schema-niveau) als aanbevolen alternatief, omdat table owners ABAC-policies niet kunnen overschrijven [16].
- Netwerkeisen voor MCP: serverless network policies, IP-allowlisting voor zowel client→workspace als workspace→extern-MCP-verkeer; legacy workspace-MCP-endpoints omzeilen Unity Gateway en missen dus UC-grants/policies/tracing [12][14].

**[ARCHITECTUURADVIES]**
- Gebruik voor Velora BikeOps's "Fleet Maintenance"-vragen de **Genie One MCP** (GA, UC-afgedwongen) in plaats van het deprecated beta-endpoint, met het oog op de sunset-datum van 31-10-2026.
- Kies expliciet tussen **OBO** (per-gebruiker, fijnmazig, vereist admin-opt-in + MLflow-versie) en **automatic passthrough / M2M service principal** (gedeelde, least-privilege app-identiteit) — en documenteer per use-case welke je kiest en waarom, want dit raakt direct de auditbaarheid ("wie deed deze query?").
- Voor "wie mag welke rij/kolom zien", gebruik ABAC-policies in plaats van per-tabel row filters/column masks zodra er meerdere Fleet Maintenance-tabellen bijkomen.

**[OPEN TESTAANNAME]**
- **Expliciet niet aannemen** dat een Entra-ID-token van de Teams/Copilot-gebruiker ooit rechtstreeks naar Databricks kan worden doorgestuurd of 1-op-1 uitgewisseld; in de opgehaalde documentatie is geen "federation"/"token-exchange-met-Entra"-pad voor Genie of MCP beschreven. Als een project dat nodig heeft, vereist dit een aparte identity-mapping-laag (bijv. Databricks SCIM-provisioning + een eigen bemiddelende stap waarbij de gebruiker zich ook los bij Databricks authenticeert via U2M/OAuth), niet een technische "doorstuur"-truc.
- Benodigd bewijs voor een werkende OBO-keten: Databricks **Unity Catalog audit logs** (system tables `system.access.audit`) die tonen dat de query-uitvoerder-identiteit (`user_identity`) overeenkomt met de eindgebruiker, niet met de service principal van de agent — te combineren met een negatieve test (gebruiker zonder UC-rechten moet een expliciete weigering krijgen, zichtbaar in het auditlog, niet slechts een leeg antwoord van het taalmodel).

---

## 7. Foundry IQ / Azure AI Search agentic retrieval: ACL's, trimming, indexer-/service-identiteit vs. doorgegeven eindgebruikersidentiteit

**[VASTGESTELD]**
- "Agentic retrieval" is een multi-query pipeline (query planning → parallelle subqueries → semantische reranking → resultaatsynthese) die zowel **GA- als preview-onderdelen** bevat; welke REST API-versie je gebruikt bepaalt wat GA is (`2026-04-01` voor GA-bronnen/minimal retrieval) versus preview (`2026-08-01-preview` voor LLM-query-planning, answer synthesis, multi-turn) [17].
- Foundry IQ is de managed knowledge-laag bovenop Azure AI Search agentic retrieval; sommige features zijn GA, andere preview — afhankelijk van de gebruikte API-versie; de Azure portal en Foundry-portal geven **alleen preview-toegang** tot alle mogelijkheden [18].
- Expliciete permissie-claim: *"Synchronize access control lists (ACLs) for supported sources and honor Microsoft Purview sensitivity labels. Enforce permissions at query time ... Run queries under the caller's Microsoft Entra identity for end-to-end permission enforcement"* [18]. Dit "run queries under the caller's identity" is de **doorgegeven eindgebruikersidentiteit** die de applicatie bij elke queryaanroep moet meesturen — dit is nadrukkelijk iets anders dan de identiteit waarmee de **indexer** (of de Azure AI Search-dienst zelf, vaak via een managed identity/service principal) de bron is binnengegaan om content en ACL-metadata op te halen. De dienst/indexer-identiteit bepaalt *wat* in de index komt; de doorgegeven eindgebruikersidentiteit bepaalt *wat ervan getoond wordt* bij een specifieke query.
- Azure AI Search kent vier documentniveau-toegangspatronen, met expliciete keuzecriteria [19]:
  1. **Security filters** — string-vergelijking; *"There's no authentication or authorization through the security principal. The principal is just a string, used in a filter expression"* [20]. Dit is géén native ACL-handhaving, slechts een zelfgebouwde, API-agnostische filter — aanbevolen alleen bij custom identity-systemen zonder Microsoft-integratie [19].
  2. **POSIX-like ACL / RBAC scopes (preview)** — native Entra-integratie voor ADLS Gen2/Blob-content; permissiemetadata wordt door de indexer gesynchroniseerd en op querytijd vergeleken met de Entra-principal van de aanroeper [19].
  3. **Microsoft Purview sensitivity labels (preview)** — labels worden door de indexer geëxtraheerd en op querytijd geëvalueerd tegen Entra-tokens en Purview-beleid [19].
  4. **SharePoint in Microsoft 365 ACLs (preview)** — permissiemetadata wordt door de indexer uit SharePoint gehaald en bij de query gecontroleerd [19].
- Foundry IQ wordt expliciet onderscheiden van **Fabric IQ** (semantische laag bovenop Fabric/OneLake/Power BI) en **Work IQ** (M365-samenwerkingssignalen); het zijn drie losstaande "IQ"-workloads die gecombineerd kunnen worden [18].

**[ARCHITECTUURADVIES]**
- Voor Velora's beleidsdocumenten: kies optie 2 of 4 hierboven (native ACL/RBAC-sync of SharePoint-ACL's) als de bron dat ondersteunt, zodat autorisatie aan de brondocumenten blijft hangen; gebruik security filters alleen als laatste redmiddel, met het besef dat dit **geen** echte autorisatie is — alleen filtering op een zelf onderhouden identiteitsstring.
- Gebruik voor productie bewust de GA REST API-versie (`2026-04-01` t.t.v. onderzoek) in plaats van de portal-only preview-paden, om niet afhankelijk te zijn van schema's die kunnen wijzigen vóór GA [17].

**[OPEN TESTAANNAME]**
- Het exacte mechanisme waarmee een aanroepende applicatie de eindgebruikersidentiteit per query meestuurt (bijv. welke header/claim/token-vorm precies) is in de opgehaalde pagina's benoemd in functionele termen ("under the caller's Microsoft Entra identity") maar niet met een letterlijk protocol-/header-citaat bevestigd in deze sessie; bevestig dit met de implementatie-documentatie voordat je dit in de simulator als protocol-detail vastlegt.
- ACL-sync kan vertraging hebben (niet "live"): een net ingetrokken SharePoint-recht kan dus tijdelijk nog doorwerken in zoekresultaten totdat de volgende indexer-run plaatsvindt — dit expliciet testen met een timing-experiment (recht intrekken, direct bevragen, opnieuw bevragen na indexer-cyclus).
- Welke van de vier patronen Foundry IQ daadwerkelijk toepast voor een OneLake/Fabric-knowledge source (relevant voor Velora's beleidsdocumenten als die ooit via OneLake lopen) wordt niet in een van de geciteerde pagina's één-op-één benoemd; dit moet per knowledge-source-type apart worden opgezocht in de "knowledge source"-referentiedocumentatie.

---

## 8. OAuth2 On-Behalf-Of correctheid (Microsoft Entra ID)

**[VASTGESTELD]**
- OBO is per definitie **delegatie**: een middle-tier API wisselt het token van de aanroeper om voor een nieuw token richting een downstream-API, met behoud van gebruikersidentiteit en -permissies door de hele keten [21].
- **Delegated scopes, en de rol van de gebruiker blijft behouden — maar niet als garantie van identieke tokenclaims**: *"It only uses delegated scopes and not application roles. Roles remain attached to the principal (the user) and never to the application operating on the user's behalf"* [21]. Correcte lezing: wat *niet* meegaat via OBO zijn de **eigen app-only rollen van de aanroepende (middle-tier) applicatie**. Gebruikers kunnen wél applicatierollen (App Roles) toegewezen hebben op een resource-API, en die rollen blijven aan de gebruiker — als principal — gekoppeld, niet aan de aanroepende app. Dit Microsoft-citaat is echter een uitspraak over **eigenaarschap van de rol** (principal vs. applicatie), **niet** een bewijs dat dezelfde roles-claims letterlijk, ongewijzigd worden gekopieerd naar elk downstream-token in de keten. Elk OBO-gegenereerd token is **resource-specifiek** (audience-gebonden, zie ook de scopes-per-resource-regel verderop); welke claims (inclusief roles) daadwerkelijk in dát specifieke token verschijnen, bepaalt de resource-API zelf via zijn eigen token-configuratie (optionele claims, app-rol-toewijzingen die specifiek voor díe API gelden, groups-overflow, enzovoort). Vermijd dus zowel de versimpeling "OBO kent nooit app roles" als de omgekeerde aanname "roles worden altijd 1-op-1 doorgekopieerd door elke hop"; beide zijn te absoluut. Voor een meerstaps-keten (zoals de Foundry/Fabric Agent-ID-tokenwissel in §4) moet per hop apart worden vastgesteld welke roles-/groups-claims in dát specifieke, voor die audience uitgegeven token terechtkomen — dit is niet uit de geciteerde OBO-pagina af te leiden en blijft een open testaanname.
- **Geen native service-principal-fallback in het protocol**: *"If a service principal requested an app-only token and sent it to an API, that API would then exchange a token that doesn't represent the original service principal. This is because the OBO flow only works for user principals. Instead, it must use the client credentials flow"* [21]. Dit is een **protocolgarantie van Entra**, geen garantie over hoe eigen applicatiecode is geschreven: niets weerhoudt een ontwikkelaar ervan om, als OBO faalt of geen gebruikerscontext beschikbaar is, zelf (onveilig) terug te vallen op een client-credentials-token en dat te laten doorgaan alsof het de gebruiker was. Dat is een **applicatiebug**, geen falende Entra-beveiliging — en moet in de simulator dus apart getest worden als een negatief testgeval (zie §8, open testaanname hieronder), niet aangenomen als "kan sowieso niet gebeuren".
- **Custom signing keys** op de middle-tier (bijv. bij enterprise-apps met eigen SSO-certificaat) breken OBO: de downstream-API kan de signature dan niet valideren [21].
- **Access token vs. ID-token**: access tokens zijn bedoeld voor autorisatie richting een resource-API en moeten door de API worden gevalideerd (audience/scope/claims); ze zijn in principe ondoorzichtig voor de client en mogen niet door de cliënt zelf worden "geïnterpreteerd" als bron van waarheid; ID-tokens bewijzen slechts authenticatie, niet autorisatie [22].
- **Scopes/permissies zijn per resource**: elke resource (Microsoft Graph, een eigen API, Key Vault, …) definieert eigen fijnmazige permissies/scopes onder zijn eigen application ID URI; een app vraagt per resource een apart (audience-specifiek) token aan — er bestaat geen universeel "alles-token" [23].

**[ARCHITECTUURADVIES]**
- Modelleer in de simulator expliciet: (a) de **audience** (`aud`-claim) van elk token, (b) of de downstream-resource delegated scopes of app roles verwacht, (c) per-resource tokencaching sleutel = (gebruiker, tenant, client-app, resource/scope-set) — nooit alleen (gebruiker), want hetzelfde gebruikerstoken voor Resource A mag nooit voor Resource B worden hergebruikt.
- Model conversatie-eigendom expliciet: een Bot Framework/Agents SDK-conversatie-token in de Token Store is gekoppeld aan (gebruiker × OAuth-connectie × conversatie-context); bij conversatie-einde of expliciete sign-out moet dat intrekbaar zijn, los van de onderliggende Entra-refresh-token-levensduur.

**[OPEN TESTAANNAME]**
- Voor de eigen simulator: bouw expliciete negatieve testgevallen voor (1) een service principal die probeert "OBO te doen" zonder gebruikerscontext (hoort te falen/geen betekenisvol token op te leveren), (2) een downstream-API die alleen app roles kent terwijl de caller alleen delegated scopes heeft (hoort een autorisatiefout te geven, niet stilzwijgend te slagen), (3) tokenintrekking tijdens een actieve, gecachete sessie (hoort bij de eerstvolgende call te falen, niet pas na volledige TTL-verloop als caching dat niet checkt), (4) **een applicatie die bij een OBO-fout zelf, als bug, terugvalt op een client-credentials-token en dat ten onrechte als gebruikerscontext doorstuurt** — dit hoort door de simulator als foutpad gemarkeerd te worden, juist omdát Entra dit zelf niet tegenhoudt.

---

## 9. Matrix: pad / effectieve identiteit / status / beperkingen

| Pad | Effectieve identiteit bij dataquery | Status bewijs | Belangrijkste beperkingen |
|---|---|---|---|
| Teams/Copilot (custom engine agent, M365 Agents SDK) → agent-backend | Gebruikerstoken via Bot Framework Token Service/Store, na SSO-consent | **[VASTGESTELD]** (mechaniek) / **[OPEN]** (exacte scheiding kanaalidentiteit vs. delegated token in nieuwe SDK) | SSO alleen 1:1 en groepschat, niet in kanaal-scope [5]; consent per app/endpoint |
| Agent-backend → Foundry Agent Service → Fabric data agent | Verplicht user identity (OBO/"Agent ID"-tokenwissel); SP-auth niet ondersteund voor deze tool | **[VASTGESTELD doc, incl. fail-closed]** / **[OPEN]** gedrag bij externe webapp met eigen 3-staps-OBO-keten | Preview; zelfde tenant **én zelfde regio/capaciteit** vereist; data kan buiten Fabric-compliance-grens komen [6][24] |
| Fabric data agent → Semantic Model / Lakehouse / Warehouse / Ontology / AI Search | "Requesting user's permissions"; RLS/CLS expliciet bevestigd voor semantic model, item-/tabelniveau voor Lakehouse/Warehouse | **[VASTGESTELD]** per brontype-tabel in [24]; **[OPEN]** volledigheid van RLS-dekking (dynamic RLS, geneste rollen) | Max. 5 bronnen per agent; capaciteit F2+/P1+ vereist [6][8][24] |
| Agent-backend → Databricks Genie One MCP (OBO-modus) | Databricks-gebruiker, mits workspace-admin OBO heeft aangezet + MLflow ≥2.22.1 | **[VASTGESTELD]** | Standaard uit; alleen via Unity-Gateway-beheerde MCP's (niet legacy-endpoints); vereist UC-compute/runtime die rijfilters/ABAC ondersteunt; geen Entra-token-forwarding gedocumenteerd [15][12][26] |
| Agent-backend → Databricks (automatic passthrough / M2M) | Systeem-gegenereerde service principal (least-privilege, gedeeld) | **[VASTGESTELD]** | Geen per-gebruiker-granulariteit; audit toont service principal, niet eindgebruiker [15] |
| Agent-backend → Foundry IQ / Azure AI Search (beleidsdocumenten) | "Caller's Microsoft Entra identity" op querytijd, vergeleken met gesynchroniseerde ACL-metadata | **[VASTGESTELD doc-claim]** / **[OPEN]** indexer- vs. query-identiteit-scheiding, sync-vertraging | ACL-ondersteuning per brontype varieert; sommige patronen (security filters) zijn geen echte autorisatie [19][20] |

---

## 10. Aanbevolen incrementele cloud-experimenten (met exact benodigd bewijs)

Voor zover de gebruiker dit ooit live wil valideren (de parent-taak bouwt een **offline** simulator; onderstaande is voor eventuele latere live-verificatie, niet nu uit te voeren):

1. **Aanbevolen eerste experiment — Fabric-OBO via het Power BI semantic model-pad**: gebruik bewust het **semantic model** als Fabric-databron (niet rechtstreeks Lakehouse/Warehouse), omdat dit het enige brontype is waarvoor de Foundry-documentatie expliciet RLS/CLS-continuïteit claimt [24]. Zet twee testgebruikers op met verschillende RLS-rollen op hetzelfde model; stel dezelfde vraag via Foundry Agent Service + Fabric data agent tool. Bewijs: Microsoft Purview unified audit log / Fabric activity log-items met de daadwerkelijke uitvoerende identiteit op het moment van de Fabric-side-queryexecutie — niet het Foundry-chatantwoord. Dit isoleert één variabele (werkt RLS door de Foundry-laag heen) in plaats van die te vermengen met workspace-brede leesrechten.
2. **Vervolgexperiment — Lakehouse/Warehouse-itemrechten**: herhaal met een gebruiker die wél toegang heeft tot het Lakehouse-item maar niet tot specifieke onderliggende tabellen ("table access, if enforced" [24]), om te zien of dat granulair werkt of een alles-of-niets leesrecht is.
3. **Databricks OBO vs. passthrough**: zet OBO aan op een testworkspace (vereist workspace-admin + MLflow ≥2.22.1); voer dezelfde Genie-vraag uit als gebruiker met en zonder UC-rechten op `maintenance.work_orders`. Bewijs: `system.access.audit`-systeemtabel, kolom met uitvoerende identiteit, vergeleken met de service-principal-executor bij passthrough-modus.
4. **Genie One MCP-migratie-tijdslijn**: test het deprecated beta-endpoint (`/api/2.0/mcp/genie`) en het nieuwe `system.ai.genie_one_mcp` naast elkaar vóór 31-10-2026, en leg vast welke UC-grants/policies wel/niet van toepassing zijn op elk endpoint (legacy omzeilt Unity Gateway-tracing) [13].
5. **Foundry IQ ACL-sync-vertraging**: trek een SharePoint- of Blob-permissie in, bevraag direct (verwacht: resultaat kan nog zichtbaar zijn), wacht op een indexer-cyclus, bevraag opnieuw (verwacht: resultaat verdwenen). Bewijs: Azure AI Search diagnostic logs (indexer-run-timestamps) gecorreleerd met query-timestamps.
6. **OBO-negatieve tests (Entra)**: probeer een access token van een service principal te laten "OBO-en" naar een downstream-API die alleen delegated scopes accepteert; verwacht een falende tokenuitgifte, niet een stilzwijgend app-only-token. Bewijs: AADSTS-foutcode in de tokenresponse, vastgelegd in Entra sign-in logs.
7. **Arbitraire-webapp-keten (expliciet onbewezen pad)**: bouw een minimale eigen webapp met eigen Entra-app-registratie, laat een eindgebruiker inloggen, wissel diens token via OBO om naar het `https://ai.azure.com`-scope, en roep daarmee Foundry Agent Service aan die op zijn beurt de Fabric-tool gebruikt. Bewijs dat door de **hele drie-staps-keten** (webapp-OBO → Foundry Agent-ID-tokenwissel → Fabric identity passthrough) consequent dezelfde eindgebruiker in de Fabric-auditlog verschijnt, en test expliciet wat er gebeurt als je de gebruiker tussentijds de rechten ontneemt (verwacht: volgende aanroep faalt, niet de eerstvolgende na volledige tokenverval).

---

## Bronnen (primair, alle in deze sessie opgehaald)

1. Bot Framework SDK README (retirement-aankondiging) — https://github.com/microsoft/botframework-sdk/blob/main/README.md
2. "What is the Microsoft 365 Agents SDK" (Microsoft Learn, ms.date 2026-04-28, updated 2026-10-01) — https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/agents-sdk-overview
3. "Compare declarative and custom engine agents" (Microsoft Learn, ms.date 2026-09-30) — https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/agents-overview
4. "Declarative agents for Microsoft 365 Copilot" (Microsoft Learn, ms.date 2026-09-30) — https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/overview-declarative-agent
5. "Enable SSO with Microsoft Entra ID - Teams" (Microsoft Learn, updated 2026-09-01) — https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview
6. "Consume a data agent in Microsoft Foundry (preview)" (Microsoft Fabric docs, ms.date 2026-05-12, updated 2026-06-23) — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-foundry
7. "Configure Fabric data agent tenant settings" (ms.date 2026-04-20, updated 2026-09-29) — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings
8. "Add a data source to Data Agent" (ms.date 2026-08-31, updated 2026-09-30) — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-add-datasources
9. "Fabric data agent sharing and permission management" (ms.date 2026-05-12, updated 2026-06-23) — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-sharing
10. "Consume a data agent from Microsoft Copilot (preview)" (ms.date 2026-05-12, updated 2026-10-13) — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-microsoft-365-copilot
11. "Genie Agents Conversation/Management API" (Databricks docs) — https://docs.databricks.com/aws/en/genie-agents/conversation-api
12. "Databricks-provided MCP services (`system.ai`)" — https://docs.databricks.com/aws/en/agents/mcp-tools/built-in-mcp-services
13. "Genie One MCP server" (GA; legacy endpoint sunset 2026-10-31) — https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp
14. "MCP authentication and networking" — https://docs.databricks.com/aws/en/agents/mcp-tools/authentication-networking
15. "Agent authentication for Model Serving" (automatic passthrough + OBO) — https://docs.databricks.com/aws/en/agents/custom-agents/model-serving/agent-authentication-model-serving
16. "Row filters and column masks" (Unity Catalog) — https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/
17. "Agentic Retrieval Overview" (Azure AI Search, ms.date 2026-09-16) — https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview
18. "What is Foundry IQ?" (ms.date 2026-07-31, updated 2026-08-01) — https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq
19. "Document-Level Access Control - Azure AI Search" (ms.date 2026-08-08) — https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview
20. "Security Filter Pattern - Azure AI Search" (ms.date 2026-08-21) — https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search
21. "Microsoft identity platform and OAuth2.0 On-Behalf-Of flow" (ms.date 2025-01-04, updated 2026-06-15) — https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow
22. "Scopes and permissions in the Microsoft identity platform" (updated 2026-09-30) — https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc
23. *(gereserveerd/niet gebruikt)*
24. "Use the Microsoft Fabric data agent with Foundry agents" (Foundry-side how-to, ms.date 2026-09-14, **updated 2026-10-12**) — https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric
25. "Agent identity concepts in Microsoft Foundry" (ms.date 2026-08-21, updated 2026-09-24) — https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity
26. "Requirements, quotas, and limitations for row filter and column mask policies" (Unity Catalog ABAC) — https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/requirements

---

*Einde document. Dit bestand bevat uitsluitend onderzoek/analyse; er zijn geen implementatiebestanden, code of cloud-resources aangemaakt.*
