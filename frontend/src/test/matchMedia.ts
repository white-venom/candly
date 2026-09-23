import { vi } from "vitest";

/** jsdom has no matchMedia; this simulates the OS colour-scheme setting (null = no preference reported). */
export function mockSystemTheme(theme: "light" | "dark" | null): void {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: theme !== null && query === `(prefers-color-scheme: ${theme})`,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}
