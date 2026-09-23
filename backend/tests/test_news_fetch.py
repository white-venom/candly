import time
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
import respx

from candly.data import clock
from candly.news import fetch
from candly.news.store import NewsStore

NOW = pd.Timestamp("2026-09-23 06:00", tz="UTC")
ET_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
NSE_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
RBI_URL = "https://www.rbi.org.in/pressreleases_rss.xml"


def rss(*items: str) -> str:
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{"".join(items)}</channel></rss>'
    )


def rss_item(title: str, link: str, published: str, extra: str = "") -> str:
    return f"<item><title>{title}</title><link>{link}</link><pubDate>{published}</pubDate>{extra}</item>"


ET_FEED = rss(
    rss_item(
        "Reliance Industries shares surge after strong Q2 results",
        "https://economictimes.indiatimes.com/a1.cms",
        "Wed, 23 Sep 2026 10:30:00 +0530",
        "<description>&lt;p&gt;RIL beat estimates&lt;/p&gt;</description>",
    ),
    rss_item(
        "Nifty 50 ends flat", "https://economictimes.indiatimes.com/a2.cms", "Wed, 23 Sep 2026 04:00:00 GMT"
    ),
)
NSE_FEED = rss(
    rss_item(
        "ITC Limited - Outcome of Board Meeting",
        "https://nsearchives.nseindia.com/x.pdf",
        "23-Sep-2026 10:45:40",
    )
)
GOOGLE_RELIANCE = rss(
    rss_item(
        "Reliance Jio plans IPO - The Economic Times",
        "https://news.google.com/rss/articles/abc",
        "Wed, 23 Sep 2026 05:00:00 GMT",
        '<source url="https://economictimes.indiatimes.com">The Economic Times</source>',
    ),
    rss_item(  # a namesake, not Reliance Industries: the mapper finds nothing, so it isn't stored
        "Reliance Power shares hit upper circuit - Mint",
        "https://news.google.com/rss/articles/def",
        "Wed, 23 Sep 2026 05:10:00 GMT",
        '<source url="https://www.livemint.com">Mint</source>',
    ),
    rss_item(  # returned for the Reliance query but about other instruments: tagged with those only
        "HDFC Bank lifts Nifty to a record close - Mint",
        "https://news.google.com/rss/articles/ghi",
        "Wed, 23 Sep 2026 05:20:00 GMT",
        '<source url="https://www.livemint.com">Mint</source>',
    ),
)


def google(request: httpx.Request) -> httpx.Response:
    if "Reliance" in request.url.params.get("q", ""):
        return httpx.Response(200, text=GOOGLE_RELIANCE)
    return httpx.Response(200, text=rss())


@pytest.fixture
def feeds(monkeypatch, tmp_data_dir, no_keys):
    now = {"value": NOW}
    monkeypatch.setattr(clock, "utc_now", lambda: now["value"])
    monkeypatch.setattr(fetch, "_sleep", lambda seconds: None)
    with respx.mock(assert_all_called=False) as mock:

        def et(request: httpx.Request) -> httpx.Response:
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, text=ET_FEED, headers={"ETag": '"v1"'})

        routes = {
            "et": mock.get(ET_URL).mock(side_effect=et),
            "nse": mock.get(NSE_URL).mock(return_value=httpx.Response(200, text=NSE_FEED)),
            "rbi": mock.get(RBI_URL).mock(return_value=httpx.Response(500)),
            "google": mock.route(host="news.google.com").mock(side_effect=google),
            "other": mock.route().mock(return_value=httpx.Response(200, text=rss())),
        }
        yield now, routes


def test_poll_stores_mapped_scored_items(feeds, tmp_data_dir):
    now, routes = feeds
    assert fetch.poll_news() == 5
    assert routes["et"].calls.last.request.headers["User-Agent"] == "candly/0.1 (personal research)"
    assert routes["google"].call_count == 10  # one query per equity
    items = {n.title: n for n in NewsStore().query(limit=100)}

    ril = items["Reliance Industries shares surge after strong Q2 results"]
    assert ril.instruments == ["NSE:RELIANCE"]
    assert ril.sentiment > 0 and ril.sentiment_method == "lexicon" and ril.event_type == "results"
    assert ril.published_at == datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
    assert ril.fetched_at == NOW.to_pydatetime()
    assert ril.summary == "RIL beat estimates" and ril.source == "Economic Times - Markets"

    nse = items["ITC Limited - Outcome of Board Meeting"]
    assert nse.published_at == datetime(2026, 9, 23, 5, 15, 40, tzinfo=UTC)
    assert nse.instruments == ["NSE:ITC"]

    jio = items["Reliance Jio plans IPO"]
    assert jio.source == "Google News / The Economic Times"
    assert jio.instruments == ["NSE:RELIANCE"] and jio.event_type == "deal"
    assert "Reliance Power shares hit upper circuit" not in items
    assert items["HDFC Bank lifts Nifty to a record close"].instruments == ["NSE:HDFCBANK", "NSE:NIFTY50"]

    rbi_state = NewsStore().feed_state("rbi_press")
    assert rbi_state["status"] == 500 and rbi_state["error"]


def test_polls_are_throttled_and_conditional(feeds):
    now, routes = feeds
    fetch.poll_news()
    et_calls, google_calls = routes["et"].call_count, routes["google"].call_count
    assert fetch.poll_news() == 0
    assert routes["et"].call_count == et_calls  # not due yet

    now["value"] = NOW + pd.Timedelta(minutes=5)
    assert fetch.poll_news() == 0
    assert routes["et"].call_count == et_calls + 1
    assert routes["et"].calls.last.response.status_code == 304
    assert routes["google"].call_count == google_calls  # Google queries poll every 30 minutes

    now["value"] = NOW + pd.Timedelta(minutes=31)
    fetch.poll_news()
    assert routes["google"].call_count == 2 * google_calls


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("23-Sep-2026 10:45:40", datetime(2026, 9, 23, 5, 15, 40, tzinfo=UTC)),
        ("3-Sep-2026 09:05", datetime(2026, 9, 3, 3, 35, tzinfo=UTC)),
        ("Wed, 23 Sep 2026 10:30:00 +0530", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)),
        ("Wed, 23 Sep 2026 10:30:00 GMT", datetime(2026, 9, 23, 10, 30, tzinfo=UTC)),
        ("Wed, 23 Sep 2026 10:30:00", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)),
        ("Wed, 23 Sep 2026 10:30:00 IST", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)),
        ("2026-09-23T10:30:00+05:30", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)),
        ("2026-09-23T05:00:00Z", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_published(raw, expected):
    assert fetch.parse_published(raw) == expected


def test_parse_published_falls_back_to_feedparser_struct():
    parsed = time.struct_time((2026, 9, 23, 5, 0, 0, 2, 266, 0))
    assert fetch.parse_published("garbage", parsed) == datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
