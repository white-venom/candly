import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { scannerRow } from "../test/fixtures";
import { jsonResponse, mockApi, never, renderWithProviders } from "../test/utils";
import { ScannerPage } from "./ScannerPage";

const rows = [
  scannerRow({}),
  scannerRow({ instrument: "NSE:TCS", name: "TCS", change_pct: -0.4, p_up: 0.5, score: 0, abstain: true, direction: "neutral", top_signal: null }),
  scannerRow({ instrument: "NSE:INFY", name: "Infosys", change_pct: 2.5, score: 0.02, rel_volume: null }),
];

const names = () => screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].querySelector("a")!.textContent);

describe("scanner page", () => {
  it("shows loading, then rows sorted by score", async () => {
    mockApi({ "/api/scanner": rows });
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    expect(screen.getByText("Scanning…")).toBeTruthy();
    await screen.findByText("TCS");
    expect(names()).toEqual(["Reliance Industries", "Infosys", "TCS"]);
    expect(screen.getByText("no clear edge")).toBeTruthy();
  });

  it("sorts by a column and flips direction", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/scanner": rows });
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await screen.findByText("TCS");
    await user.click(screen.getByRole("button", { name: /Change/ }));
    expect(names()).toEqual(["Infosys", "Reliance Industries", "TCS"]);
    await user.click(screen.getByRole("button", { name: /Change/ }));
    expect(names()).toEqual(["TCS", "Reliance Industries", "Infosys"]);
    expect(screen.getByRole("columnheader", { name: /Change/ }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("opens the chart when a row is clicked", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/scanner": rows });
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    await user.click((await screen.findByText("TCS")).closest("tr")!.querySelectorAll("td")[1]);
    expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:TCS/1D");
  });

  it("shows the empty and error states", async () => {
    mockApi({ "/api/scanner": [] });
    const { unmount } = renderWithProviders(<ScannerPage />, { route: "/scanner?tf=1D" });
    expect(await screen.findByText("No data yet — run ingest")).toBeTruthy();
    unmount();
    mockApi({ "/api/scanner": () => jsonResponse(400, { detail: "unknown timeframe 2h" }) });
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=2h" });
    expect((await screen.findByRole("alert")).textContent).toContain("unknown timeframe 2h");
  });

  it("requests the timeframe from the URL", async () => {
    const fetchMock = mockApi({ "/api/scanner": never });
    renderWithProviders(<ScannerPage />, { route: "/scanner?tf=15m" });
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/scanner?tf=15m");
  });
});
