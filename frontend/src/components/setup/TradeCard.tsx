import type { ReactNode } from "react";
import type { Trade } from "../../api/types";
import { fmtInr, fmtInt, fmtNum, fmtPrice } from "../../lib/format";
import { useShell } from "../../lib/shell";
import { positionSize, useSizing } from "../../lib/sizing";
import { Eyebrow } from "../ui/Chip";

function Cell({ label, children, sub }: { label: string; children: ReactNode; sub?: string }) {
  return (
    <div>
      <dt className="text-2xs text-ink-faint">{label}</dt>
      <dd className="text-sm font-medium text-ink tabular-nums">{children}</dd>
      {sub && <dd className="text-2xs text-ink-faint tabular-nums">{sub}</dd>}
    </div>
  );
}

/** Only for directional calls: the caller never renders it while abstaining. */
export function TradeCard({ trade, units }: { trade: Trade; units: string }) {
  const { sizing } = useSizing();
  const { openSettings } = useShell();
  const size = positionSize(trade, sizing);
  const long = trade.target >= trade.entry;

  return (
    <section aria-label="Trade plan" className="rounded-lg border border-line p-4">
      <div className="flex items-center gap-2">
        <Eyebrow>Trade plan</Eyebrow>
        <span className="ml-auto text-2xs font-medium text-ink-muted">{long ? "Long" : "Short"}</span>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
        <Cell label="Entry" sub="next open">
          {fmtPrice(trade.entry)}
        </Cell>
        <Cell label="Stop" sub={size ? `${fmtPrice(size.riskPerUnit)} per unit` : undefined}>
          {fmtPrice(trade.stop)}
        </Cell>
        <Cell label="Target">{fmtPrice(trade.target)}</Cell>
        <Cell label="Reward : risk">{fmtNum(trade.reward_risk, 2)} : 1</Cell>
      </dl>
      <div className="mt-3 border-t border-line pt-3 tabular-nums">
        {size ? (
          <>
            <p className="flex items-baseline justify-between gap-2">
              <span className="text-ink">
                Qty <span className="text-base font-semibold">{fmtInt(size.qty)}</span> <span className="text-xs text-ink-muted">{units}</span>
              </span>
              <span className="text-sm text-ink-muted">{fmtInr(size.riskAmount, 2)} at risk</span>
            </p>
            <p className="mt-1 text-2xs text-ink-faint">
              {sizing.riskPct.toLocaleString("en-IN", { maximumFractionDigits: 3 })}% of {fmtInr(sizing.capital)}
              {size.qty === 0 && <> · less than one unit’s risk ({fmtInr(size.riskPerUnit, 2)})</>}
              {" · "}
              <button type="button" onClick={openSettings} className="text-accent hover:underline">
                Change
              </button>
            </p>
          </>
        ) : (
          <p className="text-xs text-ink-muted">Can’t size this trade: the stop equals the entry.</p>
        )}
      </div>
      <p className="mt-3 text-2xs text-ink-faint">Educational — not investment advice. Costs not included.</p>
    </section>
  );
}
