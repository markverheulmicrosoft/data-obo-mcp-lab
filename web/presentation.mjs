export const EXPERIMENTS = Object.freeze({
  discovery: { title: "Account en tools", tool: "describe_access" },
  orders: { title: "Mijn orders", tool: "fabric_orders" },
  margins: { title: "Marges", tool: "fabric_margins" },
  finance: { title: "Extra gatewaycontrole", tool: "fabric_finance_only" },
});

export const LAB_ROLES = Object.freeze({ north: "North.Reader", finance: "Finance.Reader" });

export class LabError extends Error {
  constructor(code, kind = "request") {
    super(code);
    this.name = "LabError";
    this.errorCode = code;
    this.kind = kind;
  }
}

export function validateConfig(input, origin) {
  const base = new URL(origin);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname);
  if (base.protocol !== "https:" && !(local && base.protocol === "http:")) {
    throw new LabError("SECURE_ORIGIN_REQUIRED", "config");
  }
  const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
  if (!input || !uuid.test(input.tenantId) || !uuid.test(input.clientId) ||
      typeof input.scope !== "string" || !input.scope || input.scope.length > 512 ||
      /\s/.test(input.scope) || typeof input.gatewayUrl !== "string") {
    throw new LabError("CONFIG_INVALID", "config");
  }
  let gateway;
  try {
    gateway = new URL(input.gatewayUrl, base);
  } catch {
    throw new LabError("GATEWAY_URL_INVALID", "config");
  }
  if (gateway.origin !== base.origin || !["/mcp", "/mcp/"].includes(gateway.pathname) ||
      gateway.username || gateway.password || gateway.search || gateway.hash) {
    throw new LabError("GATEWAY_ORIGIN_REJECTED", "config");
  }
  return {
    tenantId: input.tenantId,
    clientId: input.clientId,
    scope: input.scope,
    gatewayUrl: gateway.href,
  };
}

export function safeErrorCode(error, fallback = "REQUEST_FAILED") {
  return typeof error?.errorCode === "string" && /^[a-z0-9_-]{1,80}$/i.test(error.errorCode)
    ? error.errorCode
    : fallback;
}

export function safeSourceUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" &&
        ["app.fabric.microsoft.com", "app.powerbi.com"].includes(url.hostname) &&
        !url.username && !url.password && !url.port) return url.href;
  } catch {
    return null;
  }
  return null;
}

const sensitiveKey = /token|authorization|secret|password|assertion|credential|connection.?string/i;
export function redact(value, depth = 0) {
  if (depth > 20) return "[dieptelimiet]";
  if (typeof value === "string") {
    return value
      .replace(/\bBearer\s+[a-zA-Z0-9._~+/-]+=*/gi, "Bearer [afgeschermd]")
      .replace(/\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b/g, "[token afgeschermd]");
  }
  if (Array.isArray(value)) return value.map((item) => redact(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key, sensitiveKey.test(key) ? "[afgeschermd]" : redact(item, depth + 1),
    ]));
  }
  return value;
}

export function isAdminSession(roles, marked = false) {
  return marked || (Array.isArray(roles) && roles.some((role) => /admin|owner|beheer/i.test(role)));
}

export function compactExpected(tool, roles, admin = false) {
  if (admin) return "Beheeraccount: niet geschikt voor deze vergelijking.";
  if (!Array.isArray(roles)) return "Nog onbekend: meld aan om je account te controleren.";
  if (tool === "describe_access") return "Je account en rollen volgens de gateway.";
  const finance = roles.includes(LAB_ROLES.finance);
  if (!finance && !roles.includes(LAB_ROLES.north)) return "Geen toegang: dit account heeft geen labrol.";
  if (tool === "fabric_orders") return finance ? "6 orders: Noord en Zuid." : "3 orders uit Noord.";
  if (tool === "fabric_margins") return finance ? "6 margeregels." : "Geen toegang: Fabric hoort te weigeren.";
  return finance ? "6 margeregels, na beide controles." : "Geen toegang: de gateway hoort te weigeren.";
}

export function expectedOutcome(tool, roles, admin = false) {
  if (admin) return "Beheeraccount: geen geldig bewijs voor eindgebruikersrechten.";
  if (!Array.isArray(roles)) return "Nog onbekend: voer eerst identiteit & tools uit. Geen rol afleiden uit een accountnaam.";
  const finance = roles.includes(LAB_ROLES.finance);
  const north = roles.includes(LAB_ROLES.north);
  if (tool === "describe_access") {
    return "De gateway retourneert de identiteit en rollen uit het gevalideerde JWT; discovery toont de aangeboden tools.";
  }
  if (!finance && !north) {
    return "Geen North.Reader-/Finance.Reader-rol: geen toegang tot de Fabric-tools verwacht. Een gatewayweigering bewijst geen bronrechten.";
  }
  if (tool === "fabric_orders") {
    return finance
      ? "Finance: 6 opgeslagen demorijen, 3 Noord + 3 Zuid, na geslaagde OBO en bronautorisatie."
      : "North: 3 opgeslagen demorijen uit Noord; Fabric RLS filtert Zuid weg.";
  }
  if (tool === "fabric_margins") {
    return finance
      ? "Finance: marges toegestaan door de bron (SELECT), onder de gedelegeerde gebruiker."
      : "North: gateway laat de aanroep door; Fabric weigert SELECT op marges met SOURCE_FORBIDDEN.";
  }
  return finance
    ? "Finance: gatewayrol voldoet; daarna moet Fabric de margesquery óók toestaan."
    : "North: TOOL_FORBIDDEN bij de gateway, vóór de bronquery. Dit zegt niets over SQL SELECT of RLS.";
}

const errors = {
  SOURCE_FORBIDDEN: {
    status: "denied", layer: "Fabric · bronautorisatie", title: "De bron weigert toegang",
    explanation: "Fabric heeft de bronaanroep geweigerd. Bij marges past dit bij ontbrekende SQL SELECT-rechten. Dit is een waargenomen bronweigering, geen gatewayrolcheck.",
  },
  TOOL_FORBIDDEN: {
    status: "denied", layer: "Gateway · toolautorisatie", title: "De gateway weigert deze tool",
    explanation: "De gateway meldt dat de toolrol ontbreekt. De bronquery wordt daardoor niet uitgevoerd; SQL-rechten en RLS zijn hiermee niet getest.",
  },
  OBO_FAILED: {
    status: "action", layer: "Entra · OBO", title: "Gebruikersdelegatie is niet gelukt",
    explanation: "De tokenuitwisseling is mislukt. Controleer consent, appconfiguratie en delegatie met de beheerder en meld opnieuw aan. Dit is géén bewijs van een bronweigering.",
  },
  SOURCE_UNAVAILABLE: {
    status: "blocked", layer: "Bronverbinding", title: "De bron is niet bereikbaar",
    explanation: "De query kon niet betrouwbaar worden uitgevoerd. Controleer de Warehouse-beschikbaarheid en verbinding. Geen uitspraak over toegangsrechten.",
  },
  SOURCE_NOT_CONFIGURED: {
    status: "blocked", layer: "Bronconfiguratie", title: "Fabric is nog niet geconfigureerd",
    explanation: "Er is nog geen uitvoerbare bronverbinding. Er worden geen voorbeeldresultaten ingevuld en geen rechtenconclusies getrokken.",
  },
};

export function resultData(result) {
  return result?.structuredContent && typeof result.structuredContent === "object"
    ? result.structuredContent
    : null;
}

export function validAccessData(value) {
  return Boolean(value && value.user && typeof value.user.id === "string" &&
    Array.isArray(value.user.roles) && value.user.roles.every((role) => typeof role === "string") &&
    Array.isArray(value.sources));
}

export function validTableData(value) {
  return Boolean(value && value.source === "fabric-warehouse" &&
    value.authentication === "delegated-obo" &&
    Array.isArray(value.columns) && value.columns.length > 0 &&
    value.columns.every((column) => typeof column === "string") &&
    Array.isArray(value.rows) &&
    value.rows.every((row) => Array.isArray(row) || (row && typeof row === "object")));
}

export function classifyResult(tool, result) {
  if (result?.isError) {
    const text = (result.content ?? [])
      .filter((part) => part.type === "text").map((part) => part.text).join("\n");
    const matches = Object.keys(errors).filter((code) => new RegExp(`\\b${code}\\b`).test(text));
    if (matches.length === 1) return { ...errors[matches[0]], code: matches[0] };
    return {
      status: "blocked", layer: "MCP · niet geclassificeerd", code: "TOOL_ERROR",
      title: "De tool retourneert een fout",
      explanation: "Geen eenduidige foutcode ontvangen. Een algemene fout is geen bewezen autorisatieweigering; bekijk de afgeschermde respons.",
    };
  }
  const data = resultData(result);
  if (tool === "describe_access" && validAccessData(data)) {
    return {
      status: "info", layer: "Gateway · gevalideerde identiteit", code: "ACCESS_DESCRIBED",
      title: "Identiteit en bronstatus ontvangen",
      explanation: "Deze informatie komt van de gateway. Een rol of geconfigureerde bron bewijst nog niet dat een SQL-query is toegestaan.",
    };
  }
  if (tool !== "describe_access" && validTableData(data)) {
    return {
      status: "allowed", layer: "Fabric · queryresultaat", code: "SOURCE_RESULT",
      title: `${data.rows.length} ${data.rows.length === 1 ? "rij" : "rijen"} ontvangen`,
      explanation: "De tool rapporteert een uitgevoerde query op de echte Fabric Warehouse via delegated OBO. Dit bewijst alleen deze aanroep, op dit moment.",
    };
  }
  return {
    status: "blocked", layer: "MCP · responscontract", code: "UNEXPECTED_RESULT",
    title: "Geen verifieerbaar bronresultaat",
    explanation: "De vereiste gestructureerde gegevens ontbreken of wijken af. De ruwe respons is beschikbaar, maar telt niet als geslaagde Fabric-test.",
  };
}

export function compareOutcome(tool, roles, outcome, data, admin = false) {
  if (admin) return "Uitgesloten als eindgebruikersbewijs: beheerrechten kunnen de bedoelde beperkingen omzeilen.";
  if (tool === "describe_access") {
    return outcome.code === "ACCESS_DESCRIBED"
      ? "Identiteit opgehaald; bronrechten zijn nog niet getest."
      : "De gateway-identiteit is niet bevestigd. Discovery alleen bewijst geen identiteit of bronrechten.";
  }
  if (!Array.isArray(roles)) return "Nog niet te vergelijken: de gatewayrollen zijn niet opgehaald.";
  const finance = roles.includes(LAB_ROLES.finance);
  const north = roles.includes(LAB_ROLES.north);
  if (!finance && !north) {
    return outcome.code === "TOOL_FORBIDDEN"
      ? "Past bij de verwachting zonder labrol; de bron is niet getest."
      : "Geen bevestigde verwachte gatewayweigering. Onderzoek de echte respons; dit account heeft geen bekende labrol.";
  }
  if (tool === "fabric_orders" && outcome.status === "allowed") {
    const regionIndex = data.columns.indexOf("region");
    const regions = data.rows.map((row) => Array.isArray(row) ? row[regionIndex] : row.region);
    const matches = finance
      ? regions.length === 6 && regions.filter((r) => r === "Noord").length === 3 &&
        regions.filter((r) => r === "Zuid").length === 3
      : regions.length === 3 && regions.every((r) => r === "Noord");
    return matches
      ? "Komt overeen met de oorspronkelijke seed en RLS-verwachting. Geen bewijs voor andere tabellen of toekomstige aanroepen."
      : "Wijkt af van de oorspronkelijke seed/RLS-verwachting. Controleer data, bronrechten en eventuele beheerrollen; dit is niet automatisch een fout in RLS.";
  }
  const expectedCode = !finance && north
    ? (tool === "fabric_margins" ? "SOURCE_FORBIDDEN" : tool === "fabric_finance_only" ? "TOOL_FORBIDDEN" : null)
    : "SOURCE_RESULT";
  if (outcome.code === expectedCode) {
    return "De waargenomen uitkomst past bij de aanvankelijke verwachting voor deze rol.";
  }
  if (outcome.status === "allowed") {
    return "Wijkt af: de bron gaf data waar aanvankelijk een weigering werd verwacht. Controleer gewijzigde grants, de laatst opgehaalde gatewayrollen en eventuele beheerrechten.";
  }
  if (outcome.status === "denied") {
    return `Wijkt af: een echte weigering is waargenomen bij ${outcome.layer}, maar niet de verwachte uitkomst voor deze rol. Onderzoek die laag; neem geen succesvolle bronquery aan.`;
  }
  return "De verwachte uitkomst is niet bevestigd. Een verbindings-, consent- of configuratiefout is geen geslaagde rechtentest.";
}
