"""Index option-chain snapshots from Fyers. Logged from 2026-09-24: this history can't be downloaded later.

Per underlying and IST date (append-only Parquet, atomic writes):
- data/options/{UNDERLYING}/{YYYY-MM-DD}.parquet: one row per snapshot, expiry and strike (CHAIN_COLUMNS)
- data/options/{UNDERLYING}/summary/{YYYY-MM-DD}.parquet: one row per snapshot and expiry (SUMMARY_COLUMNS)

`ts` is our fetch time (UTC), not an exchange timestamp. Summary metrics cover the logged strikes only
(ATM ± STRIKES_EACH_SIDE), so PCR and max pain are over that window, not the whole chain.
Fyers reports one IV per strike (the CE and PE rows carry the same value).

CLI: python -m candly.data.options   (one snapshot of every underlying now, whatever the market hours)
"""

import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from candly.core.calendar import get_calendar
from candly.core.log import setup_logging
from candly.core.settings import get_settings
from candly.data import clock
from candly.data.sources import SourceError, fyers
from candly.data.store import replace_file, series_lock

log = logging.getLogger(__name__)

# name -> (exchange, Fyers underlying symbol)
UNDERLYINGS = {
    "NIFTY": ("NSE", "NSE:NIFTY50-INDEX"),
    "BANKNIFTY": ("NSE", "NSE:NIFTYBANK-INDEX"),
    "SENSEX": ("BSE", "BSE:SENSEX-INDEX"),
}
STRIKES_EACH_SIDE = 15
N_EXPIRIES = 2
SKEW_DELTA = 0.25
SKEW_DELTA_TOLERANCE = 0.05  # no skew value when the logged strikes don't reach 25 delta

_SIDE_FIELDS = {"ltp": "ltp", "bid": "bid", "ask": "ask", "oi": "oi", "oich": "oi_chg", "volume": "volume"}
_SIDE_COLUMNS = [*_SIDE_FIELDS.values(), "iv", "delta"]
_QUOTES = ("bid", "ask", "iv")  # Fyers sends 0 for "no quote" / "no IV"; stored as NaN
KEY_COLUMNS = ["ts", "underlying", "expiry"]
CHAIN_COLUMNS = [
    *KEY_COLUMNS, "spot", "future", "strike",
    *[f"{side}_{name}" for side in ("ce", "pe") for name in _SIDE_COLUMNS],
]
SUMMARY_COLUMNS = [
    *KEY_COLUMNS, "spot", "future", "n_strikes", "atm_strike", "atm_iv",
    "ce_oi", "pe_oi", "pcr_oi", "pcr_volume", "ce_oi_chg", "pe_oi_chg", "max_pain", "skew_25d",
]


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def options_dir(underlying: str) -> Path:
    return get_settings().data_dir / "options" / underlying


def chain_path(underlying: str, day: date) -> Path:
    return options_dir(underlying) / f"{day.isoformat()}.parquet"


def summary_path(underlying: str, day: date) -> Path:
    return options_dir(underlying) / "summary" / f"{day.isoformat()}.parquet"


def nearest_expiries(expiry_data: list[dict], today: date, n: int = N_EXPIRIES) -> list[tuple[int, date]]:
    """(epoch, IST date) of the `n` nearest listed expiries on or after `today`."""
    found = set()
    for item in expiry_data or []:
        epoch = _num(item.get("expiry"))
        if np.isnan(epoch):
            continue
        day = get_calendar().local_date(pd.Timestamp(int(epoch), unit="s", tz="UTC"))
        if day >= today:
            found.add((int(epoch), day))
    return sorted(found)[:n]


def chain_frame(data: dict, ts: pd.Timestamp, underlying: str, expiry: date) -> pd.DataFrame:
    """One row per strike from an options-chain-v3 `data` payload, CE and PE side by side."""
    rows = data.get("optionsChain") or []
    spot_row = next((r for r in rows if not r.get("option_type")), {})
    strikes: dict[float, dict] = {}
    for r in rows:
        side = r.get("option_type")
        strike = _num(r.get("strike_price"))
        if side not in ("CE", "PE") or np.isnan(strike):
            continue
        greeks = r.get("greeks") or {}
        prefix = side.lower()
        record = strikes.setdefault(strike, {})
        for field, name in _SIDE_FIELDS.items():
            record[f"{prefix}_{name}"] = _num(r.get(field))
        record[f"{prefix}_iv"] = _num(greeks.get("iv"))
        record[f"{prefix}_delta"] = _num(greeks.get("delta"))
    frame = pd.DataFrame.from_dict(strikes, orient="index").rename_axis("strike").reset_index()
    frame = frame.reindex(columns=CHAIN_COLUMNS)
    frame["ts"] = pd.Series([ts] * len(frame), dtype="datetime64[ns, UTC]")
    frame["underlying"] = underlying
    frame["expiry"] = expiry
    frame["spot"] = _num(spot_row.get("ltp"))
    frame["future"] = _num(spot_row.get("fp"))
    numeric = CHAIN_COLUMNS[len(KEY_COLUMNS):]
    frame[numeric] = frame[numeric].astype("float64")
    for side in ("ce", "pe"):
        for name in _QUOTES:
            column = f"{side}_{name}"
            frame.loc[frame[column] <= 0, column] = np.nan
    return frame.sort_values("strike", ignore_index=True)


def max_pain(strikes: np.ndarray, ce_oi: np.ndarray, pe_oi: np.ndarray) -> float:
    """The expiry price, among the strikes, at which option holders would collect the least."""
    ce_oi, pe_oi = np.nan_to_num(ce_oi), np.nan_to_num(pe_oi)
    if len(strikes) == 0 or ce_oi.sum() + pe_oi.sum() <= 0:
        return np.nan
    settle = strikes[:, None]
    payout = (ce_oi * np.maximum(settle - strikes, 0) + pe_oi * np.maximum(strikes - settle, 0)).sum(axis=1)
    return float(strikes[int(np.argmin(payout))])


def _at_delta(chain: pd.DataFrame, side: str, target: float) -> float:
    """IV of the `side` option whose delta is nearest `target`, or NaN if none is within tolerance."""
    miss = (chain[f"{side}_delta"] - target).abs()
    if miss.isna().all() or miss.min() > SKEW_DELTA_TOLERANCE:
        return np.nan
    return float(chain.loc[miss.idxmin(), f"{side}_iv"])


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else np.nan


def summarize(chain: pd.DataFrame) -> dict:
    """Derived metrics of one snapshot of one expiry (the rows of chain_frame)."""
    first = chain.iloc[0]
    out = {name: first[name] for name in (*KEY_COLUMNS, "spot", "future")}
    strikes = chain["strike"].to_numpy()
    atm_row = (chain["strike"] - first["spot"]).abs().idxmin() if not np.isnan(first["spot"]) else None
    ce_oi, pe_oi = chain["ce_oi"].sum(min_count=1), chain["pe_oi"].sum(min_count=1)
    out.update(
        n_strikes=len(chain),
        atm_strike=np.nan if atm_row is None else float(chain.loc[atm_row, "strike"]),
        atm_iv=np.nan if atm_row is None else chain.loc[atm_row, ["ce_iv", "pe_iv"]].mean(),
        ce_oi=ce_oi,
        pe_oi=pe_oi,
        pcr_oi=_ratio(pe_oi, ce_oi),
        pcr_volume=_ratio(chain["pe_volume"].sum(min_count=1), chain["ce_volume"].sum(min_count=1)),
        ce_oi_chg=chain["ce_oi_chg"].sum(min_count=1),
        pe_oi_chg=chain["pe_oi_chg"].sum(min_count=1),
        max_pain=max_pain(strikes, chain["ce_oi"].to_numpy(), chain["pe_oi"].to_numpy()),
        skew_25d=_at_delta(chain, "pe", -SKEW_DELTA) - _at_delta(chain, "ce", SKEW_DELTA),
    )
    return out


def summary_frame(chain: pd.DataFrame) -> pd.DataFrame:
    rows = [summarize(group) for _, group in chain.groupby(["ts", "expiry"], sort=True)]
    frame = pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
    numeric = SUMMARY_COLUMNS[len(KEY_COLUMNS):]
    frame[numeric] = frame[numeric].astype("float64")
    return frame


def _read(path: Path) -> pd.DataFrame | None:
    try:
        return pq.read_table(path).to_pandas()
    except FileNotFoundError:
        return None


def append_rows(path: Path, frame: pd.DataFrame) -> int:
    """Append rows whose (ts, underlying, expiry) snapshot isn't stored yet; stored rows are never changed.
    Returns the number of rows written."""
    if frame.empty:
        return 0
    with series_lock(path):
        existing = _read(path)
        if existing is not None:
            stored = set(existing[KEY_COLUMNS].itertuples(index=False, name=None))
            new = [key not in stored for key in frame[KEY_COLUMNS].itertuples(index=False, name=None)]
            frame = frame[new]
            if frame.empty:
                return 0
            frame = pd.concat([existing, frame], ignore_index=True)
            added = len(frame) - len(existing)
        else:
            added = len(frame)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                pq.write_table(table, fh)
                fh.flush()
                os.fsync(fh.fileno())
            replace_file(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return added


def snapshot(underlying: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and store one snapshot of the nearest N_EXPIRIES expiries. Returns (chain rows, summary rows)."""
    _, symbol = UNDERLYINGS[underlying]
    ts = clock.utc_now()
    day = get_calendar().local_date(ts)
    listing = fyers.option_chain(symbol, 1)
    expiries = nearest_expiries(listing.get("expiryData") or [], day)
    if not expiries:
        raise fyers.FyersError(f"Fyers listed no current expiry for {symbol}")
    frames = []
    for epoch, expiry in expiries:
        frame = chain_frame(fyers.option_chain(symbol, STRIKES_EACH_SIDE, epoch), ts, underlying, expiry)
        if frame.empty:
            log.warning("%s %s: the option chain came back empty", underlying, expiry)
        frames.append(frame)
    chain = pd.concat(frames, ignore_index=True)
    summary = summary_frame(chain)
    append_rows(chain_path(underlying, day), chain)
    append_rows(summary_path(underlying, day), summary)
    return chain, summary


def take_snapshots(underlyings: list[str] | None = None) -> dict[str, int]:
    """Snapshot each underlying; a failure is logged and skipped. Returns {underlying: chain rows stored}.
    Raises FyersNotConnected at once, since every other underlying would fail the same way."""
    counts: dict[str, int] = {}
    for name in underlyings or list(UNDERLYINGS):
        try:
            chain, _ = snapshot(name)
        except fyers.FyersNotConnected:
            raise
        except (SourceError, ValueError, OSError) as exc:
            log.warning("%s option snapshot skipped: %s", name, exc)
            continue
        counts[name] = len(chain)
    return counts


def load_chain(underlying: str, day: date) -> pd.DataFrame:
    frame = _read(chain_path(underlying, day))
    return pd.DataFrame(columns=CHAIN_COLUMNS) if frame is None else frame


def load_summary(underlying: str) -> pd.DataFrame:
    """Every stored summary row of one underlying, oldest first."""
    paths = sorted((options_dir(underlying) / "summary").glob("*.parquet"))
    frames = [pq.read_table(path).to_pandas() for path in paths]
    if not frames:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(["ts", "expiry"], ignore_index=True)


def main() -> int:
    setup_logging()
    try:
        counts = take_snapshots()
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    day = get_calendar().local_date(clock.utc_now())
    for name in UNDERLYINGS:
        summary = load_summary(name)
        latest = summary[summary["ts"] == summary["ts"].max()] if not summary.empty else summary
        print(f"\n{name}: {counts.get(name, 0)} chain rows this snapshot; stored today: "
              f"{len(load_chain(name, day))} chain rows")
        for row in latest.itertuples(index=False):
            print(
                f"  {row.expiry}  spot {row.spot:.2f}  fut {row.future:.2f}  strikes {row.n_strikes:.0f}  "
                f"ATM {row.atm_strike:.0f} IV {row.atm_iv:.2f}  "
                f"PCR oi {row.pcr_oi:.3f} vol {row.pcr_volume:.3f}  "
                f"max pain {row.max_pain:.0f}  skew25d {row.skew_25d:.2f}  "
                f"dOI CE {row.ce_oi_chg:,.0f} PE {row.pe_oi_chg:,.0f}"
            )
    return 0 if len(counts) == len(UNDERLYINGS) else 1


if __name__ == "__main__":
    sys.exit(main())
