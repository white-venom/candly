"""Research universe of every NSE equity in the EQ series, from the Fyers NSE_CM symbol master.

CLI: python -m candly.data.universe_build [--out config/universe_nse_eq.yaml] [--force]
The output is a frozen candidate list in the watchlist format (see candly.data.ingest.load_universe);
regenerating it on a later day lists a different set of stocks, so an existing file needs --force.
"""

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from candly.core.instruments import load_watchlist
from candly.core.settings import get_settings
from candly.data.sources import SourceError, fyers
from candly.data.store import replace_file

SEGMENT = "NSE_CM"
NAME = "nse_eq"
# Symbol master CSV (no header): 1 name, 2 exchange instrument type, 5 ISIN, 9 ticker, 13 symbol.
MASTER_COLUMNS = {1: "name", 2: "type", 5: "isin", 9: "ticker", 13: "symbol"}
EQUITY_TYPE, EQUITY_SERIES = 0, "EQ"
# Instrument types as found in the 2026-09-24 NSE_CM master: 2 holds bonds, debentures and SGBs (series
# GB), 5 state development loans (SG), 6 G-secs (GS), 7 T-bills (TB).
TYPE_REASONS = {10: "index", 9: "etf", 8: "mutual_fund", 2: "debt", 5: "debt", 6: "debt", 7: "debt"}
SERIES_REASONS = {"RR": "reit_invit", "IV": "reit_invit", "SM": "sme", "ST": "sme", "SZ": "sme"}
REASON_LABELS = {
    "index": "indices (type 10)",
    "etf": "ETFs (type 9)",
    "mutual_fund": "mutual fund units (type 8)",
    "debt": "bonds, debentures, SGBs, SDLs, G-secs and T-bills (types 2, 5, 6, 7)",
    "reit_invit": "REITs and InvITs (series RR, IV)",
    "sme": "SME listings (series SM, ST, SZ)",
    "other": "other series (not EQ)",
}
FUND_ISIN_PREFIX = "INF"


@dataclass
class Universe:
    kept: pd.DataFrame
    total_rows: int
    excluded: Counter
    other_series: Counter
    master_day: str


def read_master(path: Path) -> pd.DataFrame:
    """name, type, isin, ticker, symbol and series of every row of a downloaded NSE_CM master."""
    try:
        raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False, on_bad_lines="skip")
        table = raw[list(MASTER_COLUMNS)].rename(columns=MASTER_COLUMNS)
    except (ValueError, KeyError) as exc:
        raise SourceError(f"unreadable symbol master {path.name} ({type(exc).__name__}: {exc})") from exc
    table = table.apply(lambda column: column.str.strip())
    table["type"] = pd.to_numeric(table["type"], errors="coerce")
    table["series"] = table["ticker"].str.extract(r"-([A-Z0-9]+)$", expand=False).fillna("")
    return table


def exclusion_reasons(master: pd.DataFrame) -> pd.Series:
    """Why each row is left out (a REASON_LABELS key), or NaN for an EQ-series equity that is kept.
    The instrument type decides first, then the series."""
    reason = master["type"].map(TYPE_REASONS)
    reason = reason.fillna(master["series"].map(SERIES_REASONS))
    not_equity = (master["type"] != EQUITY_TYPE) | (master["series"] != EQUITY_SERIES)
    reason = reason.mask(reason.isna() & not_equity, "other")
    # Fallback only: a fund ISIN on an equity row would be an ETF the type column missed.
    return reason.mask(reason.isna() & master["isin"].str.startswith(FUND_ISIN_PREFIX), "etf")


def build_universe(path: Path) -> Universe:
    master = read_master(path)
    reasons = exclusion_reasons(master)
    kept = master[reasons.isna()].sort_values("symbol").reset_index(drop=True)
    return Universe(
        kept=kept,
        total_rows=len(master),
        excluded=Counter(reasons.dropna()),
        other_series=Counter(master.loc[reasons == "other", "series"]),
        master_day=fyers.master_day(path),
    )


def _line(row: dict) -> str:
    """One YAML flow mapping per stock; JSON strings are valid YAML double-quoted scalars."""
    q = json.dumps
    return (
        f"  - {{id: {q('NSE:' + row['symbol'])}, symbol: {q(row['symbol'])}, name: {q(row['name'])}, "
        f"isin: {q(row['isin'])}, kind: equity, sources: {{fyers: {q(row['ticker'])}}}}}"
    )


def render(universe: Universe) -> str:
    kept, day = universe.kept, universe.master_day
    excluded = "\n".join(
        f"#   {universe.excluded.get(reason, 0):>5}  {label}" for reason, label in REASON_LABELS.items()
    )
    others = ", ".join(f"{series} {count}" for series, count in universe.other_series.most_common())
    header = f"""\
# Every NSE equity in the EQ series: the candidate list of the point-in-time xs_v2 test
#   (config/edge_search_v3.yaml). A research universe only; these are NOT on the dashboard watchlist.
# FROZEN {day}. Source: the Fyers {SEGMENT} symbol master ({fyers.SYMBOL_MASTER_URL.format(segment=SEGMENT)}),
#   downloaded {day} (IST), written by python -m candly.data.universe_build. Do not regenerate it: a later
#   master lists a different set of stocks.
# Filter: exchange instrument type {EQUITY_TYPE} (equity) and series {EQUITY_SERIES}.
#   {len(kept)} of {universe.total_rows} rows kept. Excluded, by instrument type first and then by series:
{excluded}
#   Other series: {others or "none"}.
#   Fallback: an equity row with a fund ISIN ({FUND_ISIN_PREFIX}...) would count as an ETF.
# SURVIVORSHIP BIAS: stocks delisted or merged before {day} are missing (Fyers serves listed symbols only),
#   and so are stocks that are listed today in another series (e.g. BE). Results on it are an upper bound.
# No industry field: the master has none, so xs_panel.universe_members reads every stock as "unknown".
# Candles go to the same store as the watchlist (data/candles/NSE/1D/<SYMBOL>.parquet).
# Backfill: python -m candly.data.universe_backfill --universe {NAME}   (progress: add --status)
# Fields: id and symbol (NSE), name and isin from the master, sources.fyers = the master's ticker.

defaults:
  timeframes: [1D]

instruments:
"""
    return header + "\n".join(_line(row) for row in kept.to_dict("records")) + "\n"


def write_universe(universe: Universe, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(universe))
        load_watchlist(Path(tmp))  # it must load as a universe before it replaces anything
        replace_file(tmp, out)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m candly.data.universe_build",
        description="Write the every-NSE-equity research universe from today's Fyers NSE_CM symbol master.",
    )
    parser.add_argument("--out", type=Path, help=f"default: config/universe_{NAME}.yaml")
    parser.add_argument("--force", action="store_true", help="overwrite an existing (frozen) universe file")
    args = parser.parse_args(argv)
    out = args.out or get_settings().config_dir / f"universe_{NAME}.yaml"
    if out.exists() and not args.force:
        print(f"error: {out} exists and is frozen; pass --force to overwrite it", file=sys.stderr)
        return 2
    try:
        fyers.symbol_master(SEGMENT)  # downloads today's master to master_path (or reuses today's copy)
        universe = build_universe(fyers.master_path(SEGMENT))
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_universe(universe, out)
    print(f"{SEGMENT} master downloaded {universe.master_day}: {universe.total_rows} rows")
    print(f"kept {len(universe.kept)} EQ-series equities -> {out}")
    for reason, label in REASON_LABELS.items():
        print(f"excluded {universe.excluded.get(reason, 0):>5}  {label}")
    print("other series: " + ", ".join(f"{s} {n}" for s, n in universe.other_series.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
