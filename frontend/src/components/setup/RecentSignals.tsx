import type { UseQueryResult } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import type { PatternSignal } from "../../api/types";
import { fmtPrice, fmtStats } from "../../lib/format";
import { contextTags } from "../../lib/messages";
import { emptySignalsText, type SignalFilters } from "../../lib/signalFilters";
import { formatBarWhenIST } from "../../lib/time";
import { CertifiedChip, DirectionGlyph, Eyebrow, FormingChip } from "../ui/Chip";
import { Dialog } from "../ui/Dialog";
import { QueryView } from "../ui/States";

const SHOWN = 5;

function SignalRow({ signal: s, tf, detailed = false }: { signal: PatternSignal; tf: string; detailed?: boolean }) {
  const stats = fmtStats(s.stats);
  return (
    <li className={clsx("flex items-start gap-2", detailed ? "border-b border-line px-4 py-3 last:border-b-0" : "py-1.5")}>
      <DirectionGlyph direction={s.direction} className="mt-1" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium text-ink">{s.label}</span>
          {s.state === "forming" && <FormingChip />}
          {s.stats?.certified && <CertifiedChip />}
        </div>
        {stats && <p className="text-2xs text-ink-faint tabular-nums">{stats}</p>}
        {detailed && (
          <>
            <ul aria-label="Context" className="mt-1.5 flex flex-wrap gap-1">
              {contextTags(s.context).map((t) => (
                <li key={t.text} className={clsx("rounded-md border px-1.5 text-2xs leading-4", t.warn ? "border-forming font-semibold text-forming" : "border-line text-ink-muted")}>
                  {t.text}
                </li>
              ))}
            </ul>
            {s.invalidation !== null && (
              <p className="mt-1 text-2xs text-ink-faint tabular-nums">
                Invalidated {s.direction === "bearish" ? "above" : "below"} {fmtPrice(s.invalidation)}
              </p>
            )}
          </>
        )}
      </div>
      <time className="shrink-0 pt-0.5 text-2xs text-ink-faint tabular-nums">{formatBarWhenIST(s.time, tf)}</time>
    </li>
  );
}

/** The five newest signals; "View all" opens the rest in a drawer. */
export function RecentSignals({ query, tf, filters }: { query: UseQueryResult<PatternSignal[]>; tf: string; filters: SignalFilters }) {
  const [open, setOpen] = useState(false);
  const sorted = [...(query.data ?? [])].sort((a, b) => b.time - a.time);

  return (
    <section aria-label="Recent signals">
      <div className="flex items-center gap-2">
        <Eyebrow>Recent signals</Eyebrow>
        {sorted.length > SHOWN && (
          <button type="button" onClick={() => setOpen(true)} className="ml-auto rounded text-xs text-accent hover:underline">
            View all {sorted.length}
          </button>
        )}
      </div>
      <QueryView
        query={query}
        isEmpty={(s) => s.length === 0}
        empty={{ title: "No signals yet", hint: emptySignalsText(filters) }}
        loadingLabel="Loading signals…"
        className="p-4"
      >
        {() => (
          <ul className="mt-1">
            {sorted.slice(0, SHOWN).map((s) => (
              <SignalRow key={s.id} signal={s} tf={tf} />
            ))}
          </ul>
        )}
      </QueryView>
      <Dialog drawer open={open} onClose={() => setOpen(false)} title={`All signals · ${tf}`}>
        <ul>
          {sorted.map((s) => (
            <SignalRow key={s.id} signal={s} tf={tf} detailed />
          ))}
        </ul>
      </Dialog>
    </section>
  );
}
