import clsx from "clsx";
import { useId, type ReactNode, type SelectHTMLAttributes } from "react";

export type SegmentOption<T extends string> = { value: T; label: ReactNode; title?: string; muted?: boolean };

/** A row of mutually exclusive buttons (aria-pressed), e.g. the timeframe picker. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  className,
}: {
  label: string;
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={clsx("inline-flex h-8 shrink-0 items-center rounded-md border border-line bg-page p-0.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={clsx(
              "h-full rounded-[5px] px-2.5 text-xs font-medium whitespace-nowrap tabular-nums transition-colors",
              active ? "bg-raised text-ink shadow-sm" : o.muted ? "text-ink-faint hover:text-ink-muted" : "text-ink-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** An on/off row with a track, for menus and settings. */
export function Switch({
  checked,
  onChange,
  label,
  description,
  leading,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description?: string;
  leading?: ReactNode;
}) {
  const descId = useId();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={description ? descId : undefined}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-raised focus-inset"
    >
      {leading}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-ink">{label}</span>
        {description && (
          <span id={descId} className="block text-2xs text-ink-faint">
            {description}
          </span>
        )}
      </span>
      <span aria-hidden="true" className={clsx("relative h-4 w-7 shrink-0 rounded-full transition-colors", checked ? "bg-accent" : "bg-line-strong")}>
        <span className={clsx("absolute top-0.5 size-3 rounded-full bg-surface transition-transform", checked ? "translate-x-3.5" : "translate-x-0.5")} />
      </span>
    </button>
  );
}

export function SelectField({ label, children, className, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className={clsx("inline-flex items-center gap-2 text-xs text-ink-muted", className)}>
      {label}
      <select
        {...props}
        className="h-8 max-w-56 rounded-md border border-line bg-surface px-2 text-sm text-ink transition-colors hover:border-line-strong disabled:opacity-60"
      >
        {children}
      </select>
    </label>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-page px-1 font-sans text-2xs font-medium text-ink-muted">
      {children}
    </kbd>
  );
}
