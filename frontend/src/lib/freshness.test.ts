import { describe, expect, it } from "vitest";
import { fmtLag, freshness } from "./freshness";

const ist = (d: number, h: number, m: number) => Date.UTC(2026, 8, d, h, m - 330) / 1000;

describe("data freshness", () => {
  it("counts from the last closed bar's close", () => {
    expect(freshness(ist(24, 10, 40), ist(24, 10, 45), "5m", true, ist(24, 10, 46))).toEqual({ lag: 60, behind: false });
    expect(freshness(ist(24, 10, 30), null, "5m", true, ist(24, 10, 44))).toEqual({ lag: 540, behind: false });
    expect(freshness(ist(24, 10, 30), null, "5m", true, ist(24, 10, 46))).toEqual({ lag: 660, behind: true });
  });

  it("flags closed bars that lag even while a live bar is forming", () => {
    // 11:35 and 11:40 never arrived; the 11:45 bar is forming from the live feed
    expect(freshness(ist(24, 11, 30), ist(24, 11, 45), "5m", true, ist(24, 11, 46))).toEqual({ lag: 660, behind: true });
  });

  it("waits for the session's first bar to close before counting", () => {
    expect(freshness(ist(23, 15, 25), ist(24, 9, 15), "5m", true, ist(24, 9, 17))).toEqual({ lag: 120, behind: false });
    expect(freshness(ist(23, 15, 25), null, "5m", true, ist(24, 9, 40))?.behind).toBe(true);
  });

  it("never flags a closed market or a daily chart", () => {
    expect(freshness(ist(24, 10, 30), null, "5m", false, ist(24, 18, 0))?.behind).toBe(false);
    expect(freshness(ist(21, 9, 15), null, "1D", true, ist(24, 11, 0))?.behind).toBe(false);
    expect(freshness(null, null, "5m", true, ist(24, 11, 0))).toBeNull();
  });

  it("reads the lag in plain units", () => {
    expect(fmtLag(660)).toBe("11 min");
    expect(fmtLag(3 * 3600)).toBe("3 h");
    expect(fmtLag(3 * 86400)).toBe("3 days");
  });
});
