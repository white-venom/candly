import clsx from "clsx";

export function TimeframeSelect({
  options,
  value,
  onChange,
  label = "Timeframe",
}: {
  options: readonly string[];
  value: string | undefined;
  onChange: (tf: string) => void;
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line-strong p-0.5">
      {options.map((tf) => {
        const active = tf === value;
        return (
          <button
            key={tf}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(tf)}
            className={clsx(
              "rounded px-2.5 py-1 text-xs font-medium tabular-nums",
              active ? "bg-raised text-ink shadow-[inset_0_0_0_1px_var(--line-strong)]" : "text-ink-muted hover:text-ink",
            )}
          >
            {tf}
          </button>
        );
      })}
    </div>
  );
}
