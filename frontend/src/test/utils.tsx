import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { SizingProvider } from "../components/SizingProvider";
import { ThemeProvider } from "../components/ThemeProvider";
import { LocationProbe } from "./LocationProbe";

export function jsonResponse(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  } as Response;
}

type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;

/** Stubs fetch. Plain values answer 200 with that JSON; functions get the URL and init. Unknown paths are 404. */
export function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const route = routes[url.pathname];
    if (route === undefined) return jsonResponse(404, { detail: `no mock for ${url.pathname}` });
    if (typeof route === "function") return (route as Handler)(url, init);
    return jsonResponse(200, route);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const never = () => new Promise<Response>(() => {});
export const unreachable = () => Promise.reject(new TypeError("Failed to fetch"));

export function renderWithProviders(ui: ReactNode, { route = "/" }: { route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(
    <ThemeProvider>
      <SizingProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[route]}>
            {ui}
            <LocationProbe />
          </MemoryRouter>
        </QueryClientProvider>
      </SizingProvider>
    </ThemeProvider>,
  );
  return { client, ...result };
}
