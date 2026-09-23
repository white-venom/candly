"""Round-trip trading costs from config/costs.yaml, as a fraction of the traded notional."""

from __future__ import annotations

from typing import Literal

from candly.research.config import load_costs_config

Holding = Literal["intraday", "multi_day"]
Side = Literal["long", "short"]

# Flat per-order and per-sell charges only become fractions for a given trade size. Without one, costs
# are quoted for this reference trade (the acceptance report's ₹1 lakh); costs.yaml
# `reference_notional_inr` overrides it.
REFERENCE_NOTIONAL_INR = 100_000.0
DELIVERY_SEGMENT = "equity_delivery"


def segment_for(kind: str, holding: Holding, side: Side = "long", costs: dict | None = None) -> str:
    """The cost segment from costs.yaml `mapping`. A short uses `<holding>_short` when the mapping has
    one (cash equity can't be held short overnight, so `multi_day_short` points at stock futures)."""
    mapping = (costs or load_costs_config())["mapping"][kind]
    if side == "short":
        return mapping.get(f"{holding}_short", mapping[holding])
    return mapping[holding]


def _rate(entry: dict | float | None, side: str) -> float:
    if entry is None:
        return 0.0
    if isinstance(entry, int | float):
        return float(entry)
    return float(entry.get(side, entry.get("both", 0.0)) or 0.0)


def _per_order(broker_cfg: dict, delivery: bool, notional: float) -> float:
    if delivery and broker_cfg.get("delivery_free"):
        return 0.0
    cap = broker_cfg.get("delivery_pct_cap") if delivery else None
    cap = cap if cap is not None else broker_cfg.get("pct_cap")
    flat = broker_cfg.get("per_order_inr")
    options = [float(cap)] if cap is not None else []
    if flat is not None:
        options.append(float(flat) / notional)
    return min(options) if options else 0.0


def cost_breakdown(
    kind: str,
    holding: Holding,
    broker: str = "fyers",
    notional_inr: float | None = None,
    costs: dict | None = None,
    side: Side = "long",
) -> dict[str, float]:
    """Components of one buy plus one sell, each as a fraction of notional.

    Brokerage per order is min(per_order_inr, pct cap * notional); delivery uses `delivery_pct_cap`, or
    nothing when the broker says `delivery_free`. Delivery also pays `dp_per_sell_inr` on its sell.
    GST applies to brokerage + DP + exchange transaction charges + SEBI fee. Slippage is charged on both
    sides. `notional_inr` defaults to costs.yaml `reference_notional_inr`, else REFERENCE_NOTIONAL_INR.
    """
    cfg = costs or load_costs_config()
    notional = float(notional_inr or cfg.get("reference_notional_inr") or REFERENCE_NOTIONAL_INR)
    segment_name = segment_for(kind, holding, side, cfg)
    seg = cfg["segments"][segment_name]
    broker_cfg = cfg["brokerage"][broker]
    delivery = segment_name == DELIVERY_SEGMENT

    def both_sides(key: str) -> float:
        entry = seg.get(key)
        return _rate(entry, "buy") + _rate(entry, "sell")

    brokerage = 2.0 * _per_order(broker_cfg, delivery, notional)
    dp = float(broker_cfg.get("dp_per_sell_inr") or 0.0) / notional if delivery else 0.0
    exchange = both_sides("exchange_txn")
    sebi = 2.0 * float(cfg.get("sebi_fee", 0.0))
    transaction_tax = both_sides("stt") + both_sides("ctt")
    stamp = both_sides("stamp_duty")
    gst = float(cfg.get("gst_rate", 0.0)) * (brokerage + dp + exchange + sebi)
    slippage_cfg = cfg.get("slippage", {})
    slippage = 2.0 * float(slippage_cfg.get(kind, slippage_cfg.get("default", 0.0)))
    return {
        "brokerage": brokerage,
        "dp_charge": dp,
        "exchange_txn": exchange,
        "sebi_fee": sebi,
        "transaction_tax": transaction_tax,
        "stamp_duty": stamp,
        "gst": gst,
        "slippage": slippage,
    }


def round_trip_cost(
    kind: str,
    holding: Holding,
    broker: str = "fyers",
    notional_inr: float | None = None,
    costs: dict | None = None,
    side: Side = "long",
) -> float:
    return float(sum(cost_breakdown(kind, holding, broker, notional_inr, costs, side).values()))
