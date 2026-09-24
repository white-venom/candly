import { describe, expect, it } from "vitest";
import { forecast } from "../test/fixtures";
import { candleWindow, coverageNote, currentStep, expectedRanges, withLastSteps } from "./expected";

const ist = (d: number, h: number, m: number) => Date.UTC(2026, 8, d, h, m - 330) / 1000;
/** 5m steps at 10:35, 10:40 and 10:45 IST on Thu 24 Sep 2026 */
const steps = [ist(24, 10, 35), ist(24, 10, 40), ist(24, 10, 45)];
const NOW = ist(24, 10, 36);

describe("current step", () => {
  it("is the bar forming now, or the first after the last closed bar", () => {
    expect(currentStep(steps, { forming: steps[0], lastClosed: ist(24, 10, 30) })).toBe(0);
    expect(currentStep(steps, { forming: null, lastClosed: ist(24, 10, 30) })).toBe(0);
    expect(currentStep(steps, { forming: null, lastClosed: null })).toBe(0);
  });

  it("moves on when the forecast is a bar behind the chart, and is -1 once every step has closed", () => {
    expect(currentStep(steps, { forming: steps[1], lastClosed: ist(24, 10, 30) })).toBe(1);
    expect(currentStep(steps, { forming: null, lastClosed: steps[0] })).toBe(1);
    expect(currentStep(steps, { forming: ist(24, 10, 50), lastClosed: steps[2] })).toBe(-1);
  });
});

describe("candle window, in IST", () => {
  it("names a 5m bar by its start and end", () => {
    expect(candleWindow(steps, 0, "5m", steps[0], NOW)).toEqual({ title: "Forming now", when: "10:35–10:40 IST", forming: true });
    expect(candleWindow(steps, 1, "5m", null, NOW)).toEqual({ title: "Next candle", when: "10:40–10:45 IST", forming: false });
  });

  it("adds the day when the bar isn't today, and ends a session's last bar at the close", () => {
    const hourly = [ist(24, 15, 15), ist(25, 9, 15), ist(25, 10, 15)];
    expect(candleWindow(hourly, 0, "1h", null, ist(24, 15, 5))?.when).toBe("15:15 IST to the close");
    expect(candleWindow(hourly, 1, "1h", null, ist(24, 15, 40))?.when).toBe("Fri 25 Sep, 09:15–10:15 IST");
  });

  it("names a daily bar by its session day", () => {
    const daily = [ist(25, 9, 15), ist(28, 9, 15)];
    expect(candleWindow(daily, 0, "1D", null, ist(24, 16, 0))).toEqual({ title: "Next session", when: "Fri 25 Sep", forming: false });
    expect(candleWindow(daily, 0, "1D", daily[0], ist(25, 11, 0))?.title).toBe("Today's session");
    expect(candleWindow(daily, 2, "1D", null, NOW)).toBeNull();
  });
});

describe("forecast steps", () => {
  it("reads each step's 80% close range in time order", () => {
    expect(expectedRanges({ bands: [...forecast.bands].reverse() })[0]).toEqual({ time: forecast.bands[0].time, low: 101, mid: 104, high: 106 });
  });

  it("carries the last steps over a refetch that comes back without any, keeping the newest call", () => {
    const empty = { ...forecast, made_at: forecast.made_at + 60, ref_time: forecast.ref_time + 1, abstain: true, abstain_reason: "stale data", bands: [], ghost_candles: [] };
    const kept = withLastSteps(empty, forecast);
    expect(kept.bands).toBe(forecast.bands);
    expect(kept.ghost_candles).toBe(forecast.ghost_candles);
    expect(kept).toMatchObject({ ref_time: forecast.ref_time, abstain: true, abstain_reason: "stale data", made_at: forecast.made_at + 60 });
  });

  it("never carries steps across charts, and prefers fresh steps", () => {
    const empty = { ...forecast, bands: [], ghost_candles: [] };
    expect(withLastSteps(empty, { ...forecast, tf: "5m" })).toBe(empty);
    expect(withLastSteps(empty, undefined)).toBe(empty);
    expect(withLastSteps(forecast, { ...forecast, bands: [] })).toBe(forecast);
  });

  it("says where the range's track record was measured, and when it hasn't been", () => {
    expect(coverageNote(forecast)).toBe("80% range — right ~78% of the time in testing");
    expect(coverageNote({ ...forecast, tf: "5m" })).toBe("80% range — right ~78% of the time in testing (NSE daily bars)");
    expect(coverageNote({ ...forecast, method: "range_v1" })).toBe("80% range — still being tested");
  });
});
