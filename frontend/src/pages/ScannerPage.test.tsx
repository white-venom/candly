import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { health, scannerRow } from "../test/fixtures";
import { jsonResponse, mockApi, never, renderWithProviders } from "../test/utils";
import { ScannerPage } from "./ScannerPage";

const rows = [
  scannerRow({}),
  scannerRow({
    instrument: "NSE:TCS",
    name: "Tata Consultancy Services",
    change_pct: -0.4,
    p_up: 0.5,
    score: 0,
    abstain: true,
    abstain_reason: "edge below costs: median move 0.12% the call's way vs 0.25% round trip (equity, multi-day)",
    direction: "neutral",
    top_signal: null,
  }),
  scannerRow({
    instrument: "NSE:INFY",
    name: "Infosys",
    change_pct: 2.5,
    score: 0.02,
    direction: "bearish",
    p_up: 0.47,
    rel_volume: null,
    expiry: { next: "2026-09-23", kind: "monthly", days_to_expiry: 0, is_expiry_day: true },
  }),
];

function api(scanner: unknown) {
  return mockApi({ "/api/health": health, "/api/scanner": scanner });
}

const symbols = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].querySelector("a")!.textContent);

/** The text of one column, row by row, found by its header. */
function column(label: string): (string | null)[] {
  const index = screen.getAllByRole("columnheader").findIndex((h) => h.textContent?.startsWith(label));
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[index].textContent);
}

describe("scanner page", () => {
  it("shows loading, then rows sorted by score", async () => {
    api(rows);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    expect(screen.getByText("Scanning…")).toBeTruthy();
    await screen.findByText("TCS");
    expect(symbols()).toEqual(["RELIANCE", "INFY", "TCS"]);
  });

  it("gives each row a verdict, with the reason for 'No edge' in plain words on hover", async () => {
    api(rows);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await screen.findByText("TCS");
    expect(column("Verdict")).toEqual(["▲Bullish", "▼Bearish", "No edge — The expected move doesn't cover trading costs."]);
    const noEdge = screen.getByText("No edge");
    expect(noEdge.getAttribute("title")).toBe("The expected move doesn't cover trading costs.");
    expect(column("p(up) vs base")).toEqual(["56% vs 52%", "47% vs 52%", "50% vs 52%"]);
  });

  it("shows each instrument's expiry, highlighted on the day", async () => {
    api([...rows, scannerRow({ instrument: "NSE:SBIN", name: "SBI", score: 0, abstain: true, expiry: null })]);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await screen.findByText("TCS");
    expect(column("Expiry")).toEqual(["Exp Tue 29 Sep", "Expiry today", "Exp Tue 29 Sep", "—"]);
    expect(screen.getByText("Expiry today").className).toContain("text-forming");
  });

  it("sorts by a column and flips direction", async () => {
    const user = userEvent.setup();
    api(rows);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await screen.findByText("TCS");
    await user.click(screen.getByRole("button", { name: /Change/ }));
    expect(symbols()).toEqual(["INFY", "RELIANCE", "TCS"]);
    await user.click(screen.getByRole("button", { name: /Change/ }));
    expect(symbols()).toEqual(["TCS", "RELIANCE", "INFY"]);
    expect(screen.getByRole("columnheader", { name: /Change/ }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("opens the chart when a row is clicked", async () => {
    const user = userEvent.setup();
    api(rows);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await user.click((await screen.findByText("TCS")).closest("tr")!.querySelectorAll("td")[1]);
    expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:TCS/1D");
  });

  it("shows the empty and error states", async () => {
    api([]);
    const { unmount } = renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    expect(await screen.findByText("Nothing to scan yet")).toBeTruthy();
    unmount();
    api(() => jsonResponse(400, { detail: "unknown timeframe 2h" }));
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=2h" });
    expect((await screen.findByRole("alert")).textContent).toContain("Unknown timeframe 2h");
  });

  it("requests the timeframe from the URL and switches it", async () => {
    const user = userEvent.setup();
    const fetchMock = api(never);
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=15m" });
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/scanner?tf=15m");
    await user.click(within(screen.getByRole("group", { name: "Timeframe" })).getByRole("button", { name: "1h" }));
    expect(screen.getByTestId("location").textContent).toBe("/scanner?tf=1h");
  });
});
