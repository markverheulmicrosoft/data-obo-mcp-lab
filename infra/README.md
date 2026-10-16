# Lokaal deployen met een private .env

**Status: beide eigen MCP-apps draaien in Azure met gezonde actieve revisies.**
De publieke gateway heeft correcte discoverymetadata en weigert anonieme toegang;
Rights MCP blijft intern. De cloudroute gebruikt de bestaande managed identity
als federated clientcredential, zonder runtime-app-secret. Het Key Vault-
netwerkbeleid blijft intact. **De gerichte browser/OBO/Fabric-baseline met twee
testgebruikers is live uitgevoerd.** Dat geldt voor de geteste orders-, marges-
en gatewaycontrole; niet automatisch voor andere tools of clients.

## Eenmalig

Vanuit de projectroot, met Python 3.11+ en Azure CLI:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
Copy-Item infra\.env.example .env
```

Vul in `.env` de subscription, tenant, een unieke korte resourceprefix, regio
en de object-ID's van twee bestaande testgebruikers in. Laat de app-ID's
leeg: de deploy maakt die zelf. Gebruik voor deployment
een beheerlogin, niet een van de testgebruikers.

`main.parameters.json` is alleen een leesbaar voorbeeld met dummy-identiteiten.
De deploy gebruikt **eigen tijdelijke parameters uit jouw `.env`**, niet dat
voorbeeldbestand. Parameterbestanden bevatten geen secrets. De cloudroute
maakt geen app-secret en leest of schrijft geen Key Vault-secret.

## Plan bekijken

```powershell
.\.venv\Scripts\python.exe infra\deploy.py
```

Zonder `--apply` toont dit alleen het doel. Er zijn dan geen Azure- of
Entra-mutaties. De regio en subscription worden niet stilzwijgend gewijzigd.

## Daadwerkelijk deployen

Na beoordeling van doel en kosten:

```powershell
.\.venv\Scripts\python.exe infra\deploy.py --apply
```

Het script doet vijf dingen:

1. Maakt/hergebruikt de eigen herkenbaar gemarkeerde lab-appregistraties: twee
   API's, een native referentieclient en een aparte browser-SPA. Het richt delegated consent en de twee
   gebruikersrollen per API in. Geen gebruikerswachtwoorden worden gewijzigd.
2. Deployt de Azure-basis en managed-identityrollen. Tijdelijke placeholder-apps
   blijven intern en krijgen geen secrets. Bij een gewone redeploy wordt een
   bestaande echte app niet teruggezet naar zo'n placeholder.
3. Koppelt de gateway-managed identity aan uitsluitend de eigen gateway-app
   via een federated identity credential. Bouwt een image via ACR uit een
   strikte allowlist: de runtime-Python-modules, de twee lokaal gebundelde
   HTML-assets, `requirements.txt` en de Docker-buildbestanden.
4. Deployt het echte image met expliciete managed-identity-clientauthenticatie.
   Voor iedere deployment wordt what-if gecontroleerd; Delete of een ander
   resourcegroepscope stopt de uitvoering. Alleen de gateway wordt publiek; Rights MCP blijft
   intern. De bestaande vault blijft afgeschermd, maar is geen runtime-afhankelijkheid.
5. Controleert publieke discoverymetadata en een 401 voor een anonieme MCP-call.
   De echte URL's worden in de private `.env` opgeslagen.

**Managed identity vervangt niet de gebruiker.** Het MI-token heeft audience
`api://AzureADTokenExchange` en bewijst alleen welke gateway-app de OBO-aanvraag
doet. Het oorspronkelijke gebruikerstoken blijft de `user_assertion`; de
Rights MCP ontvangt een afzonderlijk delegated token voor die gebruiker.
Er is geen terugval naar een app-only datatoken of een clientsecret.

## Wat hiermee wel en niet is bewezen

De browserclient staat op `/`; `/mcp` blijft het beschermde protocolendpoint.
Zie `docs\weblab.md` voor de echte Fabric-bron, beperkte item-/SQL-rechten,
de browserproef en het verschil met de oude synthetische controlegroep.
Fabric gebruikt delegated SQL-toegang; dit is nog geen Fabric data-agent-MCP.

Een geslaagde controle bewijst dat de gateway het juiste endpoint/tenant
adverteert en anonieme toegang weigert. Het is **nog geen bewijs van OBO en
verschillende gebruikersrechten**.

Daarvoor:

```powershell
.\.venv\Scripts\python.exe -m mcp_lab.client
.\.venv\Scripts\python.exe -m mcp_lab.client list_orders
.\.venv\Scripts\python.exe -m mcp_lab.client read_margin
```

Meld bewust met de eerste en daarna de tweede testgebruiker aan.
Noord ziet alleen order `N-001`; Finance ziet beide orders en mag apart marges
opvragen. De userrollen op gateway en Rights MCP zijn onafhankelijk.

De interne Rights-URL is niet rechtstreeks vanaf je laptop bereikbaar; dat is
bedoeld. De gateway benadert die binnen de Container Apps-omgeving.

## Herhalen en fouten

- Dezelfde `.env` en `.deploy/state.json` horen bij hetzelfde doel. Een andere
  tenant, subscription, regio of prefix vereist bewuste nieuwe configuratie.
- Een naam/tag is geen eigendomsbewijs. Bestaande apps worden alleen hergebruikt
  met een vastgelegd app-ID, uitsluitend de huidige deployer als eigenaar,
  de verwachte rollen/consent en zonder extra redirects of clientcredentials.
  Bij onderbroken eerste inrichting kan expliciet herstel van een gecontroleerd
  app-ID nodig zijn; het script neemt geen app op naam over.
- Afwijkende rollen/consent of een afwijkende resourcegroep worden geweigerd.
- Een bestaande afwijkende federated credential wordt geweigerd, niet overschreven.
  De MI-client-ID selecteert de runtime-identiteit; haar principal-ID is het
  subject van de federated trust.
- Eventuele secrets uit de eerdere lokale route worden niet gebruikt, verwijderd
  of stilzwijgend geroteerd door de clouddeploy. Lokaal draaien met een eigen
  app-secret blijft mogelijk; zie `docs\bouwen.md`.
- CLI-fouten en een audittrail staan in de genegeerde `.deploy`-map. Het script
  claimt geen succes als een deployment, FQDN-controle of HTTP-controle faalt.
- `.env`, `.deploy` en `.copilot-azure` blijven buiten Git. De image bevat geen
  configuratie, tests, CLI-token-caches of deploymentcode.

## De bestanden

`deploy.py` is de volgorde en Azure CLI-aansturing. `entra.py` bevat alleen de
Graph-aanroepen voor de eigen drie lab-apps. `main.bicep` maakt de resourcegroep;
de kleine modules bevatten de Azure-resources. Eén Container App-module wordt
hergebruikt voor gateway en testbron.

## Lokaal controleren

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s infra -p "test_*.py" -v
az bicep build --file infra\main.bicep --stdout
```

De tests gebruiken mocks voor Azure/Graph en bewijzen geen live provisioning.
De gekozen GA API-versies zijn via de provider gecontroleerd. Oudere Bicep
typebibliotheken kunnen daarvoor nog BCP081-waarschuwingen geven; daadwerkelijke
ARM-validatie gebeurt ook in de deploy voordat resources worden aangemaakt.
