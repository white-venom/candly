import clsx from "clsx";
import { useState } from "react";
import type { IndicatorInfo } from "../../api/types";
import { PRESETS } from "../../chart/indicatorSelection";
import type { EnabledIndicator } from "../../chart/transforms";
import { INDICATOR_BG } from "../../lib/palette";
import { Switch } from "../ui/Controls";
import { Icon } from "../ui/Icon";
import { Popover } from "../ui/Popover";
import { Tip } from "../ui/Tooltip";

const GROUPS: { key: IndicatorInfo["group"]; label: string }[] = [
  { key: "trend", label: "Trend" },
  { key: "momentum", label: "Momentum" },
  { key: "volatility", label: "Volatility" },
  { key: "volume", label: "Volume" },
];

function samePreset(enabled: EnabledIndicator[], names: string[]): boolean {
  return enabled.length === names.length && names.every((n) => enabled.some((e) => e.name === n));
}

/** "Indicators" button with a count; opens search, presets and one switch per indicator. */
export function IndicatorsMenu({
  entries,
  enabled,
  onToggle,
  onPreset,
  open,
  onOpenChange,
}: {
  entries: IndicatorInfo[];
  enabled: EnabledIndicator[];
  onToggle: (name: string) => void;
  onPreset: (names: string[]) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const shown = entries.filter((e) => !q || e.label.toLowerCase().includes(q) || e.name.includes(q));

  return (
    <div className="relative">
      <Tip label="Indicators (i)">
        <button
          type="button"
          aria-expanded={open}
          aria-label={`Indicators, ${enabled.length} on`}
          onClick={() => onOpenChange(!open)}
          className={clsx(
            "inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-sm transition-colors",
            open ? "bg-raised text-ink" : "text-ink-muted hover:bg-raised hover:text-ink",
          )}
        >
          <Icon name="indicators" className="size-[18px]" />
          <span className="hidden @[50rem]:inline">Indicators</span>
          {enabled.length > 0 && (
            <span className="min-w-4 rounded-full bg-accent/15 px-1.5 text-2xs leading-4 font-semibold text-accent tabular-nums">{enabled.length}</span>
          )}
        </button>
      </Tip>
      <Popover open={open} onClose={() => onOpenChange(false)} label="Indicators" className="flex max-h-[min(34rem,75vh)] w-80 flex-col">
        <div className="shrink-0 border-b border-line p-2">
          <label className="flex h-8 items-center gap-2 rounded-md border border-line bg-page px-2 text-ink-faint focus-within:border-accent">
            <Icon name="search" className="size-3.5" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search indicators"
              aria-label="Search indicators"
              className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-faint"
            />
          </label>
          <div role="group" aria-label="Presets" className="mt-2 flex flex-wrap gap-1.5">
            {PRESETS.map((p) => {
              const active = samePreset(enabled, p.names);
              return (
                <button
                  key={p.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onPreset(p.names)}
                  className={clsx(
                    "h-6 rounded-md border px-2 text-xs transition-colors",
                    active ? "border-accent/50 bg-accent/10 text-accent" : "border-line text-ink-muted hover:border-line-strong hover:text-ink",
                  )}
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {GROUPS.map((g) => {
            const items = shown.filter((e) => e.group === g.key);
            if (items.length === 0) return null;
            return (
              <section key={g.key} aria-label={g.label} className="mb-1">
                <h3 className="px-2 pt-2 pb-1 text-2xs font-semibold tracking-wider text-ink-faint uppercase">{g.label}</h3>
                {items.map((info) => {
                  const on = enabled.find((e) => e.name === info.name);
                  return (
                    <Switch
                      key={info.name}
                      checked={Boolean(on)}
                      onChange={() => onToggle(info.name)}
                      label={info.label}
                      leading={
                        <span
                          aria-hidden="true"
                          className={clsx("size-2 shrink-0 rounded-full", on ? INDICATOR_BG[on.slot % INDICATOR_BG.length] : "border border-line-strong")}
                        />
                      }
                    />
                  );
                })}
              </section>
            );
          })}
          {shown.length === 0 && <p className="px-2 py-4 text-center text-xs text-ink-faint">No indicator matches “{query}”.</p>}
        </div>
      </Popover>
    </div>
  );
}
