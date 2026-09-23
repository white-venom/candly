export type QueryParams = Record<string, string | number | boolean | null | undefined>;

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(status ? `${status}: ${detail}` : detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** No response from the API: fetch failed outright, or the dev proxy answered 502/504 without a JSON body. */
  get unreachable(): boolean {
    return this.status === 0;
  }

  get noData(): boolean {
    return this.status === 503;
  }
}

export function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return `/api${path}${qs ? `?${qs}` : ""}`;
}

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  // FastAPI validation errors (422) carry a list of {msg, loc}.
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  return null;
}

async function request<T>(url: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers: { Accept: "application/json", ...init.headers } });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Backend not reachable");
  }

  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (res.ok) {
    if (body === null && text) throw new ApiError(res.status, "Response was not JSON");
    return body as T;
  }

  const detail = body && typeof body === "object" && "detail" in body ? detailText((body as { detail: unknown }).detail) : null;
  if (detail === null && (res.status === 502 || res.status === 504)) {
    throw new ApiError(0, "Backend not reachable");
  }
  throw new ApiError(res.status, detail ?? (text.trim() || res.statusText || "Request failed"));
}

export function apiGet<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  return request<T>(buildUrl(path, params), { method: "GET", signal });
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(buildUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
