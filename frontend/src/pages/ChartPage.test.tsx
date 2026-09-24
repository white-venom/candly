import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import type { CandlesResponse } from "../api/types";
import type { DrawnLevel } from "../chart/levels";
import type { MarkerGlyph } from "../chart/transforms";
import {
  DAY,
  T0,
  abstainingForecast,
  candle,
  candles,
  catalog,
  forecast,
  health,
  instruments,
  levels,
  nifty,
  scannerRow,
  signal,
} from "../test/fixtures";
import { jsonResponse, mockApi, never, renderWithProviders, unreachable } from "../test/utils";

// Canvas charts can't render in jsdom; the chart itself is covered by the controller tests.
vi.mock("../chart/PriceChart", () => ({
  PriceChart: ({ label, markers, levels, forecast }: { label: string; markers: MarkerGlyph[]; levels: DrawnLevel[]; forecast: unknown }) => (
    <div
      data-testid="price-chart"
      data-markers={markers.map((m) => `${m.direction}${m.filled ? "" : "(hollow)"}`).join(",")}
      data-levels={levels.map((l) => l.label).join(",")}
      data-forecast={forecast ? "yes" : "no"}
    >
      {label}
    </div>
  ),
}));

const ROUTE = "/chart/NSE:RELIANCE/1D";
const ALL = [nifty, ...instruments];

const doji = signal({ pattern: "doji", label: "Doji", direction: "neutral", time: candles.candles[1].time, invalidation: null });

function api(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "/api/health": health,
    "/api/instruments": instruments,
    "/api/candles": candles,
    "/api/patterns": [
      signal(),
      signal({ state: "forming", time: candles.candles[1].time, pattern: "shooting_star", label: "Shooting star", direction: "bearish" }),
    ],
    "/api/levels": levels,
    "/api/forecast": forecast,
    "/api/news": [],
    "/api/scanner": [scannerRow({})],
    "/api/indicators/catalog": catalog,
    "/api/indicators": { instrument: "NSE:RELIANCE", tf: "1D", series: [] },
    ...overrides,
  });
}

const chart = () => screen.getByTestId("price-chart");
const panel = () => screen.getByRole("complementary", { name: "Setup" });
/** The setup panel, once instruments have loaded. */
const findPanel = async () => within(await screen.findByRole("complementary", { name: "Setup" }));

describe("chart page", () => {
  it("shows a loading state while candles load", async () => {
    api({ "/api/candles": never });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    // the first render in this file also pays for loading the page's modules
    expect(await screen.findByText("Loading candles…", undefined, { timeout: 5000 })).toBeTruthy();
  });

  it("renders the top bar, the chart and the setup panel", async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect((await screen.findByTestId("price-chart")).textContent).toBe("Reliance Industries 1D candlestick chart");
    const top = screen.getByRole("heading", { level: 1 }).closest("header")!;
    expect(top.textContent).toContain("Reliance Industries");
    expect(top.textContent).toContain("103.00");
    expect(top.textContent).toContain("+1.98%");
    expect(top.textContent).toContain("as of 23 Sep");
    expect(within(top).getByText("Exp Tue 29 Sep")).toBeTruthy();

    expect(await (await findPanel()).findByText("Bullish setup")).toBeTruthy();
    expect(within(panel()).getByRole("region", { name: "Trade plan" }).textContent).toContain("Qty 111 units");
    expect(within(panel()).getByText(/^Hammer at support/)).toBeTruthy();
    expect(within(panel()).getByText("Forming")).toBeTruthy();
    expect(chart().dataset.levels).toBe("PDH");
    expect(chart().dataset.forecast).toBe("yes");
    expect(chart().dataset.markers).toBe("bearish(hollow),bullish");
  });

  it("puts the expected next candle first in the panel and keys it on the chart", async () => {
    api();
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    const expected = await (await findPanel()).findByRole("region", { name: "Expected next candle" });
    expect(expected.textContent).toContain("Next session · Thu 24 Sep");
    expect(expected.textContent).toContain("Likely range 101.00 – 106.00");
    const verdict = within(panel()).getByRole("region", { name: "Verdict" });
    expect(expected.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Expected (next 3)")).toBeTruthy();
  });

  it("says abstaining plainly, with no trade card", async () => {
    api({ "/api/forecast": abstainingForecast });
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    expect(await (await findPanel()).findByText("No clear edge")).toBeTruthy();
    expect(within(panel()).getByText("Up/down unclear")).toBeTruthy();
    const verdict = within(panel()).getByRole("region", { name: "Verdict" });
    expect(within(verdict).getByText("The odds are too close to a coin flip.")).toBeTruthy();
    expect(within(panel()).queryByRole("region", { name: "Trade plan" })).toBeNull();
  });

  it("shows the plain-words explanation only when there is one", async () => {
    api({ "/api/forecast": { ...forecast, explanation: "Reliance printed a hammer at support." } });
    const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
    const section = await (await findPanel()).findByRole("region", { name: "In plain words" });
    expect(section.textContent).toContain("Reliance printed a hammer at support.");
    unmount();
    api();
    renderWithProviders(<AppRoutes />, { route: ROUTE });
    await (await findPanel()).findByText("Bullish setup");
    expect(within(panel()).queryByRole("region", { name: "In plain words" })).toBeNull();
  });

  describe("pattern filters", () => {
    const patternUrls = (fetchMock: ReturnType<typeof api>) =>
      fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://localhost")).filter((u) => u.pathname === "/api/patterns");

    it("ask for directional patterns by default, and remember “Neutral patterns” from the Patterns menu", async () => {
      const user = userEvent.setup();
      const fetchMock = api({
        "/api/patterns": (url: URL) => jsonResponse(200, url.searchParams.get("directional_only") === "true" ? [signal()] : [signal(), doji]),
      });
      const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Hammer");
      expect(patternUrls(fetchMock)[0].searchParams.get("directional_only")).toBe("true");
      expect(chart().dataset.markers).toBe("bullish");

      await user.click(screen.getByRole("button", { name: "Patterns options" }));
      const neutral = screen.getByRole("switch", { name: "Neutral patterns" });
      expect(neutral.getAttribute("aria-checked")).toBe("false");
      await user.click(neutral);
      expect(await (await findPanel()).findByText("Doji")).toBeTruthy();
      expect(patternUrls(fetchMock).at(-1)!.searchParams.get("directional_only")).toBe("false");
      expect(chart().dataset.markers).toBe("neutral,bullish");
      unmount();

      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await (await findPanel()).findByText("Doji")).toBeTruthy();
    });

    it("hide neutral patterns even when an older backend ignores the flag", async () => {
      api({ "/api/patterns": [signal(), doji] });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Hammer");
      expect(within(panel()).queryByText("Doji")).toBeNull();
    });

    it("ask for certified patterns only and explain an empty list", async () => {
      const user = userEvent.setup();
      const fetchMock = api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Hammer");
      await user.click(screen.getByRole("button", { name: "Patterns options" }));
      await user.click(screen.getByRole("switch", { name: "Certified only" }));
      expect(await (await findPanel()).findByText("No certified signals on this chart.")).toBeTruthy();
      expect(patternUrls(fetchMock).at(-1)!.searchParams.get("certified_only")).toBe("true");
    });
  });

  describe("layers", () => {
    it("hide the forecast, patterns and levels from their buttons, and remember it", async () => {
      const user = userEvent.setup();
      api();
      const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Bullish setup");
      await user.click(screen.getByRole("button", { name: "Forecast" }));
      await user.click(screen.getByRole("button", { name: "Patterns" }));
      await user.click(screen.getByRole("button", { name: "Levels" }));
      expect(chart().dataset).toMatchObject({ forecast: "no", markers: "", levels: "" });
      expect(screen.getByRole("button", { name: "Forecast" }).getAttribute("aria-pressed")).toBe("false");
      unmount();
      api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Bullish setup");
      expect(chart().dataset.forecast).toBe("no");
    });

    it("show the forecast even when an older version of the app had it switched off", async () => {
      window.localStorage.setItem("candly.layers", JSON.stringify({ patterns: true, levels: true, forecast: false, allLevels: false }));
      api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await (await findPanel()).findByText("Bullish setup");
      expect(chart().dataset.forecast).toBe("yes");
      expect(screen.getByRole("button", { name: "Forecast" }).getAttribute("aria-pressed")).toBe("true");
    });

    it("say where stale levels come from", async () => {
      api({ "/api/levels": { ...levels, as_of: T0 - 2 * DAY, stale: true } });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await screen.findByText("Levels from 21 Sep")).toBeTruthy();
    });

    it("say nothing when levels are current, or an older backend omits as_of and stale", async () => {
      api();
      const { unmount } = renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      expect(screen.queryByText(/Levels from/)).toBeNull();
      unmount();
      const { as_of: _asOf, stale: _stale, ...older } = levels;
      api({ "/api/levels": older });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      expect(screen.queryByText(/Levels from/)).toBeNull();
    });
  });

  describe("indicators", () => {
    const names = (fetchMock: ReturnType<typeof api>) =>
      fetchMock.mock.calls
        .map(([input]) => new URL(String(input), "http://localhost"))
        .filter((u) => u.pathname === "/api/indicators")
        .map((u) => u.searchParams.get("names"));

    it("open with i, and a preset replaces the selection", async () => {
      const user = userEvent.setup();
      const fetchMock = api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      expect(screen.getByRole("button", { name: "Indicators, 2 on" })).toBeTruthy();
      await user.keyboard("i");
      const menu = screen.getByRole("dialog", { name: "Indicators" });
      expect(document.activeElement).toBe(within(menu).getByLabelText("Search indicators"));
      await user.click(within(menu).getByRole("button", { name: "Momentum: RSI + MACD" }));
      await waitFor(() => expect(names(fetchMock).at(-1)).toBe("macd,rsi14"));
      expect(within(menu).getByRole("switch", { name: "MACD (12, 26, 9)" }).getAttribute("aria-checked")).toBe("true");
      await user.click(within(menu).getByRole("button", { name: "Clean" }));
      expect(screen.getByRole("button", { name: "Indicators, 0 on" })).toBeTruthy();
    });
  });

  describe("watchlist and keys", () => {
    it("groups instruments with the day's price and change, and an EXP chip near expiry", async () => {
      api({
        "/api/instruments": ALL,
        // no daily bars for the quote: the daily scanner row stands in
        "/api/candles": (url: URL) => (url.searchParams.get("limit") === "2" ? jsonResponse(503, { detail: "no data" }) : jsonResponse(200, candles)),
        "/api/scanner": [scannerRow({}), scannerRow({ instrument: nifty.id, name: nifty.name, last_close: 23414.3, change_pct: -0.4 })],
      });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      const list = await screen.findByRole("complementary", { name: "Watchlist" });
      const sections = within(list).getAllByRole("region");
      expect(sections.map((s) => s.getAttribute("aria-label"))).toEqual(["NSE Indices", "NSE Stocks", "MCX"]);
      const niftyRow = await within(list).findByRole("link", { name: /NIFTY50/ });
      await waitFor(() => expect(niftyRow.textContent).toContain("23,414.30"));
      expect(niftyRow.textContent).toContain("-0.40%");
      expect(within(niftyRow).getByText("EXP")).toBeTruthy();
      const active = within(list).getByRole("link", { name: /RELIANCE/ });
      expect(active.getAttribute("aria-current")).toBe("page");
      expect(within(active).getByText("Bullish")).toBeTruthy();
    });

    it("shows the live price and today's change while the market is open, and the last close when it isn't", async () => {
      const daily: Record<string, CandlesResponse> = {
        [nifty.id]: { ...candles, candles: [candle(T0 - DAY, 23300, 23330), candle(T0, 23330, 23414.3)], forming: candle(T0 + DAY, 23414.3, 23233.2) },
        "MCX:CRUDEOIL": { ...candles, candles: [candle(T0 - DAY, 8600, 8700), candle(T0, 8700, 8643)], forming: null },
      };
      const fetchMock = api({
        "/api/instruments": ALL,
        "/api/scanner": [scannerRow({}), scannerRow({ instrument: nifty.id, name: nifty.name, last_close: 23414.3, change_pct: 0.4 })],
        "/api/candles": (url: URL) =>
          url.searchParams.get("limit") === "2" ? jsonResponse(200, daily[url.searchParams.get("instrument")!] ?? candles) : jsonResponse(200, candles),
      });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      const list = await screen.findByRole("complementary", { name: "Watchlist" });
      const niftyRow = await within(list).findByRole("link", { name: /NIFTY50/ });
      await waitFor(() => expect(niftyRow.textContent).toContain("23,233.20"));
      expect(niftyRow.textContent).toContain("-0.77%");
      const crude = within(list).getByRole("link", { name: /CRUDEOIL/ });
      await waitFor(() => expect(crude.textContent).toContain("8,643.00"), { timeout: 4000 });
      expect(crude.textContent).toContain("-0.66%");
      const quoted = fetchMock.mock.calls.map(([u]) => new URL(String(u), "http://localhost")).filter((u) => u.searchParams.get("limit") === "2");
      expect(quoted.every((u) => u.searchParams.get("tf") === "1D")).toBe(true);
    });

    it("focuses search with /, filters, and steps with the arrow keys", async () => {
      const user = userEvent.setup();
      api({ "/api/instruments": ALL });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      await user.keyboard("/");
      const search = screen.getByRole("searchbox", { name: "Search instruments" });
      expect(document.activeElement).toBe(search);
      await user.type(search, "crude");
      expect(screen.queryByRole("link", { name: /RELIANCE/ })).toBeNull();
      await user.keyboard("{Enter}");
      expect(screen.getByTestId("location").textContent).toBe("/chart/MCX:CRUDEOIL/1D");
      await user.clear(search);
      await user.keyboard("{ArrowUp}");
      expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:RELIANCE/1D");
    });

    it("switches timeframe with 1–4 and instrument with [ and ]", async () => {
      const user = userEvent.setup();
      api({ "/api/instruments": ALL });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      await user.keyboard("2");
      expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:RELIANCE/15m");
      await user.keyboard("]");
      expect(screen.getByTestId("location").textContent).toBe("/chart/MCX:CRUDEOIL/1D");
      await user.keyboard("[[");
      await user.keyboard("[[");
      expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:NIFTY50/1D");
    });
  });

  describe("freshness", () => {
    it("turns the as-of label amber when intraday data is more than two bars behind while the market is open", async () => {
      api();
      const { unmount } = renderWithProviders(<AppRoutes />, { route: "/chart/NSE:RELIANCE/5m" });
      await screen.findByTestId("price-chart");
      const label = screen.getByText(/^as of/);
      expect(label.textContent).toMatch(/· [0-9]+ (min|h|days) behind$/);
      expect(label.className).toContain("text-forming");
      unmount();
      api({ "/api/health": { ...health, markets: health.markets.map((m) => ({ ...m, open: false })) } });
      renderWithProviders(<AppRoutes />, { route: "/chart/NSE:RELIANCE/5m" });
      await screen.findByTestId("price-chart");
      await waitFor(() => expect(screen.getByText(/^as of/).textContent).not.toContain("behind"));
    });

    it("never calls a daily chart behind", async () => {
      api();
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      await screen.findByTestId("price-chart");
      expect(screen.getByText(/^as of/).textContent).toBe("as of 23 Sep");
    });
  });

  describe("states", () => {
    it("shows the no-data message on 503", async () => {
      api({ "/api/candles": () => jsonResponse(503, { detail: "no 1D data for NSE:RELIANCE yet: run ingest" }) });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await screen.findByText("No data yet")).toBeTruthy();
      expect(screen.getByText("Connect Fyers — the backfill loads 1D candles.")).toBeTruthy();
      expect(screen.queryByTestId("price-chart")).toBeNull();
    });

    it("shows the empty state when there are no candles", async () => {
      api({ "/api/candles": { ...candles, candles: [], forming: null } });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await screen.findByText("No 1D candles for Reliance Industries yet")).toBeTruthy();
    });

    it("says the backend is not running when the API is unreachable", async () => {
      mockApi({ "/api/instruments": unreachable, "/api/health": unreachable });
      renderWithProviders(<AppRoutes />, { route: ROUTE });
      expect(await screen.findByText("Backend not running")).toBeTruthy();
      expect(screen.getByText(/uvicorn candly\.api\.app:app/)).toBeTruthy();
    });

    it("handles an unknown instrument and an unsupported timeframe", async () => {
      api();
      const { unmount } = renderWithProviders(<AppRoutes />, { route: "/chart/NSE:NOPE/1D" });
      expect(await screen.findByText("Unknown instrument “NSE:NOPE”")).toBeTruthy();
      unmount();
      renderWithProviders(<AppRoutes />, { route: "/chart/MCX:CRUDEOIL/5m" });
      expect(await screen.findByText("Crude Oil has no 5m chart")).toBeTruthy();
    });

    it("redirects / to a full chart URL", async () => {
      api();
      renderWithProviders(<AppRoutes />, { route: "/" });
      expect((await screen.findByTestId("price-chart")).textContent).toContain("1D");
      expect(screen.getByTestId("location").textContent).toBe("/chart/NSE:RELIANCE/1D");
    });
  });
});
