import { describe, expect, it } from "vitest";
import { expectedBoxGeometry } from "./expectedBox";

describe("expected box geometry", () => {
  it("spans p90 to p10 around the bar's centre, a little wider than a candle, with the median tick across it", () => {
    const g = expectedBoxGeometry(200, 10, { high: 100, mid: 130, low: 160 })!;
    expect(g).toMatchObject({ left: 194.5, top: 100, width: 11, height: 60 });
    expect(g.tick).toEqual({ x1: 191.5, x2: 208.5, y: 130 });
    expect(g.label).toEqual({ x: 200, y: 97, baseline: "bottom" });
  });

  it("keeps a usable width on dense charts and a sane one when zoomed in", () => {
    expect(expectedBoxGeometry(50, 4, { high: 10, mid: 20, low: 30 })!.width).toBe(8);
    expect(expectedBoxGeometry(50, 100, { high: 10, mid: 20, low: 30 })!.width).toBe(48);
  });

  it("puts the label above the live candle's wick when it pokes out of the box", () => {
    expect(expectedBoxGeometry(200, 10, { high: 100, mid: 130, low: 160 }, 80)!.label.y).toBe(77);
  });

  it("moves the label under the box when there is no room above", () => {
    const g = expectedBoxGeometry(200, 10, { high: 5, mid: 20, low: 40 })!;
    expect(g.label).toEqual({ x: 200, y: 43, baseline: "top" });
  });

  it("gives a tiny range a visible minimum height, centred on it", () => {
    const g = expectedBoxGeometry(200, 10, { high: 100, mid: 101, low: 102 })!;
    expect(g).toMatchObject({ top: 99, height: 4 });
  });

  it("draws nothing when the slot or the range is off the chart", () => {
    expect(expectedBoxGeometry(null, 10, { high: 1, mid: 2, low: 3 })).toBeNull();
    expect(expectedBoxGeometry(200, 10, { high: null, mid: 2, low: 3 })).toBeNull();
  });

  it("falls back to the box's middle when the median is off the scale", () => {
    expect(expectedBoxGeometry(200, 10, { high: 100, mid: null, low: 160 })!.tick.y).toBe(130);
  });
});
