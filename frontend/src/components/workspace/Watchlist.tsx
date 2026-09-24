import clsx from "clsx";
import { useMemo, useState, type KeyboardEvent, type RefObject } from "react";
import { Link, useNavigate } from "react-router";
import { useDailyQuotes, useHealth, useScanner } from "../../api/hooks";
import type { Instrument, ScannerRow } from "../../api/types";
import { expiryTitle } from "../../lib/expiry";
import { fmtPct, fmtPrice, signTone } from "../../lib/format";
import { expiryIsNear, groupInstruments, matchesSearch, watchlistOrder } from "../../lib/instruments";
import { watchQuote, type Quote } from "../../lib/quotes";
import { chartPath } from "../../lib/routes";
import { formatDayIST } from "../../lib/time";
import { defaultTimeframe } from "../../lib/timeframes";
import { IconButton } from "../ui/Button";
import { DirectionGlyph } from "../ui/Chip";
import { Kbd } from "../ui/Controls";
import { Icon } from "../ui/Icon";

function targetTf(i: Instrument, tf: string): string {
  return i.timeframes.includes(tf) ? tf : defaultTimeframe(i.timeframes);
}

function quoteTitle(q: Quote): string {
  return q.live ? "Live · change vs the previous close" : `Close on ${formatDayIST(q.time)}`;
}

function Row({ instrument: i, tf, active, quote, call }: { instrument: Instrument; tf: string; active: boolean; quote: Quote | null; call?: ScannerRow }) {
  const direction = call && !call.abstain && call.direction !== "neutral" ? call.direction : null;
  return (
    <Link
      to={chartPath(i.id, targetTf(i, tf))}
      data-id={i.id}
      aria-current={active ? "page" : undefined}
      title={i.name}
      className={clsx(
        "relative flex h-8 items-center gap-1.5 px-3 text-sm no-underline transition-colors focus-inset",
        active ? "bg-raised" : "hover:bg-raised/60",
      )}
    >
      {active && <span aria-hidden="true" className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent" />}
      <span className="flex min-w-0 flex-1 items-center gap-1">
        <span className={clsx("truncate font-medium", active ? "text-ink" : "text-ink/90")}>{i.symbol}</span>
        {direction && <DirectionGlyph direction={direction} className="text-[10px]" />}
        {expiryIsNear(i.expiry) && (
          <span
            title={expiryTitle(i.expiry)}
            className={clsx(
              "rounded border px-1 text-[10px] leading-3.5 font-semibold",
              i.expiry.is_expiry_day ? "border-forming text-forming" : "border-line text-ink-muted",
            )}
          >
            EXP
          </span>
        )}
      </span>
      {quote ? (
        <>
          <span title={quoteTitle(quote)} className="w-[4.25rem] text-right text-ink tabular-nums">
            {fmtPrice(quote.price)}
          </span>
          <span className={clsx("w-[3.25rem] text-right text-xs tabular-nums", signTone(quote.changePct))}>{fmtPct(quote.changePct)}</span>
        </>
      ) : (
        <span className="text-xs text-ink-faint">{i.data["1D"]?.bars === 0 ? "No data" : "—"}</span>
      )}
    </Link>
  );
}

/**
 * Instruments by section with the latest price and the day's change (lib/quotes), and a ▲/▼ where the
 * current timeframe has a directional call. `/` focuses the search; ↑/↓ step through the list.
 */
export function Watchlist({
  instruments,
  current,
  tf,
  searchRef,
  onCollapse,
}: {
  instruments: Instrument[];
  current: string;
  tf: string;
  searchRef: RefObject<HTMLInputElement | null>;
  onCollapse: () => void;
}) {
  const dailyScanner = useScanner("1D");
  const calls = useScanner(tf);
  const health = useHealth();
  const markets = health.data?.markets;
  const quoteItems = useMemo(() => {
    const open = new Set((markets ?? []).filter((m) => m.open).map((m) => m.exchange));
    return watchlistOrder(instruments)
      .filter((i) => i.timeframes.includes("1D"))
      .map((i) => ({ id: i.id, live: open.has(i.exchange) }));
  }, [instruments, markets]);
  const dailyBars = useDailyQuotes(quoteItems);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const dailyRows = useMemo(() => new Map((dailyScanner.data ?? []).map((r) => [r.instrument, r])), [dailyScanner.data]);
  const callsBy = useMemo(() => new Map((calls.data ?? []).map((r) => [r.instrument, r])), [calls.data]);
  const groups = groupInstruments(instruments.filter((i) => matchesSearch(i, query)));
  const visible = groups.flatMap((g) => g.items);

  const open = (i: Instrument, focusRow: boolean) => {
    navigate(chartPath(i.id, targetTf(i, tf)));
    if (focusRow) [...document.querySelectorAll<HTMLElement>("[data-id]")].find((el) => el.dataset.id === i.id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const inSearch = e.target === searchRef.current;
    if (inSearch && e.key === "Enter" && visible.length > 0) {
      e.preventDefault();
      open(visible.find((i) => i.id === current) ?? visible[0], false);
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (visible.length === 0) return;
    e.preventDefault();
    const index = visible.findIndex((i) => i.id === current);
    const next = index === -1 ? 0 : Math.max(0, Math.min(visible.length - 1, index + (e.key === "ArrowDown" ? 1 : -1)));
    open(visible[next], !inSearch);
  };

  return (
    <aside aria-label="Watchlist" onKeyDown={onKeyDown} className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line pr-2 pl-3">
        <label className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border border-line bg-page px-2 text-ink-faint transition-colors focus-within:border-accent hover:border-line-strong">
          <Icon name="search" className="size-3.5" />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && query) {
                e.stopPropagation();
                setQuery("");
              }
            }}
            placeholder="Search"
            aria-label="Search instruments"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-faint [&::-webkit-search-cancel-button]:hidden"
          />
          {!query && <Kbd>/</Kbd>}
        </label>
        <IconButton icon="chevronLeft" label="Hide watchlist" size="sm" onClick={onCollapse} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {groups.map((g) => (
          <section key={g.key} aria-label={g.label}>
            <h3 className="sticky top-0 z-10 flex h-7 items-end bg-surface px-3 pb-1 text-2xs font-semibold tracking-wider text-ink-faint uppercase">{g.label}</h3>
            <ul>
              {g.items.map((i) => (
                <li key={i.id}>
                  <Row instrument={i} tf={tf} active={i.id === current} quote={watchQuote(dailyBars.get(i.id), dailyRows.get(i.id))} call={callsBy.get(i.id)} />
                </li>
              ))}
            </ul>
          </section>
        ))}
        {visible.length === 0 && <p className="px-3 py-6 text-center text-xs text-ink-faint">No instrument matches “{query}”.</p>}
      </div>
    </aside>
  );
}
