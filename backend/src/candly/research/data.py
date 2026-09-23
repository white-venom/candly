"""Research data access with the holdout lock (config/research.yaml: holdout.start)."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from candly.core.schema import validate_candles
from candly.research.config import load_research_config

CandleLoader = Callable[..., pd.DataFrame]


def default_loader() -> CandleLoader:
    from candly.data.store import load_candles

    return load_candles


def load_research_candles(
    instrument_id: str, tf: str, allow_holdout: bool = False, load: CandleLoader | None = None
) -> pd.DataFrame:
    """Closed candles for research. Bars on or after the holdout start are removed unless
    `allow_holdout=True`, which only an official go/no-go run may pass.

    `load` has the signature of `candly.data.store.load_candles(instrument_id, tf, start=None, end=None)`.
    """
    load = load or default_loader()
    cutoff = None if allow_holdout else load_research_config().holdout_start_utc
    df = load(instrument_id, tf, start=None, end=cutoff)
    df = validate_candles(df)
    if cutoff is not None:
        df = df[df["ts"] < cutoff].reset_index(drop=True)
    return df
