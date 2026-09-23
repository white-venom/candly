import clsx from "clsx";
import type { ReactNode } from "react";
import type { Direction } from "../../api/types";

export type Tone = "neutral" | "up" | "down" | "warn" | "accent" | "forming";

// Outline chips: tinted fills would pull coloured text below AA on the raised surface.
const TONE: Record<Tone, string> = {
  neutral: "border-line text-ink-muted",
  up: "border-up/50 text-up",
  down: "border-down/50 text-down",
  // Amber but solid: an event worth noticing (expiry day), not an unfinished bar.
  warn: "border-forming text-forming font-semibold",
  accent: "border-accent/50 text-accent",
  forming: "border-dashed border-forming text-forming",
};

export function Chip({ tone = "neutral", children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={clsx("inline-flex h-5 shrink-0 items-center gap-1 rounded-md border px-1.5 text-2xs leading-none font-medium whitespace-nowrap", TONE[tone], className)}
    >
      {children}
    </span>
  );
}

export function CertifiedChip() {
  return (
    <Chip tone="accent" title="n ≥ 30, q below the FDR level, positive expectancy after costs, and holds in validation">
      Certified
    </Chip>
  );
}

export function FormingChip() {
  return (
    <Chip tone="forming" title="Would complete if the open candle closed now. Not in the scorecard.">
      Forming
    </Chip>
  );
}

const GLYPH: Record<Direction, { glyph: string; label: string; className: string }> = {
  bullish: { glyph: "▲", label: "Bullish", className: "text-up" },
  bearish: { glyph: "▼", label: "Bearish", className: "text-down" },
  neutral: { glyph: "◆", label: "Neutral", className: "text-ink-faint" },
};

/** ▲ / ▼ / ◆ in the direction's colour, named for screen readers. */
export function DirectionGlyph({ direction, className, withLabel = false }: { direction: Direction; className?: string; withLabel?: boolean }) {
  const d = GLYPH[direction];
  return (
    <span className={clsx("inline-flex shrink-0 items-center gap-1", d.className, className)}>
      <span aria-hidden="true" className="text-[0.8em] leading-none">
        {d.glyph}
      </span>
      {withLabel ? d.label : <span className="sr-only">{d.label}</span>}
    </span>
  );
}

/** Section heading inside panels and cards. */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={clsx("text-2xs font-semibold tracking-wider text-ink-faint uppercase", className)}>{children}</h3>;
}
