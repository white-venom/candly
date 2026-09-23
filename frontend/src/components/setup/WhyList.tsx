import clsx from "clsx";
import type { Forecast } from "../../api/types";
import { describeDriver, type DriverIcon } from "../../lib/messages";
import { Eyebrow } from "../ui/Chip";
import { Icon, type IconName } from "../ui/Icon";

const ICON: Record<DriverIcon, IconName> = {
  trend: "trendFlat",
  volatility: "wave",
  level: "level",
  analogs: "layers",
  pattern: "candle",
  expiry: "calendar",
  other: "dot",
};

const TONE = { bullish: "text-up", bearish: "text-down", neutral: "text-ink-faint" } as const;

/** Up to four drivers, each one plain sentence. */
export function WhyList({ drivers }: { drivers: Forecast["drivers"] }) {
  if (drivers.length === 0) return null;
  return (
    <section aria-label="Why">
      <Eyebrow>Why</Eyebrow>
      <ul className="mt-2 flex flex-col gap-2">
        {drivers.slice(0, 4).map((d, i) => {
          const { icon, text } = describeDriver(d);
          const name: IconName = icon === "trend" ? (d.effect === "bullish" ? "trendUp" : d.effect === "bearish" ? "trendDown" : "trendFlat") : ICON[icon];
          return (
            <li key={`${d.name}-${i}`} className="flex items-start gap-2.5">
              <span aria-hidden="true" className={clsx("mt-px flex size-5 shrink-0 items-center justify-center rounded-md bg-raised", TONE[d.effect])}>
                <Icon name={name} className="size-3.5" strokeWidth={2} />
              </span>
              <span className="text-sm leading-5 text-ink-muted">{text}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
