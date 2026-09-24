import csv

import pytest

from candly.core.instruments import load_watchlist
from candly.data import ingest, universe_build
from candly.data.sources import fyers

# (name, exchange instrument type, ISIN, ticker) as in the Fyers NSE_CM master; the rest is filler.
ROWS = [
    ("RELIANCE INDUSTRIES LTD", 0, "INE002A01018", "NSE:RELIANCE-EQ"),
    ("MAHINDRA & MAHINDRA LTD", 0, "INE101A01026", "NSE:M&M-EQ"),
    ("DR. REDDY'S LABORATORIES", 0, "INE089A01031", "NSE:DRREDDY-EQ"),
    ("BRAINBEES SOLUTIONS LTD", 0, "INE02RE01045", "NSE:FIRSTCRY-EQ"),  # "BEES" in the name, not an ETF
    ("NIPPON INDIA ETF NIFTY BEES", 9, "INF204KB14I2", "NSE:NIFTYBEES-EQ"),
    ("SOME GOLD FUND", 0, "INF999X01011", "NSE:GOLDFUND-EQ"),  # fund ISIN on an equity row: fallback
    ("Nifty 50", 10, "", "NSE:NIFTY50-INDEX"),
    ("BIRLASLAMC - ABSLFTTQDG", 8, "INF209KB14H3", "NSE:ABSLFTTQDG-MF"),
    ("2.50%GOLDBONDS2027SR-III", 2, "IN0020190107", "NSE:SGBAUG27-GB"),
    ("SDL PY 5.75% 2026", 5, "IN3820200035", "NSE:575PY26-SG"),
    ("GOI LOAN 5.77% 2030", 6, "IN0020200153", "NSE:577GS2030-GS"),
    ("GOI TBILL 182D-04/02/27", 7, "IN002026Y188", "NSE:182D040227-TB"),
    ("EMBASSY OFFICE PARKS REIT", 0, "INE041025011", "NSE:EMBASSY-RR"),
    ("IRB INFRASTRUCTURE TRUST", 4, "INE0C8K23012", "NSE:IRBIT-IV"),
    ("SME COMPANY LTD", 0, "INE000S01011", "NSE:SMECO-SM"),
    ("SME TRADE FOR TRADE LTD", 0, "INE000S01029", "NSE:SMET4T-ST"),
    ("BOOK ENTRY LTD", 0, "INE000B01011", "NSE:BECO-BE"),
    ("ELECTCAST WARRANTS", 3, "INE086A13016", "NSE:ELECTCAST-W1"),
]


def write_master(path, rows=ROWS):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for n, (name, kind, isin, ticker) in enumerate(rows):
            symbol = ticker.removeprefix("NSE:").rsplit("-", 1)[0]
            writer.writerow(
                [n, name, kind, 1, 0.05, isin, "0915-1530|1815-1915:", "2026-09-23", "", ticker, 10, 10, n,
                 symbol, n, -1.0, "XX", n, "None", 1, 2.0]
            )
    return path


def test_filter_keeps_eq_series_equities_and_counts_each_exclusion(tmp_path):
    universe = universe_build.build_universe(write_master(tmp_path / "NSE_CM.csv"))
    assert list(universe.kept["symbol"]) == ["DRREDDY", "FIRSTCRY", "M&M", "RELIANCE"]
    assert universe.total_rows == len(ROWS)
    assert universe.excluded == {
        "index": 1, "etf": 2, "mutual_fund": 1, "debt": 4, "reit_invit": 2, "sme": 2, "other": 2
    }
    assert universe.other_series == {"BE": 1, "W1": 1}


def test_written_universe_loads_like_the_watchlist(tmp_path):
    master = write_master(tmp_path / "NSE_CM.csv")
    out = tmp_path / "config" / "universe_nse_eq.yaml"
    universe_build.write_universe(universe_build.build_universe(master), out)
    instruments = load_watchlist(out)
    assert [i.id for i in instruments] == ["NSE:DRREDDY", "NSE:FIRSTCRY", "NSE:M&M", "NSE:RELIANCE"]
    assert all(i.kind == "equity" and i.timeframes == ("1D",) for i in instruments)
    assert instruments[0].name == "DR. REDDY'S LABORATORIES"
    assert instruments[2].source_symbol("fyers") == "NSE:M&M-EQ"
    text = out.read_text(encoding="utf-8")
    assert f"FROZEN {fyers.master_day(master)}" in text
    assert "4 of 18 rows kept" in text and "Other series: BE 1, W1 1." in text
    assert list(out.parent.iterdir()) == [out]  # no temp file left behind


def test_cli_downloads_through_the_adapter_and_never_overwrites_silently(tmp_path, monkeypatch, capsys):
    master = write_master(tmp_path / "NSE_CM.csv")
    segments: list[str] = []
    monkeypatch.setattr(fyers, "symbol_master", segments.append)
    monkeypatch.setattr(fyers, "master_path", lambda segment: master)
    out = tmp_path / "universe_nse_eq.yaml"
    assert universe_build.main(["--out", str(out)]) == 0
    assert segments == ["NSE_CM"]
    printed = capsys.readouterr().out
    assert "18 rows" in printed and "kept 4" in printed and "excluded     4  bonds" in printed
    before = out.read_text(encoding="utf-8")

    write_master(master, ROWS[:2])
    assert universe_build.main(["--out", str(out)]) == 2
    assert "--force" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == before
    assert universe_build.main(["--out", str(out), "--force"]) == 0
    assert [i.id for i in load_watchlist(out)] == ["NSE:M&M", "NSE:RELIANCE"]


def test_cli_rejects_an_unreadable_master(tmp_path, monkeypatch, capsys):
    master = tmp_path / "NSE_CM.csv"
    master.write_text("<html>maintenance</html>\n", encoding="utf-8")
    monkeypatch.setattr(fyers, "symbol_master", lambda segment: None)
    monkeypatch.setattr(fyers, "master_path", lambda segment: master)
    out = tmp_path / "universe_nse_eq.yaml"
    assert universe_build.main(["--out", str(out)]) == 2
    assert "unreadable symbol master" in capsys.readouterr().err
    assert not out.exists()


def test_frozen_nse_eq_universe_is_valid():
    universe = ingest.load_universe("nse_eq")
    ids = [i.id for i in universe]
    assert len(ids) == len(set(ids)) == 2319
    assert all(i.kind == "equity" and i.timeframes == ("1D",) for i in universe)
    assert all(i.source_symbol("fyers") == f"NSE:{i.symbol}-EQ" for i in universe)
    nifty200 = {i.id for i in ingest.load_universe("nifty200") if i.kind == "equity"}
    assert nifty200 <= set(ids)


@pytest.mark.network
def test_live_master_still_has_the_columns_the_filter_reads():
    fyers.symbol_master("NSE_CM")
    universe = universe_build.build_universe(fyers.master_path("NSE_CM"))
    assert len(universe.kept) > 1500 and universe.excluded["etf"] > 100 and universe.excluded["index"] > 50
