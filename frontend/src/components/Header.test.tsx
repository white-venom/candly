import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { health, instruments } from "../test/fixtures";
import { mockApi, never, renderWithProviders, unreachable } from "../test/utils";
import { Header } from "./Header";

describe("header", () => {
  it("shows data source, each exchange's session and the Fyers state", async () => {
    mockApi({ "/api/health": health, "/api/instruments": instruments });
    renderWithProviders(<Header />, { route: "/scanner" });
    const pill = await screen.findByRole("status", { name: "System status" });
    expect(pill.textContent).toContain("Data: yahoo");
    expect(pill.textContent).toContain("NSEopen· regular");
    expect(pill.textContent).toContain("MCXclosed");
    expect(pill.textContent).toContain("Fyers: not connected");
    expect(screen.getByRole("button", { name: "Connect Fyers" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Switch to (light|dark) theme/ })).toBeTruthy();
  });

  it("shows a stall banner with the reason and the Fyers login when ingest is blocked", async () => {
    const reason = "Fyers not connected — log in to resume data updates";
    mockApi({ "/api/health": { ...health, ingest: { status: "blocked", reason } }, "/api/instruments": instruments });
    renderWithProviders(<Header />, { route: "/scanner" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(`Data updates paused: ${reason}`);
    const connect = screen.getAllByRole("button", { name: "Connect Fyers" });
    expect(connect).toHaveLength(1);
    expect(screen.getByRole("banner").contains(connect[0])).toBe(false);
  });

  it("shows no banner when ingest is ok or the backend doesn't report it", async () => {
    mockApi({ "/api/health": health, "/api/instruments": instruments });
    const { unmount } = renderWithProviders(<Header />, { route: "/scanner" });
    await screen.findByRole("status", { name: "System status" });
    expect(screen.queryByRole("alert")).toBeNull();
    unmount();

    const { ingest: _ingest, ...older } = health;
    mockApi({ "/api/health": older, "/api/instruments": instruments });
    renderWithProviders(<Header />, { route: "/scanner" });
    await screen.findByRole("status", { name: "System status" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("banner").contains(screen.getByRole("button", { name: "Connect Fyers" }))).toBe(true);
  });

  it("says the backend is offline when health can't be reached", async () => {
    mockApi({ "/api/health": unreachable, "/api/instruments": never });
    renderWithProviders(<Header />);
    expect(screen.getByRole("status", { name: "API status" }).textContent).toBe("Checking API…");
    await screen.findByText("Backend offline");
    expect(screen.getByRole("status", { name: "API status" }).textContent).toBe("Backend offline");
  });

  it("groups instruments by exchange and opens the chart for the one picked", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": health, "/api/instruments": instruments });
    renderWithProviders(<Header />, { route: "/chart/NSE:RELIANCE/5m" });
    const select = await screen.findByLabelText<HTMLSelectElement>("Instrument");
    await screen.findByRole("option", { name: "Crude Oil (CRUDEOIL)" });
    expect([...select.querySelectorAll("optgroup")].map((g) => g.label)).toEqual(["NSE", "MCX"]);
    const tfs = within(screen.getByRole("group", { name: "Timeframe" })).getAllByRole("button");
    expect(tfs.map((b) => b.textContent)).toEqual(["5m", "15m", "1h", "1D"]);
    expect(tfs[0].getAttribute("aria-pressed")).toBe("true");

    await user.selectOptions(select, "MCX:CRUDEOIL");
    expect(screen.getByTestId("location").textContent).toBe("/chart/MCX:CRUDEOIL/1D");
    expect(within(screen.getByRole("group", { name: "Timeframe" })).getAllByRole("button").map((b) => b.textContent)).toEqual(["1D"]);
  });

  it("shows the selected instrument's expiry next to it, highlighted on expiry day", async () => {
    const user = userEvent.setup();
    const onExpiryDay = instruments.map((i) =>
      i.id === "NSE:RELIANCE" ? { ...i, expiry: { next: "2026-09-23", kind: "monthly" as const, days_to_expiry: 0, is_expiry_day: true } } : i,
    );
    mockApi({ "/api/health": health, "/api/instruments": onExpiryDay });
    renderWithProviders(<Header />, { route: "/chart/NSE:RELIANCE/1D" });
    const today = await screen.findByText("Expiry today");
    expect(today.className).toContain("text-forming");
    expect(screen.getByRole("banner").contains(today)).toBe(true);

    await user.selectOptions(screen.getByLabelText("Instrument"), "MCX:CRUDEOIL");
    const contract = screen.getByText("Contract expiry Mon 19 Oct · 17d");
    expect(contract.className).toContain("text-ink-muted");
    expect(screen.queryByText("Expiry today")).toBeNull();
  });
});
