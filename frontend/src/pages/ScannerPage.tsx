import clsx from "clsx";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useScanner } from "../api/hooks";
import type { ScannerRow } from "../api/types";
import { ExpiryBadge } from "../components/ExpiryBadge";
import { QueryView } from "../components/States";
import { TimeframeSelect } from "../components/TimeframeSelect";
import { Badge, CertifiedBadge, DirectionTag, FormingBadge } from "../components/ui";
import { fmtNum, fmtPct, fmtPrice, fmtProb, signTone } from "../lib/format";
import { chartPath, readLastSelection } from "../lib/routes";
import { sortByKey, type SortDir } from "../lib/sort";
import { formatBarTimeIST } from "../lib/time";
import { TIMEFRAMES } from "../lib/timeframes";

type SortKey = "name" | "last_close" | "change_pct" | "p_up" | "score" | "rel_volume" | "time";
type Sort = { key: SortKey; dir: SortDir };

const COLUMNS: { key: SortKey | null; label: string; numeric?: boolean }[] = [
  { key: "name", label: "Instrument" },
  { key: "last_close", label: "Last close", numeric: true },
  { key: "change_pct", label: "Change", numeric: true },
  { key: "p_up", label: "p(up) vs base", numeric: true },
  { key: "score", label: "Edge", numeric: true },
  { key: null, label: "Direction" },
  { key: null, label: "Reason" },
  { key: null, label: "Top signal" },
  { key: "rel_volume", label: "Rel vol", numeric: true },
  { key: null, label: "Trend" },
  { key: null, label: "Expiry" },
  { key: "time", label: "Bar (IST)" },
];

function Row({ row }: { row: ScannerRow }) {
  const navigate = useNavigate();
  const to = chartPath(row.instrument, row.tf);
  return (
    <tr onClick={() => navigate(to)} className="cursor-pointer border-b border-line hover:bg-raised">
      <td className="px-2 py-1.5">
        <Link to={to} className="font-medium text-ink no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
          {row.name}
        </Link>
        <div className="text-xs text-ink-faint">{row.instrument}</div>
      </td>
      <td className="px-2 py-1.5 text-right">{fmtPrice(row.last_close)}</td>
      <td className={clsx("px-2 py-1.5 text-right", signTone(row.change_pct))}>{fmtPct(row.change_pct)}</td>
      <td className="px-2 py-1.5 text-right">
        {row.abstain ? (
          <span className="text-ink-faint" title="Abstaining: shown as context only, not a call">
            {fmtProb(row.p_up)} <Badge>abstain</Badge>
          </span>
        ) : (
          <>
            <span className="text-ink">{fmtProb(row.p_up)}</span>{" "}
            <span className="text-ink-faint">vs {fmtProb(row.base_rate)}</span>
          </>
        )}
      </td>
      <td className="px-2 py-1.5 text-right">{row.abstain ? <span className="text-ink-faint">—</span> : fmtNum(row.score * 100, 1)}</td>
      <td className="px-2 py-1.5">
        {row.abstain ? <span className="text-ink-faint">no clear edge</span> : <DirectionTag direction={row.direction} />}
      </td>
      <td className="px-2 py-1.5 text-xs text-ink-muted">
        {row.abstain && row.abstain_reason ? row.abstain_reason : <span className="text-ink-faint">—</span>}
      </td>
      <td className="px-2 py-1.5">
        {row.top_signal ? (
          <span className="inline-flex flex-wrap items-center gap-1">
            {row.top_signal.label}
            {row.top_signal.state === "forming" && <FormingBadge />}
            {row.top_signal.certified && <CertifiedBadge />}
          </span>
        ) : (
          <span className="text-ink-faint">—</span>
        )}
      </td>
      <td className="px-2 py-1.5 text-right">{row.rel_volume === null ? "—" : `×${fmtNum(row.rel_volume, 1)}`}</td>
      <td className="px-2 py-1.5 text-ink-muted">{row.trend ?? "—"}</td>
      <td className="px-2 py-1.5">{row.expiry ? <ExpiryBadge expiry={row.expiry} /> : <span className="text-ink-faint">—</span>}</td>
      <td className="px-2 py-1.5 text-xs whitespace-nowrap text-ink-muted">{formatBarTimeIST(row.time, row.tf)}</td>
    </tr>
  );
}

export function ScannerPage() {
  const [params, setParams] = useSearchParams();
  const tf = params.get("tf") ?? readLastSelection()?.tf ?? "1D";
  const scanner = useScanner(tf);
  const [sort, setSort] = useState<Sort>({ key: "score", dir: "desc" });

  const sortBy = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "name" ? "asc" : "desc" }));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-ink">Scanner</h1>
        <TimeframeSelect options={TIMEFRAMES} value={tf} onChange={(next) => setParams({ tf: next })} />
        <p className="text-xs text-ink-faint">Edge = |p(up) − base rate| in points; 0 when abstaining. Click a row to open its chart.</p>
      </div>
      <QueryView query={scanner} isEmpty={(rows) => rows.length === 0} loadingLabel="Scanning…">
        {(rows) => (
          <div className="overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full text-sm tabular-nums">
              <thead className="border-b border-line-strong text-left text-xs text-ink-muted">
                <tr>
                  {COLUMNS.map((col) => {
                    const active = col.key !== null && sort.key === col.key;
                    return (
                      <th
                        key={col.label}
                        scope="col"
                        aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                        className={clsx("px-2 py-2 font-medium", col.numeric && "text-right")}
                      >
                        {col.key ? (
                          <button type="button" onClick={() => sortBy(col.key as SortKey)} className="inline-flex items-center gap-1 hover:text-ink">
                            {col.label}
                            <span aria-hidden="true" className={active ? "text-ink" : "text-ink-faint"}>
                              {active ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}
                            </span>
                          </button>
                        ) : (
                          col.label
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {sortByKey(rows, sort.key, sort.dir).map((row) => (
                  <Row key={`${row.instrument}|${row.tf}`} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
    </div>
  );
}
