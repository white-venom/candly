import { ApiError } from "../api/client";

export const BACKEND_DOWN = "Backend not running — start it with the command in CLAUDE.md";
export const BACKEND_COMMAND = "backend\\.venv\\Scripts\\python -m uvicorn candly.api.app:app --reload --port 8000";
export const NO_DATA = "No data yet — run ingest (or connect Fyers)";
export const EMPTY = "No data yet — run ingest";

export type ErrorInfo = { kind: "unreachable" | "no-data" | "other"; title: string; detail: string | null };

export function describeError(error: unknown): ErrorInfo {
  if (error instanceof ApiError) {
    if (error.unreachable) return { kind: "unreachable", title: BACKEND_DOWN, detail: null };
    if (error.noData) return { kind: "no-data", title: NO_DATA, detail: error.detail };
    // FastAPI's own 404 for a route that isn't mounted (the contract's 404 carries an "unknown instrument" detail).
    if (error.status === 404 && error.detail === "Not Found") {
      return { kind: "other", title: "This backend doesn't serve this endpoint yet", detail: "HTTP 404" };
    }
    return { kind: "other", title: error.detail, detail: `HTTP ${error.status}` };
  }
  return { kind: "other", title: "Something went wrong", detail: error instanceof Error ? error.message : String(error) };
}
