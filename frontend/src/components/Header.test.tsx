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
});
