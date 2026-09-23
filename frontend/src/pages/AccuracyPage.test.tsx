import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { accuracy, calibrationBins, instruments, ledgerEntry } from "../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../test/utils";
import { AccuracyPage } from "./AccuracyPage";

describe("accuracy page", () => {
  it("shows tiles, the calibration chart and predicted-vs-actual glyphs", async () => {
    mockApi({ "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [{ ...ledgerEntry, made_at: Date.now() / 1000 }] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("61.2 / 100")).toBeTruthy();
    expect(screen.getByText("Hit rate", { selector: "dt" }).nextElementSibling?.textContent).toBe("54.0%");
    expect(screen.getByText("Brier", { selector: "dt" }).nextElementSibling?.textContent).toBe("0.2461");
    expect(screen.getByText("12 abstained · 40 forecasts")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Calibration/ })).toBeTruthy();
    expect(await screen.findByRole("img", { name: /Predicted vs actual candles/ })).toBeTruthy();
    expect(screen.getByText("✗ miss")).toBeTruthy();
  });

  it("passes the filters to the API, scoring analog_v1 by default", async () => {
    const fetchMock = mockApi({ "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?instrument=NSE:RELIANCE&tf=1D&days=30" });
    await screen.findByText("61.2 / 100");
    expect(screen.getByLabelText<HTMLSelectElement>("Method").value).toBe("analog_v1");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/accuracy?instrument=NSE%3ARELIANCE&tf=1D&days=30&method=analog_v1");
    expect(urls).toContain("/api/ledger?instrument=NSE%3ARELIANCE&tf=1D&method=analog_v1&limit=100");
  });

  it("scores a baseline on its own and keeps it in the URL", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({ "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [] });
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
    const baseline = { ...ledgerEntry, id: 2, method: "baseline_random_walk", made_at: Date.now() / 1000 };
    const fetchMock = mockApi({
      "/api/instruments": instruments,
      "/api/accuracy": accuracy,
      "/api/ledger": [{ ...ledgerEntry, made_at: Date.now() / 1000 }, baseline],
    });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?method=all" });
    expect(await screen.findByText("baseline_random_walk")).toBeTruthy();
    expect(screen.getByText("analog_v1")).toBeTruthy();
    expect(screen.getByText(/Accuracy is scored one method at a time/)).toBeTruthy();
    expect(screen.queryByText("61.2 / 100")).toBeNull();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/ledger?limit=100");
    expect(urls.some((u) => u.startsWith("/api/accuracy"))).toBe(false);
  });

  it("falls back to analog_v1 for an unknown method in the URL", async () => {
    const fetchMock = mockApi({ "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?method=analog-v1" });
    await screen.findByText("61.2 / 100");
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/accuracy?days=90&method=analog_v1");
  });

  it("says there are no graded forecasts when every calibration bin is empty", async () => {
    mockApi({
      "/api/instruments": instruments,
      "/api/accuracy": { ...accuracy, calibration: calibrationBins({}) },
      "/api/ledger": [],
    });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("No graded forecasts yet")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /Calibration/ })).toBeNull();
  });

  it("shows empty and no-data states", async () => {
    mockApi({
      "/api/instruments": instruments,
      "/api/accuracy": { ...accuracy, summary: { ...accuracy.summary, n_forecasts: 0 } },
      "/api/ledger": () => jsonResponse(503, { detail: "ledger empty" }),
    });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy" });
    expect(await screen.findByText("No forecasts in this window yet — the ledger fills as bars close.")).toBeTruthy();
    expect(await screen.findByText("No data yet — run ingest (or connect Fyers)")).toBeTruthy();
  });
});
