import { describe, expect, it } from "vitest";
import { formatBarTimeIST, formatDateIST, formatDateTimeIST, formatTimeIST } from "./time";

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
