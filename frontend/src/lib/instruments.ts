import type { Instrument } from "../api/types";

const EXCHANGE_ORDER = ["NSE", "BSE", "MCX"];

export function groupByExchange(instruments: Instrument[]): [string, Instrument[]][] {
  const groups = new Map<string, Instrument[]>();
  for (const i of instruments) groups.set(i.exchange, [...(groups.get(i.exchange) ?? []), i]);
  const rank = (ex: string) => (EXCHANGE_ORDER.includes(ex) ? EXCHANGE_ORDER.indexOf(ex) : EXCHANGE_ORDER.length);
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b));
}
