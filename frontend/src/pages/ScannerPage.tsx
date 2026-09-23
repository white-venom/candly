import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useScanner } from "../api/hooks";
import type { ScannerRow } from "../api/types";
import { ExpiryBadge } from "../components/ExpiryBadge";
import { Page } from "../components/shell/Page";
import { CertifiedChip, FormingChip } from "../components/ui/Chip";
import { Segmented } from "../components/ui/Controls";
import { Icon } from "../components/ui/Icon";
import { QueryView } from "../components/ui/States";
import { Dash, TableFrame, Td, Th } from "../components/ui/Table";
import { fmtNum, fmtPct, fmtPrice, fmtProb, signTone } from "../lib/format";
import { abstainReason } from "../lib/messages";
import { chartPath, readLastSelection } from "../lib/routes";
import { sortByKey, type SortDir } from "../lib/sort";
import { formatBarWhenIST } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";
import { VERDICT_LABEL, scannerVerdict } from "../lib/verdict";

type SortKey = "name" | "last_close" | "change_pct" | "score" | "p_up" | "rel_volume";
type Sort = { key: SortKey; dir: SortDir };

const TREND = {
  up: { icon: "trendUp", label: "Up", className: "text-up" },
  down: { icon: "trendDown", label: "Down", className: "text-down" },
  sideways: { icon: "trendFlat", label: "Sideways", className: "text-ink-faint" },
} as const;

function Verdict({ row }: { row: ScannerRow }) {
  const v = scannerVerdict(row);
  if (v === "none") {
    const why = abstainReason(row.abstain_reason, { tf: row.tf }).text;
    return (
      <span title={why} className="cursor-help text-ink-muted underline decoration-line-strong decoration-dotted underline-offset-4">
        {VERDICT_LABEL.none}
        <span className="sr-only"> — {why}</span>
      </span>
    );
  }
  return (
    <span className={clsx("inline-flex items-center gap-1 font-medium", v === "bullish" ? "text-up" : "text-down")}>
      <span aria-hidden="true" className="text-[0.75em]">
        {v === "bullish" ? "▲" : "▼"}
      </span>
      {VERDICT_LABEL[v]}
    </span>
  );
}

function Odds({ row }: { row: ScannerRow }) {
  if (row.p_up === null) {
    return <span className="text-ink-faint">— vs {fmtProb(row.base_rate, 0)}</span>;
  }
  const v = scannerVerdict(row);
  return (
    <span className={clsx(v === "none" && "text-ink-faint")}>
      <span className={v === "bullish" ? "text-up" : v === "bearish" ? "text-down" : undefined}>{fmtProb(row.p_up, 0)}</span>
      <span className="text-ink-faint"> vs {fmtProb(row.base_rate, 0)}</span>
    </span>
  );
}

function Row({ row }: { row: ScannerRow }) {
  const navigate = useNavigate();
  const to = chartPath(row.instrument, row.tf);
  const trend = row.trend ? TREND[row.trend] : null;
  const symbol = row.instrument.split(":").pop();
  return (
    <tr onClick={() => navigate(to)} className="cursor-pointer transition-colors hover:bg-raised/60">
      <Td className="max-w-72">
        <Link to={to} onClick={(e) => e.stopPropagation()} className="rounded font-medium text-ink no-underline hover:underline">
          {symbol}
        </Link>
        <span className="ml-2 text-xs text-ink-faint">{row.name}</span>
      </Td>
      <Td numeric className="text-ink">
        {fmtPrice(row.last_close)}
      </Td>
      <Td numeric className={signTone(row.change_pct)}>
        {fmtPct(row.change_pct)}
      </Td>
      <Td>
        {trend ? (
          <span className={clsx("inline-flex items-center gap-1.5", trend.className)}>
            <Icon name={trend.icon} className="size-3.5" strokeWidth={2} />
            <span className="text-ink-muted">{trend.label}</span>
          </span>
        ) : (
          <Dash />
        )}
      </Td>
      <Td>
        {row.top_signal ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="text-ink-muted">{row.top_signal.label}</span>
            {row.top_signal.state === "forming" && <FormingChip />}
            {row.top_signal.certified && <CertifiedChip />}
          </span>
        ) : (
          <Dash />
        )}
      </Td>
      <Td>
        <Verdict row={row} />
      </Td>
      <Td numeric>
        <Odds row={row} />
      </Td>
      <Td>{row.expiry ? <ExpiryBadge expiry={row.expiry} compact /> : <Dash />}</Td>
      <Td numeric className={row.rel_volume !== null && row.rel_volume >= 1.5 ? "font-medium text-ink" : "text-ink-muted"}>
        {row.rel_volume === null ? <Dash /> : `${fmtNum(row.rel_volume, 2)}×`}
      </Td>
    </tr>
  );
}

export function ScannerPage() {
  const [params, setParams] = useSearchParams();
  const tf = params.get("tf") ?? readLastSelection()?.tf ?? "1D";
  const scanner = useScanner(tf);
  const [sort, setSort] = useState<Sort>({ key: "score", dir: "desc" });
  const rows = useMemo(() => sortByKey(scanner.data ?? [], sort.key, sort.dir), [scanner.data, sort]);
  const newest = scanner.data?.reduce((t, r) => Math.max(t, r.time), 0);

  const sortBy = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "name" ? "asc" : "desc" }));
  const th = (key: SortKey) => ({ sort: sort.key === key ? sort.dir : null, onSort: () => sortBy(key) });

  return (
    <Page
      title="Scanner"
      description="Every instrument, ranked by how strong its setup is right now"
      actions={
        <>
          {newest ? <span className="hidden text-xs text-ink-faint tabular-nums md:inline">as of {formatBarWhenIST(newest, tf)}</span> : null}
          <Segmented label="Timeframe" value={tf} onChange={(next) => setParams({ tf: next })} options={TIMEFRAMES.map((t) => ({ value: t, label: t }))} />
        </>
      }
    >
      <QueryView
        query={scanner}
        isEmpty={(r) => r.length === 0}
        empty={{ title: "Nothing to scan yet", hint: "Run ingest (or connect Fyers) so the scanner has candles to rank." }}
        loadingLabel="Scanning…"
      >
        {() => (
          <TableFrame label="Scanner" className={clsx(scanner.isPlaceholderData && "opacity-60")}>
            <thead>
              <tr>
                <Th {...th("name")}>Instrument</Th>
                <Th numeric {...th("last_close")}>
                  Last
                </Th>
                <Th numeric {...th("change_pct")}>
                  Change
                </Th>
                <Th>Trend</Th>
                <Th>Signal</Th>
                <Th {...th("score")} title="Sorted by edge: how far p(up) sits from the base rate">
                  Verdict
                </Th>
                <Th numeric {...th("p_up")} title="Probability of closing higher over the horizon vs the base rate">
                  p(up) vs base
                </Th>
                <Th>Expiry</Th>
                <Th numeric {...th("rel_volume")} title="Volume vs its 20-bar average">
                  Rel vol
                </Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row key={`${row.instrument}|${row.tf}`} row={row} />
              ))}
            </tbody>
          </TableFrame>
        )}
      </QueryView>
      <p className="text-2xs text-ink-faint">Click a row to open its chart. Hover “No edge” to see why.</p>
    </Page>
  );
}
