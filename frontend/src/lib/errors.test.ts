import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { describeError } from "./errors";

describe("describeError", () => {
  it("maps each API failure to the message the views show", () => {
    expect(describeError(new ApiError(0, "Backend not reachable")).title).toBe("Backend not running — start it with the command in CLAUDE.md");
    expect(describeError(new ApiError(503, "no candles")).title).toBe("No data yet — run ingest (or connect Fyers)");
    expect(describeError(new ApiError(404, "unknown instrument NSE:NOPE")).title).toBe("unknown instrument NSE:NOPE");
    expect(describeError(new ApiError(404, "Not Found")).title).toBe("This backend doesn't serve this endpoint yet");
    expect(describeError(new Error("boom"))).toMatchObject({ kind: "other", detail: "boom" });
  });
});
