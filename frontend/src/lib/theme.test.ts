import { describe, expect, it, vi } from "vitest";
import { mockSystemTheme } from "../test/matchMedia";
import { THEME_KEY, initialTheme, resolveTheme } from "./theme";

describe("resolveTheme", () => {
  it("prefers the URL, then the saved choice, then the system, then dark", () => {
    expect(resolveTheme({ urlParam: "light", saved: "dark", system: "dark" })).toBe("light");
    expect(resolveTheme({ urlParam: null, saved: "light", system: "dark" })).toBe("light");
    expect(resolveTheme({ urlParam: null, saved: null, system: "light" })).toBe("light");
    expect(resolveTheme({ urlParam: null, saved: null, system: null })).toBe("dark");
  });

  it("ignores values that are not themes", () => {
    expect(resolveTheme({ urlParam: "blue", saved: "sepia", system: null })).toBe("dark");
  });
});

describe("initialTheme", () => {
  it("follows the system preference when nothing is saved, without saving it", () => {
    mockSystemTheme("light");
    expect(initialTheme()).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("uses the saved choice over the system preference", () => {
    mockSystemTheme("light");
    window.localStorage.setItem(THEME_KEY, "dark");
    expect(initialTheme()).toBe("dark");
  });

  it("applies and saves a ?theme= override", () => {
    window.localStorage.setItem(THEME_KEY, "dark");
    window.history.replaceState(null, "", "/chart?theme=light");
    expect(initialTheme()).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("falls back to dark when storage throws and matchMedia is missing", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.stubGlobal("matchMedia", undefined);
    expect(initialTheme()).toBe("dark");
  });
});
