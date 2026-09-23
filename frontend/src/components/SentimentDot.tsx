import clsx from "clsx";
import { sentimentOf, type Sentiment } from "../lib/format";

const LOOK: Record<Sentiment, { dot: string; label: string }> = {
  positive: { dot: "bg-up", label: "Positive" },
  negative: { dot: "bg-down", label: "Negative" },
  neutral: { dot: "bg-line-strong", label: "Neutral" },
  unknown: { dot: "border border-line-strong", label: "Not scored" },
};

export function SentimentDot({ value, className }: { value: number | null; className?: string }) {
  const look = LOOK[sentimentOf(value)];
  const score = value === null ? "" : ` (${value > 0 ? "+" : ""}${value.toFixed(2)})`;
  return (
    <span
      role="img"
      aria-label={`${look.label} sentiment${score}`}
      title={`${look.label} sentiment${score}`}
      className={clsx("inline-block size-2 shrink-0 rounded-full", look.dot, className)}
    />
  );
}
