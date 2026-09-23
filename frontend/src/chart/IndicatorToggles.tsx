import clsx from "clsx";
import type { IndicatorInfo } from "../api/types";
import { INDICATOR_BG } from "../lib/palette";
import type { EnabledIndicator } from "./transforms";

const GROUP_ORDER: IndicatorInfo["group"][] = ["trend", "momentum", "volatility", "volume"];

export function IndicatorToggles({
  catalog,
  enabled,
  onToggle,
}: {
  catalog: IndicatorInfo[];
  enabled: EnabledIndicator[];
  onToggle: (name: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      {GROUP_ORDER.map((group) => {
        const items = catalog.filter((c) => c.group === group);
        if (items.length === 0) return null;
        return (
          <fieldset key={group} className="flex flex-wrap items-center gap-1.5">
            <legend className="float-left mr-1 text-[11px] tracking-wide text-ink-faint uppercase">{group}</legend>
            {items.map((info) => {
              const on = enabled.find((e) => e.name === info.name);
              return (
                <label
                  key={info.name}
                  title={info.pane === "price" ? "Overlay on the price pane" : "Opens in its own pane"}
                  className={clsx(
                    "inline-flex cursor-pointer items-center gap-1.5 rounded border px-2 py-0.5 text-xs",
                    on ? "border-line-strong bg-raised text-ink" : "border-line text-ink-muted hover:text-ink",
                  )}
                >
                  <input type="checkbox" checked={Boolean(on)} onChange={() => onToggle(info.name)} className="accent-accent" />
                  {on && <span aria-hidden="true" className={clsx("size-2 rounded-full", INDICATOR_BG[on.slot % INDICATOR_BG.length])} />}
                  {info.label}
                </label>
              );
            })}
          </fieldset>
        );
      })}
    </div>
  );
}
