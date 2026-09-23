import { describe, expect, it } from "vitest";
import { signal } from "../test/fixtures";
import { DEFAULT_SIGNAL_FILTERS, applySignalFilters, emptySignalsText, readSignalFilters } from "./signalFilters";

const bullish = signal();
const neutral = signal({ pattern: "doji", label: "Doji", direction: "neutral", invalidation: null });
const certified = signal({ pattern: "engulfing", label: "Bullish engulfing", stats: { ...signal().stats!, certified: true } });

describe("signal filters", () => {
  it("default to directional signals only, certified or not", () => {
    expect(DEFAULT_SIGNAL_FILTERS).toEqual({ showNeutral: false, certifiedOnly: false });
    expect(readSignalFilters()).toEqual(DEFAULT_SIGNAL_FILTERS);
    expect(applySignalFilters([bullish, neutral, certified], DEFAULT_SIGNAL_FILTERS)).toEqual([bullish, certified]);
  });

  it("show neutral patterns and keep only certified ones when asked", () => {
    expect(applySignalFilters([bullish, neutral, certified], { showNeutral: true, certifiedOnly: false })).toHaveLength(3);
    expect(applySignalFilters([bullish, neutral, certified], { showNeutral: true, certifiedOnly: true })).toEqual([certified]);
    expect(applySignalFilters([signal({ stats: null })], { showNeutral: false, certifiedOnly: true })).toEqual([]);
  });

  it("read back what was saved, falling back per field on bad values", () => {
    window.localStorage.setItem("candly.signalFilters", JSON.stringify({ showNeutral: true, certifiedOnly: true }));
    expect(readSignalFilters()).toEqual({ showNeutral: true, certifiedOnly: true });
    window.localStorage.setItem("candly.signalFilters", JSON.stringify({ showNeutral: "yes", certifiedOnly: true }));
    expect(readSignalFilters()).toEqual({ showNeutral: false, certifiedOnly: true });
    window.localStorage.setItem("candly.signalFilters", "nope");
    expect(readSignalFilters()).toEqual(DEFAULT_SIGNAL_FILTERS);
  });

  it("explain an empty list in terms of the active filter", () => {
    expect(emptySignalsText(DEFAULT_SIGNAL_FILTERS)).toMatch(/^No directional signals/);
    expect(emptySignalsText({ showNeutral: true, certifiedOnly: false })).toBe("No pattern signals on this chart yet.");
    expect(emptySignalsText({ showNeutral: false, certifiedOnly: true })).toBe("No certified signals on this chart.");
  });
});
