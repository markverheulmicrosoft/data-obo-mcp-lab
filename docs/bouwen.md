# De kleine MCP-proef uitvoeren

## Wat is gebouwd?

Vier modules, zonder FastAPI, database of LLM:

| Bestand | Verantwoordelijkheid |
|---|---|
| `mcp_lab\auth.py` | JWT controleren; dezelfde toolpolicy bij `tools/list` en `tools/call` |
| `mcp_lab\gateway.py` | Toegestane call via Entra OBO doorgeven aan de test-MCP |
| `mcp_lab\rights_mcp.py` | Zelfstandig authenticeren en toegestane records/velden selecteren |
| `mcp_lab\client.py` | Met een echte gebruiker aanmelden en tools opvragen/aanroepen |

De gateway en testbron gebruiken dezelfde validatiecode, maar **verschillende
app-ID's, tokens en roltoewijzingen**. Ze draaien als twee aparte servers.
De gateway is bewust geen generieke proxy: alleen `list_orders` en `read_margin`
kunnen naar een vast geconfigureerde testbron.

## Wat gebeurt er bij een call?

1. De client vraagt met MSAL een gebruikerstoegangstoken voor de gateway.
2. De gateway controleert signatuur, issuer, tenant, audience, geldigheid,
   v2-gebruikersclaims en `access_as_user`.
3. De gateway controleert of de gebruiker de tool mag gebruiken.
4. Alleen na toestemming wisselt MSAL het inkomende token via OBO om voor
   een token voor de Rights Test MCP.
5. De gateway roept dezelfde toolnaam via MCP/HTTP aan op de testbron.
6. De testbron valideert zelf haar eigen token en rol, filtert haar data en
   geeft uitsluitend dat resultaat terug.

Een geweigerde tool bereikt OBO of de testbron niet. Een gateway-token werkt
niet rechtstreeks op de testbron. Toolargumenten kunnen geen gebruiker of
rol kiezen: deze twee tools accepteren geen argumenten.

## Lokaal bewijs zonder tenantconfiguratie

Python 3.11 of nieuwer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_mcp_lab.py" -v
```

In deze workspace bestaat `.venv` al. De directe runtimeversies staan vast in
`requirements.txt`; de gebruikte MCP-SDK is 2.3.0.

De tests starten echte HTTP-servers op tijdelijke loopbackpoorten en gebruiken
de echte MCP-client en echte RSA/JWT-signatuurvalidatie. Alleen het ophalen van
Entra-signingsleutels en de Entra-tokenuitwisseling worden vervangen door
testdoubles. Private testsleutels bestaan uitsluitend in het geheugen.

**Lokaal aangetoond:** toolfiltering, weigering van direct aangeroepen verborgen
tools, aparte audiences, verkeerde/verlopen tokens, ontbrekende scopes,
onafhankelijke bronrollen, rij-/veldselectie, foutafhandeling zonder app-only
fallback en isolatie tussen gelijktijdige clients.

**Niet daarmee aangetoond:** echte Entra-login/OBO/consent, werkelijke
intrekkingsvertraging, Copilot-/Scout-compatibiliteit of Fabric-/Databricks-toegang.
Deze tests draaien niet met een echte tenant en zijn geen bewijs van database-RLS.

## Rollen en verwachte resultaten

Maak op **beide** API-appregistraties de gebruikersrollen `North.Reader` en
`Finance.Reader`. Wijs gebruikers afzonderlijk toe op beide enterprise apps.
De rolnaam is gelijk, maar de bron vertrouwt uitsluitend de rol in haar eigen token.

| Rol op de betreffende server | Tools | Data in de testbron |
|---|---|---|
| `North.Reader` | `list_orders` | Alleen `N-001`, regio Noord, omzet 1200; geen marge |
| `Finance.Reader` | Beide tools | Orders Noord/Zuid; aparte margeresultaten 240 en 540 |
| Geen van deze rollen | Geen | Geen data |

`list_orders` bevat ook voor Finance geen margeveld. Daarvoor is de afzonderlijk
beveiligde tool `read_margin` nodig.

De belangrijkste kruisproef: geef een gebruiker `Finance.Reader` op de gateway
maar alleen `North.Reader` op de testbron. De gateway laat `read_margin` door,
maar de bron moet hem alsnog weigeren. Ook `list_orders` blijft dan tot Noord
beperkt. De gateway kan bronrollen niet zelf toekennen.

## Echte Entra-proef

Er zijn drie appregistraties in dezelfde testtenant nodig:

| Registratie | Configuratie |
|---|---|
| Gateway API | App-ID als audience; App ID URI `api://<gateway-id>`; delegated scope `access_as_user`; beide gebruikersrollen |
| Rights API | Ander app-ID; URI `api://<rights-id>`; eigen scope `access_as_user`; eigen gebruikersrollen |
| Referentieclient | Public/native client; redirect URI `http://localhost`; delegated toegang tot de gateway-scope; geen clientsecret |

Beide API's moeten v2-access-tokens uitgeven (`api.requestedAccessTokenVersion: 2`).
De gateway-app vraagt delegated toegang tot de Rights API en krijgt daarvoor
de benodigde consent. De gateway gebruikt een confidential-clientcredential
voor OBO. De referentieclient gebruikt interactieve login met PKCE, geen wachtwoordflow.

Kopieer `.env.example` naar `.env` en vul tenant- en app-ID's in. Het
gateway-secret hoort alleen lokaal in `.env` of in de secretconfiguratie van
de hostingomgeving; nooit in Git, chat, commandlineargumenten of logs.
Deze app start niet zonder de vereiste configuratie en heeft geen anonieme fallback.

Start in afzonderlijke terminals, vanuit de projectmap:

```powershell
# Terminal 1: de bron
.\.venv\Scripts\python.exe -m mcp_lab.rights_mcp
```

```powershell
# Terminal 2: de gateway
.\.venv\Scripts\python.exe -m mcp_lab.gateway
```

```powershell
# Terminal 3: iedere run laat je bewust een account kiezen
.\.venv\Scripts\python.exe -m mcp_lab.client
.\.venv\Scripts\python.exe -m mcp_lab.client list_orders
.\.venv\Scripts\python.exe -m mcp_lab.client read_margin
```

Geen toolargument betekent `tools/list`. Een geweigerde tool geeft een zichtbaar
MCP-foutresultaat en de client eindigt met exitcode 1.
Tokens worden niet afgedrukt of door deze client op schijf opgeslagen.

Zonder token moet `/mcp` een 401 met `WWW-Authenticate` opleveren. De openbare
discoverymetadata staat op `/.well-known/oauth-protected-resource/mcp`.
De scope in die metadata is volledig: `api://<app-id>/access_as_user`.
Omdat Entra een app-ID als audience gebruikt, controleert onze verifier dat
expliciet; een HTTP-resource-URL wordt niet als Entra-audience verondersteld.

## Wat bewust nog ontbreekt

- Fabric-/Databricks-routes: eerst deze twee grenzen live bewijzen.
- Copilot-/Scout-configuratie: iedere client heeft eigen OAuth-voorwaarden.
- Algemene tool-discovery/proxying, responsecache, agentconversaties en UI.
- Een garantie op onmiddellijke rechtenintrekking: bestaande tokens kunnen
  geldig blijven tot hun verloop; meet dat in de echte tenant.
- Cross-tenant toegang: deze proef gebruikt bewust een vaste testtenant.

De servers loggen de laag, tool, gebruikersobject-ID en toestemming; nooit bearer
tokens. Behandel ook die object-ID's als afgeschermde testtelemetry en bewaar
logs niet langer dan nodig. Gebruik geen echte bedrijfsdata in deze proef.

## Live deployment

De gebruiker heeft Zava aangewezen; toegang en beheerrechten zijn read-only
bevestigd. De publieke hosting, appregistraties en deploypipeline zijn nog niet
aangemaakt. Dat volgt na keuze en goedkeuring van de concrete deployment.
De servers gebruiken lokaal standaard loopback en poort 8000/8001.
`HOST` en `PORT` kunnen per proces worden ingesteld; containerhosting gebruikt
expliciet `HOST=0.0.0.0`. Zet geen gedeelde `PORT` in de lokale `.env` als beide
servers tegelijk draaien. `GATEWAY_URL` en `RIGHTS_URL` zijn de daadwerkelijke
MCP-URL's voor discovery en bronverkeer, niet de luisterinterface.

## Bronnen voor de implementatie

- [Python MCP SDK: resource-serverauthenticatie](https://py.sdk.modelcontextprotocol.io/run/authorization/)
- [Python MCP SDK: HTTP-clienttransport](https://py.sdk.modelcontextprotocol.io/client/transports/)
- [MSAL Python: tokens verwerven, inclusief OBO](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
