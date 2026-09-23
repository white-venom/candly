import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { abstainingForecast, forecast } from "../../test/fixtures";
import { VerdictCard } from "./VerdictCard";

const NOW = Date.UTC(2026, 8, 23, 6, 30) / 1000;
const card = () => screen.getByRole("region", { name: "Verdict" });

describe("verdict card", () => {
  it("calls a bullish setup with the odds against the usual", () => {
    render(<VerdictCard forecast={forecast} tf="1D" canConnect={false} />);
    const c = within(card());
    expect(c.getByText("Bullish setup")).toBeTruthy();
    expect(c.getByText("Medium confidence")).toBeTruthy();
    expect(card().textContent).toContain("56% chance it closes higher");
    expect(card().textContent).toContain("usually 52%");
    expect(card().textContent).toContain("Likely range 51%–61% · 64 similar past setups");
  });

  it("calls a bearish setup when p(up) sits below the base rate", () => {
    render(<VerdictCard forecast={{ ...forecast, p_up: 0.44, p_up_ci: [0.39, 0.49] }} tf="1D" canConnect={false} />);
    expect(within(card()).getByText("Bearish setup")).toBeTruthy();
  });

  it("says no clear edge in plain words, with what would change it and p(up) only as context", () => {
    render(<VerdictCard forecast={abstainingForecast} tf="1D" canConnect={false} />);
    const c = within(card());
    expect(c.getByText("No clear edge")).toBeTruthy();
    expect(c.getByText("The odds are too close to a coin flip.")).toBeTruthy();
    expect(c.getByText("A call needs odds at least 3 points away from the usual.")).toBeTruthy();
    expect(c.getByText("Context only, not a call: p(up) 51.0% vs usual 50.5%")).toBeTruthy();
    expect(card().textContent).not.toContain("|p(up)");
    expect(c.queryByText(/chance it closes higher/)).toBeNull();
    expect(c.queryByText(/confidence/)).toBeNull();
  });

  it("never shows a raw reason or a UTC timestamp", () => {
    const stale = {
      ...abstainingForecast,
      p_up: null,
      abstain_reason: "stale data: last closed bar 2026-09-23T04:45:00+00:00, expected 2026-09-23T09:45:00+00:00",
    };
    render(<VerdictCard forecast={stale} tf="1h" canConnect now={NOW} />);
    expect(card().textContent).toContain("Waiting for fresh data — last bar 10:15 IST, next expected 15:15 IST.");
    expect(card().textContent).toContain("Connect Fyers to resume live data.");
    expect(card().textContent).toContain("Historically up 51% of the time over 3 bars.");
    expect(card().textContent).not.toMatch(/stale data|\+00:00|T04:45/);
  });
});
