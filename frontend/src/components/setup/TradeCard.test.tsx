import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { forecast } from "../../test/fixtures";
import { renderWithProviders } from "../../test/utils";
import { TradeCard } from "./TradeCard";

const card = () => screen.getByRole("region", { name: "Trade plan" });
const value = (label: string) => within(card()).getByText(label).nextElementSibling?.textContent;

describe("trade card", () => {
  it("shows entry, stop, target, reward:risk and a size from the default settings", () => {
    renderWithProviders(<TradeCard trade={forecast.trade!} units="units" />);
    expect(value("Entry")).toBe("103.00");
    expect(value("Stop")).toBe("98.50");
    expect(within(card()).getByText("4.50 per unit")).toBeTruthy();
    expect(value("Target")).toBe("104.50");
    expect(value("Reward : risk")).toBe("0.33 : 1");
    expect(within(card()).getByText("Long")).toBeTruthy();
    // ₹1,00,000 × 0.5% = ₹500; 500 / 4.5 = 111.1 → 111 units risking ₹499.50
    expect(card().textContent).toContain("Qty 111 units");
    expect(card().textContent).toContain("₹499.50 at risk");
    expect(card().textContent).toContain("0.5% of ₹1,00,000");
    expect(within(card()).getByText("Educational — not investment advice. Costs not included.")).toBeTruthy();
  });

  it("labels index and MCX quantities as units, not lots", () => {
    renderWithProviders(<TradeCard trade={forecast.trade!} units="units (not lots)" />);
    expect(card().textContent).toContain("Qty 111 units (not lots)");
  });

  it("says so when the budget is below one unit's risk, and marks a short", () => {
    renderWithProviders(<TradeCard trade={{ entry: 25_000, stop: 26_000, target: 24_000, reward_risk: 1 }} units="units" />);
    expect(card().textContent).toContain("Qty 0");
    expect(card().textContent).toContain("less than one unit’s risk (₹1,000.00)");
    expect(within(card()).getByText("Short")).toBeTruthy();
  });

  it("can't size a stop that equals the entry", () => {
    renderWithProviders(<TradeCard trade={{ entry: 100, stop: 100, target: 101, reward_risk: 0 }} units="units" />);
    expect(card().textContent).toContain("Can’t size this trade: the stop equals the entry.");
  });
});
