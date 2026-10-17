# Velora BikeOps - data & identity lab

Een Nederlandstalig leerlab voor een logische agent met meerdere kanalen,
meerdere databronnen en verschillende gebruikersrechten.

**Huidige richting: live webleerlab met echte Fabric-opslag.**
Zie **`docs\weblab.md`**: aanmelden, uitgelegde rechtenexperimenten, verwachte versus
werkelijke uitkomsten en dezelfde bron in Fabric bekijken. De nieuwe webclient
staat los van de oude offline `index.html`-simulator.

**Bestaande basis: Python MCP-gateway, aparte Rights Test MCP en referentieclient.**
De twee MCP-endpoints, JWT-validatie en onafhankelijke rechtencontrole werken in
lokale HTTP-tests. Beide MCP-apps zijn in Azure uitgerold: een publieke
HTTPS-gateway en een interne testbron, met gezonde actieve revisies.
Entra-apps en managed-identity-federatie zijn ingericht; discovery en anonieme
weigering zijn live gecontroleerd. **De gerichte Fabric/OBO-baseline met twee
gebruikers is live uitgevoerd:** Noord krijgt 3 orders en geen marges, Finance
krijgt 6 orders en 6 margeregels. De extra gatewaycheck weigert Noord ook vooraf.
Dit is bewijs voor deze tools en inrichting, niet voor alle mogelijke bronroutes.
De nieuwe Fabric-route gebruikt een echt Warehouse met opgeslagen
mockdata en SQL-RLS, geen productiedata. Databricks is bewust uitgesteld;
Microsoft 365/Copilot en Scout zijn nog niet aangesloten.
De nieuwe Foundry-hosted Teams-agent staat in **`teams\velora-teams-f9ba\`**.
De hosted deployment en OAuth-configuratie zijn uitgevoerd; de echte persoonlijke
Teams-aanmelding en rechtenproef moeten nog worden afgerond.
De Foundry-voorbereidingscheck heeft lokaal ontbrekende `azd`-extensies geinstalleerd;
deze zijn niet nodig voor de simulator.

## Begin hier: de echte MCP-code

Lees **`docs\bouwen.md`** voor de vier modules, de route van een aanvraag en het
verschil tussen lokaal bewijs en de echte Entra-proef.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_mcp_lab.py" -v
```

Installeren op een nieuwe machine: maak een Python 3.11+-venv en installeer
`requirements.txt`. De instructies staan in dezelfde handleiding.
De browserdemo hieronder is de eerdere conceptsimulator, niet deze MCP-server.

## Azure-deployment

De deploymentbestanden staan in **`infra\`**. Zie **`infra\README.md`** voor de
korte route met een private `.env`: eerst het doel tonen, daarna expliciet
`python infra\deploy.py --apply`. De basisdeployment is uitgevoerd.
De cloudroute gebruikt managed-identity-federatie voor gateway-clientauthenticatie,
met behoud van gebruikers-OBO. Key Vault hoeft daarvoor niet bereikbaar te zijn.

## Nieuwe richting: MCP in het midden

De verduidelijkte onderzoeksvraag is **een centrale, server-side MCP-toegangslaag**:
meerdere clients en accounts links, dezelfde MCP-server in het midden, en
Fabric/Databricks-API's rechts. Foundry is dan niet verplicht het middelpunt.

Lees hiervoor `docs\mcp-plan.md` en `docs\mcp-onderzoek.md`.
Het plan onderscheidt echte clientauthenticatie, toolautorisatie op iedere
`tools/call`, resource-specifieke tokenflows en de eigen datarechten van de bron.
De eerste gateway en custom test-MCP zijn nu geimplementeerd in `mcp_lab`.
De bestaande HTML-demo hieronder blijft uitsluitend conceptueel lesmateriaal.

## Starten

Onderstaande instructies starten alleen de **oude offline simulator**.
Voor de echte webclient gebruik je de root-URL van de gedeployde gateway.

Open `index.html` direct in een browser. Het bestand bevat alle code, stijl en synthetische
data en heeft geen externe assets of internetverbinding nodig.

Of gebruik Node.js 22 of nieuwer, zonder `npm install`:

```powershell
npm start
```

Open vervolgens <http://127.0.0.1:4173>. De server luistert uitsluitend op loopback.
Stop met Ctrl+C. Is de poort bezet, gebruik:

```powershell
$env:PORT = '4174'; npm start
```

## De use case

Velora verhuurt fietsen in Noord en Zuid. Dezelfde logische BikeOps-agent helpt:

- Regiomanagers met hun eigen omzet en onderhoudsproblemen.
- Finance met landelijke omzet, marges en vertrouwelijke contracten.
- Monteurs met onderhoud, zonder financiele toegang.

Beoogde echte bronnen: omzet uit Fabric, onderhoud uit Databricks en beleidsdocumenten
via Foundry IQ / Azure AI Search. De browser en M365 Copilot / Teams zijn verschillende
ingangen naar dezelfde logica, **niet** verschillende rechtenmodellen.
In deze eerste versie zijn al deze producten uitsluitend labels bij lokale fixtures.

Het interessante experiment is niet alleen of de agent een antwoord geeft, maar:
**wie zag de databron, en kwam uitsluitend toegestane data in de modelcontext terecht?**

## De begrippen in het kort

**Kanaal** is waar je met de agent praat. **Orchestratie** bepaalt welke tool
wordt gebruikt. **Data agent / Genie** helpt vragen over gestructureerde data
omzetten in bronqueries. **Foundry IQ / retrieval** levert relevante kennis
en bronverwijzingen; dit is niet zonder meer hetzelfde als betrouwbare
omzetberekeningen over live tabellen.

**OBO** gaat over het verkrijgen van een passend downstream-token namens een
gebruiker. **Autorisatie** bepaalt daarna wat die gebruiker in de bron mag zien.
Een geslaagde login, gedeelde agent of geldige API-scope geeft dus niet
automatisch toegang tot alle tabellen en documenten.

**Managed identity / serviceprincipal** is een app-identiteit, niet de
ingelogde eindgebruiker. Dat kan een correcte bouwsteen zijn, maar het is geen
bewijs dat gebruikersrechten worden doorgegeven. **RLS** beperkt rijen;
**OLS/kolombeveiliging** beperkt objecten of kolommen; **document-ACL's**
bepalen welke documenten bij retrieval zichtbaar mogen zijn.
De feitelijke ondersteuning verschilt per productpad: zie `docs\onderzoek.md`.

## Probeer dit in tien minuten

1. Kies Noor, gebruikerscontext en de overzichtsvraag. Noord heeft EUR 20.000 omzet;
   marge ontbreekt en het finance-contract is niet zichtbaar.
2. Kies Sam. Het eerdere antwoord wordt direct gewist. Voer opnieuw uit: Zuid heeft
   EUR 24.000 omzet en andere onderhoudsgegevens/documenten.
3. Kies Finn. Omzet is EUR 44.000, marge EUR 16.000. Onderhoud wordt expliciet geweigerd.
4. Kies Gast. Alle drie de bronnen weigeren toegang.
5. Laat Gast staan en kies **Brede app-identiteit**. Nu ziet de app alles, inclusief
   de synthetische canary `VLR-FIN-ONLY`. Dit is een bewust te ruim ontworpen
   app-only scenario, geen aanbevolen architectuur of impliciete foutafhandeling.
6. Ga terug naar Noor en gebruikerscontext. Probeer de promptaanval. De vraag kent
   geen rollen toe: marges en finance-documenten blijven buiten het antwoord.
7. Kies een verkeerd audience of verlopen token. De aanvraag stopt voor iedere bron.
8. Laat Fabric-consent ontbreken: alleen die bron faalt; er is geen app-only fallback.
9. Herhaal de overzichtsvraag zonder fout voor een cache-hit. Kies daarna
   **Bronrechten ingetrokken**: de eerder zichtbare data komt niet terug.
10. Vergelijk Webapp en Copilot. Dezelfde persona krijgt dezelfde bronresultaten.

Lees de identity trace en het uitklapbare payload bij ieder experiment.
De `lab://...`-audiences, scopes en claims zijn **fictief**, niet te gebruiken als
configuratie voor echte Azure- of Databricks-API's.

## Wat dit wel en niet aantoont

| Onderdeel | In dit lab | Voor echte cloud nog nodig |
|---|---|---|
| Identiteit | Persona-selector en fictieve claims | Gevalideerde Entra-tokens en kanaalauthenticatie |
| OBO | Conceptuele resource-specifieke credentials | Gedocumenteerde en werkend bewezen tokenflow per hop |
| Rijen en kolommen | Lokale policy filtert/projecteert voor antwoordsamenstelling | RLS/OLS of row filters/masks in gekozen bron |
| Documentrechten | Lokale ACL-filtering | Ondersteunde ACL-ingestie + query-time handhaving en syncbeleid |
| Agent | Deterministische trefwoordrouter | Foundry-model/agent en echte toolaanroepen |
| Kanaal | UI-label Webapp of Copilot | Werkelijke webapp en Agents SDK-/M365-publicatie |
| Cache | Werkgeheugen, per gebruiker/tenant/policy; maximaal 100 antwoorden | Werkelijke revocation-, token-, thread- en cache-isolatie |
| Tests | Werking van ons lesmodel | Bron-auditlogs en end-to-end tests met echte testgebruikers |

**Geen beveiligingsgrens:** alle fixturedata staat in de HTML-bron. De browser policy
is lesmateriaal en beschermt niets. Een aanvaller kan de code of persona aanpassen.
Gebruik dit bestand nooit voor echte bedrijfsdata, tokens of secrets.

**Geen productbewijs:** de simulator pretendeert niet dat een Fabric-tool, Genie API
of Foundry IQ-connector automatisch alle getoonde delegatieopties ondersteunt.
Een app-identiteit kan passend zijn met expliciete, afdwingbare rechten; de risicodemo
laat specifiek het effect van ontbrekende gebruikersautorisatie zien.

Intrekking werkt hier onmiddellijk omdat de lokale policy per aanvraag opnieuw
wordt gelezen. Echte platformen kunnen token-, groeps- en indexvertraging hebben.
Er is geen gespreksgeschiedenis. Threads, modelcontext en citations moeten in
de cloudvariant expliciet per gebruiker/tenant worden geisoleerd.

## Bestanden

| Bestand | Doel |
|---|---|
| `index.html` | Zelfstandig interactief leerlab, inclusief fixtures en engine |
| `server.cjs` | Optionele dependency-vrije lokale server |
| `tests\lab.test.cjs` | Tests van precies de engine uit het HTML-bestand |
| `tests\server.test.cjs` | Serverroutes, loopback, headers en afscherming van bestanden |
| `docs\onderzoek.md` | Primaire bronnen en product-/identiteitsbeperkingen |
| `docs\cloud-lab.md` | Gefaseerde bouwroute, configuratiebehoeften en bewijscriteria |
| `docs\mcp-plan.md` | Nieuwe MCP-centrale architectuur en echte multi-client testopzet |
| `docs\mcp-onderzoek.md` | Primair brononderzoek naar MCP-authenticatie, clients en bronroutes |
| `docs\bouwen.md` | Korte uitleg, lokale MCP-tests en echte Entra-configuratie |
| `mcp_lab\` | Gateway, onafhankelijke testbron, gedeelde authenticatie en referentieclient |
| `tests\test_mcp_lab.py` | Echte HTTP/MCP-tests; Entra-uitwisseling blijft een testdouble |
| `infra\` | Bicep, image, lokale deployroute, Entra-bootstrap en veiligheidstests |
| `.archify\architecture-mcp-federation-20261015-092111\mcp-federation.html` | Eigen gateway, custom Rights Test MCP en 3P MCP-servers; nog ontwerp |

De `.archify`-diagrammen en `.copilot-azure`-sessies zijn lokale artifacts en
worden niet gepubliceerd. `.env` en `.deploy` blijven eveneens buiten Git.

## Tests uitvoeren

```powershell
npm test
```

Tests gebruiken alleen de standaardbibliotheek van Node.js. Ze controleren onder
meer rij-/kolom-/documentselectie, kanaalpariteit, ontbrekende consent, verkeerd
audience, ingetrokken rechten, promptclaims en scheiding van antwoordcaches.
Dit zijn simulator- en servertests, **geen integratietests tegen Microsoft of Databricks**.

## Verder bouwen

Volg voor de verduidelijkte vraag `docs\mcp-plan.md`: begin met **een echte
MCP-gateway, echte Entra-login en twee echte gebruikers**, met een kleine aparte
Rights Test MCP met eigen audience, eigen rechtencontrole en synthetische data.
Sluit daarna een echte bron aan en bewijs bronautorisatie voordat
je de tweede bron toevoegt. `docs\cloud-lab.md` blijft achtergrond bij de eerdere,
Foundry-centrale variant.
Voor provisioning moeten tenant, sandbox-resources, bevoegdheden, regio en budget
expliciet vaststaan. `infra\deploy.py` verzorgt daarna de eigen appregistraties,
consent, labrollen en Azure-deployment. Zonder `--apply` verandert het niets.
