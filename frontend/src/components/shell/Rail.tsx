import clsx from "clsx";
import { Link, NavLink, useMatch } from "react-router";
import { chartPath, readLastSelection } from "../../lib/routes";
import { useShell } from "../../lib/shell";
import { IconButton } from "../ui/Button";
import { Icon, LogoMark, type IconName } from "../ui/Icon";
import { Tip } from "../ui/Tooltip";
import { ConnectionStatus } from "./ConnectionStatus";
import { ThemeToggle } from "./ThemeToggle";

function RailLink({ to, label, icon, active }: { to: string; label: string; icon: IconName; active?: boolean }) {
  return (
    <Tip label={label} side="right">
      <NavLink
        to={to}
        aria-label={label}
        className={({ isActive }) => {
          const on = active ?? isActive;
          return clsx(
            "relative flex size-10 items-center justify-center rounded-lg transition-colors",
            "before:absolute before:top-2.5 before:-left-2 before:h-5 before:w-0.5 before:rounded-full",
            on ? "bg-raised text-ink before:bg-accent" : "text-ink-muted hover:bg-raised hover:text-ink",
          );
        }}
      >
        <Icon name={icon} className="size-5" />
      </NavLink>
    </Tip>
  );
}

export function Rail() {
  const { openSettings, openShortcuts } = useShell();
  const chartMatch = useMatch("/chart/*");
  const current = useMatch("/chart/:instrument/:tf")?.params;
  const last = current?.instrument && current.tf ? { instrument: current.instrument, tf: current.tf } : readLastSelection();
  const chartTo = last ? chartPath(last.instrument, last.tf) : "/chart";

  return (
    <aside className="flex w-14 shrink-0 flex-col items-center border-r border-line bg-page py-2">
      <Link to="/" aria-label="candly — home" className="mb-3 flex size-10 items-center justify-center rounded-lg hover:bg-raised">
        <LogoMark />
      </Link>
      <nav aria-label="Main">
        <ul className="flex flex-col gap-1">
          <li>
            <RailLink to={chartTo} label="Chart" icon="chart" active={chartMatch !== null} />
          </li>
          <li>
            <RailLink to="/scanner" label="Scanner" icon="scanner" />
          </li>
          <li>
            <RailLink to="/scorecard" label="Scorecard" icon="scorecard" />
          </li>
          <li>
            <RailLink to="/accuracy" label="Accuracy" icon="accuracy" />
          </li>
          <li>
            <RailLink to="/news" label="News" icon="news" />
          </li>
        </ul>
      </nav>
      <div className="mt-auto flex flex-col items-center gap-1">
        <IconButton icon="keyboard" label="Keyboard shortcuts" tip="Keyboard shortcuts (?)" side="right" size="lg" onClick={openShortcuts} />
        <IconButton icon="settings" label="Settings" tip="Settings — capital and risk per trade" side="right" size="lg" onClick={openSettings} />
        <ThemeToggle />
        <ConnectionStatus />
      </div>
    </aside>
  );
}
