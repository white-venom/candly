import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { accuracy, calibrationBins } from "../test/fixtures";
import { CalibrationChart } from "./CalibrationChart";

function dots(container: HTMLElement) {
  return [...container.querySelectorAll("circle")].map((c) => ({
    cx: Number(c.getAttribute("cx")),
    cy: Number(c.getAttribute("cy")),
    title: c.querySelector("title")?.textContent,
  }));
}

function tickLabels(container: HTMLElement) {
  return [...container.querySelectorAll("text")].map((t) => t.textContent).filter((s) => s?.endsWith("%"));
}

describe("calibration chart", () => {
  it("plots only the bins that have graded forecasts and fits the axes to them", () => {
    const { container } = render(<CalibrationChart bins={accuracy.calibration} />);
    expect(accuracy.calibration).toHaveLength(10);
    expect(dots(container).map((d) => d.title)).toEqual([
      "Bin 40%–50%: predicted 48.0%, observed 46.0%, n=10",
      "Bin 50%–60%: predicted 53.0%, observed 55.0%, n=14",
    ]);
    expect(new Set(tickLabels(container))).toEqual(new Set(["40%", "50%", "60%"]));
    expect(container.querySelector("polyline")?.getAttribute("points")).not.toMatch(/NaN/);
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("never draws a dot at 0/0 for an empty bin", () => {
    const bins = calibrationBins({ 0: { mean_pred: 0.05, observed: 0.1, n: 3 }, 9: { mean_pred: 0.95, observed: 0.9, n: 4 } });
    const { container } = render(<CalibrationChart bins={bins} />);
    expect(tickLabels(container)).toContain("0%");
    const points = dots(container);
    expect(points).toHaveLength(2);
    // The origin of the plot area: where a null coerced to 0 would land.
    expect(points.some((p) => p.cx === 44 || p.cy === 260)).toBe(false);
    expect(points.every((p) => Number.isFinite(p.cx) && Number.isFinite(p.cy))).toBe(true);
  });

  it("says so when no bin has a graded forecast", () => {
    const { container } = render(<CalibrationChart bins={calibrationBins({})} />);
    expect(screen.getByText("No graded forecasts yet")).toBeTruthy();
    expect(container.querySelector("svg")).toBeNull();
  });
});
