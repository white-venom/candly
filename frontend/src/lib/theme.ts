import { createContext, useContext } from "react";
import { readStorage, writeStorage } from "./storage";

// The inline script in index.html runs the same resolution before first paint; keep the two in step.

export type Theme = "dark" | "light";

export const THEME_KEY = "candly.theme";

export function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

export type ThemeInputs = {
  /** `?theme=` from the URL */
  urlParam: string | null;
  /** the saved choice from localStorage */
  saved: string | null;
  /** `prefers-color-scheme`, or null when the browser can't tell */
  system: Theme | null;
};

/** URL override, then the saved choice, then the system preference, then dark. */
export function resolveTheme({ urlParam, saved, system }: ThemeInputs): Theme {
  if (isTheme(urlParam)) return urlParam;
  if (isTheme(saved)) return saved;
  return system ?? "dark";
}

export function systemTheme(): Theme | null {
  try {
    if (window.matchMedia("(prefers-color-scheme: light)").matches) return "light";
    if (window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  } catch {
    // matchMedia missing
  }
  return null;
}

export function storeTheme(theme: Theme): void {
  writeStorage(THEME_KEY, theme);
}

/** Reads the environment; a valid `?theme=` is also saved so it sticks. */
export function initialTheme(): Theme {
  const urlParam = new URLSearchParams(window.location.search).get("theme");
  const theme = resolveTheme({ urlParam, saved: readStorage(THEME_KEY), system: systemTheme() });
  if (isTheme(urlParam)) storeTheme(theme);
  return theme;
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
}

export const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "dark",
  toggle: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}
