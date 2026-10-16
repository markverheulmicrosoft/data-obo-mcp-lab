import "./csp.mjs";
import { BrowserCacheLocation, CacheLookupPolicy, PublicClientApplication } from "@azure/msal-browser";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport, StreamableHTTPError } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { CfWorkerJsonSchemaValidator } from "@modelcontextprotocol/sdk/validation/cfworker";
import {
  EXPERIMENTS, LabError, classifyResult, compareOutcome, expectedOutcome,
  isAdminSession, redact, resultData, safeErrorCode, safeSourceUrl,
  validAccessData, validTableData, validateConfig,
} from "./presentation.mjs";

const $ = (id) => document.getElementById(id);
const state = {
  config: null, msal: null, ready: false, busy: false, account: null,
  access: null, tools: null, records: [], selected: null, admin: false, authLost: false,
  sessionVersion: 0, closeActive: null,
};
const statusLabels = {
  idle: "Nog geen aanroep", busy: "Aanroep loopt", info: "Informatie ontvangen",
  allowed: "Data ontvangen", denied: "Weigering waargenomen",
  blocked: "Niet bevestigd", action: "Actie nodig",
};
const dateFormat = new Intl.DateTimeFormat("nl-NL", { dateStyle: "short", timeStyle: "medium" });
const timeFormat = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(redact(text));
  if (className) element.className = className;
  return element;
}

function notice(text, kind = "info") {
  $("notice").textContent = text;
  $("notice").dataset.kind = kind;
}

function status(value) {
  $("result-status").dataset.status = value;
  $("result-status").textContent = statusLabels[value] ?? statusLabels.blocked;
}

function updateControls() {
  $("login").disabled = !state.ready || state.busy;
  $("login").hidden = Boolean(state.account);
  for (const id of ["switch-account", "logout"]) {
    $(id).hidden = !state.account;
    $(id).disabled = !state.account || state.busy || (id === "switch-account" && !state.ready);
  }
  $("admin-account").disabled = !state.account || state.busy;
  for (const button of document.querySelectorAll("[data-run]")) {
    button.disabled = !state.account || !state.ready || state.busy;
  }
  for (const button of document.querySelectorAll("#history-list button")) button.disabled = state.busy;
  $("result-panel").setAttribute("aria-busy", String(state.busy));
}

function renderIdentity() {
  const account = state.account;
  $("account-name").textContent = account ? redact(account.name || account.username || "Aangemelde gebruiker") : "Nog niet aangemeld";
  $("account-detail").textContent = account
    ? redact(account.username || "Microsoft-account aangemeld; vraag de gateway-identiteit op met experiment 1.")
    : "Meld aan met een North- of Finance-testaccount, niet met een beheeraccount.";
  $("account-icon").textContent = account ? "✓" : "?";
  $("gateway-user").textContent = redact(state.access?.user.id ?? "Nog niet opgehaald");
  $("gateway-roles").textContent = state.access
    ? redact(state.access.user.roles.join(", ") || "Geen approllen in het JWT")
    : "Nog niet opgehaald";
  const source = state.access?.sources.find((item) => item?.id === "fabric");
  const sourceLabels = {
    configured: "Geconfigureerd · nog geen bewijs van querytoegang",
    not_configured: "Nog niet geconfigureerd · geen bronresultaten",
  };
  $("source-status").textContent = source
    ? sourceLabels[source.status] ?? "Onbekende bronstatus ontvangen"
    : state.access ? "Geen Fabric-bron in de respons" : "Status nog niet opgehaald";
  const url = state.account && safeSourceUrl(source?.url);
  $("source-link").hidden = !url;
  if (url) $("source-link").href = url;
  else $("source-link").removeAttribute("href");
  $("source-link-description").textContent = url
    ? "Deze itemlink komt uit de geauthenticeerde describe_access-respons. Een link is geen bewijs dat je het item of alle tabellen mag lezen."
    : state.access
      ? "De gateway heeft geen geldige Fabric-itemlink teruggegeven. Er wordt geen URL geraden."
      : "De echte Fabric-itemlink verschijnt pas na aanmelden én een geslaagde describe_access-aanroep. We verzinnen geen bron-URL.";
  updateControls();
}

function renderEmpty(message) {
  const empty = node("div", undefined, "empty-state");
  empty.append(node("div", "↗", "empty-symbol"), node("h3", "Nog geen bewijs in deze sessie"));
  empty.append(node("p", message || "Meld aan en voer een experiment uit. Alleen werkelijk ontvangen antwoorden verschijnen hier."));
  empty.append(node("p", "Verwachtingen zijn geen resultaten. Er is geen fallback naar voorbeelddata.", "small"));
  $("result-body").replaceChildren(empty);
  $("result-title").textContent = "De waarneming";
  $("history").hidden = true;
  $("history-list").replaceChildren();
  status("idle");
  for (const card of document.querySelectorAll("[data-experiment]")) delete card.dataset.selected;
}

function clearSessionView() {
  state.sessionVersion += 1;
  state.account = null;
  state.access = null;
  state.tools = null;
  state.records = [];
  state.selected = null;
  state.admin = false;
  $("admin-account").checked = false;
  state.msal?.setActiveAccount(null);
  renderIdentity();
  renderEmpty("De vorige identiteit en alle gegevens zijn gewist. Meld opnieuw aan om een nieuwe, echte waarneming te doen.");
}

async function clearTokenCache() {
  if (!state.msal) return true;
  try {
    await state.msal.clearCache();
    return true;
  } catch (error) {
    state.ready = false;
    notice(`De lokale aanmeldcache kon niet worden gewist (${safeErrorCode(error, "CACHE_CLEAR_FAILED")}). Sluit dit tabblad en open het lab opnieuw. Aanroepen zijn geblokkeerd.`, "error");
    updateControls();
    return false;
  }
}

async function authFailure(error, timing) {
  const code = safeErrorCode(error, "AUTHENTICATION_FAILED");
  clearSessionView();
  const cacheCleared = await clearTokenCache();
  if (cacheCleared) {
    const explanation = code === "OBO_FAILED"
      ? "Entra OBO is mislukt. Laat consent en delegatieconfiguratie controleren en meld opnieuw aan; dit is geen bewezen bronweigering."
      : "Aanmelding of tokenvernieuwing vereist actie. Meld opnieuw aan; als dat niet lukt, controleer consent en appconfiguratie met de beheerder.";
    notice(`${explanation} Foutcode: ${code}. Oude identiteit en resultaten zijn gewist.`, "warning");
  }
  const panel = node("div", undefined, "empty-state");
  panel.append(node("h3", "Actie nodig — geen rechtenconclusie"));
  panel.append(node("p", `De aanmelding of delegatie is niet bruikbaar (${code}). Er worden geen oude gegevens, beheertokens of voorbeeldresultaten getoond.`));
  if (timing) panel.append(node("p", `${dateFormat.format(timing.started)} · ${formatDuration(timing.elapsed)} totale clientactie`, "small"));
  $("result-body").replaceChildren(panel);
  status("action");
}

function formatDuration(milliseconds) {
  return milliseconds < 1000
    ? `${Math.round(milliseconds)} ms`
    : `${(milliseconds / 1000).toLocaleString("nl-NL", { maximumFractionDigits: 2 })} s`;
}

async function login() {
  if (state.busy || !state.ready) return;
  state.busy = true;
  state.authLost = false;
  clearSessionView();
  updateControls();
  notice("Kies in het Microsoft-venster het account waarmee je dit experiment echt wilt uitvoeren.");
  try {
    if (!await clearTokenCache()) return;
    const sessionVersion = state.sessionVersion;
    const response = await state.msal.loginPopup({
      scopes: [state.config.scope],
      prompt: "select_account",
      httpMethod: "GET",
      redirectUri: `${window.location.origin}/auth.html`,
    });
    if (sessionVersion !== state.sessionVersion) throw new LabError("SESSION_CHANGED", "auth");
    if (!response.account) throw new LabError("ACCOUNT_MISSING", "auth");
    state.account = response.account;
    state.msal.setActiveAccount(response.account);
    renderIdentity();
    notice("Microsoft-aanmelding geslaagd. Voer experiment 1 uit: de gateway-identiteit en bronrechten zijn nog niet gecontroleerd.", "success");
    renderEmpty("Je bent aangemeld. Begin met experiment 1 om de gateway-identiteit en aangeboden tools op te halen.");
  } catch (error) {
    await authFailure(error);
  } finally {
    state.busy = false;
    updateControls();
  }
}

async function logout() {
  if (state.busy || !state.account) return;
  const account = state.account;
  state.busy = true;
  clearSessionView();
  updateControls();
  notice("Identiteit en alle waarnemingen zijn gewist. Microsoft-afmelding afronden…");
  try {
    await state.msal.logoutPopup({
      account,
      postLogoutRedirectUri: `${window.location.origin}/auth.html`,
    });
    if (await clearTokenCache()) notice("Afgemeld. Alle resultaten en tokens van deze client zijn gewist. De volgende aanmelding vraagt opnieuw een accountkeuze.");
  } catch (error) {
    if (await clearTokenCache()) {
      notice(`De client is lokaal leeggemaakt, maar Microsoft-afmelding is niet bevestigd (${safeErrorCode(error, "LOGOUT_FAILED")}). Sluit zo nodig dit tabblad; kies bij opnieuw aanmelden expliciet het juiste account.`, "warning");
    }
  } finally {
    state.busy = false;
    updateControls();
  }
}

async function withMcp(action, cleanup) {
  const account = state.account;
  const sessionVersion = state.sessionVersion;
  let accessToken = "";
  try {
    const response = await state.msal.acquireTokenSilent({
      scopes: [state.config.scope], account, redirectUri: `${window.location.origin}/auth.html`,
      // Avoid MSAL's dynamically styled iframe fallback under the hash-only CSP.
      cacheLookupPolicy: CacheLookupPolicy.AccessTokenAndRefreshToken,
    });
    if (!response.accessToken || response.account?.homeAccountId !== account.homeAccountId ||
        sessionVersion !== state.sessionVersion) {
      throw new LabError("TOKEN_ACCOUNT_MISMATCH", "auth");
    }
    accessToken = response.accessToken;
  } catch (error) {
    throw new LabError(safeErrorCode(error, "TOKEN_ACQUISITION_FAILED"), "auth");
  }

  const endpoint = new URL(state.config.gatewayUrl);
  let active = true;
  // Only this guarded fetch receives the token; redirects and other endpoints are rejected.
  const guardedFetch = async (input, init = {}) => {
    const target = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    if (!active || sessionVersion !== state.sessionVersion ||
        target.href !== endpoint.href || target.origin !== window.location.origin) {
      throw new LabError("MCP_DESTINATION_REJECTED");
    }
    if (!accessToken || state.authLost) throw new LabError("TOKEN_UNAVAILABLE", "auth");
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    headers.set("Authorization", `Bearer ${accessToken}`);
    const response = await fetch(input, {
      ...init, headers, redirect: "error", credentials: "omit", cache: "no-store",
      mode: "same-origin", referrerPolicy: "no-referrer",
    });
    if (response.status === 401) {
      accessToken = "";
      state.authLost = true;
      clearSessionView();
      notice("De gateway accepteert de aanmelding niet meer (HTTP_401). Oude identiteit en resultaten zijn direct gewist; meld opnieuw aan.", "warning");
      throw new LabError("HTTP_401", "auth");
    }
    return response;
  };
  const transport = new StreamableHTTPClientTransport(endpoint, {
    fetch: guardedFetch,
    requestInit: { cache: "no-store", credentials: "omit", redirect: "error" },
    reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 1000, maxReconnectionDelay: 1000, reconnectionDelayGrowFactor: 1 },
  });
  const client = new Client({ name: "fabric-obo-browser-lab", version: "1.0.0" }, {
    capabilities: {},
    jsonSchemaValidator: new CfWorkerJsonSchemaValidator(),
  });
  state.closeActive = () => {
    active = false;
    accessToken = "";
    return transport.close();
  };
  let connected = false;
  try {
    await client.connect(transport);
    connected = true;
    if (state.authLost) throw new LabError("HTTP_401", "auth");
    const result = await action(client);
    if (state.authLost) throw new LabError("HTTP_401", "auth");
    return result;
  } finally {
    active = false;
    accessToken = "";
    state.closeActive = null;
    try {
      if (connected) await client.close();
      else await transport.close();
    } catch (error) {
      cleanup.code = safeErrorCode(error, "TRANSPORT_CLOSE_FAILED");
      state.ready = false;
    }
  }
}

function classifyRequestError(error) {
  if (error instanceof StreamableHTTPError && error.code === 403) {
    return {
      status: "denied", layer: "Gateway · HTTP", code: "HTTP_403",
      title: "Het HTTP-verzoek is geweigerd",
      explanation: "De gateway retourneerde HTTP 403. Er is geen specifieke TOOL_FORBIDDEN- of SOURCE_FORBIDDEN-respons: dit bewijst niet welke rolcheck faalde en test geen SQL-rechten.",
    };
  }
  const code = error instanceof StreamableHTTPError && Number.isInteger(error.code)
    ? `HTTP_${error.code}` : safeErrorCode(error);
  return {
    status: "blocked", layer: "Client / MCP / verbinding", code,
    title: "Geen bruikbare toolrespons ontvangen",
    explanation: "De aanroep is niet bevestigd. Controleer verbinding, gateway en toolbeschikbaarheid. Een ontbrekende tool, time-out of protocolfout is geen geslaagde autorisatieweigering.",
  };
}

async function runExperiment(key) {
  if (state.busy || !state.account || !EXPERIMENTS[key]) return;
  state.busy = true;
  state.authLost = false;
  const started = new Date();
  const clock = performance.now();
  const experiment = EXPERIMENTS[key];
  const record = {
    id: crypto.randomUUID(), key, started,
    account: state.account.username || state.account.name || "Aangemelde gebruiker",
    roles: state.access?.user.roles ? [...state.access.user.roles] : null,
    admin: isAdminSession(state.access?.user.roles, state.admin),
    operations: [], raw: null, data: null, outcome: null,
  };
  const cleanup = {};
  if (key === "discovery") {
    state.access = null;
    state.tools = null;
    record.roles = null;
    renderIdentity();
  }
  updateControls();
  status("busy");
  $("result-title").textContent = experiment.title;
  const pending = node("div", undefined, "empty-state");
  pending.append(node("h3", "Echte aanroep wordt uitgevoerd"));
  pending.append(node("p", "Token ophalen, MCP verbinden en de aanvraag versturen. Er wordt nog geen uitkomst aangenomen."));
  $("result-body").replaceChildren(pending);
  notice(`${experiment.title} gestart om ${timeFormat.format(started)}. Accountkeuze en andere aanroepen zijn tijdelijk geblokkeerd.`);

  const measure = async (label, action) => {
    const operation = { label, started: new Date(), elapsed: 0, received: false };
    const start = performance.now();
    try {
      const result = await action();
      operation.received = true;
      operation.toolError = Boolean(result?.isError);
      return result;
    } finally {
      operation.elapsed = performance.now() - start;
      record.operations.push(operation);
    }
  };
  try {
    await withMcp(async (client) => {
      if (key === "discovery") {
        const tools = [];
        const pages = [];
        const cursors = new Set();
        let cursor;
        do {
          const page = await measure("tools/list", () => client.listTools(cursor ? { cursor } : {}, { timeout: 30000 }));
          tools.push(...page.tools);
          pages.push(page);
          cursor = page.nextCursor;
          if (cursor && (cursors.has(cursor) || pages.length >= 30)) throw new LabError("DISCOVERY_PAGINATION_INVALID");
          if (cursor) cursors.add(cursor);
        } while (cursor);
        state.tools = redact(tools);
        record.tools = state.tools;
        if (!tools.some((tool) => tool.name === "describe_access")) {
          record.outcome = {
            status: "blocked", layer: "Gateway · discovery", code: "DESCRIBE_ACCESS_MISSING",
            title: "Tools ontvangen, identiteitstool ontbreekt",
            explanation: "describe_access wordt niet aangeboden. De gatewayrollen, bronstatus en echte itemlink zijn daarom niet vastgesteld. Er wordt geen werkende Fabric-koppeling aangenomen.",
          };
          record.roles = null;
          record.raw = redact({ discovery: pages });
          return;
        }
        const result = await measure("tools/call · describe_access", () => client.callTool(
          { name: "describe_access", arguments: {} }, undefined, { timeout: 60000 },
        ));
        record.outcome = classifyResult("describe_access", result);
        record.raw = redact({ discovery: pages, describe_access: result });
        if (!result.isError && validAccessData(resultData(result))) {
          state.access = redact(resultData(result));
          record.data = state.access;
          record.roles = [...state.access.user.roles];
          record.admin = isAdminSession(record.roles, state.admin);
        }
      } else {
        const result = await measure(`tools/call · ${experiment.tool}`, () => client.callTool(
          { name: experiment.tool, arguments: {} }, undefined, { timeout: 60000 },
        ));
        record.outcome = classifyResult(experiment.tool, result);
        record.raw = redact(result);
        if (!result.isError && validTableData(resultData(result))) record.data = redact(resultData(result));
        if (state.tools && !state.tools.some((tool) => tool.name === experiment.tool)) record.notDiscovered = true;
      }
      if (record.outcome.code === "OBO_FAILED") throw new LabError("OBO_FAILED", "auth");
    }, cleanup);
  } catch (error) {
    if (state.authLost || error?.kind === "auth" ||
        (error instanceof StreamableHTTPError && error.code === 401)) {
      await authFailure(state.authLost ? new LabError("HTTP_401", "auth") : error, {
        started, elapsed: performance.now() - clock,
      });
      return;
    }
    record.outcome = classifyRequestError(error);
    record.raw = { clientError: record.outcome.code };
    record.data = null;
  } finally {
    state.busy = false;
    updateControls();
  }
  record.elapsed = performance.now() - clock;
  record.cleanupCode = cleanup.code;
  state.records.unshift(record);
  state.records = state.records.slice(0, 12);
  state.selected = record.id;
  renderIdentity();
  renderRecord(record);
  renderHistory();
  const kind = record.outcome.status === "allowed" ? "success" : record.outcome.status === "blocked" ? "warning" : "info";
  notice(cleanup.code
    ? `Aanroep afgerond, maar het sluiten van de verbinding is niet bevestigd (${cleanup.code}). Heropen het lab voor een volgende test.`
    : `${experiment.title}: ${record.outcome.title}. ${record.outcome.code}. Vergelijk de verwachting met de waarneming.`, cleanup.code ? "warning" : kind);
}

function fact(list, label, value, full = false) {
  const entry = node("div", undefined, full ? "full" : undefined);
  entry.append(node("dt", label), node("dd", value));
  list.append(entry);
}

function appendDataTable(parent, data) {
  const wrapper = node("div", undefined, "table-wrap");
  wrapper.tabIndex = 0;
  wrapper.setAttribute("role", "region");
  wrapper.setAttribute("aria-label", "Teruggegeven Fabric-demodata, horizontaal scrollbaar");
  const table = node("table");
  table.append(node("caption", `${data.rows.length} rijen ontvangen · opgeslagen synthetische demodata · geen productiebedrijfsdata`));
  const head = node("thead");
  const headerRow = node("tr");
  for (const column of data.columns) {
    const th = node("th", column);
    th.scope = "col";
    headerRow.append(th);
  }
  head.append(headerRow);
  table.append(head);
  const body = node("tbody");
  for (const row of data.rows) {
    const tr = node("tr");
    data.columns.forEach((column, index) => {
      const value = Array.isArray(row) ? row[index] : row[column];
      tr.append(node("td", value === null ? "null" : value === undefined ? "—"
        : typeof value === "object" ? JSON.stringify(value) : String(value)));
    });
    body.append(tr);
  }
  table.append(body);
  wrapper.append(table);
  parent.append(wrapper);
  if (data.rows.length === 0) parent.append(node("p", "De query gaf geen rijen terug. Dat kan door RLS of lege data komen; het is geen expliciete weigering.", "small muted"));
}

function renderRecord(record) {
  const experiment = EXPERIMENTS[record.key];
  const admin = isAdminSession(record.roles, state.admin || record.admin);
  $("result-title").textContent = experiment.title;
  status(record.outcome.status);
  for (const card of document.querySelectorAll("[data-experiment]")) card.dataset.selected = String(card.dataset.experiment === record.key);
  const body = document.createDocumentFragment();
  const grid = node("div", undefined, "evidence-grid");
  const expected = node("section", undefined, "evidence");
  expected.append(node("p", "Verwacht · initiële inrichting", "eyebrow"));
  expected.append(node("p", expectedOutcome(experiment.tool, record.roles, admin)));
  const actual = node("section", undefined, "evidence actual");
  actual.append(node("p", "Werkelijk · ontvangen respons", "eyebrow"));
  actual.append(node("h4", record.outcome.title), node("p", record.outcome.explanation));
  grid.append(expected, actual);
  body.append(grid);
  body.append(node("p", compareOutcome(experiment.tool, record.roles, record.outcome, record.data, admin), "comparison"));
  if (admin) body.append(node("p", "Beheeraccount: deze waarneming is uitgesloten als bewijs voor beperkte gebruikersrechten. Gebruik een apart testaccount.", "notice"));
  const facts = node("dl", undefined, "facts");
  fact(facts, "Gestart · lokale tijd", dateFormat.format(record.started));
  fact(facts, "Totale clientactie · incl. token en MCP-verbinding", formatDuration(record.elapsed));
  fact(facts, "Waargenomen laag", record.outcome.layer);
  fact(facts, "Resultaatcode", record.outcome.code);
  fact(facts, "Microsoft-account bij deze aanroep", record.account, true);
  fact(facts, "Laatst opgehaalde gatewayrollen in deze sessie", record.roles ? record.roles.join(", ") || "Geen approllen" : "Niet opgehaald", true);
  if (record.data && typeof record.data.source_principal === "string") {
    fact(facts, "Bronidentiteit · SQL USER_NAME(), zoals de tool retourneert", record.data.source_principal, true);
  }
  if (record.data?.authentication) fact(facts, "Authenticatie · zoals de tool retourneert", record.data.authentication);
  if (record.data?.source) fact(facts, "Bron · zoals de tool retourneert", record.data.source);
  body.append(facts);
  if (record.notDiscovered) body.append(node("p", "Deze tool stond niet in de laatst ontvangen discoverylijst. De knop heeft toch een echte directe aanroep verstuurd.", "notice"));
  if (record.key === "discovery") {
    if (record.tools) {
      body.append(node("h4", `${record.tools.length} aangeboden tools`));
      const list = node("ul", undefined, "tools-list");
      for (const tool of record.tools) {
        const item = node("li");
        item.append(node("code", tool.name));
        if (tool.description) item.append(node("p", tool.description));
        list.append(item);
      }
      body.append(list);
      if (!record.tools.length) body.append(node("p", "De gateway retourneerde een lege toollijst.", "small muted"));
    }
    const source = record.data?.sources?.find((item) => item?.id === "fabric");
    if (source) {
      const sourceFacts = node("dl", undefined, "facts");
      fact(sourceFacts, "Bronstatus · ontvangen", source.status);
      fact(sourceFacts, "Type · ontvangen", source.kind ?? "Niet geretourneerd");
      fact(sourceFacts, "Datakarakter · ontvangen", source.dataKind ?? "Niet geretourneerd");
      fact(sourceFacts, "Handhaving · ontvangen", source.enforcement ?? "Niet geretourneerd", true);
      body.append(sourceFacts);
    }
  } else if (record.data) appendDataTable(body, record.data);
  if (record.operations.length) {
    const details = node("details");
    details.append(node("summary", "Werkelijk gemeten MCP-aanroepen"));
    const list = node("ul", undefined, "tools-list");
    for (const operation of record.operations) {
      const result = operation.received
        ? operation.toolError ? "toolfout ontvangen" : "respons ontvangen"
        : "geen bruikbare respons";
      const item = node("li");
      item.append(node("code", operation.label));
      item.append(node("p", `${timeFormat.format(operation.started)} · ${formatDuration(operation.elapsed)} · ${result}`));
      list.append(item);
    }
    details.append(list);
    body.append(details);
  }
  if (record.raw) {
    const details = node("details");
    details.append(node("summary", "Ruwe JSON-respons · gevoelige velden afgeschermd"));
    details.append(node("pre", JSON.stringify(record.raw, null, 2)));
    body.append(details);
  }
  $("result-body").replaceChildren(body);
}

function renderHistory() {
  $("history").hidden = !state.records.length;
  const list = document.createDocumentFragment();
  for (const record of state.records) {
    const item = node("li");
    const button = node("button");
    button.type = "button";
    button.disabled = state.busy;
    button.setAttribute("aria-pressed", String(record.id === state.selected));
    button.append(node("span", `${timeFormat.format(record.started)} · ${EXPERIMENTS[record.key].title}`));
    button.append(node("span", record.outcome.code));
    button.addEventListener("click", () => {
      if (state.busy) return;
      state.selected = record.id;
      renderRecord(record);
      renderHistory();
    });
    item.append(button);
    list.append(item);
  }
  $("history-list").replaceChildren(list);
}

async function initialize() {
  try {
    const response = await fetch("/lab-config.json", {
      credentials: "omit", cache: "no-store", redirect: "error",
      referrerPolicy: "no-referrer", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new LabError("CONFIG_UNAVAILABLE", "config");
    state.config = validateConfig(await response.json(), window.location.origin);
    state.msal = new PublicClientApplication({
      auth: {
        clientId: state.config.clientId,
        authority: `https://login.microsoftonline.com/${state.config.tenantId}`,
        redirectUri: `${window.location.origin}/auth.html`,
        postLogoutRedirectUri: `${window.location.origin}/auth.html`,
      },
      cache: { cacheLocation: BrowserCacheLocation.MemoryStorage },
      system: {
        allowPlatformBroker: false,
        serverTelemetryEnabled: false,
        loggerOptions: { piiLoggingEnabled: false, loggerCallback: () => {} },
      },
    });
    await state.msal.initialize();
    state.ready = true;
    notice("Client gereed. Meld aan met een echt testaccount. De bronconfiguratie en toegang zijn nog niet gecontroleerd.");
  } catch (error) {
    state.ready = false;
    notice(`De client kan niet starten (${safeErrorCode(error, "CLIENT_INITIALIZATION_FAILED")}). Controleer /lab-config.json, HTTPS en de same-origin /mcp-configuratie; herlaad daarna de pagina. Er worden geen gesimuleerde resultaten getoond.`, "error");
  }
  updateControls();
}

$("login").addEventListener("click", login);
$("switch-account").addEventListener("click", login);
$("logout").addEventListener("click", logout);
$("theme-toggle").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark";
  document.documentElement.dataset.theme = dark ? "light" : "dark";
});
$("admin-account").addEventListener("change", () => {
  state.admin = $("admin-account").checked;
  if (state.admin) for (const record of state.records) record.admin = true;
  const selected = state.records.find((record) => record.id === state.selected);
  if (selected) renderRecord(selected);
  notice(state.admin
    ? "Deze sessie is gemarkeerd als beheeraccount en telt niet als bewijs voor beperkte eindgebruikersrechten."
    : "De browser kan verborgen Fabric- of workspace-beheerrechten niet uitsluiten. Gebruik uitsluitend aparte testaccounts.", "warning");
});
for (const button of document.querySelectorAll("[data-run]")) {
  button.addEventListener("click", () => runExperiment(button.dataset.run));
}
window.addEventListener("pagehide", () => {
  clearSessionView();
  if (state.closeActive) {
    state.closeActive().catch((error) => {
      state.ready = false;
      notice(`De verbinding kon niet worden afgesloten (${safeErrorCode(error, "TRANSPORT_CLOSE_FAILED")}). Heropen het lab.`, "error");
    });
  }
  void clearTokenCache();
});
// A restored back-forward-cache page must never redisplay a prior identity or query.
window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
initialize();
