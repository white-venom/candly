"""Data-quality checks on the candle store (acceptance report 2026-09-23, findings F1, F2 and F8).

- missing_daily_sessions: sessions in the stored intraday data with no 1D bar (the daily ingest re-fetches
  them).
- suspicious_bars: spike-and-reverse bars such as LT 2006-09-27, stored at half price. Flagged, never deleted.
  The report skips non-tradable series (INDIAVIX).
- bar_count_anomalies: partial sessions, sessions that peer series have but this one lacks, and bars on days
  the calendar has no session for.

CLI: python -m candly.data.quality --report   (writes data/quality/report.json)
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.calendar import IST, get_calendar
from candly.core.instruments import Instrument, get_instrument, load_watchlist
from candly.core.log import setup_logging
from candly.core.settings import get_settings
from candly.core.timeframes import is_intraday, tf_delta
from candly.data import clock
from candly.data.store import candle_source, load_candles, load_ts

# (gap vs the previous close, gap back on the next bar) as fractions, per instrument kind; tuned for 1D.
SPIKE_LIMITS = {"equity": (0.25, 0.20), "future": (0.25, 0.20), "index": (0.15, 0.10)}
LIST_LIMIT = 50  # longest date list per series in the report (most recent kept)


def _local_dates(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST).dt.date


def _closed(exchange: str, d: date, now: pd.Timestamp) -> bool:
    return get_calendar().session_times(exchange, d)[1] <= now


def missing_daily_sessions(instrument: str | Instrument) -> list[date]:
    """Closed sessions (IST dates) that exist in the stored intraday data but have no 1D bar.
    Takes an id (watchlist) or an Instrument (e.g. from a research universe)."""
    inst = instrument if isinstance(instrument, Instrument) else get_instrument(instrument)
    daily = set(_local_dates(load_ts(inst.id, "1D")))
    intraday: set[date] = set()
    for tf in inst.timeframes:
        if is_intraday(tf):
            intraday |= set(_local_dates(load_ts(inst.id, tf)))
    now = clock.utc_now()
    return sorted(d for d in intraday - daily if _closed(inst.exchange, d, now))


def suspicious_bars(df: pd.DataFrame, kind: str) -> pd.Series:
    """True for spike-and-reverse bars: the open gaps more than the kind's limit away from the previous
    close, and the next bar's open gaps back the other way by more than the reversal limit."""
    gap_limit, back_limit = SPIKE_LIMITS[kind]
    gap = df["open"] / df["close"].shift(1) - 1
    back = df["open"].shift(-1) / df["close"] - 1
    return (gap.abs() > gap_limit) & (back.abs() > back_limit) & (np.sign(gap) != np.sign(back))


def _expected_bars(exchange: str, d: date, tf: str) -> int:
    open_, close = get_calendar().session_times(exchange, d)
    return -(-(close - open_) // tf_delta(tf)) if is_intraday(tf) else 1


def _peer_expected(first: date, peers: list[set[date]]) -> set[date]:
    """Days on or after `first` that a strict majority of the peer series covering them have a bar on."""
    spans = [(min(p), max(p), p) for p in peers if p]
    union = set().union(*(p for *_, p in spans)) if spans else set()
    return {
        d
        for d in union
        if d >= first
        and 2 * sum(d in p for *_, p in spans) > sum(lo <= d <= hi for lo, hi, _ in spans)
    }


def bar_count_anomalies(
    ts: pd.Series, tf: str, exchange: str, peers: list[set[date]] | None = None
) -> dict[str, list]:
    """Bar-count problems of one stored series (closed sessions only):
    - partial: [(day, expected, present)] trading days with fewer intraday bars than the calendar expects;
    - missing: days with no bar that are configured special sessions, or that most peer series (same
      market, same tf) have, from this series' first day on;
    - unexpected: days with bars on which the calendar has no session."""
    result: dict[str, list] = {"partial": [], "missing": [], "unexpected": []}
    if ts.empty:
        return result
    cal, now = get_calendar(), clock.utc_now()
    counts = _local_dates(ts).value_counts()
    own, first = set(counts.index), min(counts.index)
    for day, have in sorted(counts.items()):
        if not cal.is_trading_day(exchange, day):
            result["unexpected"].append(day)
        elif is_intraday(tf) and _closed(exchange, day, now):
            expected = _expected_bars(exchange, day, tf)
            if have < expected:
                result["partial"].append((day, expected, int(have)))
    special = {d for d, timing in cal.spec(exchange).special_sessions.items() if timing is not None}
    expected_days = (_peer_expected(first, peers or []) | {d for d in special if d >= first}) - own
    result["missing"] = sorted(d for d in expected_days if _closed(exchange, d, now))
    return result


def _market(exchange: str) -> str:
    return "MCX" if exchange == "MCX" else "NSE/BSE"  # NSE and BSE share sessions and holidays


def _round(value: float) -> float:
    return round(float(value), 4)


def _suspicious_rows(inst: Instrument, tf: str) -> list[dict]:
    if not inst.tradable:
        return []  # INDIAVIX: a volatility index jumps and reverts by nature, so the spike rule doesn't apply
    df = load_candles(inst.id, tf)
    if df.empty:
        return []
    hit = suspicious_bars(df, inst.kind)
    prev_close, next_open = df["close"].shift(1), df["open"].shift(-1)
    return [
        {
            "tf": tf,
            "date": df.at[i, "ts"].tz_convert(IST).date().isoformat(),
            "time": clock.epoch_seconds(df.at[i, "ts"]),
            "prev_close": _round(prev_close[i]),
            "open": _round(df.at[i, "open"]),
            "close": _round(df.at[i, "close"]),
            "next_open": _round(next_open[i]),
            "gap_pct": round(100 * (df.at[i, "open"] / prev_close[i] - 1), 2),
            "back_pct": round(100 * (next_open[i] / df.at[i, "close"] - 1), 2),
        }
        for i in df.index[hit]
    ]


def _days(days: list[date]) -> dict:
    """Count plus the most recent LIST_LIMIT days."""
    return {"count": len(days), "recent": [d.isoformat() for d in days[-LIST_LIMIT:]]}


def build_report() -> dict:
    instruments = load_watchlist()
    stored = {(i.id, tf): load_ts(i.id, tf) for i in instruments for tf in i.timeframes}
    dates = {key: set(_local_dates(ts)) for key, ts in stored.items()}
    per_instrument: dict[str, dict] = {}
    for inst in instruments:
        tfs = [tf for tf in inst.timeframes if not stored[(inst.id, tf)].empty]
        if not tfs:
            continue
        bar_counts = {}
        for tf in tfs:
            peers = [
                dates[(p.id, tf)]
                for p in instruments
                if p.id != inst.id and _market(p.exchange) == _market(inst.exchange) and tf in p.timeframes
            ]
            found = bar_count_anomalies(stored[(inst.id, tf)], tf, inst.exchange, peers)
            bar_counts[tf] = {
                "partial_sessions": {
                    "count": len(found["partial"]),
                    "missing_bars": sum(expected - present for _, expected, present in found["partial"]),
                    "recent": [
                        {"date": d.isoformat(), "expected": expected, "present": present}
                        for d, expected, present in found["partial"][-LIST_LIMIT:]
                    ],
                },
                "missing_sessions": _days(found["missing"]),
                "unexpected_sessions": _days(found["unexpected"]),
            }
        per_instrument[inst.id] = {
            "source": {tf: candle_source(inst.id, tf) for tf in tfs},
            "bars": {tf: len(stored[(inst.id, tf)]) for tf in tfs},
            "suspicious_bars": [row for tf in tfs for row in _suspicious_rows(inst, tf)],
            "missing_daily_sessions": _days(missing_daily_sessions(inst.id)),
            "bar_counts": bar_counts,
        }

    def total(check: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in per_instrument.values():
            for tf, counts in entry["bar_counts"].items():
                out[tf] = out.get(tf, 0) + counts[check]["count"]
        return out

    entries = per_instrument.values()
    return {
        "generated_at": clock.epoch_seconds(clock.utc_now()),
        "summary": {
            "instruments_with_data": len(per_instrument),
            "instruments_without_data": [i.id for i in instruments if i.id not in per_instrument],
            "suspicious_bars": sum(len(e["suspicious_bars"]) for e in entries),
            "missing_daily_sessions": sum(e["missing_daily_sessions"]["count"] for e in entries),
            "partial_sessions": total("partial_sessions"),
            "missing_sessions": total("missing_sessions"),
            "unexpected_sessions": total("unexpected_sessions"),
        },
        "instruments": per_instrument,
    }


def report_path() -> Path:
    return get_settings().data_dir / "quality" / "report.json"


def write_report(report: dict) -> Path:
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".report.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m candly.data.quality", description="Candle data quality.")
    parser.add_argument("--report", action="store_true", help="write data/quality/report.json")
    args = parser.parse_args(argv)
    if not args.report:
        parser.print_help()
        return 2
    setup_logging()
    report = build_report()
    path = write_report(report)
    summary = report["summary"]
    print(f"wrote {path}")
    for key, value in summary.items():
        print(f"{key:<26} {value}")
    for instrument_id, entry in report["instruments"].items():
        for row in entry["suspicious_bars"]:
            print(
                f"suspicious: {instrument_id} {row['tf']} {row['date']} "
                f"gap {row['gap_pct']:+.1f}% then {row['back_pct']:+.1f}%"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
