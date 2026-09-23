import { describe, expect, it } from "vitest";
import { jsonResponse, mockApi } from "../test/utils";
import { ApiError, apiGet, buildUrl } from "./client";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected the request to fail");
}

describe("api client", () => {
  it("builds /api URLs and drops empty params", () => {
    expect(buildUrl("/candles", { instrument: "NSE:RELIANCE", tf: "1D", end: null, x: "" })).toBe("/api/candles?instrument=NSE%3ARELIANCE&tf=1D");
  });

  it("returns parsed JSON", async () => {
    mockApi({ "/api/health": { status: "ok" } });
    expect(await apiGet("/health")).toEqual({ status: "ok" });
  });

  it("throws a typed error with status and detail", async () => {
    mockApi({ "/api/candles": () => jsonResponse(503, { detail: "no candles for NSE:RELIANCE 1D" }) });
    const err = await failure(apiGet("/candles"));
    expect(err.status).toBe(503);
    expect(err.detail).toBe("no candles for NSE:RELIANCE 1D");
    expect(err.noData).toBe(true);
  });

  it("joins FastAPI validation messages", async () => {
    mockApi({ "/api/candles": () => jsonResponse(422, { detail: [{ msg: "field required" }, { msg: "bad tf" }] }) });
    expect((await failure(apiGet("/candles"))).detail).toBe("field required; bad tf");
  });

  it("treats a network failure or an empty proxy 502 as unreachable", async () => {
    mockApi({ "/api/health": () => Promise.reject(new TypeError("Failed to fetch")) });
    expect((await failure(apiGet("/health"))).unreachable).toBe(true);
    mockApi({ "/api/health": () => jsonResponse(502) });
    expect((await failure(apiGet("/health"))).unreachable).toBe(true);
  });
});
