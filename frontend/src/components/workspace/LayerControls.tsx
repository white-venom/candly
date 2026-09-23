import clsx from "clsx";
import { useState, type ReactNode } from "react";
import type { DrawnLevel } from "../../chart/levels";
import { fmtPrice } from "../../lib/format";
import type { Layers } from "../../lib/layers";
import type { SignalFilters } from "../../lib/signalFilters";
import { IconButton } from "../ui/Button";
import { Segmented, Switch } from "../ui/Controls";
import { Icon, type IconName } from "../ui/Icon";
import { Popover } from "../ui/Popover";

function LayerButton({ icon, label, on, onToggle, menu }: { icon: IconName; label: string; on: boolean; onToggle: () => void; menu?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative flex items-center">
      <IconButton
        icon={icon}
        label={label}
        tip={`${label}: ${on ? "shown" : "hidden"}`}
        pressed={on}
        size="sm"
        onClick={onToggle}
        className={menu ? "rounded-r-none" : undefined}
      />
      {menu && (
        <>
          <button
            type="button"
            aria-label={`${label} options`}
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className={clsx(
              "flex h-7 w-4 items-center justify-center rounded-r-md transition-colors",
              open ? "bg-raised text-ink" : "text-ink-faint hover:bg-raised hover:text-ink",
            )}
          >
            <Icon name="chevronDown" className="size-3" strokeWidth={2} />
          </button>
          <Popover open={open} onClose={() => setOpen(false)} label={`${label} options`} className="w-72 p-1.5">
            {menu}
          </Popover>
        </>
      )}
    </div>
  );
}

/** Patterns, Levels and Forecast: each shows or hides its layer; the chevrons hold their options. */
export function LayerControls({
  layers,
  onLayers,
  filters,
  onFilters,
  levels,
}: {
  layers: Layers;
  onLayers: (patch: Partial<Layers>) => void;
  filters: SignalFilters;
  onFilters: (next: SignalFilters) => void;
  /** the lines on the chart, listed with their prices */
  levels: DrawnLevel[];
}) {
  return (
    <div role="group" aria-label="Chart layers" className="flex items-center gap-1">
      <LayerButton
        icon="patterns"
        label="Patterns"
        on={layers.patterns}
        onToggle={() => onLayers({ patterns: !layers.patterns })}
        menu={
          <>
            <Switch
              label="Neutral patterns"
              description="Doji, inside and outside bars — no direction, hidden by default"
              checked={filters.showNeutral}
              onChange={(showNeutral) => onFilters({ ...filters, showNeutral })}
            />
            <Switch
              label="Certified only"
              description="Only patterns that passed the scorecard's out-of-sample tests"
              checked={filters.certifiedOnly}
              onChange={(certifiedOnly) => onFilters({ ...filters, certifiedOnly })}
            />
            <p className="px-2 pt-1 pb-1.5 text-2xs text-ink-faint">Also filters the Recent signals list.</p>
          </>
        }
      />
      <LayerButton
        icon="levels"
        label="Levels"
        on={layers.levels}
        onToggle={() => onLayers({ levels: !layers.levels })}
        menu={
          <div className="flex flex-col gap-2 p-2">
            <Segmented
              label="Which levels"
              value={layers.allLevels ? "all" : "key"}
              onChange={(v) => onLayers({ allLevels: v === "all", levels: true })}
              options={[
                { value: "key", label: "Key levels" },
                { value: "all", label: "All levels" },
              ]}
            />
            {levels.length > 0 && (
              <ul aria-label="Levels on the chart" className="flex flex-col py-1 text-xs tabular-nums">
                {levels.map((l) => (
                  <li key={`${l.label}-${l.price}`} className="flex justify-between gap-3 py-0.5">
                    <span className="text-ink-muted">{l.label}</span>
                    <span className="text-ink">{fmtPrice(l.price)}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-2xs text-ink-faint">
              Key: previous day high, low and close, the pivot, and the nearest support and resistance. Levels closer than a fraction of a
              typical bar share one line.
            </p>
          </div>
        }
      />
      <LayerButton icon="forecast" label="Forecast" on={layers.forecast} onToggle={() => onLayers({ forecast: !layers.forecast })} />
    </div>
  );
}
