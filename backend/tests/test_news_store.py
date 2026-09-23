import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from candly.news.models import NewsItem, news_id
from candly.news.store import NewsStore

T0 = datetime(2026, 9, 23, 5, 0, tzinfo=UTC)


def item(title: str, url: str, minutes: int = 0, instruments=(), published: bool = True) -> NewsItem:
    return NewsItem(
        id=news_id(title, url),
        title=title,
        url=url,
        source="Test feed",
        published_at=T0 + timedelta(minutes=minutes) if published else None,
        fetched_at=T0 + timedelta(minutes=minutes + 1),
        instruments=list(instruments),
        sentiment=0.5,
        sentiment_method="lexicon",
        event_type="results",
    )


@pytest.fixture
def store(tmp_data_dir, no_keys):
    return NewsStore()


def test_default_path_and_wal(store, tmp_data_dir):
    assert store.path == tmp_data_dir / "db" / "news.sqlite"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_add_dedupes_and_query_orders_newest_first(store):
    a = item("TCS results beat estimates", "https://x.test/a", 0, ["NSE:TCS"])
    b = item("Infosys wins order", "https://x.test/b?utm_source=rss", 10, ["NSE:INFY"])
    assert store.add([a, b]) == 2
    again = item("TCS results  beat estimates!", "https://X.test/a/", 30, ["NSE:TCS"])
    assert again.id == a.id
    assert store.add([again]) == 0
    got = store.query()
    assert [n.title for n in got] == ["Infosys wins order", "TCS results beat estimates"]
    assert got[1].fetched_at == T0 + timedelta(minutes=1)  # the first sighting is kept
    assert got[0].instruments == ["NSE:INFY"]


def test_repeat_sighting_from_another_feed_adds_instrument_links(store):
    first = item("Reliance and TCS lead gains", "https://x.test/r", 0, ["NSE:RELIANCE"])
    again = item("Reliance and TCS lead gains", "https://x.test/r", 30, ["NSE:TCS", "NSE:RELIANCE"])
    assert store.add([first]) == 1
    assert store.add([again]) == 0
    got = store.query(instrument_id="NSE:TCS")
    assert len(got) == 1 and got[0].instruments == ["NSE:RELIANCE", "NSE:TCS"]
    assert got[0].fetched_at == T0 + timedelta(minutes=1)  # the row itself is the first sighting


def test_query_filters(store):
    store.add(
        [
            item("A", "https://x.test/1", 0, ["NSE:TCS", "NSE:INFY"]),
            item("B", "https://x.test/2", 20, ["NSE:INFY"]),
            item("C", "https://x.test/3", 40, []),
        ]
    )
    assert [n.title for n in store.query(instrument_id="NSE:INFY")] == ["B", "A"]
    assert store.query(instrument_id="NSE:TCS")[0].instruments == ["NSE:INFY", "NSE:TCS"]
    assert [n.title for n in store.query(since=T0 + timedelta(minutes=15))] == ["C", "B"]
    assert len(store.query(limit=1)) == 1
    assert store.query(instrument_id="NSE:TCS' OR '1'='1") == []


def test_unpublished_items_sort_by_fetch_time(store):
    store.add(
        [item("no date", "https://x.test/n", 50, published=False), item("dated", "https://x.test/d", 5)]
    )
    got = store.query()
    assert got[0].title == "no date" and got[0].published_at is None
    assert got[0].to_api()["published_at"] is None
    assert got[1].to_api()["fetched_at"] == int((T0 + timedelta(minutes=6)).timestamp())


def test_naive_timestamps_rejected():
    with pytest.raises(ValueError):
        NewsItem(id="x", title="t", url="u", source="s", published_at=None, fetched_at=datetime(2026, 1, 1))


def test_feed_state_roundtrip(store):
    assert store.feed_state("et") is None
    store.save_feed_state("et", T0, status=200, etag='"abc"', last_modified="Wed, 23 Sep 2026 05:00:00 GMT")
    store.save_feed_state("et", T0 + timedelta(minutes=5), status=304)
    state = store.feed_state("et")
    assert state["etag"] == '"abc"' and state["status"] == 304
    assert state["polled_at"] == int((T0 + timedelta(minutes=5)).timestamp())
