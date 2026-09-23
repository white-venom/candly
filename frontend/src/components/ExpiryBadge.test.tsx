import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ExpiryInfo } from "../api/types";
import { expiryLabel, expiryTitle, formatExpiryDate } from "../lib/expiry";
import { ExpiryBadge } from "./ExpiryBadge";

const weekly: ExpiryInfo = { next: "2026-09-29", kind: "weekly", days_to_expiry: 4, is_expiry_day: false };

describe("expiry labels", () => {
  it("formats the IST date as weekday, day and month without shifting it", () => {
    expect(formatExpiryDate("2026-09-29")).toBe("Tue 29 Sep");
    expect(formatExpiryDate("2026-10-06")).toBe("Tue 6 Oct");
    expect(formatExpiryDate("2026-01-01")).toBe("Thu 1 Jan");
    expect(formatExpiryDate("soon")).toBe("soon");
  });

  it("names weekly, monthly and MCX contract expiries with trading days to go", () => {
    expect(expiryLabel(weekly)).toBe("Weekly expiry Tue 29 Sep · 4d");
    expect(expiryLabel({ ...weekly, kind: "monthly", next: "2026-10-27", days_to_expiry: 20 })).toBe("Monthly expiry Tue 27 Oct · 20d");
    expect(expiryLabel({ next: "2026-10-19", kind: "contract", days_to_expiry: 17, is_expiry_day: false })).toBe(
      "Contract expiry Mon 19 Oct · 17d",
    );
    expect(expiryTitle(weekly)).toBe("Weekly expiry on Tue 29 Sep, 4 trading days away");
    expect(expiryTitle({ ...weekly, days_to_expiry: 1 })).toBe("Weekly expiry on Tue 29 Sep, 1 trading day away");
  });

  it("says “Expiry today” on the day, whatever the kind", () => {
    for (const kind of ["weekly", "monthly", "contract"] as const) {
      const today: ExpiryInfo = { next: "2026-09-29", kind, days_to_expiry: 0, is_expiry_day: true };
      expect(expiryLabel(today)).toBe("Expiry today");
    }
    expect(expiryTitle({ ...weekly, kind: "monthly", days_to_expiry: 0, is_expiry_day: true })).toBe("Monthly expiry today (Tue 29 Sep)");
  });
});

describe("ExpiryBadge", () => {
  it("is subtle before expiry day", () => {
    render(<ExpiryBadge expiry={weekly} />);
    const badge = screen.getByText("Weekly expiry Tue 29 Sep · 4d");
    expect(badge.className).toContain("text-ink-muted");
    expect(badge.className).not.toContain("text-forming");
    expect(badge.getAttribute("title")).toBe("Weekly expiry on Tue 29 Sep, 4 trading days away");
  });

  it("says where the date comes from, and has a compact form for tight rows", () => {
    render(<ExpiryBadge expiry={{ ...weekly, source: "exchange" }} compact />);
    const badge = screen.getByText("Exp Tue 29 Sep");
    expect(badge.getAttribute("title")).toBe("Weekly expiry on Tue 29 Sep, 4 trading days away — from the exchange contract list");
    expect(expiryTitle({ ...weekly, source: "rules" })).toMatch(/— estimated from rules$/);
  });

  it("is highlighted on expiry day", () => {
    render(<ExpiryBadge expiry={{ ...weekly, days_to_expiry: 0, is_expiry_day: true }} />);
    const badge = screen.getByText("Expiry today");
    expect(badge.className).toContain("text-forming");
    expect(badge.className).toContain("font-semibold");
    expect(badge.className).not.toContain("border-dashed");
  });
});
