import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { abstainingForecast, forecast } from "../test/fixtures";
import { ForecastSummary } from "./ForecastSummary";

const card = () => screen.getByRole("region", { name: "Trade plan" });

describe("trade card", () => {
  it("shows entry, stop, target, reward:risk and a size from the default settings", () => {
    render(<ForecastSummary forecast={forecast} tf="1D" />);
    const c = within(card());
    expect(c.getByText("Entry").nextElementSibling?.textContent).toBe("103.00");
    expect(c.getByText("Stop (invalidation)").nextElementSibling?.textContent).toBe("98.50");
    expect(c.getByText("4.50 per unit")).toBeTruthy();
    expect(c.getByText("Target (p50)").nextElementSibling?.textContent).toBe("104.50");
    expect(c.getByText("Reward : risk").nextElementSibling?.textContent).toBe("0.33 : 1");
    // ₹1,00,000 × 0.5% = ₹500; 500 / 4.5 = 111.1 → 111 units risking ₹499.50
    expect(card().textContent).toContain("Qty 111 · ₹ at risk ₹499.50");
    expect(card().textContent).toContain("0.5% of ₹1,00,000 = ₹500.00 budget · position value ₹11,433.00");
    expect(c.getByText("Educational — not investment advice. Costs not included.")).toBeTruthy();
  });

  it("re-sizes from capital and risk % edited in the settings popover, and remembers them", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ForecastSummary forecast={forecast} tf="1D" />);
    await user.click(screen.getByRole("button", { name: "Sizing settings" }));
    const dialog = screen.getByRole("dialog", { name: "Position sizing" });
    const capital = within(dialog).getByLabelText("Capital (₹)");
    expect(document.activeElement).toBe(capital);

    await user.clear(capital);
    await user.type(capital, "200000");
    expect(card().textContent).toContain("Qty 222 · ₹ at risk ₹999.00");

    const risk = within(dialog).getByLabelText("Risk per trade (%)");
    await user.clear(risk);
    expect(within(dialog).getByText("Enter a percentage above 0 and at most 100.")).toBeTruthy();
    expect(card().textContent).toContain("Qty 222");
    await user.type(risk, "1");
    expect(card().textContent).toContain("Qty 444 · ₹ at risk ₹1,998.00");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("candly.sizing")!)).toEqual({ capital: 200000, riskPct: 1 });

    unmount();
    render(<ForecastSummary forecast={forecast} tf="1D" />);
    expect(card().textContent).toContain("Qty 444");
  });

  it("says so when the budget is below one unit's risk", () => {
    render(<ForecastSummary forecast={{ ...forecast, trade: { entry: 25_000, stop: 24_000, target: 26_000, reward_risk: 1 } }} tf="1D" />);
    expect(card().textContent).toContain("Qty 0");
    expect(card().textContent).toContain("less than one unit’s risk (₹1,000.00)");
  });

  it("is hidden while abstaining, which shows only the reason", () => {
    render(<ForecastSummary forecast={{ ...abstainingForecast, trade: forecast.trade }} tf="1D" />);
    expect(screen.getByText("|p(up) − base rate| < 0.03")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Trade plan" })).toBeNull();
    expect(screen.queryByText(/Qty/)).toBeNull();
  });

  it("is hidden when the backend sends no trade (older backends omit it)", () => {
    const { trade: _trade, ...older } = forecast;
    render(<ForecastSummary forecast={older} tf="1D" />);
    expect(screen.getByText("56.0%")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Trade plan" })).toBeNull();
  });
});
