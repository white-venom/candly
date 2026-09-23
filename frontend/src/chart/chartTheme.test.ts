import { ColorType, LineStyle } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import { TOKENS } from "../lib/palette";
import { chartTheme } from "./chartTheme";

describe("chartTheme", () => {
  it("maps dark to the dark tokens", () => {
    const th = chartTheme("dark");
    expect(th.chart.layout?.background).toEqual({ type: ColorType.Solid, color: "#1a1a19" });
    expect(th.chart.layout?.textColor).toBe("#c3c2b7");
    expect(th.chart.grid?.vertLines?.color).toBe("#2a2a28");
    expect(th.chart.grid?.horzLines?.color).toBe("#2a2a28");
    expect(th.candles).toMatchObject({ upColor: "#26a69a", downColor: "#f06560", wickUpColor: "#26a69a", wickDownColor: "#f06560" });
    expect(th.forming).toEqual({ color: "rgba(240, 180, 41, 0.12)", borderColor: "#f0b429", wickColor: "#f0b429" });
  });

  it("maps light to the light tokens", () => {
    const th = chartTheme("light");
    expect(th.chart.layout?.background).toEqual({ type: ColorType.Solid, color: "#fcfcfb" });
    expect(th.chart.layout?.textColor).toBe("#52514e");
    expect(th.chart.grid?.vertLines?.color).toBe("#e6e5df");
    expect(th.candles).toMatchObject({ upColor: "#08756a", downColor: "#c62f2f" });
    expect(th.forming.borderColor).toBe("#8a6200");
  });

  it("changes every chart colour between themes", () => {
    const [dark, light] = [chartTheme("dark"), chartTheme("light")];
    const pick = (th: typeof dark) => [
      th.chart.layout?.background,
      th.chart.layout?.textColor,
      th.chart.grid?.vertLines?.color,
      th.chart.rightPriceScale?.borderColor,
      th.candles.upColor,
      th.candles.downColor,
      th.forming.borderColor,
      th.volume.up,
      th.volume.down,
      th.ghost.upColor,
      th.ghostAbstain.upColor,
      th.band.p50.color,
      th.invalidation.color,
      th.levels.pdh.color,
      th.levels.pdl.color,
      th.levels.pivot.color,
      th.markers.forming.bullish,
    ];
    const [d, l] = [pick(dark), pick(light)];
    d.forEach((value, i) => expect(value, `entry ${i}`).not.toEqual(l[i]));
    // Indicator hues are stepped per mode (one slot happens to be shared).
    expect(dark.indicators).toEqual(TOKENS.dark.indicators);
    expect(light.indicators).toEqual(TOKENS.light.indicators);
    expect(dark.indicators).not.toEqual(light.indicators);
  });

  it("draws ghosts translucent with an opaque outline, grey when abstaining", () => {
    const { ghost, ghostAbstain } = chartTheme("dark");
    expect(ghost).toMatchObject({ upColor: "rgba(38, 166, 154, 0.28)", borderUpColor: "#26a69a", borderDownColor: "#f06560" });
    expect(ghostAbstain.upColor).toBe(ghostAbstain.downColor);
    expect(ghostAbstain.borderUpColor).toBe(TOKENS.dark.abstain);
  });

  it("dashes the median and the invalidation line", () => {
    const th = chartTheme("light");
    expect(th.band.p50).toMatchObject({ color: "#0e6f8c", lineStyle: LineStyle.Dashed });
    expect(th.band.p10.lineStyle).toBe(LineStyle.Solid);
    expect(th.band.p90.lineStyle).toBe(LineStyle.Solid);
    expect(th.invalidation).toEqual({ color: "#b91c1c", lineStyle: LineStyle.Dashed, lineWidth: 2 });
  });

  it("colours levels by role and dims forming markers", () => {
    const th = chartTheme("dark");
    expect(th.levels.pdh.color).toBe(TOKENS.dark.down);
    expect(th.levels.s1.color).toBe(TOKENS.dark.up);
    expect(th.levels.vwap.color).toBe(TOKENS.dark.neutral);
    expect(th.markers.confirmed.bullish).toBe("#26a69a");
    expect(th.markers.forming.bullish).toBe("rgba(38, 166, 154, 0.5)");
  });

  it("is pure: same input, equal output, no DOM access", () => {
    expect(chartTheme("dark")).toEqual(chartTheme("dark"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});
