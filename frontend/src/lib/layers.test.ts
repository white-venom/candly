import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEFAULT_LAYERS, LAYERS_KEY, useLayers } from "./layers";

describe("chart layers", () => {
  it("show the forecast by default", () => {
    expect(renderHook(() => useLayers()).result.current[0]).toEqual(DEFAULT_LAYERS);
  });

  it("turn a forecast switched off under the old setting back on once, keeping the other choices", () => {
    window.localStorage.setItem("candly.layers", JSON.stringify({ patterns: false, levels: true, forecast: false, allLevels: true }));
    const { result, unmount } = renderHook(() => useLayers());
    expect(result.current[0]).toEqual({ patterns: false, levels: true, forecast: true, allLevels: true });

    act(() => result.current[1]({ forecast: false }));
    expect(JSON.parse(window.localStorage.getItem(LAYERS_KEY)!)).toMatchObject({ forecast: false });
    unmount();
    expect(renderHook(() => useLayers()).result.current[0].forecast).toBe(false);
  });
});
