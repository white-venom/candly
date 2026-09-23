import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import {
  abstainingForecast,
  candles,
  catalog,
  forecast,
  instruments,
  signal,
} from "../test/fixtures";
import { jsonResponse, mockApi, never, renderWithProviders, unreachable } from "../test/utils";

// Canvas charts can't render in jsdom; the chart itself is covered by the controller tests.
vi.mock("../chart/PriceChart", () => ({
  PriceChart: ({ label }: { label: string }) => <div data-testid="price-chart">{label}</div>,
}));

const ROUTE = "/chart/NSE:RELIANCE/1D";

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/api/instruments": instruments,
    "/api/candles": candles,
    "/api/patterns": [signal(), signal({ state: "forming", time: candles.candles[1].time, pattern: "doji", label: "Doji", direction: "neutral" })],
    "/api/levels": { instrument: "NSE:RELIANCE", tf: "1D", levels: [] },
    "/api/forecast": forecast,
    "/api/news": [],
    "/api/indicators/catalog": catalog,
    "/api/indicators": { instrument: "NSE:RELIANCE", tf: "1D", series: [] },
    ...overrides,
  });
}

describe("chart page", () => {
  it("shows a loading state while candles load", async () => {
    api({ "/api/candles": never });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await screen.findByText("Loading candles…")).toBeTruthy();
  });

  it("renders the chart and the forecast call", async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect((await screen.findByTestId("price-chart")).textContent).toContain("Reliance Industries 1D");
    expect(await screen.findByText("56.0%")).toBeTruthy();
    expect(screen.getByText(/p\(up\) in 3 bars/)).toBeTruthy();
    expect(screen.getByText("Hammer at support")).toBeTruthy();
    expect(screen.getByText("◌ forming")).toBeTruthy();
  });

  it("says abstaining plainly and shows p(up) only as muted context", async () => {
    api({ "/api/forecast": abstainingForecast });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    const box = await screen.findByText("No clear edge right now — abstaining");
    const panel = box.closest("div")!;
    expect(within(panel).getByText("|p(up) − base rate| < 0.03")).toBeTruthy();
    expect(within(panel).getByText("Context only, not a call: p(up) 51.0% vs base rate 50.5%")).toBeTruthy();
    expect(screen.queryByText(/p\(up\) in 3 bars/)).toBeNull();
    expect(screen.queryByText("51.0%")).toBeNull();
  });

  it("shows the no-data message on 503", async () => {
    api({ "/api/candles": () => jsonResponse(503, { detail: "no candles for NSE:RELIANCE 1D" }) });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await screen.findByText("No data yet — run ingest (or connect Fyers)")).toBeTruthy();
    expect(screen.queryByTestId("price-chart")).toBeNull();
  });

  it("shows the empty state when there are no candles", async () => {
    api({ "/api/candles": { ...candles, candles: [], forming: null } });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await screen.findByText("No data yet — run ingest")).toBeTruthy();
  });

  it("says the backend is not running when the API is unreachable", async () => {
    mockApi({ "/api/instruments": unreachable });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await screen.findByText("Backend not running — start it with the command in CLAUDE.md")).toBeTruthy();
    expect(screen.getByText(/uvicorn candly\.api\.app:app/)).toBeTruthy();
  });

  it("handles an unknown instrument and an unsupported timeframe", async () => {
    api();
    const { unmount } = renderWithProviders(<AppRoutes />, { route: "/chart/NSE:NOPE/1D" });
    expect(await screen.findByText(/Unknown instrument “NSE:NOPE”/)).toBeTruthy();
    unmount();
    renderWithProviders(<AppRoutes />, { route: "/chart/MCX:CRUDEOIL/5m" });
    expect(await screen.findByText(/Crude Oil has no 5m data/)).toBeTruthy();
  });

  it("redirects / to a full chart URL", async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect((await screen.findByTestId("price-chart")).textContent).toContain("1D");
    expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:RELIANCE/1D");
  });
});
