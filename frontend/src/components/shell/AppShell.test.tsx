import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SyncStatus } from "../../api/types";
import { forecast, health, nifty } from "../../test/fixtures";
import { jsonResponse, mockApi, renderWithProviders } from "../../test/utils";
import { TradeCard } from "../setup/TradeCard";
import { AppShell } from "./AppShell";
import { NoticeStrip } from "./NoticeStrip";

const NOW = Math.floor(Date.now() / 1000);
const sync = (over: Partial<SyncStatus>): SyncStatus => ({
  status: "running",
  step: "backfill-1d",
  progress: 0.42,
  message: null,
  started_at: NOW - 60,
  finished_at: null,
  ...over,
});

describe("app shell", () => {
  it("has a rail with every page, marking the current one", async () => {
    mockApi({ "/api/health": health });
    renderWithProviders(<AppShell />, { route: "/scanner" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("aria-label"))).toEqual(["Chart", "Scanner", "Scorecard", "Accuracy", "News"]);
    expect(within(nav).getByRole("link", { name: "Scanner" }).getAttribute("aria-current")).toBe("page");
    expect(await screen.findByRole("button", { name: /^Data connection: Live data/ })).toBeTruthy();
  });

  it("edits capital and risk in Settings, and the trade card re-sizes and remembers", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": health });
    renderWithProviders(
      <AppShell>
        <TradeCard trade={forecast.trade!} units="units" />
      </AppShell>,
    );
    const card = screen.getByRole("region", { name: "Trade plan" });
    expect(card.textContent).toContain("Qty 111 units");

    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = screen.getByRole("dialog", { name: "Position sizing" });
    const capital = within(dialog).getByLabelText("Capital");
    expect(document.activeElement).toBe(capital);
    await user.clear(capital);
    await user.type(capital, "200000");
    expect(card.textContent).toContain("Qty 222 units");

    const risk = within(dialog).getByLabelText("Risk per trade");
    await user.clear(risk);
    expect(within(dialog).getByText("Enter a percentage above 0 and at most 100.")).toBeTruthy();
    await user.type(risk, "1");
    expect(card.textContent).toContain("Qty 444 units");
    expect(card.textContent).toContain("₹1,998.00 at risk");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("candly.sizing")!)).toEqual({ capital: 200000, riskPct: 1 });
  });

  it("opens the shortcut help with ? and switches theme with t", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": health });
    renderWithProviders(<AppShell />);
    await user.keyboard("t");
    expect(document.documentElement.dataset.theme).toBe("light");
    await user.keyboard("?");
    const help = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(help.textContent).toContain("Search instruments");
    // Shortcuts rest while a dialog is open.
    await user.keyboard("t");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("shows the first sync's progress in place of the paused strip", async () => {
    mockApi({ "/api/health": { ...health, ingest: { status: "blocked", reason: "Fyers not connected" }, sync: sync({}) } });
    renderWithProviders(<NoticeStrip />);
    const strip = await screen.findByRole("status", { name: "Notices" });
    expect(strip.textContent).toBe("Setting up from Fyers — Downloading daily history · 42%");
    expect(within(strip).getByRole("progressbar", { name: "Fyers setup" }).getAttribute("aria-valuenow")).toBe("42");
    expect(within(strip).queryByText(/paused/)).toBeNull();
  });

  it("offers Retry when the sync stops, posting JSON to /api/sync/fyers", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({
      "/api/health": { ...health, sync: sync({ status: "error", message: "Fyers rate limit" }) },
      "/api/sync/fyers": () => jsonResponse(202, sync({ progress: 0 })),
    });
    renderWithProviders(<NoticeStrip />);
    const strip = await screen.findByRole("status", { name: "Notices" });
    expect(strip.textContent).toContain("Fyers setup stopped — Fyers rate limit");
    await user.click(within(strip).getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u) === "/api/sync/fyers");
      expect(call?.[1]?.method).toBe("POST");
      expect(new Headers(call?.[1]?.headers).get("Content-Type")).toBe("application/json");
    });
  });

  it("says once that setup finished", async () => {
    mockApi({ "/api/health": { ...health, sync: sync({ status: "done", progress: 1, finished_at: NOW - 5 }) } });
    const { unmount } = renderWithProviders(<AppShell />);
    expect(await screen.findByText("Fyers setup complete")).toBeTruthy();
    unmount();
    renderWithProviders(<AppShell />);
    await screen.findByRole("button", { name: /^Data connection/ });
    expect(screen.queryByText("Fyers setup complete")).toBeNull();
  });

  it("notes an expiry schedule that changed", async () => {
    mockApi({
      "/api/health": {
        ...health,
        expiry_check: { status: "mismatch", checked_at: NOW, mismatches: [{ instrument: nifty.id, rules: "2026-09-30", exchange: "2026-09-29" }] },
      },
    });
    renderWithProviders(<NoticeStrip />);
    expect((await screen.findByRole("status", { name: "Notices" })).textContent).toBe("Expiry schedule changed for NIFTY50 — exchange says Tue 29 Sep");
  });
});
