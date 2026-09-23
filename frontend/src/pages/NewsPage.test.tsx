import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { NewsItem } from "../api/types";
import { T0, instruments } from "../test/fixtures";
import { mockApi, renderWithProviders } from "../test/utils";
import { NewsPage } from "./NewsPage";

const item: NewsItem = {
  id: "n1",
  title: "Reliance wins large order",
  url: "https://example.com/r",
  source: "Economic Times",
  published_at: T0 - 3600,
  fetched_at: T0,
  instruments: ["NSE:RELIANCE"],
  sentiment: 0.42,
  sentiment_method: "lexicon",
  event_type: "order_win",
  summary: null,
};

describe("news page", () => {
  it("shows sentiment, event type and both timestamps in IST", async () => {
    mockApi({ "/api/instruments": instruments, "/api/news": [item, { ...item, id: "n2", published_at: null, sentiment: -0.3, event_type: null }] });
    renderWithProviders(<NewsPage />, { route: "/news" });
    expect((await screen.findAllByText("Reliance wins large order")).length).toBe(2);
    expect(screen.getByText("▲ positive +0.42")).toBeTruthy();
    expect(screen.getByText("▼ negative -0.30")).toBeTruthy();
    expect(screen.getByText("order win")).toBeTruthy();
    expect(screen.getByText("Published 23 Sep 2026, 08:15 IST")).toBeTruthy();
    expect(screen.getByText("Published unknown")).toBeTruthy();
    expect(screen.getAllByText("Fetched 23 Sep 2026, 09:15 IST")).toHaveLength(2);
  });

  it("filters by instrument through the URL and shows the empty state", async () => {
    const fetchMock = mockApi({ "/api/instruments": instruments, "/api/news": [] });
    renderWithProviders(<NewsPage />, { route: "/news?instrument=NSE:RELIANCE" });
    expect(await screen.findByText("No news logged for this instrument yet.")).toBeTruthy();
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/news?instrument=NSE%3ARELIANCE&limit=100");
  });
});
