import { ColorType, LineStyle } from "lightweight-charts";
import { describe, expect, it } from "vitest";
import { TOKENS } from "../lib/palette";
import { chartTheme } from "./chartTheme";

describe("chartTheme", () => {
  it("maps dark to the dark tokens", () => {
    const th = chartTheme("dark");
    expect(th.chart.layout?.background).toEqual({ type: ColorType.Solid, color: "#1a1a19" });
    expect(th.chart.layout?.textColor).toBe("#c3c2b7");
    expect(th.chart.grid?.horzLines?.color).toBe("#2a2a28");
    expect(th.chart.grid?.vertLines?.visible).toBe(false);
    expect(th.candles).toMatchObject({ upColor: "#26a69a", downColor: "#f06560", wickUpColor: "#26a69a", wickDownColor: "#f06560" });
    expect(th.forming).toEqual({ color: "rgba(240, 180, 41, 0.12)", borderColor: "#f0b429", wickColor: "#f0b429" });
  });

  it("maps light to the light tokens", () => {
    const th = chartTheme("light");
    expect(th.chart.layout?.background).toEqual({ type: ColorType.Solid, color: "#fcfcfb" });
    expect(th.chart.layout?.textColor).toBe("#52514e");
    expect(th.chart.grid?.horzLines?.color).toBe("#e6e5df");
    expect(th.candles).toMatchObject({ upColor: "#08756a", downColor: "#c62f2f" });
    expect(th.forming.borderColor).toBe("#8a6200");
  });

  it("changes every chart colour between themes", () => {
    const [dark, light] = [chartTheme("dark"), chartTheme("light")];
    const pick = (th: typeof dark) => [
      th.chart.layout?.background,
      th.chart.layout?.textColor,
      th.chart.grid?.horzLines?.color,
      th.chart.rightPriceScale?.borderColor,
      th.candles.upColor,
      th.candles.downColor,
      th.forming.borderColor,
      th.volume.up,
      th.volume.down,
      th.ghost.upColor,
      th.ghostAbstain.upColor,
      th.band.p50.color,
      th.bandFill,
      th.stop.color,
      th.level.color,
      th.level.tagBackground,
      th.lastPrice.up.tagBackground,
      th.markers.bullish,
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

  it("keeps the forecast subtle: thin band lines, a dashed median, a faint fill", () => {
    const th = chartTheme("light");
    expect(th.band.p50).toMatchObject({ color: "#0e6f8c", lineStyle: LineStyle.Dashed, lineWidth: 1 });
    expect(th.band.p10).toMatchObject({ lineStyle: LineStyle.Solid, lineWidth: 1 });
    expect(th.bandFill).toBe("rgba(14, 111, 140, 0.1)");
  });

  it("dashes the stop in the danger colour and draws levels thin, dotted and neutral", () => {
    const th = chartTheme("dark");
    expect(th.stop).toMatchObject({ color: TOKENS.dark.danger, width: 1 });
    expect(th.stop.dash.length).toBe(2);
    expect(th.level).toMatchObject({ color: TOKENS.dark.lineStrong, dash: [1, 3], width: 1 });
    expect(th.levelStale.color).not.toBe(th.level.color);
    expect(th.markers).toEqual({ bullish: "#26a69a", bearish: "#f06560", neutral: TOKENS.dark.neutral });
  });

  it("is pure: same input, equal output, no DOM access", () => {
    expect(chartTheme("dark")).toEqual(chartTheme("dark"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});
