import clsx from "clsx";
import { Link, NavLink, useMatch, useNavigate } from "react-router";
import { useHealth, useInstruments } from "../api/hooks";
import { chartPath, readLastSelection } from "../lib/routes";
import { defaultTimeframe, sortTimeframes } from "../lib/timeframes";
import { FyersConnect } from "./FyersConnect";
import { InstrumentOptions } from "./InstrumentSelect";
import { StatusPill } from "./StatusPill";
import { ThemeToggle } from "./ThemeToggle";
import { TimeframeSelect } from "./TimeframeSelect";

function useChartSelection() {
  const match = useMatch("/chart/:instrument/:tf");
  if (match?.params.instrument && match.params.tf) return { instrument: match.params.instrument, tf: match.params.tf };
  return readLastSelection();
}

function Logo() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5">
      <path d="M7 3v18" className="stroke-up" strokeWidth="1.5" />
      <rect x="5" y="7" width="4" height="9" rx="0.5" className="fill-up" />
      <path d="M17 2v18" className="stroke-down" strokeWidth="1.5" />
      <rect x="15" y="5" width="4" height="10" rx="0.5" className="fill-down" />
    </svg>
  );
}

/** Picks the chart: changing either one opens /chart/<instrument>/<tf>. */
function ChartSelectors() {
  const instruments = useInstruments();
  const navigate = useNavigate();
  const current = useChartSelection();
  const list = instruments.data ?? [];
  const info = list.find((i) => i.id === current?.instrument);

  const pickInstrument = (id: string) => {
    const next = list.find((i) => i.id === id);
    if (!next) return;
    const tf = current && next.timeframes.includes(current.tf) ? current.tf : defaultTimeframe(next.timeframes);
    navigate(chartPath(id, tf));
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="header-instrument">
        Instrument
      </label>
      <select
        id="header-instrument"
        value={info ? info.id : ""}
        onChange={(e) => pickInstrument(e.target.value)}
        disabled={list.length === 0}
        className="max-w-[16rem] rounded-md border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink disabled:opacity-60"
      >
        {!info && <option value="">{instruments.isPending ? "Loading instruments…" : "Select instrument"}</option>}
        <InstrumentOptions instruments={list} />
      </select>
      {info && current && (
        <TimeframeSelect
          options={sortTimeframes(info.timeframes)}
          value={current.tf}
          onChange={(tf) => navigate(chartPath(info.id, tf))}
        />
      )}
    </div>
  );
}

const NAV = [
  { label: "Scanner", to: "/scanner" },
  { label: "Scorecard", to: "/scorecard" },
  { label: "Accuracy", to: "/accuracy" },
  { label: "News", to: "/news" },
];

function navClass({ isActive }: { isActive: boolean }) {
  return clsx(
    "inline-block border-b-2 px-3 py-2 text-sm no-underline",
    isActive ? "border-accent font-medium text-ink" : "border-transparent text-ink-muted hover:text-ink",
  );
}

function NavTabs() {
  const current = useChartSelection();
  const onChart = useMatch("/chart/*") !== null;
  const chartTo = current ? chartPath(current.instrument, current.tf) : "/chart";
  return (
    <nav aria-label="Main" className="mx-auto max-w-[1600px] px-2">
      <ul className="flex flex-wrap">
        <li>
          <NavLink to={chartTo} className={() => navClass({ isActive: onChart })}>
            Chart
          </NavLink>
        </li>
        {NAV.map((item) => (
          <li key={item.to}>
            <NavLink to={item.to} className={navClass}>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function Header() {
  const health = useHealth();
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 pt-2">
        <Link to="/" className="flex items-center gap-2 text-base font-semibold text-ink no-underline">
          <Logo />
          candly
        </Link>
        <ChartSelectors />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <StatusPill health={health} />
          <FyersConnect health={health.data} />
          <ThemeToggle />
        </div>
      </div>
      <NavTabs />
    </header>
  );
}
