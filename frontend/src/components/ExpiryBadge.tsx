import type { ExpiryInfo } from "../api/types";
import { expiryLabel, expiryShortLabel, expiryTitle } from "../lib/expiry";
import { Chip } from "./ui/Chip";

/** Amber on expiry day, quiet otherwise. `compact` drops the kind and day count for tight rows. */
export function ExpiryBadge({ expiry, compact = false, className }: { expiry: ExpiryInfo; compact?: boolean; className?: string }) {
  return (
    <Chip tone={expiry.is_expiry_day ? "warn" : "neutral"} title={expiryTitle(expiry)} className={className}>
      {compact ? expiryShortLabel(expiry) : expiryLabel(expiry)}
    </Chip>
  );
}
