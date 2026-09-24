"""Long-only top-decile portfolios for xs_v1 / pv2_xs (config/edge_search.yaml `cross_section.portfolio`).

At each rebalance the candidates are the stocks eligible on the signal day (the session before the entry)
that have a bar on the entry day. A strategy buys the top decile of its scores (ceil(10% of candidates),
ties broken by universe order) in equal weights at the entry day's open and holds them, without trading,
until the next rebalance day's open (the last traded close before it when a stock has no bar that day).
The benchmark is the equal-weight portfolio of all candidates over the same period, gross of costs (a
frictionless index, which is conservative for the strategy).

Costs: before each rebalance the drifted old weights w- move to the new equal weights w+. One-way turnover
is sum|w+ - w-| / 2 (the first rebalance buys from cash: 0.5), and each unit of one-way turnover pays one
equity-delivery round trip from config/costs.yaml. The final liquidation pays another half round trip.
Net period return = (1 - cost) x (1 + gross) - 1.

Rank IC of a period: Spearman correlation between the scores and the candidates' holding-period returns.
Weekly marks: the portfolio (net) and benchmark wealth at the open of the first trading day of every week
inside the evaluated span, for the weekly hit rate.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from candly.research.xs_panel import Panel, first_trading_days

TOP_SHARE = 0.10
MIN_CANDIDATES = 10


@dataclass(frozen=True)
class PeriodBook:
    """Everything a strategy needs about one holding period; shared by every strategy."""

    signal: int
    entry: int
    exit: int
    rows: np.ndarray  # rows of the panel frame (signal day, candidate)
    stocks: np.ndarray  # panel column positions of the candidates
    rel_exit: np.ndarray  # exit mark / entry open, per candidate
    week_marks: np.ndarray  # calendar positions of week starts strictly inside (entry, exit)
    rel_marks: np.ndarray  # (len(week_marks), candidates): open mark / entry open
    entry_is_week_start: bool
    exit_is_week_start: bool


def top_count(n: int) -> int:
    return max(1, math.ceil(TOP_SHARE * n))


def select_top(scores: np.ndarray, n_top: int) -> np.ndarray:
    """Positions of the n_top highest scores; ties keep the earlier position."""
    return np.argsort(-np.asarray(scores, float), kind="stable")[:n_top]


def rank_ic(scores, returns) -> float:
    a, b = rankdata(scores), rankdata(returns)
    if a.size < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def open_marks(p: Panel) -> np.ndarray:
    """Price at each day's open: the open when the stock trades, else the last traded close before it."""
    return p.open.where(p.traded, p.close.ffill().shift(1)).to_numpy(dtype="float64")


def build_books(p: Panel, frame: pd.DataFrame, periods: pd.DataFrame) -> list[PeriodBook]:
    """One PeriodBook per rebalance period. Periods must be contiguous and each needs MIN_CANDIDATES."""
    opens = p.open.to_numpy(dtype="float64")
    marks = open_marks(p)
    week_starts = first_trading_days(p.dates, "weekly_first_trading_day")
    t = frame["t"].to_numpy()
    stock = frame["stock"].to_numpy()
    signals = periods["signal"].to_numpy()
    first_row = np.searchsorted(t, signals, side="left")
    last_row = np.searchsorted(t, signals, side="right")
    books = []
    for (signal, entry, exit_), lo, hi in zip(
        periods[["signal", "entry", "exit"]].to_numpy(), first_row, last_row, strict=True
    ):
        if books and books[-1].exit != entry:
            raise ValueError("rebalance periods must be contiguous")
        rows = np.arange(lo, hi)
        cols = stock[rows]
        entry_open = opens[entry, cols]
        tradable = np.isfinite(entry_open) & np.isfinite(marks[exit_, cols])
        rows, cols, entry_open = rows[tradable], cols[tradable], entry_open[tradable]
        if rows.size < MIN_CANDIDATES:
            raise ValueError(f"only {rows.size} candidates for the period entering at position {entry}")
        inner = week_starts[(week_starts > entry) & (week_starts < exit_)]
        books.append(
            PeriodBook(
                signal=int(signal),
                entry=int(entry),
                exit=int(exit_),
                rows=rows,
                stocks=cols,
                rel_exit=marks[exit_, cols] / entry_open,
                week_marks=inner,
                rel_marks=marks[np.ix_(inner, cols)] / entry_open,
                entry_is_week_start=bool(np.isin(entry, week_starts)),
                exit_is_week_start=bool(np.isin(exit_, week_starts)),
            )
        )
    return books


@dataclass
class Simulation:
    entry: np.ndarray
    exit: np.ndarray
    n_candidates: np.ndarray
    n_top: np.ndarray
    ic: np.ndarray
    gross: np.ndarray
    bench: np.ndarray
    turnover: np.ndarray
    cost: np.ndarray
    net: np.ndarray
    week_pos: np.ndarray  # calendar position of each weekly mark
    week_period: np.ndarray  # period whose holding starts at or before the mark
    week_wealth: np.ndarray
    week_bench: np.ndarray
    held_rows: list[np.ndarray]  # panel-frame rows of each period's holdings

    @property
    def excess(self) -> np.ndarray:
        return self.net - self.bench

    @property
    def excess_gross(self) -> np.ndarray:
        return self.gross - self.bench

    def weekly(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(portfolio net return, benchmark return, period of the week's first mark) per week."""
        port = self.week_wealth[1:] / self.week_wealth[:-1] - 1.0
        bench = self.week_bench[1:] / self.week_bench[:-1] - 1.0
        return port, bench, self.week_period[:-1]


def simulate(books: list[PeriodBook], scores: np.ndarray, n_stocks: int, cost_rt: float) -> Simulation:
    """Run one strategy over contiguous periods; `scores` is aligned with the rows of the panel frame."""
    scores = np.asarray(scores, float)
    k = len(books)
    out = {name: np.full(k, np.nan) for name in ("ic", "gross", "bench", "turnover", "cost", "net")}
    n_candidates, n_top = np.zeros(k, dtype=np.int64), np.zeros(k, dtype=np.int64)
    marks: list[tuple[int, int, float, float]] = []
    held_rows = []
    held = np.zeros(n_stocks)
    wealth = bench_wealth = 1.0
    if k and books[0].entry_is_week_start:
        marks.append((books[0].entry, 0, wealth, bench_wealth))
    for i, book in enumerate(books):
        s = scores[book.rows]
        if not np.isfinite(s).all():
            raise ValueError(f"missing scores on signal day {book.signal}")
        top = select_top(s, top_count(s.size))
        target = np.zeros(n_stocks)
        target[book.stocks[top]] = 1.0 / top.size
        turnover = 0.5 * float(np.abs(target - held).sum())
        cost = cost_rt * (turnover + (0.5 if i == k - 1 else 0.0))
        gross = float(book.rel_exit[top].mean()) - 1.0
        bench = float(book.rel_exit.mean()) - 1.0
        net = (1.0 - cost) * (1.0 + gross) - 1.0
        for j, pos in enumerate(book.week_marks):
            marks.append(
                (
                    int(pos),
                    i,
                    wealth * (1.0 - cost) * float(book.rel_marks[j, top].mean()),
                    bench_wealth * float(book.rel_marks[j].mean()),
                )
            )
        wealth *= 1.0 + net
        bench_wealth *= 1.0 + bench
        if book.exit_is_week_start:
            marks.append((book.exit, i + 1, wealth, bench_wealth))
        drift = book.rel_exit[top]
        held = np.zeros(n_stocks)
        held[book.stocks[top]] = drift / drift.sum()
        out["ic"][i] = rank_ic(s, book.rel_exit - 1.0)
        out["gross"][i], out["bench"][i], out["net"][i] = gross, bench, net
        out["turnover"][i], out["cost"][i] = turnover, cost
        n_candidates[i], n_top[i] = s.size, top.size
        held_rows.append(book.rows[top])
    pos, period, w, wb = (np.asarray(x) for x in zip(*marks, strict=True)) if marks else ([],) * 4
    return Simulation(
        entry=np.array([b.entry for b in books], dtype=np.int64),
        exit=np.array([b.exit for b in books], dtype=np.int64),
        n_candidates=n_candidates,
        n_top=n_top,
        week_pos=np.asarray(pos, dtype=np.int64),
        week_period=np.minimum(np.asarray(period, dtype=np.int64), max(k - 1, 0)),
        week_wealth=np.asarray(w, dtype=float),
        week_bench=np.asarray(wb, dtype=float),
        held_rows=held_rows,
        **out,
    )


def restrict_books(books: list[PeriodBook], keep_row: np.ndarray) -> list[PeriodBook]:
    """The same periods with only the candidates whose panel-frame row passes `keep_row`."""
    out = []
    for b in books:
        keep = np.asarray(keep_row, bool)[b.rows]
        if keep.sum() < MIN_CANDIDATES:
            raise ValueError(f"only {int(keep.sum())} candidates left for the period entering at {b.entry}")
        out.append(
            dataclasses.replace(
                b,
                rows=b.rows[keep],
                stocks=b.stocks[keep],
                rel_exit=b.rel_exit[keep],
                rel_marks=b.rel_marks[:, keep],
            )
        )
    return out
