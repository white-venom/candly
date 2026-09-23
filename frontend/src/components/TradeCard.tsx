import { useEffect, useId, useRef, useState, type Ref } from "react";
import type { Trade } from "../api/types";
import { fmtInr, fmtInt, fmtNum, fmtPrice } from "../lib/format";
import { isValidCapital, isValidRiskPct, positionSize, useSizing, type Sizing } from "../lib/sizing";
import { Stat } from "./ui";

function NumberField({
  label,
  value,
  valid,
  hint,
  onValid,
  inputRef,
  step,
}: {
  label: string;
  value: number;
  valid: (x: number) => boolean;
  hint: string;
  onValid: (x: number) => void;
  inputRef?: Ref<HTMLInputElement>;
  step: string;
}) {
  const [draft, setDraft] = useState(String(value));
  const id = useId();
  const bad = draft.trim() === "" || !valid(Number(draft));
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-ink-muted">
        {label}
      </label>
      <input
        id={id}
        ref={inputRef}
        type="number"
        inputMode="decimal"
        step={step}
        min="0"
        value={draft}
        aria-invalid={bad}
        aria-describedby={bad ? `${id}-hint` : undefined}
        onChange={(e) => {
          setDraft(e.target.value);
          const x = Number(e.target.value);
          if (e.target.value.trim() !== "" && valid(x)) onValid(x);
        }}
        className="w-full rounded-md border border-line-strong bg-page px-2 py-1 text-sm text-ink tabular-nums"
      />
      {bad && (
        <p id={`${id}-hint`} className="text-xs text-danger">
          {hint}
        </p>
      )}
    </div>
  );
}

function SizingSettings({ sizing, onChange }: { sizing: Sizing; onChange: (next: Sizing) => void }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const button = useRef<HTMLButtonElement>(null);
  const firstInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) firstInput.current?.focus();
  }, [open]);

  const close = () => {
    setOpen(false);
    button.current?.focus();
  };

  return (
    <div className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-line-strong px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
      >
        Sizing settings
      </button>
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Position sizing"
          onKeyDown={(e) => e.key === "Escape" && close()}
          className="absolute right-0 top-full z-30 mt-1 flex w-60 flex-col gap-2 rounded-lg border border-line-strong bg-surface p-3 shadow-lg"
        >
          <NumberField
            label="Capital (₹)"
            value={sizing.capital}
            valid={isValidCapital}
            hint="Enter an amount above 0."
            onValid={(capital) => onChange({ ...sizing, capital })}
            inputRef={firstInput}
            step="1000"
          />
          <NumberField
            label="Risk per trade (%)"
            value={sizing.riskPct}
            valid={isValidRiskPct}
            hint="Enter a percentage above 0 and at most 100."
            onValid={(riskPct) => onChange({ ...sizing, riskPct })}
            step="0.1"
          />
          <p className="text-[11px] text-ink-faint">Saved in this browser only.</p>
          <button type="button" onClick={close} className="self-end rounded-md border border-accent px-3 py-1 text-xs font-medium text-accent hover:bg-raised">
            Done
          </button>
        </div>
      )}
    </div>
  );
}

/** Only for directional calls: the caller never renders it while abstaining. */
export function TradeCard({ trade }: { trade: Trade }) {
  const { sizing, update } = useSizing();
  const size = positionSize(trade, sizing);
  return (
    <section aria-label="Trade plan" className="rounded-md border border-line p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-[11px] tracking-wide text-ink-faint uppercase">Trade plan</h3>
        <div className="ml-auto">
          <SizingSettings sizing={sizing} onChange={update} />
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        <Stat label="Entry" value={fmtPrice(trade.entry)} sub="reference close, fills at next open" />
        <Stat label="Stop (invalidation)" value={fmtPrice(trade.stop)} sub={size ? `${fmtPrice(size.riskPerUnit)} per unit` : undefined} />
        <Stat label="Target (p50)" value={fmtPrice(trade.target)} />
        <Stat label="Reward : risk" value={`${fmtNum(trade.reward_risk, 2)} : 1`} />
      </dl>
      <div className="mt-2 border-t border-line pt-2 tabular-nums">
        {size ? (
          <>
            <p className="text-ink">
              Qty <span className="font-semibold">{fmtInt(size.qty)}</span>
              <span className="text-ink-muted"> · ₹ at risk {fmtInr(size.riskAmount, 2)}</span>
            </p>
            <p className="text-xs text-ink-faint">
              {sizing.riskPct.toLocaleString("en-IN", { maximumFractionDigits: 3 })}% of {fmtInr(sizing.capital)} ={" "}
              {fmtInr(size.riskBudget, 2)} budget
              {size.qty > 0 && <> · position value {fmtInr(size.positionValue, 2)}</>}
              {size.qty === 0 && <> · less than one unit’s risk ({fmtInr(size.riskPerUnit, 2)})</>}
            </p>
          </>
        ) : (
          <p className="text-ink-muted">Can’t size this trade: the stop equals the entry.</p>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-faint">Educational — not investment advice. Costs not included.</p>
    </section>
  );
}
