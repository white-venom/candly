import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { accuracy, instruments, ledgerEntry } from "../test/fixtures";
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

  it("passes the filters to the API", async () => {
    const fetchMock = mockApi({ "/api/instruments": instruments, "/api/accuracy": accuracy, "/api/ledger": [] });
    renderWithProviders(<AccuracyPage />, { route: "/accuracy?instrument=NSE:RELIANCE&tf=1D&days=30" });
    await screen.findByText("61.2 / 100");
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/accuracy?instrument=NSE%3ARELIANCE&tf=1D&days=30");
    expect(urls).toContain("/api/ledger?instrument=NSE%3ARELIANCE&tf=1D&limit=100");
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
