import { TickMarkType, type Time, type UTCTimestamp } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import {
  chartTimeFormatter,
  formatBarTimeIST,
  formatDateIST,
  formatDateTimeIST,
  formatTimeIST,
  istTickMarkFormatter,
  timeToUnix,
} from "./time";

const utc = (y: number, mo: number, d: number, h = 0, mi = 0) => Date.UTC(y, mo - 1, d, h, mi) / 1000;
const t = (unix: number) => unix as UTCTimestamp;

describe("IST formatters", () => {
  it("shows the NSE open (03:45 UTC) as 09:15 IST", () => {
    const open = utc(2026, 9, 23, 3, 45);
    expect(formatTimeIST(open)).toBe("09:15");
    expect(formatDateIST(open)).toBe("23 Sep 2026");
    expect(formatDateTimeIST(open)).toBe("23 Sep 2026, 09:15");
  });

  it("rolls the date over at IST midnight, not UTC midnight", () => {
    expect(formatDateTimeIST(utc(2026, 9, 23, 18, 30))).toBe("24 Sep 2026, 00:00");
    expect(formatDateTimeIST(utc(2026, 9, 23, 18, 29))).toBe("23 Sep 2026, 23:59");
  });

  it("shows only the date for daily bars", () => {
    const open = utc(2026, 9, 23, 3, 45);
    expect(formatBarTimeIST(open, "1D")).toBe("23 Sep 2026");
    expect(formatBarTimeIST(open, "5m")).toBe("23 Sep 2026, 09:15");
  });

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
