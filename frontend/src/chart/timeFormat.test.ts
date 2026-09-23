import { TickMarkType, type Time, type UTCTimestamp } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import { chartTimeFormatter, istTickMarkFormatter, timeToUnix } from "./timeFormat";

const utc = (y: number, mo: number, d: number, h = 0, mi = 0) => Date.UTC(y, mo - 1, d, h, mi) / 1000;
const t = (unix: number) => unix as UTCTimestamp;

describe("chart time formatters", () => {
  it("drives the chart crosshair label without shifting the timestamp", () => {
    const open = utc(2026, 9, 23, 9, 45);
    expect(chartTimeFormatter("15m")(t(open))).toBe("23 Sep 2026, 15:15");
    expect(timeToUnix(t(open))).toBe(open);
  });

  it("formats tick marks in IST", () => {
    const open = utc(2026, 9, 23, 3, 45);
    expect(istTickMarkFormatter(t(open), TickMarkType.Time, "en-IN")).toBe("09:15");
    expect(istTickMarkFormatter(t(open), TickMarkType.DayOfMonth, "en-IN")).toBe("23 Sep");
    expect(istTickMarkFormatter(t(open), TickMarkType.Month, "en-IN")).toBe("Sep");
    expect(istTickMarkFormatter(t(open), TickMarkType.Year, "en-IN")).toBe("2026");
  });

  it("accepts business-day and string times too", () => {
    expect(timeToUnix({ year: 2026, month: 9, day: 23 } as Time)).toBe(utc(2026, 9, 23));
    expect(timeToUnix("2026-09-23")).toBe(utc(2026, 9, 23));
  });
});
