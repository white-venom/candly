import { ApiError } from "../api/client";
import type { Forecast, Health, PatternSignal } from "../api/types";
import { formatExpiryDate } from "./expiry";
import { formatBarWhenIST, formatDayIST, formatIsoDate, formatWhenIST, isoToUnix, nowUnix } from "./time";

// Every backend reason, status and error reaches the screen through this file, in plain language.

export const BACKEND_COMMAND = "backend\\.venv\\Scripts\\python -m uvicorn candly.api.app:app --reload --port 8000";

export type FriendlyError = {
  kind: "unreachable" | "no-data" | "not-found" | "other";
  title: string;
  hint: string | null;
  /** a command to copy, e.g. to start the backend */
  command: string | null;
};

const ISO_TIME = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g;
const ISO_DATE = /\b(\d{4}-\d{2}-\d{2})\b/g;

/** Rewrites any ISO timestamp or date inside backend text as IST, for human eyes. */
export function humanizeText(text: string, now = nowUnix()): string {
  return text
    .replace(ISO_TIME, (iso) => {
      const unix = isoToUnix(iso);
      return unix === null ? iso : `${formatWhenIST(unix, now)} IST`;
    })
    .replace(ISO_DATE, (date) => formatIsoDate(date));
}

function sentence(text: string): string {
  const t = text.trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

export function friendlyError(error: unknown, noDataHint = "Run ingest, or connect Fyers, to load it."): FriendlyError {
  if (error instanceof ApiError) {
    if (error.unreachable) {
      return { kind: "unreachable", title: "Backend not running", hint: "Start it from the repo root:", command: BACKEND_COMMAND };
    }
    if (error.noData) return { kind: "no-data", title: "No data yet", hint: noDataHint, command: null };
    // FastAPI's own 404 for a route that isn't mounted; the contract's 404 carries an "unknown instrument" detail.
    if (error.status === 404 && error.detail === "Not Found") {
      return { kind: "other", title: "This backend doesn't serve this yet", hint: "Update and restart the backend.", command: null };
    }
    const kind = error.status === 404 ? "not-found" : "other";
    return { kind, title: sentence(humanizeText(error.detail)), hint: null, command: null };
  }
  const detail = error instanceof Error ? humanizeText(error.message) : null;
  return { kind: "other", title: "Something went wrong", hint: detail, command: null };
}

// ---- Why a forecast abstains ----

export type Reason = { text: string; hint: string | null };

export type ReasonContext = {
  tf: string;
  now?: number;
  /** Fyers can be connected from here, which would bring fresh data */
  canConnect?: boolean;
};

function barAt(iso: string, ctx: ReasonContext): string | null {
  const unix = isoToUnix(iso);
  if (unix === null) return null;
  return ctx.tf === "1D" ? formatDayIST(unix, ctx.now) : `${formatBarWhenIST(unix, ctx.tf, ctx.now)} IST`;
}

/** A backend abstain reason → one friendly line plus what would change it. Unknown reasons never leak through raw. */
export function abstainReason(raw: string | null | undefined, ctx: ReasonContext): Reason {
  const r = (raw ?? "").trim();
  const freshHint = ctx.canConnect ? "Connect Fyers to resume live data." : "A new call comes once the next bar is in.";
  let m: RegExpExecArray | null;

  if ((m = /^stale data: last closed bar (\S+?),? expected (\S+)/.exec(r))) {
    const last = barAt(m[1], ctx);
    const next = barAt(m[2], ctx);
    if (last && next) return { text: `Waiting for fresh data — last bar ${last}, next expected ${next}.`, hint: freshHint };
  }
  if ((m = /^stale data: last closed bar (\S+?),? other instruments have (\S+)/.exec(r))) {
    const last = barAt(m[1], ctx);
    const others = barAt(m[2], ctx);
    if (last && others) return { text: `Waiting for fresh data — last bar ${last}; others have ${others}.`, hint: freshHint };
  }
  if (r.startsWith("stale data")) return { text: "Waiting for fresh data.", hint: freshHint };

  if ((m = /^too few analogs: (\d+) < (\d+)/.exec(r))) {
    return { text: "Not enough similar past setups.", hint: `Found ${m[1]}, needs ${m[2]}. More history would help.` };
  }
  if (r.startsWith("too few analogs")) return { text: "Not enough similar past setups.", hint: "More history would help." };
  if (r.startsWith("no analogs")) {
    return { text: "No similar past setups to learn from.", hint: "A pattern or context with more history would change this." };
  }
  if (r.startsWith("not enough history")) {
    return { text: "Not enough price history yet.", hint: "A longer backfill would change this." };
  }
  if (r.startsWith("horizon crosses session close")) {
    return { text: "Too close to the close for an intraday call.", hint: "The next call comes after the open." };
  }
  if (r.startsWith("unvalidated bucket")) {
    const text = "This setup hasn't proven itself out-of-sample yet.";
    if ((m = /has (\d+) validation events?, fewer than (\d+)/.exec(r))) {
      return { text, hint: `Only ${m[1]} validation cases so far; it needs ${m[2]}.` };
    }
    if ((m = /(\d+(?:\.\d+)?%) of the time in validation vs a base rate of (\d+(?:\.\d+)?%), n=(\d+)/.exec(r))) {
      return { text, hint: `In validation it worked ${m[1]} of the time vs ${m[2]} usual (n ${m[3]}).` };
    }
    return { text, hint: r.includes("no scorecard") ? "The scorecard isn't built yet." : "It has to hold up in validation first." };
  }
  if ((m = /^edge below costs: median move (-?[\d.]+)% .*?vs (-?[\d.]+)% round trip/.exec(r))) {
    return { text: "The expected move doesn't cover trading costs.", hint: `Expected ${m[1]}% vs ${m[2]}% round-trip costs.` };
  }
  if (r.startsWith("edge below costs")) return { text: "The expected move doesn't cover trading costs.", hint: null };
  // range_v1 forecasts only the range; a direction comes only from a validated daily trend model.
  if (r.startsWith("direction unclear")) {
    return { text: "Range only — the model doesn't predict up/down here.", hint: "The expected range still applies." };
  }
  // The minimum-edge gate: "edge below minimum: …", "below minimum edge…" or the older "|p(up) − base rate| < 0.03".
  if (/^(edge|below minimum edge|min(imum)?[ _-]edge)|^\|p\(up\) ?[−-] ?base rate\|/i.test(r)) {
    const threshold = /<\s*(0?\.\d+)/.exec(r);
    const pts = threshold ? Math.round(Number(threshold[1]) * 100) : 3;
    return { text: "The odds are too close to a coin flip.", hint: `A call needs odds at least ${pts} points away from the usual.` };
  }
  if (/market (is )?closed/i.test(r)) return { text: "The market is closed.", hint: "Calls resume at the next session." };

  return { text: "No clear edge right now.", hint: null };
}

// ---- Data connection ----

export type DataStatus = {
  state: "checking" | "live" | "syncing" | "paused" | "offline";
  title: string;
  detail: string | null;
  /** the Fyers paste-code login would fix it */
  canConnect: boolean;
};

export function dataStatus(health: Health | undefined, error: unknown): DataStatus {
  if (!health) {
    if (error) {
      const f = friendlyError(error);
      return { state: "offline", title: f.title, detail: f.kind === "unreachable" ? "Start the API to see live data." : null, canConnect: false };
    }
    return { state: "checking", title: "Checking the data connection…", detail: null, canConnect: false };
  }
  const canConnect = health.keys.fyers && !health.keys.fyers_connected;
  if (health.sync?.status === "running") {
    const pct = health.sync.progress !== null ? ` · ${Math.round(health.sync.progress * 100)}%` : "";
    return { state: "syncing", title: "Setting up from Fyers", detail: `${syncStepName(health.sync.step)}${pct}`, canConnect: false };
  }
  if (health.ingest?.status === "blocked") {
    const reason = health.ingest.reason ?? "";
    let detail = "Data updates are on hold.";
    if (/not connected/i.test(reason)) detail = "Fyers not connected";
    else if (/keys? (are|is) not set|FYERS_APP_ID/i.test(reason)) detail = "Fyers keys missing — add FYERS_APP_ID and FYERS_SECRET_KEY to .env";
    return { state: "paused", title: "Live data paused", detail, canConnect };
  }
  const source = health.data_source === "fyers" ? "Fyers" : "Yahoo (dev data)";
  return { state: "live", title: "Live data", detail: `Source: ${source}`, canConnect };
}

// ---- Fyers first sync ----

const SYNC_STEPS: Record<string, string> = {
  "refresh-expiries": "Refreshing expiry dates",
  expiries: "Refreshing expiry dates",
  "archive-yahoo": "Archiving the Yahoo data",
  archive: "Archiving the Yahoo data",
  "backfill-1d": "Downloading daily history",
  "1d-backfill": "Downloading daily history",
  "backfill-5m": "Downloading 5-minute history",
  "5m-backfill": "Downloading 5-minute history",
  resample: "Building 15m/1h",
  "resample-15m-1h": "Building 15m/1h",
  "build-15m-1h": "Building 15m/1h",
  "fill-1d-gaps": "Filling gaps in the daily history",
  "gap-fill": "Filling gaps in the daily history",
  "gap-fill-1d": "Filling gaps in the daily history",
  "1d-gap-fill": "Filling gaps in the daily history",
  quality: "Checking data quality",
  "quality-report": "Checking data quality",
  holidays: "Learning market holidays",
  "observed-holidays": "Learning market holidays",
  calendar: "Reloading the market calendar",
  "reload-calendar": "Reloading the market calendar",
  scorecards: "Building scorecards",
  scorecard: "Building scorecards",
};

/** A sync step id → a friendly name; unknown ids read as their words. */
export function syncStepName(step: string | null): string {
  if (!step) return "Getting started";
  const key = step.trim().toLowerCase().replace(/_/g, "-");
  if (SYNC_STEPS[key]) return SYNC_STEPS[key];
  const words = step.replace(/[-_]+/g, " ").trim();
  return words ? sentence(words) : "Getting started";
}

export type SyncNotice = { state: "running"; text: string; progress: number | null } | { state: "error"; text: string };

/** What the notice strip says about the Fyers first sync, or null when there is nothing to show. */
export function syncNotice(health: Health | undefined): SyncNotice | null {
  const sync = health?.sync;
  if (!sync) return null;
  if (sync.status === "running") {
    const pct = sync.progress !== null ? Math.round(Math.min(1, Math.max(0, sync.progress)) * 100) : null;
    return { state: "running", text: `Setting up from Fyers — ${syncStepName(sync.step)}${pct !== null ? ` · ${pct}%` : ""}`, progress: pct };
  }
  if (sync.status === "error") {
    const why = sync.message ? humanizeText(sync.message) : null;
    return { state: "error", text: why ? `Fyers setup stopped — ${why}` : "Fyers setup stopped before it finished" };
  }
  return null;
}

/** "Expiry schedule changed for NIFTY50 — exchange says Tue 29 Sep", or null when the check found nothing. */
export function expiryNotice(health: Health | undefined): string | null {
  const check = health?.expiry_check;
  if (check?.status !== "mismatch" || check.mismatches.length === 0) return null;
  const [first, ...rest] = check.mismatches;
  const symbol = first.instrument.split(":").pop() ?? first.instrument;
  const more = rest.length > 0 ? ` (+${rest.length} more)` : "";
  return `Expiry schedule changed for ${symbol} — exchange says ${formatExpiryDate(first.exchange)}${more}`;
}

// ---- Forecast drivers ----

export type DriverIcon = "trend" | "volatility" | "level" | "analogs" | "pattern" | "expiry" | "other";

const LEVEL_NAMES: Record<string, string> = {
  pdh: "the previous day's high",
  pdl: "the previous day's low",
  pdc: "the previous close",
  pivot: "the pivot",
  r1: "R1",
  r2: "R2",
  s1: "S1",
  s2: "S2",
  cpr_top: "the CPR top",
  cpr_bottom: "the CPR bottom",
  swing_high: "a swing high",
  swing_low: "a swing low",
  vwap: "VWAP",
};

export function levelName(kind: string): string {
  return LEVEL_NAMES[kind] ?? kind.replace(/_/g, " ");
}

export type ContextTag = { text: string; warn?: boolean };

/** A signal's market context as short tags; an expiry-day bar is flagged. */
export function contextTags(c: PatternSignal["context"]): ContextTag[] {
  const tags: ContextTag[] = [];
  if (c.expiry_day) tags.push({ text: "Expiry day", warn: true });
  else if (typeof c.days_to_expiry === "number") tags.push({ text: `Expiry in ${c.days_to_expiry}d` });
  if (c.trend) tags.push({ text: c.trend === "up" ? "Uptrend" : c.trend === "down" ? "Downtrend" : "Sideways" });
  if (c.vol_regime) tags.push({ text: `${c.vol_regime[0].toUpperCase()}${c.vol_regime.slice(1)} volatility` });
  if (c.rsi14 !== null) tags.push({ text: `RSI ${Math.round(c.rsi14)}` });
  if (c.rel_volume !== null) tags.push({ text: `Volume ×${c.rel_volume.toFixed(1)}` });
  if (c.near_level) tags.push({ text: `Near ${levelName(c.near_level)}` });
  return tags;
}

function ordinal(n: number): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

type Driver = Forecast["drivers"][number];

/** A forecast driver as one plain sentence. */
export function describeDriver(d: Driver): { icon: DriverIcon; text: string } {
  const detail = d.detail ?? "";
  let m: RegExpExecArray | null;
  switch (d.name) {
    case "Trend": {
      m = /^(up|down|sideways)(?: \(ADX (\d+)\))?/.exec(detail);
      if (!m) break;
      const word = m[1] === "up" ? "Uptrend" : m[1] === "down" ? "Downtrend" : "Sideways, no clear trend";
      return { icon: "trend", text: m[2] ? `${word} (ADX ${m[2]})` : word };
    }
    case "Volatility regime": {
      m = /^(low|normal|high) \(ATR at the (\d+)\w* percentile of the last (\d+) sessions/.exec(detail);
      if (!m) break;
      const pct = Number(m[2]);
      if (m[1] === "low") return { icon: "volatility", text: `Low volatility — calmer than ${100 - pct}% of the last ${m[3]} sessions` };
      if (m[1] === "high") return { icon: "volatility", text: `High volatility — busier than ${pct}% of the last ${m[3]} sessions` };
      return { icon: "volatility", text: `Normal volatility (${ordinal(pct)} percentile)` };
    }
    case "Near level": {
      m = /of (\w+)\s*$/.exec(detail);
      if (!m) break;
      return { icon: "level", text: `Price is sitting near ${levelName(m[1])}` };
    }
    case "Analogs": {
      m = /^(\d+) .*?analogs.*?: (\d+) closed higher after (\d+) bars/.exec(detail);
      if (!m) break;
      return { icon: "analogs", text: `${m[1]} similar past setups — ${m[2]} closed higher after ${m[3]} bars` };
    }
    case "No pattern":
      return { icon: "pattern", text: "No candlestick pattern on the last bar" };
    case "Expiry": {
      const kind = /the (\w+) expiry/.exec(detail)?.[1] ?? "";
      const text = /\bon the\b/.test(detail) ? `Expiry day (${kind})` : `One trading day before the ${kind} expiry`;
      return { icon: "expiry", text: text.replace(" ()", "") };
    }
  }
  if (detail.startsWith("Confirmed on the reference bar")) {
    m = /hit (\d+(?:\.\d+)?%) vs base (\d+(?:\.\d+)?%), n=(\d+)/.exec(detail);
    return { icon: "pattern", text: m ? `${d.name} on the last bar — hit ${m[1]} vs base ${m[2]} (n ${m[3]})` : `${d.name} on the last bar` };
  }
  return { icon: "other", text: detail ? `${d.name}: ${humanizeText(detail)}` : d.name };
}
