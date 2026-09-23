import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { NewsItem } from "../api/types";
import { health, instruments } from "../test/fixtures";
import { mockApi, renderWithProviders } from "../test/utils";
import { NewsPage } from "./NewsPage";

const NOW = Math.floor(Date.now() / 1000);

const item: NewsItem = {
  id: "n1",
  title: "Reliance wins large order",
  url: "https://example.com/r",
  source: "Economic Times",
  published_at: NOW - 2 * 3600,
  fetched_at: NOW - 3600,
  instruments: ["NSE:RELIANCE"],
  sentiment: 0.42,
  sentiment_method: "lexicon",
  event_type: "order_win",
  summary: null,
};

const items: NewsItem[] = [
  item,
  { ...item, id: "n2", title: "Crude slips on supply worries", published_at: null, sentiment: -0.3, event_type: null, instruments: [] },
  { ...item, id: "n3", title: "Board meeting scheduled", sentiment: 0, event_type: "board_meeting", instruments: [] },
];

const articles = () => screen.getAllByRole("article");

describe("news page", () => {
  it("shows a clean feed with sentiment dots, event chips and relative times", async () => {
    mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/news": items });
    renderWithProviders(<NewsPage />, { route: "/news" });
    expect(await screen.findByText("Reliance wins large order")).toBeTruthy();
    const [first, second] = articles();
    expect(within(first).getByRole("img", { name: "Positive sentiment (+0.42)" })).toBeTruthy();
    expect(within(first).getByText("Order win")).toBeTruthy();
    expect(within(first).getByText("RELIANCE")).toBeTruthy();
    expect(within(first).getByText("2h ago")).toBeTruthy();
    // No publish time: the fetch time stands in, and says so.
    expect(within(second).getByText("1h ago").getAttribute("title")).toMatch(/^Fetched .* IST$/);
    expect(within(second).getByRole("img", { name: "Negative sentiment (-0.30)" })).toBeTruthy();
  });

  it("filters by sentiment and event type through the URL", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/news": items });
    renderWithProviders(<NewsPage />, { route: "/news" });
    await screen.findByText("Reliance wins large order");
    await user.click(within(screen.getByRole("group", { name: "Sentiment" })).getByRole("button", { name: "Negative" }));
    expect(articles().map((a) => within(a).getByRole("link", { name: /./ }).textContent)).toEqual(["Crude slips on supply worries"]);
    expect(screen.getByTestId("location").textContent).toBe("/news?sentiment=negative");

    await user.click(within(screen.getByRole("group", { name: "Sentiment" })).getByRole("button", { name: "All" }));
    await user.selectOptions(screen.getByLabelText("Event"), "board_meeting");
    expect(articles()).toHaveLength(1);
    expect(screen.getByText("Board meeting scheduled")).toBeTruthy();
  });

  it("asks the API for one instrument and explains an empty feed", async () => {
    const fetchMock = mockApi({ "/api/health": health, "/api/instruments": instruments, "/api/news": [] });
    renderWithProviders(<NewsPage />, { route: "/news?instrument=NSE:RELIANCE" });
    expect(await screen.findByText("No news for this instrument yet")).toBeTruthy();
    expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain("/api/news?instrument=NSE%3ARELIANCE&limit=100");
  });
});
