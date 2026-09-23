import type { ExpiryInfo } from "../api/types";

const KIND: Record<ExpiryInfo["kind"], string> = { weekly: "Weekly", monthly: "Monthly", contract: "Contract" };

// `next` is already an IST calendar date, so it is formatted as a plain date (UTC midnight), never shifted.
const dateFormat = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });

/** "2026-09-29" → "Tue 29 Sep"; anything unparseable is returned as sent. */
export function formatExpiryDate(next: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(next);
  if (!m) return next;
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(date.getTime())) return next;
  const p: Record<string, string> = {};
  for (const part of dateFormat.formatToParts(date)) p[part.type] = part.value;
  return `${p.weekday} ${p.day} ${p.month}`;
}

/** "Expiry today", or e.g. "Weekly expiry Tue 29 Sep · 4d". */
export function expiryLabel(expiry: ExpiryInfo): string {
  if (expiry.is_expiry_day) return "Expiry today";
  return `${KIND[expiry.kind]} expiry ${formatExpiryDate(expiry.next)} · ${expiry.days_to_expiry}d`;
}

export function expiryTitle(expiry: ExpiryInfo): string {
  const date = formatExpiryDate(expiry.next);
  if (expiry.is_expiry_day) return `${KIND[expiry.kind]} expiry today (${date})`;
  const n = expiry.days_to_expiry;
  return `${KIND[expiry.kind]} expiry on ${date}, ${n} trading day${n === 1 ? "" : "s"} away`;
}
