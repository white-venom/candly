import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ScorecardResponse, ScorecardRow } from "../api/types";
import { T0, health, instruments } from "../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../test/utils";
import { ScorecardPage } from "./ScorecardPage";

const row = (over: Partial<ScorecardRow> = {}): ScorecardRow => ({
  pattern: "hammer",
  label: "Hammer",
  direction: "bullish",
  instrument: "ALL",
  context: "trend=down",
  horizon_bars: 3,
  n: 64,
  hits: 37,
  hit_rate: 0.58,
  base_rate: 0.52,
  ci_low: 0.46,
  ci_high: 0.69,
  p_value: 0.04,
  q_value: 0.08,
  posterior: 0.55,
  expectancy_after_cost_pct: 0.12,
  validation_n: 40,
  validation_hit_rate: 0.56,
  certified: false,
  ...over,
});

const scorecard: ScorecardResponse = {
  meta: {
    tf: "1D",
    built_at: T0,
    train_end: "2019-01-01",
    holdout_start: "2025-10-01",
    n_tests: 1240,
    n_rows: 2,
    instruments: ["NSE:RELIANCE"],
    fdr_alpha: 0.1,
    horizons: [1, 3, 5],
    config_sha256: {},
  },
  rows: [row(), row({ pattern: "shooting_star", label: "Shooting star", direction: "bearish", instrument: "NSE:RELIANCE", certified: true })],
};

describe("scorecard page", () => {
  it("explains itself in one line and shows the table with CI bars", async () => {
    mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/scorecard": scorecard });
    renderWithProviders(<ScorecardPage />, { route: "/scorecard?tf=1D" });
    expect(screen.getByText("How often each pattern worked, out-of-sample, vs the base rate")).toBeTruthy();
    const table = await screen.findByRole("table", { name: "Scorecard" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("All (pooled)");
    expect(rows[0].textContent).toContain("Trend: down");
    expect(rows[0].textContent).toContain("+6.0 pts");
    expect(within(rows[0]).getByRole("img", { name: "CI 46.0% to 69.0%, hit rate 58.0%, base rate 52.0%" })).toBeTruthy();
    expect(within(rows[1]).getByText("Certified")).toBeTruthy();
    expect(within(rows[0]).queryByText("Certified")).toBeNull();
    expect(screen.getByText(/Trained to 1 Jan 2019 · holdout from 1 Oct 2025/)).toBeTruthy();
  });

  it("passes the filters to the API through the URL", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/scorecard": scorecard });
    renderWithProviders(<ScorecardPage />, { route: "/scorecard?tf=1D" });
    await screen.findByRole("table", { name: "Scorecard" });
    await user.click(screen.getByRole("switch", { name: "Certified only" }));
    expect(screen.getByTestId("location").textContent).toBe("/scorecard?tf=1D&certified=1");
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/scorecard?tf=1D&certified_only=true");
  });

  it("explains a missing scorecard (503) instead of showing an error", async () => {
    mockApi({
      "/api/health": health,
      "/api/instruments": instruments,
      "/api/scorecard": () => jsonResponse(503, { detail: "no 1D scorecard yet; run rebuild_scorecard" }),
    });
    renderWithProviders(<ScorecardPage />, { route: "/scorecard?tf=1D" });
    expect(await screen.findByText("No scorecard yet")).toBeTruthy();
    expect(screen.getByText(/built after the Fyers backfill/)).toBeTruthy();
    expect(screen.queryByText(/rebuild_scorecard/)).toBeNull();
    expect(await screen.findByRole("button", { name: "Connect Fyers" })).toBeTruthy();
  });
});
