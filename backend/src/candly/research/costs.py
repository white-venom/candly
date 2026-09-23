"""Round-trip trading costs from config/costs.yaml, as a fraction of the traded notional."""

from __future__ import annotations

from typing import Literal

from candly.research.config import load_costs_config

Holding = Literal["intraday", "multi_day"]


def _rate(entry: dict | float | None, side: str) -> float:
    if entry is None:
        return 0.0
    if isinstance(entry, int | float):
        return float(entry)
    return float(entry.get(side, entry.get("both", 0.0)) or 0.0)


def cost_breakdown(
    kind: str,
    holding: Holding,
    broker: str = "fyers",
    notional_inr: float | None = None,
    costs: dict | None = None,
) -> dict[str, float]:
    """Components of one buy plus one sell, each as a fraction of notional.

    Brokerage per order is min(per_order_inr, pct_cap * notional). Without a notional the pct cap is
    used, which is the most it can be. Delivery trades are free when the broker says so.
    GST applies to brokerage + exchange transaction charges + SEBI fee. Slippage is charged on both sides.
    """
    cfg = costs or load_costs_config()
    segment_name = cfg["mapping"][kind][holding]
    seg = cfg["segments"][segment_name]
    broker_cfg = cfg["brokerage"][broker]

    if broker_cfg.get("delivery_free") and segment_name == "equity_delivery":
        per_order = 0.0
    else:
        cap, flat = broker_cfg.get("pct_cap"), broker_cfg.get("per_order_inr")
        options = [float(cap)] if cap is not None else []
        if notional_inr and flat is not None:
            options.append(float(flat) / notional_inr)
        if not options:
            raise ValueError(f"{broker} brokerage has no pct_cap; pass notional_inr")
        per_order = min(options)

    def both_sides(key: str) -> float:
        entry = seg.get(key)
        return _rate(entry, "buy") + _rate(entry, "sell")

    brokerage = 2.0 * per_order
    exchange = both_sides("exchange_txn")
    sebi = 2.0 * float(cfg.get("sebi_fee", 0.0))
    transaction_tax = both_sides("stt") + both_sides("ctt")
    stamp = both_sides("stamp_duty")
    gst = float(cfg.get("gst_rate", 0.0)) * (brokerage + exchange + sebi)
    slippage_cfg = cfg.get("slippage", {})
    slippage = 2.0 * float(slippage_cfg.get(kind, slippage_cfg.get("default", 0.0)))
    return {
        "brokerage": brokerage,
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
) -> float:
    return float(sum(cost_breakdown(kind, holding, broker, notional_inr, costs).values()))
