import { describe, expect, it } from "vitest";
import {
  formatBarTimeIST,
  formatBarWhenIST,
  formatDateIST,
  formatDateTimeIST,
  formatDayIST,
  formatIsoDate,
  formatTimeIST,
  formatWhenIST,
  relativeTime,
} from "./time";

const utc = (y: number, mo: number, d: number, h = 0, mi = 0) => Date.UTC(y, mo - 1, d, h, mi) / 1000;

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
});

describe("human times", () => {
  const now = utc(2026, 9, 23, 6, 30); // 12:00 IST

  it("drops the day for today and the year for this year", () => {
    expect(formatWhenIST(utc(2026, 9, 23, 4, 45), now)).toBe("10:15");
    expect(formatWhenIST(utc(2026, 9, 3, 4, 45), now)).toBe("3 Sep, 10:15");
    expect(formatWhenIST(utc(2025, 12, 31, 4, 45), now)).toBe("31 Dec 2025, 10:15");
    expect(formatDayIST(utc(2026, 9, 21, 3, 45), now)).toBe("21 Sep");
  });

  it("shows daily bars as a day and intraday bars as a time", () => {
    expect(formatBarWhenIST(utc(2026, 9, 23, 3, 45), "1D", now)).toBe("23 Sep");
    expect(formatBarWhenIST(utc(2026, 9, 23, 3, 45), "15m", now)).toBe("09:15");
  });

  it("says how long ago, then falls back to the day", () => {
    expect(relativeTime(now - 20, now)).toBe("just now");
    expect(relativeTime(now - 5 * 60, now)).toBe("5m ago");
    expect(relativeTime(now - 2 * 3600, now)).toBe("2h ago");
    expect(relativeTime(now - 3 * 86400, now)).toBe("3d ago");
    expect(relativeTime(now - 30 * 86400, now)).toBe("24 Aug");
  });

  it("formats a plain calendar date without shifting it", () => {
    expect(formatIsoDate("2025-10-01")).toBe("1 Oct 2025");
    expect(formatIsoDate("soon")).toBe("soon");
  });
});
