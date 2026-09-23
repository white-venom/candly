import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import { accuracy, health, instruments, ledgerEntry } from "./test/fixtures";
import { mockApi, renderWithProviders } from "./test/utils";

describe("app shell", () => {
  it.each(["light", "dark"] as const)("renders /accuracy?theme=%s without console errors", async (theme) => {
    const errors = vi.spyOn(console, "error");
    const warnings = vi.spyOn(console, "warn");
    const route = `/accuracy?theme=${theme}`;
    window.history.replaceState(null, "", route);
    mockApi({
      "/api/health": health,
      "/api/instruments": instruments,
      "/api/accuracy": accuracy,
      "/api/ledger": [{ ...ledgerEntry, made_at: Date.now() / 1000 }],
    });
    renderWithProviders(<App />, { route });

    expect(await screen.findByRole("heading", { name: "Accuracy", level: 1 })).toBeTruthy();
    expect(await screen.findByRole("img", { name: /Calibration/ })).toBeTruthy();
    expect(await screen.findByRole("img", { name: /Predicted vs actual candles/ })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /^Data connection: Live data/ })).toBeTruthy();
    expect(document.documentElement.dataset.theme).toBe(theme);
    expect(errors).not.toHaveBeenCalled();
    expect(warnings).not.toHaveBeenCalled();
  });
});
