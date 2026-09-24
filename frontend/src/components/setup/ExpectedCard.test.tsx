import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Forecast } from "../../api/types";
import { abstainingForecast, candle, forecast } from "../../test/fixtures";
import { ExpectedCard } from "./ExpectedCard";

const ist = (d: number, h: number, m: number) => Date.UTC(2026, 8, d, h, m - 330) / 1000;
const card = () => screen.getByRole("region", { name: "Expected next candle" });
const text = () => card().textContent ?? "";

/** NIFTY 5m during the session: last closed bar 10:30 IST, steps at 10:35, 10:40 and 10:45. */
const t = [ist(24, 10, 35), ist(24, 10, 40), ist(24, 10, 45)];
const nifty5m: Forecast = {
  ...abstainingForecast,
  instrument: "NSE:NIFTY50",
  tf: "5m",
  ref_time: ist(24, 10, 30),
  ref_close: 23228.15,
  p_up: 0.4987,
  base_rate: 0.5132,
  abstain_reason: "edge below minimum: |p_up - base_rate| = 0.015 < 0.03",
  ghost_candles: t.map((time) => candle(time, 23228, 23229, 0)),
  bands: [
    { time: t[0], p10: 23208.4, p50: 23228.15, p90: 23246.9 },
    { time: t[1], p10: 23199.1, p50: 23228.15, p90: 23255.2 },
    { time: t[2], p10: 23192.1, p50: 23228.15, p90: 23261.6 },
  ],
};
const live = { forming: t[0], lastClosed: ist(24, 10, 30) };
const NOW = ist(24, 10, 37);

describe("expected next candle card", () => {
  it("while abstaining: the forming bar's window in IST, its likely range, and up/down plainly unclear", () => {
    render(<ExpectedCard forecast={nifty5m} tf="5m" bars={live} canConnect={false} now={NOW} />);
    expect(text()).toContain("Forming now · 10:35–10:40 IST");
    expect(text()).toContain("Likely range 23,208 – 23,247");
    expect(text()).toContain("Middle 23,228");
    expect(text()).toContain("80% range — right ~78% of the time in testing (NSE daily bars)");
    expect(within(card()).getByText("Up/down unclear")).toBeTruthy();
    expect(within(card()).getByText("The odds are too close to a coin flip.")).toBeTruthy();
    expect(text()).not.toMatch(/[▲▼]|Leaning|49\.9%|50%/);
    expect(card().querySelector(".text-up, .text-down")).toBeNull();
  });

  it("shows the next three ranges with their times", () => {
    render(<ExpectedCard forecast={nifty5m} tf="5m" bars={live} canConnect={false} now={NOW} />);
    const items = within(within(card()).getByRole("list", { name: "Next 3 expected ranges" })).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["10:3523,208–23,247", "10:4023,199–23,255", "10:4523,192–23,262"]);
  });

  it("says “range only” for the range model", () => {
    const rangeOnly = { ...nifty5m, method: "range_v1", p_up: null, abstain_reason: "direction unclear: range forecast only" };
    render(<ExpectedCard forecast={rangeOnly} tf="5m" bars={live} canConnect={false} now={NOW} />);
    expect(within(card()).getByText("Up/down unclear")).toBeTruthy();
    expect(text()).toContain("Range only — the model doesn't predict up/down here.");
    expect(text()).toContain("80% range — still being tested");
  });

  it("follows the chart when the forecast is a bar behind, and says so once every bar has closed", () => {
    const { rerender } = render(<ExpectedCard forecast={nifty5m} tf="5m" bars={{ forming: t[1], lastClosed: t[0] }} canConnect={false} now={NOW} />);
    expect(text()).toContain("Forming now · 10:40–10:45 IST");
    expect(text()).toContain("Likely range 23,199 – 23,255");
    expect(within(card()).getByRole("list", { name: "Next 2 expected ranges" })).toBeTruthy();
    rerender(<ExpectedCard forecast={nifty5m} tf="5m" bars={{ forming: ist(24, 10, 50), lastClosed: t[2] }} canConnect={false} now={NOW} />);
    expect(text()).toContain("The bars in this forecast have closed.");
    expect(text()).not.toContain("Likely range");
  });

  it("says why there is no expected candle when the forecast comes without one", () => {
    const stale = {
      ...nifty5m,
      bands: [],
      ghost_candles: [],
      abstain_reason: "stale data: last closed bar 2026-09-24T06:00:00+00:00, expected 2026-09-24T06:10:00+00:00",
    };
    render(<ExpectedCard forecast={stale} tf="5m" bars={live} canConnect={false} now={ist(24, 11, 46)} />);
    expect(text()).toBe("Expected next candleNo expected range right now. Waiting for fresh data — last bar 11:30 IST, next expected 11:40 IST.");
  });

  it("for a directional daily call: the next session by day, and which way it leans against the usual", () => {
    // fixture: ref bar Wed 23 Sep 2026, steps Thu 24, Fri 25 and Sat 26 (the fixture ignores weekends)
    render(<ExpectedCard forecast={forecast} tf="1D" bars={{ forming: null, lastClosed: forecast.ref_time }} canConnect={false} now={ist(23, 18, 0)} />);
    expect(text()).toContain("Next session · Thu 24 Sep");
    expect(text()).toContain("Likely range 101.00 – 106.00");
    expect(text()).toContain("80% range — right ~78% of the time in testing");
    expect(text()).not.toContain("NSE daily bars");
    const lean = within(card()).getByText(/Leaning up/);
    expect(lean.textContent).toBe("▲ Leaning up over 3 sessions (56% vs usual 52%)");
    expect(lean.className).toContain("text-up");
    expect(text()).not.toContain("Up/down unclear");
    const items = within(card()).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toBe("Thu 24 Sep101.00–106.00");
  });

  it("leans down when p(up) sits below the usual", () => {
    render(
      <ExpectedCard forecast={{ ...forecast, p_up: 0.44 }} tf="1D" bars={{ forming: null, lastClosed: forecast.ref_time }} canConnect={false} now={ist(23, 18, 0)} />,
    );
    const lean = within(card()).getByText(/Leaning down/);
    expect(lean.textContent).toBe("▼ Leaning down over 3 sessions (44% up vs usual 52%)");
    expect(lean.className).toContain("text-down");
  });
});
