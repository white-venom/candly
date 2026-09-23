import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PatternSignal } from "../api/types";
import { signal } from "../test/fixtures";
import { SignalList } from "./SignalList";

const tags = () => within(screen.getByRole("list", { name: "Context" }));

describe("signal context tags", () => {
  it("shows the trading days to expiry, subtly", () => {
    render(<SignalList signals={[signal()]} tf="1D" />);
    const tag = tags().getByText("expiry in 4d");
    expect(tag.className).toContain("text-ink-muted");
  });

  it("highlights an expiry-day signal", () => {
    const s = signal();
    render(<SignalList signals={[{ ...s, context: { ...s.context, expiry_day: true, days_to_expiry: 0 } }]} tf="1D" />);
    const tag = tags().getByText("expiry day");
    expect(tag.className).toContain("text-forming");
    expect(tags().queryByText(/expiry in/)).toBeNull();
  });

  it("shows no expiry tag without expiry info (no expiry, or an older backend)", () => {
    const s = signal();
    const { expiry_day: _day, days_to_expiry: _days, ...older } = s.context;
    const none: PatternSignal = { ...s, context: { ...s.context, expiry_day: null, days_to_expiry: null } };
    const { unmount } = render(<SignalList signals={[none]} tf="1D" />);
    expect(tags().queryByText(/expiry/)).toBeNull();
    unmount();
    render(<SignalList signals={[{ ...s, context: older }]} tf="1D" />);
    expect(tags().queryByText(/expiry/)).toBeNull();
    expect(tags().getByText("trend down")).toBeTruthy();
  });
});
