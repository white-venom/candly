"""Daily futures-proxy returns for tsmom_v1 (config/edge_search_v2.yaml `tsmom`).

Index futures proxy: spot close-to-close return minus 5% a year of carry, accrued per calendar day.

MCX front-month series (Fyers continuous, unadjusted) jump at every contract change. The registered roll
rule replaces the close-to-close return with the open-to-close return on the first trading day of a new
front contract. There is no historical MCX expiry calendar in the repo (data/expiries holds only contracts
listed since 2026-09-23), so the roll days are detected from the series itself. The rule below was fixed
from the pre-holdout series before any strategy number was computed:

- One roll per contract cycle, searched inside a calendar window from the MCX contract specifications:
  GOLD expires on the 5th of Feb/Apr/Jun/Aug/Oct/Dec, SILVER on the 5th of Mar/May/Jul/Sep/Dec (days 1-10
  searched); CRUDEOIL every month around the 19th (days 14-25); NATURALGAS every month a few business
  days before month end (days 19-31).
- Inside a window the roll day is the bar with the strongest contract-change evidence, in this order:
  3 the first bar after a data hole (> HOLE_DAYS calendar days without a bar); 2 open interest at least
  doubles (OI exists from 2024); 1 a volume switch (volume >= 3x the previous weekday bar, whose volume
  was <= 35% of its 20-bar median: the expiring contract dried up and the next one took over); 0 none.
  Ties go to the larger jump. A window with no evidence takes the largest volume jump among the days of
  the month on which the instrument's evidenced rolls fell (CRUDEOIL 17-22, NATURALGAS 24-31 on the
  pre-holdout data): a weak guess, counted separately in the report.

Data repairs (listed as deviations in the report), applied the same way to every strategy and baseline:
- a bar after a data hole uses open-to-close (the hole's days are not traded);
- a one-day off-market print (volume < 10% of its 20-bar median, an opening gap over 5x the 60-bar median
  absolute gap, reversed by at least half on the next bar: a stale expiry-week print or a bar from
  another contract) uses open-to-close on both bars;
- a bar with a price <= 1 (the vendor's floor for the negative April 2020 crude settlement) earns 0, and
  the bar after it uses open-to-close.

The roll calendar is an exogenous input: it reconstructs the exchange's published expiry calendar, which
a trader knows in advance, and it is computed once from the whole pre-holdout series.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from candly.core.calendar import IST

INDEX_CARRY_PER_YEAR = 0.05
HOLE_DAYS = 5
OI_SWITCH = 2.0
VOLUME_JUMP = 3.0
VOLUME_TROUGH = 0.35
VOLUME_MEDIAN_BARS = 20
GAP_MEDIAN_BARS = 60
GLITCH_VOLUME = 0.10
GLITCH_GAP_MULTIPLE = 5.0
INVALID_PRICE = 1.0
MIN_EVIDENCED = 5


@dataclass(frozen=True)
class RollWindow:
    months: tuple[int, ...]
    first_day: int
    last_day: int


ALL_MONTHS = tuple(range(1, 13))
ROLL_WINDOWS: dict[str, RollWindow] = {
    "MCX:GOLD": RollWindow((2, 4, 6, 8, 10, 12), 1, 10),
    "MCX:SILVER": RollWindow((3, 5, 7, 9, 12), 1, 10),
    "MCX:CRUDEOIL": RollWindow(ALL_MONTHS, 14, 25),
    "MCX:NATURALGAS": RollWindow(ALL_MONTHS, 19, 31),
}
EVIDENCE = {3: "data_hole", 2: "oi_switch", 1: "volume_switch", 0: "weak_volume_jump"}


def _local(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST)


def _previous_weekday_volume(df: pd.DataFrame) -> pd.Series:
    """Volume of the latest earlier bar on a weekday: a weekend special session (Muhurat, budget day) is
    short, so the next day's volume jump against it says nothing about a contract change."""
    weekday = _local(df["ts"]).dt.dayofweek < 5
    return df["volume"].where(weekday).shift(1).ffill()


def change_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Per bar: calendar days since the previous bar, OI ratio, volume jump and trough, opening gap."""
    prev_volume = _previous_weekday_volume(df)
    median = prev_volume.rolling(VOLUME_MEDIAN_BARS, min_periods=10).median().shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        jump = df["volume"] / prev_volume
        trough = prev_volume / median
        oi_ratio = df["oi"] / df["oi"].shift(1)
    gap = np.log(df["open"] / df["close"].shift(1))
    return pd.DataFrame(
        {
            "ts": df["ts"],
            "days_since": df["ts"].diff().dt.total_seconds() / 86400.0,
            "oi_ratio": oi_ratio,
            "volume_jump": jump,
            "volume_trough": trough,
            "gap": gap,
        }
    )


def _priority(ev: pd.DataFrame) -> pd.Series:
    hole = ev["days_since"] > HOLE_DAYS
    oi = ev["oi_ratio"] >= OI_SWITCH
    volume = (ev["volume_jump"] >= VOLUME_JUMP) & (ev["volume_trough"] <= VOLUME_TROUGH)
    return pd.Series(np.select([hole, oi, volume], [3, 2, 1], 0), index=ev.index)


def _best(cand: pd.DataFrame) -> pd.DataFrame:
    return cand.sort_values(["window", "priority", "strength"]).groupby("window").tail(1)


def detect_rolls(df: pd.DataFrame, instrument_id: str) -> pd.DataFrame:
    """One detected roll day per contract window: ts, window key, evidence and the evidence values.

    A window without contract-change evidence falls back to the largest volume jump, searched only on the
    days of the month where this instrument's evidenced rolls happened (their min..max day), when there
    are at least MIN_EVIDENCED of them; a window with no bar on those days (the data ends first) has no
    roll."""
    window = ROLL_WINDOWS[instrument_id]
    ev = change_evidence(df)
    local = _local(df["ts"])
    weekday = local.dt.dayofweek < 5
    in_window = local.dt.month.isin(window.months) & local.dt.day.between(window.first_day, window.last_day)
    priority = _priority(ev)
    strength = np.where(priority == 2, ev["oi_ratio"], ev["volume_jump"])
    cand = pd.DataFrame(
        {
            "row": np.arange(len(df)),
            "window": local.dt.strftime("%Y-%m"),
            "day": local.dt.day,
            "priority": priority,
            "strength": np.nan_to_num(np.asarray(strength, float), nan=-np.inf),
        }
    )[in_window & weekday & ev["days_since"].notna()]
    # a window cut short by a data hole rolls on the first bar after the hole
    after = cand.groupby("window")["row"].max() + 1
    holes = after[(after < len(df)) & (ev["days_since"].reindex(after.to_numpy()).to_numpy() > HOLE_DAYS)]
    extra = pd.DataFrame(
        {"row": holes.to_numpy(), "window": holes.index, "day": 0, "priority": 3, "strength": np.inf}
    )
    cand = pd.concat([cand, extra], ignore_index=True)
    best = _best(cand)
    evidenced = best[best["priority"].isin([1, 2])]
    if len(evidenced) >= MIN_EVIDENCED:
        lo, hi = int(evidenced["day"].min()), int(evidenced["day"].max())
        weak = best["priority"] == 0
        core = cand[cand["window"].isin(best.loc[weak, "window"]) & cand["day"].between(lo, hi)]
        best = pd.concat([best[~weak], _best(core)])
    rows = best["row"].to_numpy()
    out = ev.iloc[rows].reset_index(drop=True)
    out.insert(1, "window", best["window"].to_numpy())
    out.insert(2, "evidence", [EVIDENCE[int(p)] for p in best["priority"]])
    return out.sort_values("ts").reset_index(drop=True)


def glitch_rows(df: pd.DataFrame) -> np.ndarray:
    """Rows of one-day switches to another contract: both the odd bar and the bar that switches back."""
    volume_median = df["volume"].shift(1).rolling(VOLUME_MEDIAN_BARS, min_periods=10).median()
    gap = np.log(df["open"] / df["close"].shift(1))
    gap_median = gap.abs().shift(1).rolling(GAP_MEDIAN_BARS, min_periods=20).median()
    nxt = gap.shift(-1)
    odd = (
        (df["volume"] < GLITCH_VOLUME * volume_median)
        & (gap.abs() > GLITCH_GAP_MULTIPLE * gap_median)
        & (np.sign(nxt) == -np.sign(gap))
        & (nxt.abs() >= 0.5 * gap.abs())
    )
    rows = np.flatnonzero(odd.to_numpy())
    return np.union1d(rows, rows + 1)


def invalid_rows(df: pd.DataFrame) -> np.ndarray:
    return np.flatnonzero((df[["open", "high", "low", "close"]] <= INVALID_PRICE).any(axis=1).to_numpy())


@dataclass(frozen=True)
class ReturnAdjustments:
    rolls: pd.DataFrame  # detect_rolls output
    roll_rows: np.ndarray
    weak_roll_rows: np.ndarray  # rolls guessed without contract-change evidence
    repair_rows: np.ndarray  # open-to-close rows that are data repairs, not rolls
    zero_rows: np.ndarray  # invalid bars
    repairs: dict[str, list[str]]  # IST dates of each data repair, by kind


def mcx_adjustments(df: pd.DataFrame, instrument_id: str) -> ReturnAdjustments:
    rolls = detect_rolls(df, instrument_id)
    roll_rows = np.flatnonzero(df["ts"].isin(rolls["ts"]).to_numpy())
    weak = rolls.loc[rolls["evidence"] == EVIDENCE[0], "ts"]
    weak_rows = np.flatnonzero(df["ts"].isin(weak).to_numpy())
    days = df["ts"].diff().dt.total_seconds().to_numpy() / 86400.0
    hole_rows = np.flatnonzero(days > HOLE_DAYS)
    glitches = glitch_rows(df)
    glitches = glitches[glitches < len(df)]
    invalid = invalid_rows(df)
    after_invalid = np.setdiff1d(invalid + 1, invalid)
    after_invalid = after_invalid[after_invalid < len(df)]
    repair = np.union1d(np.union1d(hole_rows, glitches), after_invalid)
    dates = _local(df["ts"]).dt.date.astype(str).to_numpy()

    def on(rows: np.ndarray) -> list[str]:
        return [str(dates[r]) for r in rows]

    return ReturnAdjustments(
        rolls=rolls,
        roll_rows=roll_rows,
        weak_roll_rows=weak_rows,
        repair_rows=np.setdiff1d(repair[repair > 0], invalid),
        zero_rows=invalid,
        repairs={
            "after_data_hole": on(hole_rows),
            "one_day_off_market_print": on(glitches),
            "invalid_price_zeroed": on(invalid),
            "after_invalid_price": on(after_invalid),
        },
    )


def index_futures_returns(df: pd.DataFrame, carry: float = INDEX_CARRY_PER_YEAR) -> pd.Series:
    """Spot close-to-close return minus `carry` a year, accrued over the calendar days since the last bar."""
    days = df["ts"].diff().dt.total_seconds() / 86400.0
    return df["close"] / df["close"].shift(1) - 1.0 - carry * days / 365.0


ROLL_MODES = ("registered", "evidenced_only", "none")


def mcx_futures_returns(
    df: pd.DataFrame, adj: ReturnAdjustments, rolls: str = "registered", zero_invalid: bool = True
) -> pd.Series:
    """Close-to-close returns with open-to-close on roll days and repaired bars. `rolls` picks which roll
    days are adjusted (every detected one, only evidenced ones, or none) for sensitivity checks."""
    if rolls not in ROLL_MODES:
        raise ValueError(f"rolls must be one of {ROLL_MODES}")
    roll_rows = {
        "registered": adj.roll_rows,
        "evidenced_only": np.setdiff1d(adj.roll_rows, adj.weak_roll_rows),
        "none": np.array([], dtype=int),
    }[rolls]
    o2c = np.union1d(roll_rows, adj.repair_rows)
    o2c = np.setdiff1d(o2c[o2c > 0], adj.zero_rows)
    close_to_close = df["close"] / df["close"].shift(1) - 1.0
    open_to_close = df["close"] / df["open"] - 1.0
    out = close_to_close.to_numpy(copy=True)
    out[o2c] = open_to_close.to_numpy()[o2c]
    if zero_invalid:
        out[adj.zero_rows] = 0.0
    return pd.Series(out, index=df.index)
