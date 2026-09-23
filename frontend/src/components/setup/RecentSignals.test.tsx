import type { UseQueryResult } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { PatternSignal } from "../../api/types";
import { DEFAULT_SIGNAL_FILTERS } from "../../lib/signalFilters";
import { DAY, T0, signal } from "../../test/fixtures";
import { renderWithProviders } from "../../test/utils";
import { RecentSignals } from "./RecentSignals";

const query = (data: PatternSignal[]) => ({ data, isError: false, isPlaceholderData: false }) as unknown as UseQueryResult<PatternSignal[]>;

const section = () => screen.getByRole("region", { name: "Recent signals" });

describe("recent signals", () => {
  it("lists the five newest, compactly, with stats only when the scorecard has them", () => {
    const list = Array.from({ length: 7 }, (_, i) => signal({ time: T0 - i * DAY, pattern: `p${i}`, label: `Pattern ${i}`, stats: i === 0 ? signal().stats : null }));
    renderWithProviders(<RecentSignals query={query(list)} tf="1D" filters={DEFAULT_SIGNAL_FILTERS} />);
    const rows = within(section()).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(rows[0].textContent).toContain("Pattern 0");
    expect(rows[0].textContent).toContain("hit 58% vs base 52% · n 64");
    expect(rows[0].textContent).toContain("23 Sep");
    expect(rows[1].textContent).not.toContain("hit");
    expect(section().textContent).not.toMatch(/No scorecard stats/i);
  });

  it("marks certified and forming signals, and nothing else", () => {
    const certified = signal({ pattern: "a", stats: { ...signal().stats!, certified: true } });
    const forming = signal({ pattern: "b", label: "Shooting star", direction: "bearish", state: "forming", time: T0 - DAY });
    renderWithProviders(<RecentSignals query={query([certified, forming, signal({ pattern: "c", time: T0 - 2 * DAY })])} tf="1D" filters={DEFAULT_SIGNAL_FILTERS} />);
    const rows = within(section()).getAllByRole("listitem");
    expect(within(rows[0]).getByText("Certified")).toBeTruthy();
    expect(within(rows[1]).getByText("Forming")).toBeTruthy();
    expect(within(rows[2]).queryByText(/Certified|Forming/)).toBeNull();
  });

  it("opens all signals, with context, in a drawer", async () => {
    const user = userEvent.setup();
    const onExpiryDay = signal({ time: T0, context: { ...signal().context, expiry_day: true, days_to_expiry: 0 } });
    const list = [onExpiryDay, ...Array.from({ length: 6 }, (_, i) => signal({ time: T0 - (i + 1) * DAY, pattern: `p${i}` }))];
    renderWithProviders(<RecentSignals query={query(list)} tf="1D" filters={DEFAULT_SIGNAL_FILTERS} />);
    await user.click(within(section()).getByRole("button", { name: "View all 7" }));
    const drawer = screen.getByRole("dialog", { name: "All signals · 1D" });
    expect(within(drawer).getAllByRole("listitem", { name: "" }).length).toBeGreaterThan(0);
    const tags = within(drawer).getAllByRole("list", { name: "Context" });
    expect(tags).toHaveLength(7);
    const expiryTag = within(tags[0]).getByText("Expiry day");
    expect(expiryTag.className).toContain("text-forming");
    expect(within(tags[1]).getByText("Expiry in 4d").className).toContain("text-ink-muted");
    expect(drawer.textContent).toContain("Invalidated below 98.50");
  });

  it("explains an empty list in terms of the filter", () => {
    renderWithProviders(<RecentSignals query={query([])} tf="1D" filters={DEFAULT_SIGNAL_FILTERS} />);
    expect(section().textContent).toContain("Neutral patterns can be shown from the Patterns menu.");
  });
});
