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
