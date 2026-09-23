import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { accuracy, calibrationBins, health, instruments, ledgerEntry } from "../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../test/utils";
import { AccuracyPage } from "./AccuracyPage";

const recent = { ...ledgerEntry, made_at: Math.floor(Date.now() / 1000) };

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [recent], ...overrides });
}

describe("accuracy page", () => {
  it("shows KPI tiles, the calibration chart and predicted-vs-actual glyphs", async () => {
    api();
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("61.2 / 100")).toBeTruthy();
    expect(screen.getByText("Hit rate", { selector: "dt" }).nextElementSibling?.textContent).toBe("54.0%");
    expect(screen.getByText("Brier", { selector: "dt" }).nextElementSibling?.textContent).toBe("0.2461");
    expect(screen.getByText("12 abstained · 40 forecasts")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Calibration/ })).toBeTruthy();
    expect(await screen.findByRole("img", { name: /Predicted vs actual candles/ })).toBeTruthy();
    expect(screen.getByText("✗ Miss")).toBeTruthy();
    expect(screen.getByRole("region", { name: "By group" }).textContent).toContain("NSE:RELIANCE");
  });

  it("passes the filters to the API, scoring analog_v1 by default", async () => {
    const fetchMock = api({ "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?instrument=NSE:RELIANCE&tf=1D&days=30" });
    await screen.findByText("61.2 / 100");
    expect(screen.getByLabelText<HTMLSelectElement>("Method").value).toBe("analog_v1");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/accuracy?instrument=NSE%3ARELIANCE&tf=1D&days=30&method=analog_v1");
    expect(urls).toContain("/api/ledger?instrument=NSE%3ARELIANCE&tf=1D&method=analog_v1&limit=100");
  });

  it("scores a baseline on its own and keeps it in the URL", async () => {
    const user = userEvent.setup();
    const fetchMock = api({ "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    await screen.findByText("61.2 / 100");
    await user.selectOptions(screen.getByLabelText("Method"), "baseline_persistence");
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]) => String(u));
      expect(urls).toContain("/api/accuracy?days=90&method=baseline_persistence");
      expect(urls).toContain("/api/ledger?method=baseline_persistence&limit=100");
    });
    expect(screen.getByTestId("location").textContent).toBe("/accuracy?method=baseline_persistence");
  });

  it("lists every method in the ledger but never pools them into one summary", async () => {
    const fetchMock = api({ "/api/ledger": [recent, { ...recent, id: 2, method: "baseline_random_walk" }] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?method=all" });
    expect(await screen.findByText("baseline_random_walk")).toBeTruthy();
    expect(screen.getByText("analog_v1")).toBeTruthy();
    expect(screen.getByText("Accuracy is scored one method at a time")).toBeTruthy();
    expect(screen.queryByText("61.2 / 100")).toBeNull();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/ledger?limit=100");
    expect(urls.some((u) => u.startsWith("/api/accuracy"))).toBe(false);
  });

  it("falls back to analog_v1 for an unknown method in the URL", async () => {
    const fetchMock = api({ "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?method=analog-v1" });
    await screen.findByText("61.2 / 100");
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/accuracy?days=90&method=analog_v1");
  });

  it("says there are no graded forecasts when every calibration bin is empty", async () => {
    api({ "/api/accuracy": { ...accuracy, calibration: calibrationBins({}) }, "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("No graded forecasts yet")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /Calibration/ })).toBeNull();
  });

  it("shows friendly empty and no-data states", async () => {
    api({
      "/api/accuracy": { ...accuracy, summary: { ...accuracy.summary, n_forecasts: 0 } },
      "/api/ledger": () => jsonResponse(503, { detail: "ledger empty" }),
    });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("No graded forecasts yet")).toBeTruthy();
    expect(screen.getByText("They appear after the first sessions with live data.")).toBeTruthy();
    expect(await screen.findByText("No data yet")).toBeTruthy();
  });
});
