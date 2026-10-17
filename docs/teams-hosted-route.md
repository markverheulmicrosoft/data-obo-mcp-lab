# Minimale Foundry-hosted Teams-route

## Keuze

Een eigen, kleine Foundry-hosted Activity-agent voor persoonlijke Teams-chats,
met de bestaande MCP als enige databron. De gebruiker koos een nieuw apart
Foundry-project in het bestaande lab. De bestaande MCP/Fabric-baseline blijft
ongewijzigd.

Uit [Kratos](https://github.com/aiappsgbb/kratos-agent/tree/e063dc2db1475facba5da786a08d673d4160cd6d)
nemen we het ontwerp over: een agent met MCP-tools, en gebruikerscredentials
buiten de modeltekst. Niet de complete webapp, Cosmos DB, skillregistry,
opslag, warm pool of Copilot Studio-bridge. Die bridge levert in de onderzochte
versie geen delegated MCP-token mee en is dus niet de rechtenproef die we nodig hebben.

De lokale implementatie gebruikt de officiële
[Activity-startbasis](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/bring-your-own/activity/echo).
De modelselectie is klein: het model kiest orders en/of marges; tabellen worden
rechtstreeks uit echte MCP-resultaten weergegeven. Tokens en bronrijen gaan
niet naar het model. Er is geen gedeelde gesprekshistorie.

## Identiteiten

- Entra meldt de gebruiker aan via een Azure Bot OAuth connection.
- De Activity-handler vraagt het token op via de Agents SDK. De scope is die
  van onze MCP, niet Microsoft Graph of het Foundry-invocation-endpoint.
- De MCP valideert het token. De handler vergelijkt `describe_access.user.id`
  met de afzender uit de geauthenticeerde Teams-activity voordat data wordt gevraagd.
- De bestaande gateway doet OBO naar Fabric SQL. Fabric beslist over rijen
  en tabellen; agentrollen vervangen dat niet.
- De hosted agent-identiteit wordt alleen gebruikt voor platform/model/channel-
  verbindingen. Zij krijgt geen Fabric-datarechten als fallback.

De aparte OAuth-client kan een
[Bot Token Store federated credential](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/azure-bot-user-authorization-federated-credentials)
gebruiken. Dat is een andere trust dan de bestaande gateway-MI-federatie.
De [Agents SDK OAuth-configuratie](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/agent-oauth-configuration)
ondersteunt een connection per resource en per-route gebruikersauthenticatie.

## Wat nog echt bewezen moet worden

De bouwstenen zijn officieel beschikbaar:
[Activity-host SDK](https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/agentserver/azure-ai-agentserver-activity),
[hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents)
en Agents SDK OAuth. Dat bewijst nog niet dat de combinatie in deze tenant werkt.
De beslissende proef is de Bot-tokenservice vanuit de hosted agent, gevolgd door
een persoonlijke Teams-aanmelding en dezelfde twee-gebruikersmatrix als in de webapp.

Sweden Central staat in de hosted-agent-regiolijst. De daadwerkelijke
Activity-uitrol, quota en tenantvoorwaarden blijven een deploymentcontrole.
BotServiceRbac vereist passende Foundry-toegang; dat moet beperkt blijven tot
de eigen project-/agentscope en de twee testaccounts.

Als een extra Teams-adapter nodig blijkt, blijft de agentloop in Foundry.
Zo'n adapter is extra infrastructuur, niet automatisch een niet-hosted agent.
Gebruik dan uitsluitend de gedocumenteerde
[x-client-tokenheaders](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/use-on-behalf-of-flow)
en [runtimecontract](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agent-contract).
Kratos' eigen JSON-bodytransport is geen bewijs voor de huidige platformgaranties.

## Afbakening

Alleen persoonlijke chats, vaste tenant, maximaal twee vaste read-only tools
per bericht, geen willekeurige SQL/URL/identiteitsargumenten. Groepschats en
kanalen worden afgewezen. Een onbekende tool, accountmismatch of authfout
geeft een expliciete fout, nooit een app-only of fixturefallback.

Modeldataresidency, hosted sessiekosten en OAuth-tokenopslag zijn afzonderlijke
platformeigenschappen; alleen een Azure-resourceregio noemen bewijst die niet.
