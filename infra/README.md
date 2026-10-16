# Lokaal deployen met een private .env

**Status: basisinfrastructuur en imagebouw zijn in Azure uitgevoerd; de eigen
MCP-diensten zijn nog niet live geverifieerd.** De huidige secret-route kan
door Key Vault-netwerkbeleid worden geblokkeerd. Het script zet dat beleid
niet uit. De lokale MCP-tests zijn geen bewijs van echte Entra/OBO.

## Eenmalig

Vanuit de projectroot, met Python 3.11+ en Azure CLI:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item infra\.env.example .env
```

Vul in `.env` de subscription, tenant, een unieke korte resourceprefix, regio
en de object-ID's van twee bestaande testgebruikers in. Laat de app-ID's en
het gateway-secret leeg: de deploy maakt die zelf. Gebruik voor deployment
een beheerlogin, niet een van de testgebruikers.

`main.parameters.json` is alleen een leesbaar voorbeeld met dummy-identiteiten.
De deploy gebruikt **eigen tijdelijke parameters uit jouw `.env`**, niet dat
voorbeeldbestand. Parameterbestanden bevatten geen secrets. Het app-secret
gaat vanuit het geheugen via de Key Vault-API naar de vault, nooit als
commandlinewaarde of tijdelijk secretbestand.

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

1. Maakt/hergebruikt drie herkenbaar gemarkeerde lab-appregistraties: twee
   API's en een public client. Het richt delegated consent en de twee
   gebruikersrollen per API in. Geen gebruikerswachtwoorden worden gewijzigd.
2. Deployt de Azure-basis en managed-identityrollen. Tijdelijke placeholder-apps
   blijven intern en krijgen geen secrets. Bij een gewone redeploy wordt een
   bestaande echte app niet teruggezet naar zo'n placeholder.
3. Bouwt een image via ACR uit een strikte allowlist: de vijf Python-modules,
   `requirements.txt` en de Docker-buildbestanden.
4. Slaat het secret via de Key Vault-API op en deployt het echte image.
   Voor iedere deployment wordt what-if gecontroleerd; Delete of een ander
   resourcegroepscope stopt de uitvoering. Alleen de gateway wordt publiek; Rights MCP blijft
   intern. Het gateway-secret komt uit Key Vault, alleen leesbaar door de
   gateway-identiteit.
5. Controleert publieke discoverymetadata en een 401 voor een anonieme MCP-call.
   De echte URL's worden in de private `.env` opgeslagen.

## Wat hiermee wel en niet is bewezen

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
- Secrets worden niet stilzwijgend geroteerd. Het gegenereerde app-secret is
  90 dagen geldig. Bestaande credentials worden lokaal of uit Key Vault hergebruikt.
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
