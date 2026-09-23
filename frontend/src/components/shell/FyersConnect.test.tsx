import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { health, pausedHealth } from "../../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../../test/utils";
import { AppShell } from "./AppShell";
import { NoticeStrip } from "./NoticeStrip";

const PASTED = "https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&auth_code=abc.def&state=x";

function renderShell() {
  return renderWithProviders(
    <AppShell>
      <NoticeStrip />
    </AppShell>,
    { route: "/scanner" },
  );
}

async function pasteAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  const dialog = screen.getByRole("dialog", { name: "Connect Fyers" });
  const login = within(dialog).getByRole("link", { name: "Log in to Fyers" });
  expect(login.getAttribute("href")).toBe("/api/auth/fyers/login");
  expect(login.getAttribute("target")).toBe("_blank");
  const input = within(dialog).getByLabelText("After logging in, copy the full address-bar URL from the Fyers page and paste it here");
  expect(document.activeElement).toBe(input);
  await user.type(input, PASTED);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));
  return dialog;
}

describe("Fyers paste-code connect", () => {
  it("opens from the paused strip, posts the pasted URL and says setup has started", async () => {
    const user = userEvent.setup();
    const expiresAt = Date.UTC(2026, 8, 24, 0, 30) / 1000; // 06:00 IST
    const fetchMock = mockApi({
      "/api/health": pausedHealth,
      "/api/auth/fyers/code": () => jsonResponse(200, { connected: true, expires_at: expiresAt }),
    });
    const { client } = renderShell();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    const strip = await screen.findByRole("status", { name: "Notices" });
    expect(strip.textContent).toContain("Live data paused — Fyers not connected");
    await user.click(within(strip).getByRole("button", { name: "Connect" }));
    const dialog = await pasteAndSubmit(user);

    expect(await within(dialog).findByText("Connected — setting everything up now")).toBeTruthy();
    expect(dialog.textContent).toMatch(/The login lasts until (24 Sep, )?06:00 IST\./);
    const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).includes("/auth/fyers/code"))!;
    expect(String(url)).toBe("/api/auth/fyers/code");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ code: PASTED });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["health"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["fyers-status"] });
  });

  it("opens from the rail's data dot too", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": pausedHealth });
    renderShell();
    const dot = await screen.findByRole("button", { name: "Data connection: Live data paused — Fyers not connected" });
    await user.click(dot);
    const popover = screen.getByRole("dialog", { name: "Data connection" });
    expect(within(popover).getByRole("list", { name: "Market sessions" }).textContent).toContain("MCXClosed");
    await user.click(within(popover).getByRole("button", { name: "Connect Fyers" }));
    expect(screen.getByRole("dialog", { name: "Connect Fyers" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Data connection" })).toBeNull();
  });

  it("offers no Connect when Fyers has no key or is already connected", async () => {
    mockApi({ "/api/health": { ...health, keys: { ...health.keys, fyers_connected: true } } });
    renderShell();
    await screen.findByRole("button", { name: "Data connection: Live data — Source: Yahoo (dev data)" });
    expect(screen.queryByRole("status", { name: "Notices" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("shows the API's detail when the exchange fails with 400", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": pausedHealth, "/api/auth/fyers/code": () => jsonResponse(400, { detail: "auth_code expired — log in again" }) });
    renderShell();
    await user.click(await screen.findByRole("button", { name: "Connect" }));
    const dialog = await pasteAndSubmit(user);
    expect((await within(dialog).findByRole("alert")).textContent).toBe("auth_code expired — log in again");
    expect(within(dialog).queryByText(/Connected/)).toBeNull();
  });

  it("says the backend is not running when it can't be reached", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": pausedHealth, "/api/auth/fyers/code": () => Promise.reject(new TypeError("Failed to fetch")) });
    renderShell();
    await user.click(await screen.findByRole("button", { name: "Connect" }));
    const dialog = await pasteAndSubmit(user);
    expect((await within(dialog).findByRole("alert")).textContent).toBe("Backend not running");
  });

  it("closes on Escape and returns focus to the opener", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": pausedHealth });
    renderShell();
    const connect = await screen.findByRole("button", { name: "Connect" });
    await user.click(connect);
    expect(screen.getByRole("dialog", { name: "Connect Fyers" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Connect Fyers" })).toBeNull();
    expect(document.activeElement).toBe(connect);
  });
});
