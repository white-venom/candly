import { describe, expect, it } from "vitest";
import { placeTags } from "./tagLayout";

const H = 17;
const free = (y: number) => ({ y, fixed: false });
const fixed = (y: number) => ({ y, fixed: true });

function overlaps(ys: (number | null)[]): boolean {
  const placed = ys.filter((y): y is number => y !== null).sort((a, b) => a - b);
  return placed.some((y, i) => i > 0 && y - placed[i - 1] < H);
}

describe("axis tag placement", () => {
  it("leaves tags that don't overlap where they are", () => {
    expect(placeTags([free(10), free(50)], H, 500)).toEqual([10, 50]);
  });

  it("spreads overlapping tags, keeping price order", () => {
    const ys = placeTags([free(105), free(100), free(110)], H, 500);
    expect(overlaps(ys)).toBe(false);
    expect(ys[1]! < ys[0]! && ys[0]! < ys[2]!).toBe(true);
  });

  it("never moves the last price or the stop; levels make room around them", () => {
    const ys = placeTags([fixed(100), fixed(200), free(95), free(104)], H, 500);
    expect(ys.slice(0, 2)).toEqual([100, 200]);
    expect(overlaps(ys)).toBe(false);
    expect(ys[2]!).toBeLessThan(100);
    expect(ys[3]!).toBeGreaterThan(100);
  });

  it("drops the least important tags when a gap between fixed tags is too small", () => {
    // Room for two tags between 100 and 160; four want in. The first two in the input win.
    const ys = placeTags([fixed(100), fixed(160), free(140), free(120), free(130), free(150)], H, 500);
    expect(ys[2]).not.toBeNull();
    expect(ys[3]).not.toBeNull();
    expect(ys[4]).toBeNull();
    expect(ys[5]).toBeNull();
    expect(overlaps(ys)).toBe(false);
    expect(ys[3]! < ys[2]!).toBe(true);
  });

  it("keeps tags inside the axis", () => {
    const ys = placeTags([free(490), free(495)], H, 500);
    expect(Math.max(...(ys as number[]))).toBeLessThanOrEqual(500 - H / 2);
    expect(overlaps(ys)).toBe(false);
  });
});
