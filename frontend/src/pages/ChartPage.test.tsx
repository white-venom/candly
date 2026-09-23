import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import {
  DAY,
  T0,
  abstainingForecast,
  candles,
  catalog,
  forecast,
  instruments,
  levels,
  signal,
} from "../test/fixtures";
import { jsonResponse, mockApi, never, renderWithProviders, unreachable } from "../test/utils";

// Canvas charts can't render in jsdom; the chart itself is covered by the controller tests.
vi.mock("../chart/PriceChart", () => ({
  PriceChart: ({ label, signals }: { label: string; signals: { label: string }[] }) => (
    <div data-testid="price-chart" data-markers={signals.map((s) => s.label).join(",")}>
      {label}
    </div>
  ),
}));

const ROUTE = "/chart/NSE:RELIANCE/1D";

const doji = signal({ pattern: "doji", label: "Doji", direction: "neutral", time: candles.candles[1].time, invalidation: null });

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/api/instruments": instruments,
    "/api/candles": candles,
    "/api/patterns": [
      signal(),
      signal({ state: "forming", time: candles.candles[1].time, pattern: "shooting_star", label: "Shooting star", direction: "bearish" }),
    ],
    "/api/levels": levels,
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
    expect(screen.getByRole("region", { name: "Trade plan" }).textContent).toContain("Qty 111");
  });

  it("shows the expiry next to the chart title, and none when the backend doesn't send it", async () => {
    api();
    const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
    const heading = await screen.findByRole("heading", { level: 1 });
    expect(within(heading.parentElement!).getByText("Monthly expiry Tue 29 Sep · 4d")).toBeTruthy();
    unmount();

    api({ "/api/instruments": instruments.map(({ expiry: _expiry, ...older }) => older) });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    const plain = await screen.findByRole("heading", { level: 1 });
    expect(plain.parentElement!.textContent).not.toMatch(/expiry/i);
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
    expect(screen.queryByRole("region", { name: "Trade plan" })).toBeNull();
  });

  describe("signal filters", () => {
    const patternUrls = (fetchMock: ReturnType<typeof api>) =>
      fetchMock.mock.calls
        .map(([input]) => new URL(String(input), "http://localhost"))
        .filter((u) => u.pathname === "/api/patterns");

    it("ask for directional signals only by default, and remember “Show neutral patterns”", async () => {
      const user = userEvent.setup();
      const fetchMock = api({
        "/api/patterns": (url: URL) =>
          jsonResponse(200, url.searchParams.get("directional_only") === "true" ? [signal()] : [signal(), doji]),
      });
      const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByText("Hammer");
      const first = patternUrls(fetchMock)[0].searchParams;
      expect(first.get("directional_only")).toBe("true");
      expect(first.get("certified_only")).toBe("false");
      expect(screen.queryByText("Doji")).toBeNull();
      expect(screen.getByTestId("price-chart").dataset.markers).toBe("Hammer");

      const neutral = screen.getByRole<HTMLInputElement>("checkbox", { name: "Show neutral patterns" });
      expect(neutral.checked).toBe(false);
      expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Certified only" }).checked).toBe(false);
      await user.click(neutral);
      expect(await screen.findByText("Doji")).toBeTruthy();
      expect(patternUrls(fetchMock).at(-1)!.searchParams.get("directional_only")).toBe("false");
      expect(screen.getByTestId("price-chart").dataset.markers).toBe("Hammer,Doji");
      unmount();

      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect((await screen.findByRole<HTMLInputElement>("checkbox", { name: "Show neutral patterns" })).checked).toBe(true);
      expect(await screen.findByText("Doji")).toBeTruthy();
    });

    it("hide neutral signals even when an older backend ignores the flags", async () => {
      api({ "/api/patterns": [signal(), doji] });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByText("Hammer");
      expect(screen.queryByText("Doji")).toBeNull();
      expect(screen.getByTestId("price-chart").dataset.markers).toBe("Hammer");
    });

    it("ask for certified signals only and explain an empty list", async () => {
      const user = userEvent.setup();
      const fetchMock = api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByText("Hammer");
      await user.click(screen.getByRole("checkbox", { name: "Certified only" }));
      expect(await screen.findByText("No certified signals on this chart.")).toBeTruthy();
      expect(patternUrls(fetchMock).at(-1)!.searchParams.get("certified_only")).toBe("true");
      expect(JSON.parse(window.localStorage.getItem("candly.signalFilters")!)).toEqual({ showNeutral: false, certifiedOnly: true });
    });
  });

  describe("key levels", () => {
    it("warn when they come from an older session than the newest data", async () => {
      api({ "/api/levels": { ...levels, as_of: T0 - 2 * DAY, stale: true } });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await screen.findByText("Levels from 21 Sep 2026 — newer session data exists")).toBeTruthy();
      expect(screen.getByText("Levels (stale)")).toBeTruthy();
    });

    it("say nothing when current, or when an older backend omits as_of and stale", async () => {
      api();
      const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByText("Levels");
      expect(screen.queryByText(/newer session data exists/)).toBeNull();
      unmount();

      const { as_of: _asOf, stale: _stale, ...older } = levels;
      api({ "/api/levels": older });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByText("Levels");
      expect(screen.queryByText(/newer session data exists/)).toBeNull();
    });
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
