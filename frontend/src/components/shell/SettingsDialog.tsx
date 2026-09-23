import { useId, useState } from "react";
import { fmtInr } from "../../lib/format";
import { isValidCapital, isValidRiskPct, useSizing } from "../../lib/sizing";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

function NumberField({
  label,
  value,
  valid,
  hint,
  onValid,
  step,
  suffix,
}: {
  label: string;
  value: number;
  valid: (x: number) => boolean;
  hint: string;
  onValid: (x: number) => void;
  step: string;
  suffix: string;
}) {
  const [draft, setDraft] = useState(String(value));
  const id = useId();
  const bad = draft.trim() === "" || !valid(Number(draft));
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-ink-muted">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
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
          className="h-8 w-full rounded-md border border-line bg-page px-2 text-sm text-ink tabular-nums hover:border-line-strong aria-[invalid=true]:border-danger"
        />
        <span className="w-6 text-xs text-ink-faint">{suffix}</span>
      </div>
      {bad && (
        <p id={`${id}-hint`} className="text-xs text-danger">
          {hint}
        </p>
      )}
    </div>
  );
}

/** Capital and risk per trade, used to size the trade card. */
export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { sizing, update } = useSizing();
  return (
    <Dialog open={open} onClose={onClose} title="Position sizing">
      <div className="flex flex-col gap-4">
        <p className="text-xs text-ink-muted">The trade card sizes each call so that hitting the stop costs this share of your capital.</p>
        <NumberField
          label="Capital"
          suffix="₹"
          value={sizing.capital}
          valid={isValidCapital}
          hint="Enter an amount above 0."
          onValid={(capital) => update({ ...sizing, capital })}
          step="1000"
        />
        <NumberField
          label="Risk per trade"
          suffix="%"
          value={sizing.riskPct}
          valid={isValidRiskPct}
          hint="Enter a percentage above 0 and at most 100."
          onValid={(riskPct) => update({ ...sizing, riskPct })}
          step="0.1"
        />
        <p className="text-xs text-ink-faint tabular-nums">
          Risk budget {fmtInr((sizing.capital * sizing.riskPct) / 100, 2)} per trade · saved in this browser only.
        </p>
        <div className="flex justify-end">
          <Button variant="primary" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
