import type { PatternSignal } from "../../api/types";
import { fmtStats } from "../../lib/format";
import { CertifiedChip, DirectionGlyph, FormingChip } from "../ui/Chip";

const WIDTH = 232;

/** A small card beside the crosshair when its bar has patterns: name, state and track record. */
export function PatternTooltip({ signals, x, containerWidth }: { signals: PatternSignal[]; x: number; containerWidth: number }) {
  const left = x + 16 + WIDTH > containerWidth - 64 ? Math.max(8, x - 16 - WIDTH) : x + 16;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute top-16 z-20 flex flex-col gap-2 rounded-lg border border-line bg-surface p-2.5 text-xs shadow-md shadow-page/40"
      style={{ left, width: WIDTH }}
    >
      {signals.slice(0, 3).map((s) => (
        <div key={s.id}>
          <div className="flex items-center gap-1.5">
            <DirectionGlyph direction={s.direction} />
            <span className="truncate font-medium text-ink">{s.label}</span>
            {s.state === "forming" && <FormingChip />}
            {s.stats?.certified && <CertifiedChip />}
          </div>
          <p className="mt-0.5 pl-4 text-ink-faint tabular-nums">{fmtStats(s.stats) ?? "No track record in the scorecard yet"}</p>
        </div>
      ))}
      {signals.length > 3 && <p className="pl-4 text-ink-faint">+{signals.length - 3} more on this bar</p>}
    </div>
  );
}
