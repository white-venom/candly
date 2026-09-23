import { describe, expect, it, vi } from "vitest";
import html from "../../index.html?raw";
import { THEME_KEY } from "../lib/theme";
import { mockSystemTheme } from "./matchMedia";

const script = /<script id="theme-init">([\s\S]*?)<\/script>/.exec(html)?.[1];

function runInlineScript() {
  if (!script) throw new Error("theme-init script not found in index.html");
  new Function(script)();
  return document.documentElement.getAttribute("data-theme");
}

describe("index.html theme-init script (runs before React, avoids a flash)", () => {
  it("runs before the app bundle", () => {
    expect(html.indexOf('id="theme-init"')).toBeLessThan(html.indexOf("/src/main.tsx"));
  });

  it("defaults to dark", () => {
    expect(runInlineScript()).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("follows the system preference when nothing is saved", () => {
    mockSystemTheme("light");
    expect(runInlineScript()).toBe("light");
  });

  it("prefers the saved choice", () => {
    mockSystemTheme("light");
    window.localStorage.setItem(THEME_KEY, "dark");
    expect(runInlineScript()).toBe("dark");
  });

  it("applies and saves ?theme= over everything else", () => {
    window.localStorage.setItem(THEME_KEY, "light");
    window.history.replaceState(null, "", "/scanner?theme=dark");
    expect(runInlineScript()).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("still applies the URL theme when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    window.history.replaceState(null, "", "/?theme=light");
    expect(runInlineScript()).toBe("light");
  });
});
