import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { T0, health } from "../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../test/utils";
import { FyersConnect } from "./FyersConnect";

const PASTED = "https://trade.fyers.in/api-login/redirect-uri/index.html?s=ok&auth_code=abc.def&state=x";

async function openAndSubmit() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Connect Fyers" }));
  const login = screen.getByRole("link", { name: "Log in to Fyers" });
  expect(login.getAttribute("href")).toBe("/api/auth/fyers/login");
  expect(login.getAttribute("target")).toBe("_blank");
  await user.type(
    screen.getByLabelText("After logging in, copy the full address-bar URL from the Fyers page and paste it here"),
    PASTED,
  );
  await user.click(screen.getByRole("button", { name: "Submit" }));
}

describe("Fyers paste-code connect", () => {
  it("is hidden when Fyers has no key or is already connected", () => {
    const { unmount } = renderWithProviders(<FyersConnect health={{ ...health, keys: { ...health.keys, fyers: false } }} />);
    expect(screen.queryByRole("button", { name: "Connect Fyers" })).toBeNull();
    unmount();
    renderWithProviders(<FyersConnect health={{ ...health, keys: { ...health.keys, fyers_connected: true } }} />);
    expect(screen.queryByRole("button", { name: "Connect Fyers" })).toBeNull();
  });

  it("posts the pasted URL, refreshes health and status, and shows the IST expiry", async () => {
    const expiresAt = Date.UTC(2026, 8, 24, 0, 30) / 1000; // 06:00 IST
    const fetchMock = mockApi({
      "/api/auth/fyers/code": () => jsonResponse(200, { connected: true, expires_at: expiresAt }),
      "/api/health": { ...health, time: T0 },
    });
    const { client } = renderWithProviders(<FyersConnect health={health} />);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await openAndSubmit();

    expect(await screen.findByText("Connected until 24 Sep 2026, 06:00 IST")).toBeTruthy();
    const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).includes("/auth/fyers/code"))!;
    expect(String(url)).toBe("/api/auth/fyers/code");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ code: PASTED });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["health"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["fyers-status"] });
  });

  it("shows the API's detail when the exchange fails with 400", async () => {
    mockApi({ "/api/auth/fyers/code": () => jsonResponse(400, { detail: "auth_code expired — log in again" }) });
    renderWithProviders(<FyersConnect health={health} />);
    await openAndSubmit();
    expect((await screen.findByRole("alert")).textContent).toBe("auth_code expired — log in again");
    expect(screen.queryByText(/Connected until/)).toBeNull();
  });

  it("says the backend is down when it can't be reached", async () => {
    mockApi({ "/api/auth/fyers/code": () => Promise.reject(new TypeError("Failed to fetch")) });
    renderWithProviders(<FyersConnect health={health} />);
    await openAndSubmit();
    expect((await screen.findByRole("alert")).textContent).toBe("Backend not running — start it with the command in CLAUDE.md");
  });
});
