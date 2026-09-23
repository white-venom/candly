import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { health, pausedHealth, signal } from "../test/fixtures";
import {
  BACKEND_COMMAND,
  abstainReason,
  contextTags,
  dataStatus,
  describeDriver,
  expiryNotice,
  friendlyError,
  humanizeText,
  syncNotice,
  syncStepName,
} from "./messages";

/** 2026-09-23 12:00 IST */
const NOW = Date.UTC(2026, 8, 23, 6, 30) / 1000;
const ISO = /\d{4}-\d{2}-\d{2}T|\+00:00|UTC/;

describe("abstain reasons in plain language", () => {
  it("turns stale data into IST times, never ISO strings", () => {
    const raw = "stale data: last closed bar 2026-09-23T04:45:00+00:00, expected 2026-09-23T09:45:00+00:00";
    const r = abstainReason(raw, { tf: "1h", now: NOW });
    expect(r.text).toBe("Waiting for fresh data — last bar 10:15 IST, next expected 15:15 IST.");
    expect(r.hint).toBe("A new call comes once the next bar is in.");
    expect(abstainReason(raw, { tf: "1h", now: NOW, canConnect: true }).hint).toBe("Connect Fyers to resume live data.");
  });

  it("shows dates only for daily bars, and the day for older intraday bars", () => {
    const daily = abstainReason("stale data: last closed bar 2026-09-21T03:45:00+00:00, expected 2026-09-23T03:45:00+00:00", { tf: "1D", now: NOW });
    expect(daily.text).toBe("Waiting for fresh data — last bar 21 Sep, next expected 23 Sep.");
    const older = abstainReason("stale data: last closed bar 2026-09-22T09:45:00+00:00, expected 2026-09-23T04:45:00+00:00", { tf: "1h", now: NOW });
    expect(older.text).toBe("Waiting for fresh data — last bar 22 Sep, 15:15 IST, next expected 10:15 IST.");
    const scanner = abstainReason("stale data: last closed bar 2026-09-22T03:45:00+00:00, other instruments have 2026-09-23T03:45:00+00:00", {
      tf: "1D",
      now: NOW,
    });
    expect(scanner.text).toBe("Waiting for fresh data — last bar 22 Sep; others have 23 Sep.");
  });

  it("maps every known prefix, using the detail only for the hint", () => {
    const text = (raw: string) => abstainReason(raw, { tf: "1D", now: NOW }).text;
    expect(text("unvalidated bucket: trend=down has 12 validation events, fewer than 30")).toBe("This setup hasn't proven itself out-of-sample yet.");
    expect(abstainReason("unvalidated bucket: trend=down, pattern in {hammer} has 12 validation events, fewer than 30", { tf: "1D" }).hint).toBe(
      "Only 12 validation cases so far; it needs 30.",
    );
    expect(text("horizon crosses session close: last target bar 2026-09-23T09:45:00+00:00 ends after this session")).toBe(
      "Too close to the close for an intraday call.",
    );
    const costs = abstainReason("edge below costs: median move 0.12% the call's way vs 0.25% round trip (equity, multi-day)", { tf: "1D" });
    expect(costs).toEqual({ text: "The expected move doesn't cover trading costs.", hint: "Expected 0.12% vs 0.25% round-trip costs." });
    expect(text("too few analogs: 12 < 30")).toBe("Not enough similar past setups.");
    expect(abstainReason("too few analogs: 12 < 30", { tf: "1D" }).hint).toBe("Found 12, needs 30. More history would help.");
    expect(text("not enough history: 40 bars, need ATR, trend and a base rate")).toBe("Not enough price history yet.");
    expect(text("no analogs in this pattern/trend bucket")).toBe("No similar past setups to learn from.");
    expect(text("edge below minimum: |p_up - base_rate| = 0.012 < 0.03")).toBe("The odds are too close to a coin flip.");
    expect(text("below minimum edge")).toBe("The odds are too close to a coin flip.");
    expect(text("|p(up) − base rate| < 0.03")).toBe("The odds are too close to a coin flip.");
  });

  it("never passes an unknown reason through raw", () => {
    expect(abstainReason("model exploded at 2026-09-23T04:45:00+00:00", { tf: "1D" })).toEqual({ text: "No clear edge right now.", hint: null });
    expect(abstainReason(null, { tf: "1D" }).text).toBe("No clear edge right now.");
  });
});

describe("errors in plain language", () => {
  it("says the backend is not running, with the command to start it", () => {
    const f = friendlyError(new ApiError(0, "Backend not reachable"));
    expect(f).toMatchObject({ kind: "unreachable", title: "Backend not running", command: BACKEND_COMMAND });
  });

  it("calls a 503 'No data yet' with a hint", () => {
    expect(friendlyError(new ApiError(503, "no 5m data for NSE:NIFTY50 yet: run ingest"))).toMatchObject({
      kind: "no-data",
      title: "No data yet",
      hint: "Run ingest, or connect Fyers, to load it.",
    });
    expect(friendlyError(new ApiError(503, "x"), "Connect Fyers.").hint).toBe("Connect Fyers.");
  });

  it("keeps readable details, capitalised and with IST times", () => {
    expect(friendlyError(new ApiError(404, "unknown instrument NSE:NOPE")).title).toBe("Unknown instrument NSE:NOPE");
    expect(friendlyError(new ApiError(404, "Not Found")).title).toBe("This backend doesn't serve this yet");
    expect(friendlyError(new ApiError(400, "no bar at 2026-09-23T04:45:00+00:00")).title).not.toMatch(ISO);
    expect(friendlyError(new Error("boom"))).toMatchObject({ kind: "other", title: "Something went wrong", hint: "boom" });
  });

  it("rewrites ISO times and dates as IST", () => {
    expect(humanizeText("at 2026-09-23T04:45:00+00:00 for 2026-09-29", NOW)).toBe("at 10:15 IST for 29 Sep 2026");
  });
});

describe("data connection", () => {
  it("is live, paused (with a Connect action) or offline", () => {
    expect(dataStatus(health, null)).toMatchObject({ state: "live", title: "Live data", detail: "Source: Yahoo (dev data)", canConnect: true });
    expect(dataStatus(pausedHealth, null)).toMatchObject({ state: "paused", title: "Live data paused", detail: "Fyers not connected", canConnect: true });
    const noKeys = { ...health, keys: { ...health.keys, fyers: false }, ingest: { status: "blocked" as const, reason: "Fyers keys are not set — add FYERS_APP_ID and FYERS_SECRET_KEY to .env" } };
    expect(dataStatus(noKeys, null)).toMatchObject({ state: "paused", canConnect: false });
    expect(dataStatus(undefined, new ApiError(0, "x"))).toMatchObject({ state: "offline", title: "Backend not running" });
    expect(dataStatus(undefined, null).state).toBe("checking");
  });

  it("reports the Fyers first sync with friendly step names", () => {
    const running = { ...pausedHealth, sync: { status: "running" as const, step: "backfill-5m", progress: 0.42, message: null, started_at: NOW, finished_at: null } };
    expect(syncNotice(running)).toEqual({ state: "running", text: "Setting up from Fyers — Downloading 5-minute history · 42%", progress: 42 });
    expect(dataStatus(running, null)).toMatchObject({ state: "syncing", detail: "Downloading 5-minute history · 42%" });
    const failed = { ...health, sync: { ...running.sync, status: "error" as const, message: "rate limit at 2026-09-23T04:45:00+00:00" } };
    expect(syncNotice(failed)).toMatchObject({ state: "error", text: expect.stringMatching(/^Fyers setup stopped — rate limit at (23 Sep, )?10:15 IST$/) });
    expect(syncNotice({ ...health, sync: { ...running.sync, status: "done" as const } })).toBeNull();
    expect(syncNotice(health)).toBeNull();
  });

  it("names sync steps, falling back to the id's words", () => {
    expect(syncStepName("backfill-1d")).toBe("Downloading daily history");
    expect(syncStepName("resample_15m_1h")).toBe("Building 15m/1h");
    expect(syncStepName("quality")).toBe("Checking data quality");
    expect(syncStepName("scorecards")).toBe("Building scorecards");
    expect(syncStepName("warm-up-caches")).toBe("Warm up caches");
    expect(syncStepName(null)).toBe("Getting started");
  });

  it("flags an expiry schedule that changed", () => {
    expect(expiryNotice(health)).toBeNull();
    const mismatch = {
      ...health,
      expiry_check: {
        status: "mismatch" as const,
        checked_at: NOW,
        mismatches: [
          { instrument: "NSE:NIFTY50", rules: "2026-09-30", exchange: "2026-09-29" },
          { instrument: "NSE:BANKNIFTY", rules: "2026-09-30", exchange: "2026-09-29" },
        ],
      },
    };
    expect(expiryNotice(mismatch)).toBe("Expiry schedule changed for NIFTY50 — exchange says Tue 29 Sep (+1 more)");
  });
});

describe("drivers and context", () => {
  it("reads each driver as one plain sentence", () => {
    expect(describeDriver({ name: "Trend", effect: "bearish", detail: "down (ADX 31)" })).toEqual({ icon: "trend", text: "Downtrend (ADX 31)" });
    expect(describeDriver({ name: "Volatility regime", effect: "neutral", detail: "low (ATR at the 21th percentile of the last 252 sessions)" }).text).toBe(
      "Low volatility — calmer than 79% of the last 252 sessions",
    );
    expect(describeDriver({ name: "Near level", effect: "neutral", detail: "close within 0.5 ATR of cpr_bottom" }).text).toBe("Price is sitting near the CPR bottom");
    expect(
      describeDriver({
        name: "Analogs",
        effect: "bullish",
        detail: "64 same-instrument analogs (trend=down, pattern in {hammer}; effective n 21): 37 closed higher after 3 bars; posterior 58% vs base rate 52%",
      }).text,
    ).toBe("64 similar past setups — 37 closed higher after 3 bars");
    expect(
      describeDriver({
        name: "Hammer",
        effect: "bullish",
        detail: "Confirmed on the reference bar. Scorecard (train, 3 bars): hit 58% vs base 52%, n=64, q=0.080, not certified",
      }).text,
    ).toBe("Hammer on the last bar — hit 58% vs base 52% (n 64)");
    expect(describeDriver({ name: "Expiry", effect: "neutral", detail: "The reference bar is on the weekly expiry (2026-09-29)." }).text).toBe("Expiry day (weekly)");
  });

  it("tags a signal's context, flagging expiry day", () => {
    const s = signal();
    expect(contextTags(s.context).map((t) => t.text)).toEqual(["Expiry in 4d", "Downtrend", "Normal volatility", "RSI 31", "Volume ×1.4", "Near the previous day's low"]);
    expect(contextTags({ ...s.context, expiry_day: true, days_to_expiry: 0 })[0]).toEqual({ text: "Expiry day", warn: true });
    const { expiry_day: _d, days_to_expiry: _n, ...older } = s.context;
    expect(contextTags(older).some((t) => /Expiry/.test(t.text))).toBe(false);
  });
});
