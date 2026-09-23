from datetime import timedelta

TIMEFRAMES: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1D": timedelta(days=1),
}
INTRADAY: tuple[str, ...] = ("5m", "15m", "1h")


def validate_tf(tf: str) -> str:
    if tf not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {tf!r}; expected one of {list(TIMEFRAMES)}")
    return tf


def is_intraday(tf: str) -> bool:
    return validate_tf(tf) in INTRADAY


def tf_delta(tf: str) -> timedelta:
    return TIMEFRAMES[validate_tf(tf)]
