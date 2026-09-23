"""Hand-made scorecards carrying only what the live validated-bucket gate reads."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from candly.research.config import load_research_config
from candly.research.scorecard import (
    ROW_COLUMNS,
    VALIDATION_BASE_COLUMNS,
    Scorecard,
    ScorecardMeta,
    validation_columns,
)


def validation_card(events: pd.DataFrame, base: pd.DataFrame, exchange: str | None = "NSE") -> Scorecard:
    cfg = load_research_config()
    meta = ScorecardMeta(
        tf="1D",
        exchange=exchange,
        built_at=None,
        train_end=cfg.train_end_for("1D").isoformat(),
        holdout_start=cfg.holdout_start.isoformat(),
        n_tests=0,
        fdr_alpha=cfg.fdr_alpha,
        horizons=list(cfg.horizons),
    )
    return Scorecard(meta, pd.DataFrame(columns=ROW_COLUMNS), None, events, base)


def bucket_events(
    ups: np.ndarray,
    instrument: str = "NSE:RELIANCE",
    pattern: str = "hammer",
    trend: str = "down",
    window_days: int = 1,
    first: str = "2020-01-01",
    direction: str = "bullish",
) -> pd.DataFrame:
    """Validation events, one a day from `first`, each with an outcome window of `window_days` days,
    closing up where `ups` is 1 and down elsewhere, at every horizon."""
    cfg = load_research_config()
    ups = np.asarray(ups, dtype=float)
    start = pd.date_range(first, periods=len(ups), freq="D", tz="UTC").as_unit("ns").asi8
    end = start + window_days * 86_400 * 10**9
    frame = {"instrument": instrument, "pattern": pattern, "direction": direction, "start": start}
    frame |= {"trend": trend, "vol_regime": "normal", "expiry": "no"}
    for h in cfg.horizons:
        frame |= {f"end_{h}": end, f"up_{h}": ups, f"down_{h}": 1.0 - ups}
    return pd.DataFrame(frame)[validation_columns(cfg)]


def bucket_base(instruments: Iterable[str], base_up: float, trend: str = "down") -> pd.DataFrame:
    """Validation base rates: `base_up` up and 1 - `base_up` down, in "all" and the trend bucket."""
    cfg = load_research_config()
    rows = [
        (i, ctx, h, base_up, 1.0 - base_up)
        for i in instruments
        for ctx in ("all", f"trend={trend}")
        for h in cfg.horizons
    ]
    return pd.DataFrame(rows, columns=VALIDATION_BASE_COLUMNS)


def gate_kwargs() -> dict:
    cfg = load_research_config()
    return {"min_clusters": cfg.validation_min_clusters, "max_p": cfg.validation_max_p}
