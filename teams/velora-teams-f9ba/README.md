# Velora in Teams

Een kleine **Foundry-hosted** agent voor persoonlijke Teams-chats.
Gebaseerd op Kratos' idee van een agent met MCP-tools, zonder de uitgebreide
webapp, Cosmos DB, skillregistry en backendproxy.

**Status:** hosted deployment en OAuth-inrichting zijn uitgevoerd. De echte
persoonlijke Teams-aanmelding en twee-gebruikersproef moeten nog worden afgerond.

De Microsoft Activity-host SDK regelt de Teams-verbinding. GPT-5.4-mini kiest
alleen de tool: orders of marges. De tabellen komen rechtstreeks uit het
MCP-resultaat, niet uit een verzonnen modelantwoord. Er is geen gesprekshistorie
in de agent; iedere vraag staat op zichzelf.

## Hoe identiteit werkt

`Teams-gebruiker -> Bot OAuth -> hosted agent -> MCP -> OBO -> Fabric`

De OAuth-connection geeft een token voor **onze MCP**. De agent controleert via
`describe_access` dat de MCP dezelfde gebruiker ziet als de afzender in Teams.
Pas daarna mag een datatool worden aangeroepen. De bestaande MCP wisselt het
token via OBO om voor Fabric SQL; Fabric bepaalt tabel- en rijrechten.

De hosted agent-identiteit wordt alleen voor model- en kanaalverkeer gebruikt.
Zij krijgt geen algemene Fabric-datarechten. Een accountmismatch of authfout
geeft een expliciete fout, zonder andere identiteit of voorbeelddata als fallback.

De OAuth-client gebruikt een federated credential voor de Microsoft Bot Token
Store. Er is geen clientsecret in code, runtimeconfiguratie of Teams-package.
Dit is aanvankelijk gewone gebruikers-OAuth met een aanmeldknop, niet een claim
dat stille Teams SSO al is ingericht. Meld aan met hetzelfde account als in Teams.

## Proberen

Installeer het lokale `.deploy\velora-teams.zip` als persoonlijke aangepaste app.
Het pakket bevat concrete omgevings-ID's en blijft buiten de publieke repo.

1. Open **Velora data-assistent** in een persoonlijke Teams-chat.
2. Vraag: **Welke orders mag ik zien?** Volg zo nodig de aanmeldknop.
3. Vraag: **Welke marges mag ik zien?**
4. Herhaal met het andere testaccount. `/afmelden` wist de OAuth-verbinding voor
   de huidige Teams-gebruiker; het verandert geen wachtwoord of MFA-instelling.

Verwacht voor Noord: 3 Noord-orders, geen margedata.
Verwacht voor Finance: 6 orders en 6 margeregels.
De tekst "de aanvraag is niet gelukt" is geen geslaagde rechtenweigering.
Groepschats, kanalen en bestandsuploads zijn bewust niet ondersteund.

## Deployen

Deze submap is een afzonderlijk azd-project. Het parent-project blijft zijn
bestaande Bicep/deployscript gebruiken. De geëxporteerde Foundry-template vereist
de bestaande labresourcegroep en laat haar eigendomstags ongemoeid.

Vanuit deze map, met de eigen private azd-omgeving:

```powershell
$env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
azd provision --preview --no-prompt
azd provision --no-prompt
azd deploy velora-teams-f9ba --no-prompt
```

Vanuit de repositoryroot, na deployment:

```powershell
.\.venv\Scripts\python.exe teams\velora-teams-f9ba\scripts\configure_oauth.py --apply
.\.venv\Scripts\python.exe teams\velora-teams-f9ba\scripts\package_teams.py
```

De OAuth-inrichting controleert vastgelegde app-ID's, exclusief eigenaarschap,
een exacte redirect, de eigen bot en de eigen resourcegroep. Zij wijzigt geen
Fabric-rechten. De twee geselecteerde testers krijgen Foundry Agent Consumer
op dit aparte project.

De kern staat in `src/echo-activity/`; de mapnaam komt uit de officiële
starttemplate. Code deploy verpakt alleen `main.py`, `data_agent.py` en
`requirements.txt`. De live kanaalproef moet via Teams: `azd ai agent invoke`
ondersteunt het Activity-protocol niet.

## Privacy

Alleen voor het interne leerlab. De agent ontvangt je Teams-identiteit, bericht
en een gedelegeerd MCP-token. Het token wordt alleen gebruikt voor de vaste MCP.
Het model krijgt je vraag en toolbeschrijvingen, maar geen OAuth-token of
Fabric-rijen. Bronresultaten worden in je persoonlijke Teams-chat weergegeven.
Er is geen agentgesprekshistorie; Teams, Entra, Bot Token Store en Foundry
behouden hun eigen platformgegevens volgens de tenantinstellingen.
Technische foutcodes kunnen worden gelogd; tokens en ruwe foutinhoud worden
niet door de applicatie gelogd.

## Gebruik

Niet voor productie of echte bedrijfsgevoelige gegevens. Gebruik de toegewezen
testaccounts en opgeslagen demodata. Deze proef geeft geen algemene garantie
voor andere tools, kanalen of directe OneLake/Spark-toegang.

## Referenties

- [Kratos Agent](https://github.com/aiappsgbb/kratos-agent/tree/e063dc2db1475facba5da786a08d673d4160cd6d)
- [Foundry Activity-starttemplate](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/bring-your-own/activity/echo)
- [Gebruikers-OAuth met federated credentials](https://learn.microsoft.com/microsoft-365/agents-sdk/azure-bot-user-authorization-federated-credentials)
- [Ontwerpkeuze en bewijsgrenzen](../../docs/teams-hosted-route.md)
