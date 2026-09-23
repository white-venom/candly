import clsx from "clsx";
import type { ReactNode, SelectHTMLAttributes } from "react";
import type { Direction } from "../api/types";

export function Card({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("rounded-lg border border-line bg-surface p-3", className)}>
      {(title || actions) && (
        <div className="mb-2 flex items-center gap-2">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export type Tone = "up" | "down" | "forming" | "warn" | "neutral" | "accent" | "danger";

// Outline only: tinted fills would pull coloured text below AA on the raised surface.
const TONE: Record<Tone, string> = {
  up: "text-up border-up",
  down: "text-down border-down",
  forming: "text-forming border-forming border-dashed",
  // Amber like "forming" but solid: an event worth noticing (expiry day), not an unfinished bar.
  warn: "text-forming border-forming font-semibold",
  neutral: "text-ink-muted border-line-strong",
  accent: "text-accent border-accent",
  danger: "text-danger border-danger",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 rounded border px-1.5 py-px text-[11px] leading-4 font-medium whitespace-nowrap", TONE[tone])}>
      {children}
    </span>
  );
}

const DIRECTION: Record<Direction, { glyph: string; label: string; className: string }> = {
  bullish: { glyph: "▲", label: "Bullish", className: "text-up" },
  bearish: { glyph: "▼", label: "Bearish", className: "text-down" },
  neutral: { glyph: "◆", label: "Neutral", className: "text-ink-muted" },
};

export function DirectionTag({ direction, compact = false }: { direction: Direction; compact?: boolean }) {
  const d = DIRECTION[direction];
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap", d.className)}>
      <span aria-hidden="true">{d.glyph}</span>
      {compact ? <span className="sr-only">{d.label}</span> : d.label}
    </span>
  );
}

export function FormingBadge() {
  return (
    <Badge tone="forming" title="The pattern would complete if the open candle closed now. Not in the scorecard.">
      ◌ forming
    </Badge>
  );
}

export function CertifiedBadge() {
  return (
    <Badge tone="accent" title="n ≥ 30, q below the FDR level, positive expectancy after costs, and holds in validation">
      ✓ certified
    </Badge>
  );
}

export function SelectField({
  label,
  children,
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className={clsx("flex flex-col gap-1 text-xs text-ink-muted", className)}>
      {label}
      <select
        {...props}
        className="rounded-md border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink disabled:opacity-60"
      >
        {children}
      </select>
    </label>
  );
}

export function Stat({ label, value, sub, className }: { label: ReactNode; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <dt className="text-[11px] tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className="text-ink tabular-nums">{value}</dd>
      {sub && <dd className="text-xs text-ink-faint">{sub}</dd>}
    </div>
  );
}
