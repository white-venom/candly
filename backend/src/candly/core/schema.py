import pandas as pd

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
_FLOAT_COLUMNS = [*PRICE_COLUMNS, "volume", "oi"]


class CandleSchemaError(ValueError):
    pass


def empty_candles() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="float64") for c in _FLOAT_COLUMNS})
    frame.insert(0, "ts", pd.Series(dtype="datetime64[ns, UTC]"))
    return frame


def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in CANDLE_COLUMNS if c not in df.columns]
    if missing:
        raise CandleSchemaError(f"missing columns: {missing}")
    out = df[CANDLE_COLUMNS].copy()
    if not isinstance(out["ts"].dtype, pd.DatetimeTZDtype):
        raise CandleSchemaError("ts must be timezone-aware (UTC)")
    out["ts"] = out["ts"].dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    out[_FLOAT_COLUMNS] = out[_FLOAT_COLUMNS].astype("float64")
    if out[PRICE_COLUMNS].isna().any().any():
        raise CandleSchemaError("prices contain NaN")
    if out["ts"].duplicated().any():
        raise CandleSchemaError("duplicate timestamps")
    if not out["ts"].is_monotonic_increasing:
        raise CandleSchemaError("timestamps are not sorted ascending")
    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    bad = (out["high"] < body_high) | (out["low"] > body_low)
    if bad.any():
        raise CandleSchemaError(f"{int(bad.sum())} bars violate low <= open/close <= high")
    if (out["volume"] < 0).any():
        raise CandleSchemaError("negative volume")
    return out.reset_index(drop=True)
