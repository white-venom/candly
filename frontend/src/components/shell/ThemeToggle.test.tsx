import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { THEME_KEY } from "../../lib/theme";
import { mockSystemTheme } from "../../test/matchMedia";
import { ThemeProvider } from "../ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

const html = document.documentElement;

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

describe("theme toggle", () => {
  it("flips data-theme and saves the choice", async () => {
    const user = userEvent.setup();
    renderToggle();
    expect(html.dataset.theme).toBe("dark");

    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(html.dataset.theme).toBe("light");
    expect(html.style.colorScheme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeTruthy();
  });

  it("works from the keyboard", async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Switch to light theme" }));
    await user.keyboard("{Enter}");
    expect(html.dataset.theme).toBe("light");
    await user.keyboard(" ");
    expect(html.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("uses the system preference when nothing is saved", () => {
    mockSystemTheme("light");
    renderToggle();
    expect(html.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("keeps a saved choice over the system preference", () => {
    mockSystemTheme("light");
    window.localStorage.setItem(THEME_KEY, "dark");
    renderToggle();
    expect(html.dataset.theme).toBe("dark");
  });

  it("lets a ?theme= URL override win, and saves it", () => {
    mockSystemTheme("dark");
    window.localStorage.setItem(THEME_KEY, "dark");
    window.history.replaceState(null, "", "/chart/NSE:RELIANCE/1D?theme=light");
    renderToggle();
    expect(html.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
  });
});
