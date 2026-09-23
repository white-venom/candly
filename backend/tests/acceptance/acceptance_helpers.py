"""Shared helpers for acceptance tests: real stored data and independent (non-candly) formulas."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from candly.core.instruments import load_watchlist
from candly.core.schema import CANDLE_COLUMNS
from candly.core.settings import REPO_ROOT

REAL_DATA = REPO_ROOT / "data"
REAL_CANDLES = REAL_DATA / "candles"
API_BASE = "http://127.0.0.1:8000"
IST = "Asia/Kolkata"


def real_path(instrument_id: str, tf: str) -> Path:
    exchange, symbol = instrument_id.split(":")
    return REAL_CANDLES / exchange / tf / f"{symbol}.parquet"


def read_real(instrument_id: str, tf: str) -> pd.DataFrame | None:
    path = real_path(instrument_id, tf)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df[CANDLE_COLUMNS].sort_values("ts").reset_index(drop=True)


def stored_ids(
    tf: str, exchanges: tuple[str, ...] = ("NSE", "BSE"), kinds: tuple[str, ...] | None = None
) -> list[str]:
    return [
        inst.id
        for inst in load_watchlist()
        if inst.exchange in exchanges
        and tf in inst.timeframes
        and (kinds is None or inst.kind in kinds)
        and real_path(inst.id, tf).exists()
    ]


def local(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(IST)


# ---- secret scanning: reports names only, never values

SECRET_PATTERNS = [
    re.compile(
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"
    ),  # JWT (Fyers access token)
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.:]{16,}"),
    re.compile(r"(?i)auth_code=[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r"(?i)access_token[\"'=: ]+[A-Za-z0-9_\-\.]{12,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
]


def env_secret_values() -> dict[str, str]:
    """.env values of 6+ characters, keyed by name. Callers must never print the values."""
    env = REPO_ROOT / ".env"
    if not env.exists():
        return {}
    out = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip("'\"")
        if sep and not key.strip().startswith("#") and len(value) >= 6 and not value.startswith("http"):
            out[key.strip()] = value
    return out


def leaks(text: str) -> list[str]:
    """What leaked into `text`, as pattern indexes or .env key names; never the value itself."""
    found = [f"pattern#{i}" for i, rx in enumerate(SECRET_PATTERNS) if rx.search(text)]
    return found + [f"env:{k}" for k, v in env_secret_values().items() if v in text]


# ---- independent reference formulas (TradingView-style SMA seeds), deliberately not candly code


def rma(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    start = int(np.flatnonzero(~np.isnan(x))[0])
    out[start + n - 1] = np.mean(x[start : start + n])
    for i in range(start + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def ema_sma_seed(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    start = int(np.flatnonzero(~np.isnan(x))[0])
    out[start + n - 1] = np.mean(x[start : start + n])
    alpha = 2.0 / (n + 1)
    for i in range(start + n, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def true_range(df: pd.DataFrame) -> np.ndarray:
    prev = df["close"].shift(1)
    tr = np.maximum(df["high"] - df["low"], np.maximum((df["high"] - prev).abs(), (df["low"] - prev).abs()))
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr.to_numpy()


def ref_atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    return rma(true_range(df), n)


def ref_rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(np.asarray(close, dtype=float), prepend=np.nan)
    gain, loss = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    gain[0] = loss[0] = np.nan
    ag, al = rma(gain, n), rma(loss, n)
    return 100.0 - 100.0 / (1.0 + ag / al)


def ref_macd(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    line = ema_sma_seed(close, 12) - ema_sma_seed(close, 26)
    return line, ema_sma_seed(line, 9)


def ref_supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    a = rma(true_range(df), n)
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    c = df["close"].to_numpy()
    bu, bl = hl2 + mult * a, hl2 - mult * a
    fu, fl = np.full(len(c), np.nan), np.full(len(c), np.nan)
    line, direction = np.full(len(c), np.nan), np.zeros(len(c))
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fu[i - 1]):
            fu[i], fl[i] = bu[i], bl[i]
            direction[i] = 1 if c[i] > fu[i] else -1
        else:
            fu[i] = bu[i] if (bu[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = bl[i] if (bl[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
            if direction[i - 1] < 0:
                direction[i] = 1 if c[i] > fu[i] else -1
            else:
                direction[i] = -1 if c[i] < fl[i] else 1
        line[i] = fl[i] if direction[i] > 0 else fu[i]
    return line, direction


def ref_floor_levels(h: float, lo: float, c: float) -> dict[str, float]:
    p = (h + lo + c) / 3
    bc = (h + lo) / 2
    tc = 2 * p - bc
    return {
        "pdh": h,
        "pdl": lo,
        "pdc": c,
        "pivot": p,
        "r1": 2 * p - lo,
        "s1": 2 * p - h,
        "r2": p + (h - lo),
        "s2": p - (h - lo),
        "cpr_top": max(tc, bc),
        "cpr_bottom": min(tc, bc),
    }
