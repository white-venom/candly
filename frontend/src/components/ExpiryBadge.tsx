import type { ExpiryInfo } from "../api/types";
import { expiryLabel, expiryTitle } from "../lib/expiry";
import { Badge } from "./ui";

/** Highlighted on expiry day, subtle otherwise. */
export function ExpiryBadge({ expiry }: { expiry: ExpiryInfo }) {
  return (
    <Badge tone={expiry.is_expiry_day ? "warn" : "neutral"} title={expiryTitle(expiry)}>
      {expiryLabel(expiry)}
    </Badge>
  );
}
