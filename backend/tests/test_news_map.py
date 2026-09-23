import pytest

from candly.core.instruments import load_watchlist
from candly.news.map import InstrumentMapper

mapper = InstrumentMapper(load_watchlist())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ITC shares rise after Q2 results", ["NSE:ITC"]),
        ("itc gains", ["NSE:ITC"]),
        ("Pitch perfect: switching to a new strategy", []),
        ("ITC Hotels lists on the exchanges", []),
        ("L&T bags a large order from NHAI", ["NSE:LT"]),
        ("L &amp; T wins contract", ["NSE:LT"]),
        ("L & T shares at record high", ["NSE:LT"]),
        ("L&T Finance raises funds", []),
        ("L&T Technology Services hires", []),
        ("Larsen and Toubro Q2 profit", ["NSE:LT"]),
        ("Reliance Industries hits 52-week high", ["NSE:RELIANCE"]),
        ("RIL AGM today", ["NSE:RELIANCE"]),
        ("Reliance Power stock jumps 5%", []),
        ("Reliance's retail arm grows", ["NSE:RELIANCE"]),
        ("Nifty Bank slips; Nifty 50 flat", ["NSE:BANKNIFTY", "NSE:NIFTY50"]),
        ("Bank Nifty options expiry", ["NSE:BANKNIFTY"]),
        ("SBI Life premiums grow", []),
        ("SBI cuts lending rates", ["NSE:SBIN"]),
        ("State Bank of India raises bonds", ["NSE:SBIN"]),
        ("Goldman Sachs upgrades Infosys", ["NSE:INFY"]),
        ("Gold and silver prices climb", ["MCX:GOLD", "MCX:SILVER"]),
        ("Brent crude slides below $70", ["MCX:CRUDEOIL"]),
        ("Airtel Africa results", []),
        ("Bharti Airtel ARPU rises", ["NSE:BHARTIARTL"]),
        ("Sensex, Nifty end higher", ["BSE:SENSEX", "NSE:NIFTY50"]),
        ("", []),
    ],
)
def test_mapping(text, expected):
    assert sorted(mapper.map(text)) == sorted(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nifty IT index falls 2% on weak US cues", []),
        ("Nifty Midcap 100 hits a record high", []),
        ("Nifty Smallcap 250 underperforms", []),
        ("Nifty Next 50 rebalancing: five stocks in", []),
        ("Nifty PSU Bank rallies 3%", []),
        ("Nifty Pharma, Nifty Metal drag", []),
        ("Nifty Financial Services slips", []),
        ("Nifty next week: key levels to watch", ["NSE:NIFTY50"]),
        ("Nifty IT drags, but Nifty ends flat", ["NSE:NIFTY50"]),
        ("GIFT Nifty signals a gap-up open", ["NSE:NIFTY50"]),
    ],
)
def test_sectoral_indices_are_not_the_nifty_50(text, expected):
    assert sorted(mapper.map(text)) == sorted(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Crude palm oil imports rise 12% in August", []),
        ("India's crude steel output climbs", []),
        ("Crude soybean oil prices ease", []),
        ("Crude oil slips below $70 a barrel", ["MCX:CRUDEOIL"]),
        ("Crude extends losses on supply glut", ["MCX:CRUDEOIL"]),
    ],
)
def test_crude_means_crude_oil_only(text, expected):
    assert mapper.map(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Senco Gold shares jump 5% after Q2 update", []),
        ("Senco Gold IPO price band fixed", []),
        ("Silver Lake to invest in Reliance Retail", ["NSE:RELIANCE"]),
        ("Muthoot Finance cuts gold loan rates", []),
        ("Golden jubilee: silver screen legend honoured", []),
        ("Gold prices hit a record; MCX gold futures up 1%", ["MCX:GOLD"]),
        ("Gold rate today: the yellow metal climbs ₹500 per 10 grams", ["MCX:GOLD"]),
        ("Silver futures surge on MCX", ["MCX:SILVER"]),
        ("Bullion demand picks up before Diwali", ["MCX:GOLD"]),
    ],
)
def test_gold_and_silver_need_commodity_context(text, expected):
    assert sorted(mapper.map(text)) == sorted(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CBOE Volatility Index spikes as Wall Street tumbles", []),
        ("Cboe Volatility Index jumps; Nifty falls 1%", ["NSE:NIFTY50"]),
        ("US stocks: volatility index at a three-month high", []),
        ("Volatility index falls as Nifty hits a record", ["NSE:INDIAVIX", "NSE:NIFTY50"]),
        ("India VIX cools to 11", ["NSE:INDIAVIX"]),
    ],
)
def test_volatility_index_means_india_vix_only(text, expected):
    assert sorted(mapper.map(text)) == sorted(expected)
