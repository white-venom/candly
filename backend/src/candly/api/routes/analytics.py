"""Analytics routes (docs/CONTRACTS.md §4, "Analytics routes"). Read-only: only jobs write the ledger."""

from __future__ import annotations

import math
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from candly.core.calendar import get_calendar
from candly.core.instruments import EXCHANGES, Instrument, UnknownInstrument, get_instrument, load_watchlist
from candly.core.schema import CANDLE_COLUMNS
from candly.core.timeframes import validate_tf
from candly.features.context import compute_context, has_meaningful_volume
from candly.features.expiry import expiry_with_source
from candly.features.levels import Level, key_levels, levels_as_of, previous_trading_day
from candly.forecast import Forecast, make_forecast
from candly.forecast.timing import to_unix
from candly.indicators import CATALOG_BY_NAME, INDICATOR_CATALOG, compute_indicators, resolve_names
from candly.ledger import AccuracyResponse, Ledger, LedgerEntry, default_ledger_path
from candly.ledger.accuracy import accuracy_from_frame
from candly.patterns import PATTERN_INFO, detect_patterns
from candly.research.config import load_research_config
from candly.research.scorecard import ScorecardMeta, ScoreStats, load_scorecard

router = APIRouter(tags=["analytics"])

Direction = Literal["bullish", "bearish", "neutral"]
Pane = Literal["price", "oscillator", "volume"]


# ---------------------------------------------------------------- loaders (lazy, patchable in tests)


def _load_candles(instrument_id: str, tf: str) -> pd.DataFrame:
    from candly.data.store import load_candles

    return load_candles(instrument_id, tf)


def _get_forming(instrument_id: str, tf: str) -> pd.Series | None:
    try:
        from candly.data.live import get_forming
    except ImportError:
        return None
    try:
        return get_forming(instrument_id, tf)
    except Exception:
        return None


def _series_last(instrument_id: str, tf: str) -> pd.Timestamp | None:
    """Open time of the newest stored bar (cached per file by the store)."""
    from candly.data.store import series_stats

    return series_stats(instrument_id, tf)["last"]


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _ledger() -> Ledger | None:
    path = default_ledger_path()
    return Ledger(path) if path.exists() else None


# ---------------------------------------------------------------- response models


class IndicatorInfoOut(BaseModel):
    name: str
    label: str
    pane: Pane
    group: Literal["trend", "momentum", "volatility", "volume"]


class IndicatorPoint(BaseModel):
    time: int
    value: float | None


class IndicatorSeries(BaseModel):
    name: str
    label: str
    pane: Pane
    points: list[IndicatorPoint]


class IndicatorsResponse(BaseModel):
    instrument: str
    tf: str
    series: list[IndicatorSeries]


class PatternContext(BaseModel):
    trend: Literal["up", "down", "sideways"] | None
    vol_regime: Literal["low", "normal", "high"] | None
    session_phase: str | None
    rel_volume: float | None
    near_level: str | None
    rsi14: float | None
    expiry_day: bool | None
    days_to_expiry: int | None


class PatternSignal(BaseModel):
    id: str
    instrument: str
    tf: str
    time: int
    pattern: str
    label: str
    direction: Direction
    state: Literal["confirmed", "forming"]
    bars: int
    invalidation: float | None
    context: PatternContext
    stats: ScoreStats | None


class LevelsResponse(BaseModel):
    instrument: str
    tf: str
    levels: list[Level]
    as_of: int  # open time of the bar (1D) or session (intraday) the day levels come from
    stale: bool  # another timeframe's store has a newer session than this series


class ExpiryInfoOut(BaseModel):
    next: str  # YYYY-MM-DD, IST
    kind: Literal["weekly", "monthly", "contract"]
    days_to_expiry: int
    is_expiry_day: bool
    source: Literal["exchange", "rules"]  # exchange-listed dates, or config/expiry.yaml rules


class TopSignal(BaseModel):
    label: str
    state: Literal["confirmed", "forming"]
    certified: bool


class ScannerRow(BaseModel):
    instrument: str
    name: str
    tf: str
    time: int
    last_close: float
    change_pct: float
    p_up: float | None
    base_rate: float | None
    abstain: bool
    abstain_reason: str | None
    score: float
    direction: Direction
    top_signal: TopSignal | None
    rel_volume: float | None
    trend: Literal["up", "down", "sideways"] | None
    expiry: ExpiryInfoOut | None


class ScorecardRow(BaseModel):
    pattern: str
    label: str
    direction: Direction
    instrument: str
    context: str
    horizon_bars: int
    n: int
    hits: int
    hit_rate: float
    base_rate: float
    ci_low: float
    ci_high: float
    p_value: float
    q_value: float | None  # null: outside the Benjamini-Hochberg family (too few clusters to test)
    posterior: float
    expectancy_after_cost_pct: float | None
    validation_n: int
    validation_hit_rate: float | None
    certified: bool


class ScorecardResponse(BaseModel):
    meta: ScorecardMeta
    rows: list[ScorecardRow]


# ---------------------------------------------------------------- helpers


def _num(x) -> float | None:
    if x is None:
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _str(x) -> str | None:
    return x if isinstance(x, str) else None


def _bool(x) -> bool | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return bool(x)


def _int(x) -> int | None:
    value = _num(x)
    return None if value is None else int(value)


def _expiry_out(instrument_id: str, now: pd.Timestamp) -> ExpiryInfoOut | None:
    info, source = expiry_with_source(instrument_id, get_calendar().local_date(now))
    if info is None:
        return None
    return ExpiryInfoOut(
        next=info.next_expiry.isoformat(),
        kind=info.kind,
        days_to_expiry=int(info.days_to_expiry),
        is_expiry_day=bool(info.is_expiry_day),
        source=source,
    )


def _tf(tf: str) -> str:
    try:
        return validate_tf(tf)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _instrument(instrument_id: str) -> Instrument:
    try:
        return get_instrument(instrument_id)
    except UnknownInstrument:
        raise HTTPException(404, f"unknown instrument {instrument_id!r}") from None


def _limit(value: int, upper: int) -> int:
    if not 1 <= value <= upper:
        raise HTTPException(400, f"limit must be between 1 and {upper}")
    return value


def _candles(instrument_id: str, tf: str) -> pd.DataFrame:
    try:
        df = _load_candles(instrument_id, tf)
    except ImportError:
        raise HTTPException(503, "candle store is not available yet") from None
    if df is None or df.empty:
        raise HTTPException(503, f"no {tf} candles for {instrument_id} yet; run ingest")
    return df.reset_index(drop=True)


def _stats_horizon() -> int:
    cfg = load_research_config()
    return cfg.forecast_steps if cfg.forecast_steps in cfg.horizons else cfg.horizons[0]


def _with_forming(df: pd.DataFrame, forming: pd.Series | None) -> pd.DataFrame:
    if forming is None or pd.Timestamp(forming["ts"]) <= df["ts"].iloc[-1]:
        return df
    row = pd.DataFrame({c: [forming.get(c, float("nan"))] for c in CANDLE_COLUMNS})
    row["ts"] = [pd.Timestamp(forming["ts"]).tz_convert("UTC")]
    row[CANDLE_COLUMNS[1:]] = row[CANDLE_COLUMNS[1:]].astype("float64")
    return pd.concat([df[CANDLE_COLUMNS], row], ignore_index=True)


# ---------------------------------------------------------------- routes


@router.get("/indicators/catalog", response_model=list[IndicatorInfoOut])
def indicator_catalog() -> list[IndicatorInfoOut]:
    return [IndicatorInfoOut(**i.as_dict()) for i in INDICATOR_CATALOG]


@router.get("/indicators", response_model=IndicatorsResponse)
def indicators(instrument: str, tf: str, names: str | None = None, limit: int = 500) -> IndicatorsResponse:
    tf = _tf(tf)
    _instrument(instrument)
    limit = _limit(limit, 5000)
    try:
        wanted = resolve_names(names.split(",") if names else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    df = _candles(instrument, tf)
    values = compute_indicators(df, tf, wanted).iloc[-limit:]
    if "rel_volume" in wanted and not has_meaningful_volume(instrument):
        values = values.assign(rel_volume=float("nan"))
    times = [to_unix(t) for t in df["ts"].iloc[-limit:]]
    series = []
    for name in wanted:
        info = CATALOG_BY_NAME[name]
        points = [IndicatorPoint(time=t, value=_num(v)) for t, v in zip(times, values[name], strict=True)]
        series.append(IndicatorSeries(name=name, label=info.label, pane=info.pane, points=points))
    return IndicatorsResponse(instrument=instrument, tf=tf, series=series)


@router.get("/patterns", response_model=list[PatternSignal])
def patterns(
    instrument: str,
    tf: str,
    limit: int = 200,
    directional_only: bool = False,
    certified_only: bool = False,
) -> list[PatternSignal]:
    """Newest first. `directional_only` drops neutral patterns; `certified_only` keeps signals whose
    scorecard stats are certified (none without a scorecard). `limit` applies after filtering."""
    tf = _tf(tf)
    inst = _instrument(instrument)
    limit = _limit(limit, 5000)
    df = _candles(instrument, tf)
    frame = _with_forming(df, _get_forming(instrument, tf))
    forming_bar = frame.iloc[-1] if len(frame) > len(df) else None
    found = detect_patterns(df, tf, forming_bar=forming_bar)
    if directional_only:
        found = found[found["direction"] != "neutral"]
    card = load_scorecard(tf, inst.exchange)
    if found.empty or (certified_only and card is None):
        return []
    found = found.iloc[::-1]
    if not certified_only:
        found = found.head(limit)
    ctx = compute_context(frame, tf, inst.exchange, instrument_id=instrument)
    pos = pd.Index(frame["ts"]).get_indexer(found["ts"])
    cfg = load_research_config()
    horizon = _stats_horizon()
    out = []
    for (_, row), p in zip(found.iterrows(), pos, strict=True):
        c = ctx.iloc[p]
        trend = _str(c["trend"])
        stats = card.stats_for(row["pattern"], instrument, trend, horizon, cfg.min_samples) if card else None
        if certified_only and not (stats is not None and stats.certified):
            continue
        time = to_unix(row["ts"])
        out.append(
            PatternSignal(
                id=f"{instrument}|{tf}|{time}|{row['pattern']}",
                instrument=instrument,
                tf=tf,
                time=time,
                pattern=row["pattern"],
                label=row["label"],
                direction=row["direction"],
                state=row["state"],
                bars=int(row["bars"]),
                invalidation=_num(row["invalidation"]),
                context=PatternContext(
                    trend=trend,
                    vol_regime=_str(c["vol_regime"]),
                    session_phase=_str(c["session_phase"]),
                    rel_volume=_num(c["rel_volume"]),
                    near_level=_str(c["near_level"]),
                    rsi14=_num(c["rsi14"]),
                    expiry_day=_bool(c["expiry_day"]),
                    days_to_expiry=_int(c["days_to_expiry"]),
                ),
                stats=stats,
            )
        )
        if len(out) == limit:
            break
    return out


def _levels_stale(inst: Instrument, tf: str, df: pd.DataFrame, now: pd.Timestamp) -> bool:
    """True when another timeframe's store holds a newer IST session than this series' last bar.

    A store's newest session only counts once this timeframe could have closed a bar in it (a daily
    bar closes at the session close); until then it stands for the trading day before it."""
    cal = get_calendar()
    used = cal.local_date(df["ts"].iloc[-1])
    for other in inst.timeframes:
        if other == tf:
            continue
        try:
            last = _series_last(inst.id, other)
        except ImportError:
            return False
        if last is None:
            continue
        day = cal.local_date(pd.Timestamp(last))
        session_open, _ = cal.session_times(inst.exchange, day)
        if now < cal.bar_close_time(inst.exchange, session_open, tf):
            day = previous_trading_day(inst.exchange, day)
        if day > used:
            return True
    return False


@router.get("/levels", response_model=LevelsResponse)
def levels(instrument: str, tf: str) -> LevelsResponse:
    tf = _tf(tf)
    inst = _instrument(instrument)
    df = _candles(instrument, tf)
    return LevelsResponse(
        instrument=instrument,
        tf=tf,
        levels=key_levels(df, tf, inst.exchange),
        as_of=to_unix(levels_as_of(df, tf, inst.exchange)),
        stale=_levels_stale(inst, tf, df, _now()),
    )


@router.get("/forecast", response_model=Forecast)
def forecast(instrument: str, tf: str, steps: int | None = None) -> Forecast:
    tf = _tf(tf)
    inst = _instrument(instrument)
    if steps is not None and not 1 <= steps <= 10:
        raise HTTPException(400, "steps must be between 1 and 10")
    df = _candles(instrument, tf)
    try:
        fc = make_forecast(instrument, tf, df, load_scorecard(tf, inst.exchange), steps, now=_now())
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from None
    if not fc.abstain:
        from candly.llm import get_explanation

        fc.explanation = get_explanation(fc.instrument, fc.tf, fc.ref_time, fc.method)
    return fc


def _top_signal(fc: Forecast, forming: pd.DataFrame, card, instrument: str) -> TopSignal | None:
    """The directional signal to show: certified before not, confirmed before forming. Neutral patterns
    are never picked."""
    cfg = load_research_config()
    horizon = _stats_horizon()
    trend = fc.context.trend if fc.context else None
    candidates = [(p, "confirmed") for p in (fc.context.patterns if fc.context else [])]
    candidates += [(p, "forming") for p in forming["pattern"]]
    best, best_rank = None, None
    for pattern, state in candidates:
        if PATTERN_INFO[pattern].direction == "neutral":
            continue
        stats = card.stats_for(pattern, instrument, trend, horizon, cfg.min_samples) if card else None
        certified = bool(stats and stats.certified)
        rank = (certified, state == "confirmed")
        if best_rank is None or rank > best_rank:
            best = TopSignal(label=PATTERN_INFO[pattern].label, state=state, certified=certified)
            best_rank = rank
    return best


def _iso(unix: int) -> str:
    return pd.Timestamp(unix, unit="s", tz="UTC").isoformat()


def _session_key(exchange: str, now: pd.Timestamp) -> tuple:
    cal = get_calendar()
    return cal.session_times(exchange, cal.local_date(now))


def _mark_stale(rows: list[ScannerRow], now: pd.Timestamp) -> list[ScannerRow]:
    """Rank every row as of the latest session its peers (instruments on the same session hours) have
    reached: a row whose last closed bar is older abstains as stale."""
    latest: dict[tuple, int] = {}
    keys = [_session_key(r.instrument.split(":", 1)[0], now) for r in rows]
    for key, row in zip(keys, rows, strict=True):
        latest[key] = max(latest.get(key, row.time), row.time)
    out = []
    for key, row in zip(keys, rows, strict=True):
        newest = latest[key]
        if row.time < newest and not (row.abstain_reason or "").startswith("stale data"):
            row = row.model_copy(
                update={
                    "abstain": True,
                    "abstain_reason": (
                        f"stale data: last closed bar {_iso(row.time)}, other instruments have {_iso(newest)}"
                    ),
                    "score": 0.0,
                    "direction": "neutral",
                }
            )
        out.append(row)
    return out


@router.get("/scanner", response_model=list[ScannerRow])
def scanner(tf: str = "1D") -> list[ScannerRow]:
    tf = _tf(tf)
    cards = {}
    now = _now()
    rows = []
    for inst in load_watchlist():
        if tf not in inst.timeframes or not inst.tradable:
            continue
        if inst.exchange not in cards:
            cards[inst.exchange] = load_scorecard(tf, inst.exchange)
        card = cards[inst.exchange]
        try:
            df = _load_candles(inst.id, tf)
        except ImportError:
            raise HTTPException(503, "candle store is not available yet") from None
        if df is None or df.empty:
            continue
        df = df.reset_index(drop=True)
        try:
            fc = make_forecast(inst.id, tf, df, card, now=now)
        except ValueError:
            continue
        forming_bar = _get_forming(inst.id, tf)
        forming = detect_patterns(df, tf, forming_bar=forming_bar) if forming_bar is not None else None
        forming = (
            forming[forming["state"] == "forming"]
            if forming is not None
            else pd.DataFrame(columns=["pattern"])
        )
        closes = df["close"]
        ref_pos = int(pd.Index(df["ts"]).get_indexer([pd.Timestamp(fc.ref_time, unit="s", tz="UTC")])[0])
        prev = float(closes.iloc[ref_pos - 1]) if ref_pos > 0 else float(closes.iloc[ref_pos])
        edge = (fc.p_up - fc.base_rate) if fc.p_up is not None and fc.base_rate is not None else None
        rows.append(
            ScannerRow(
                instrument=inst.id,
                name=inst.name,
                tf=tf,
                time=fc.ref_time,
                last_close=fc.ref_close,
                change_pct=100.0 * (fc.ref_close / prev - 1.0),
                p_up=fc.p_up,
                base_rate=fc.base_rate,
                abstain=fc.abstain,
                abstain_reason=fc.abstain_reason,
                score=abs(edge) if (edge is not None and not fc.abstain) else 0.0,
                direction="neutral"
                if fc.abstain or edge is None or edge == 0
                else ("bullish" if edge > 0 else "bearish"),
                top_signal=_top_signal(fc, forming, card, inst.id),
                rel_volume=fc.context.rel_volume if fc.context else None,
                trend=fc.context.trend if fc.context else None,
                expiry=_expiry_out(inst.id, now),
            )
        )
    return sorted(_mark_stale(rows, now), key=lambda r: r.score, reverse=True)


def _scorecard_exchange(exchange: str | None, instrument: str | None) -> str:
    """The build to read: `exchange`, else the instrument's own exchange, else NSE."""
    own = instrument.split(":", 1)[0] if instrument else None
    own = own if own in EXCHANGES else None
    if exchange is None:
        return own or "NSE"
    if exchange not in EXCHANGES:
        raise HTTPException(400, f"exchange must be one of {', '.join(EXCHANGES)}")
    if own is not None and own != exchange:
        raise HTTPException(400, f"{instrument} is not on {exchange}; its stats are in the {own} scorecard")
    return exchange


@router.get("/scorecard", response_model=ScorecardResponse)
def scorecard(
    tf: str = "1D",
    instrument: str | None = None,
    pattern: str | None = None,
    certified_only: bool = False,
    exchange: str | None = None,
) -> ScorecardResponse:
    """One exchange's scorecard: `exchange`, else the exchange of `instrument`, else NSE."""
    tf = _tf(tf)
    exchange = _scorecard_exchange(exchange, instrument)
    card = load_scorecard(tf, exchange)
    if card is None:
        raise HTTPException(503, f"no {tf} {exchange} scorecard yet; run rebuild_scorecard")
    rows = card.rows
    if instrument:
        rows = rows[rows["instrument"] == instrument]
    if pattern:
        rows = rows[rows["pattern"] == pattern]
    if certified_only:
        rows = rows[rows["certified"]]
    fields = list(ScorecardRow.model_fields)
    out = []
    for rec in rows[fields].to_dict("records"):
        for key in ("q_value", "expectancy_after_cost_pct", "validation_hit_rate"):
            rec[key] = _num(rec[key])
        out.append(ScorecardRow(**rec))
    return ScorecardResponse(meta=card.meta, rows=out)


@router.get("/ledger", response_model=list[LedgerEntry])
def ledger_entries(
    instrument: str | None = None,
    tf: str | None = None,
    status: str | None = None,
    method: str | None = None,
    limit: int = 100,
) -> list[LedgerEntry]:
    if tf is not None:
        tf = _tf(tf)
    if status is not None and status not in ("pending", "graded", "void"):
        raise HTTPException(400, "status must be pending, graded or void")
    limit = _limit(limit, 1000)
    book = _ledger()
    return book.entries(instrument, tf, status, method, limit) if book else []


@router.get("/accuracy", response_model=AccuracyResponse)
def accuracy(
    instrument: str | None = None, tf: str | None = None, days: int = 90, method: str = "analog_v1"
) -> AccuracyResponse:
    if tf is not None:
        tf = _tf(tf)
    if days < 1:
        raise HTTPException(400, "days must be at least 1")
    book = _ledger()
    if book is None:
        return accuracy_from_frame(pd.DataFrame())
    return book.accuracy(instrument, tf, days, method, now=_now())
