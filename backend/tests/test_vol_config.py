from datetime import date

import pytest
import yaml

from candly.core.settings import get_settings
from candly.research.edge_config import EDGE_FILE, load_edge_config, parse_edge_config


def raw_config() -> dict:
    return yaml.safe_load((get_settings().config_dir / EDGE_FILE).read_bytes())


def dump(raw: dict) -> bytes:
    return yaml.safe_dump(raw, sort_keys=False).encode()


def _node(raw: dict, path: tuple[str, ...]) -> dict:
    for key in path:
        raw = raw[key]
    return raw


def test_loads_every_section_of_the_registered_file():
    cfg = load_edge_config()
    assert cfg.common.holdout_start == date(2025, 10, 1)
    assert cfg.common.costs == "config/costs.yaml"
    boot = cfg.common.bootstrap
    assert (boot.kind, boot.resamples, boot.ci_level, boot.seed) == ("date_block", 2000, 0.95, 20260924)
    vol = cfg.volatility
    assert (vol.name, vol.underlying, vol.implied) == ("vol_v1", "NSE:NIFTY50", "NSE:INDIAVIX")
    assert vol.horizons_days == (5, 20)
    assert vol.train_end == date(2016, 1, 1)
    assert set(vol.baselines) == {"india_vix", "har_rv", "ewma_rv"}
    assert vol.pass_if.qlike_improvement_vs_vix_ci_lower_above == 0.0
    assert vol.pass_if.conditional_vs_always_short_sharpe_ci_lower_above == 0.0
    assert vol.pass_if.rule == "at least one horizon passes both checks"
    assert vol.caveat.startswith("a pass is a lead")
    xs = cfg.cross_section
    assert xs.rebalance == {5: "weekly_first_trading_day", 20: "monthly_first_trading_day"}
    assert (xs.portfolio.long, xs.portfolio.weighting, xs.portfolio.short) == ("top_decile", "equal", "none")
    assert xs.purge_days == 20 and xs.min_price_inr == 20.0
    assert xs.pass_if.beats_best_baseline is True
    assert "momentum_12_1" in xs.feature_groups and "low_volatility" in xs.baselines
    assert cfg.options_flow.name == "oi_v1"
    assert len(cfg.sha256) == 64
    assert vol.train_end_utc.isoformat() == "2015-12-31T18:30:00+00:00"


def test_a_re_dumped_copy_parses_to_the_same_values():
    cfg, again = load_edge_config(), parse_edge_config(dump(raw_config()))
    assert again.volatility == cfg.volatility
    assert again.cross_section == cfg.cross_section
    assert again.common == cfg.common


@pytest.mark.parametrize(
    "path",
    [(), ("common",), ("common", "bootstrap"), ("cross_section",), ("cross_section", "pass_if"),
     ("cross_section", "portfolio"), ("volatility",), ("volatility", "pass_if"), ("options_flow",)],
)
def test_an_unknown_key_anywhere_is_an_error(path):
    raw = raw_config()
    _node(raw, path)["surprise"] = 1
    with pytest.raises(ValueError, match="unknown keys"):
        parse_edge_config(dump(raw))


@pytest.mark.parametrize(
    "path, key",
    [((), "options_flow"), (("common",), "bootstrap"), (("volatility",), "loss"),
     (("volatility", "pass_if"), "rule"), (("cross_section",), "purge_days")],
)
def test_a_missing_key_is_an_error(path, key):
    raw = raw_config()
    del _node(raw, path)[key]
    with pytest.raises(ValueError, match="missing keys"):
        parse_edge_config(dump(raw))


@pytest.mark.parametrize(
    "path, key, value",
    [
        (("volatility",), "loss", "mse"),
        (("volatility",), "forecast", "garch"),
        (("volatility",), "pnl_proxy", "straddle"),
        (("volatility",), "baselines", ["india_vix", "har_rv"]),
        (("volatility",), "horizons_days", [5, 5]),
        (("volatility",), "underlying", "NIFTY50"),
        (("volatility", "pass_if"), "rule", "at least one horizon passes either check"),
        (("cross_section",), "model", "lightgbm_regression"),
        (("cross_section",), "purge_days", 5),
        (("cross_section",), "rebalance", {5: "weekly_first_trading_day"}),
        (("cross_section", "portfolio"), "short", "bottom_decile"),
        (("cross_section", "pass_if"), "rule", "any check passes"),
        (("common",), "costs", "config/other_costs.yaml"),
        (("common", "bootstrap"), "kind", "iid"),
        (("options_flow",), "name", "oi_v2"),
    ],
)
def test_values_the_code_does_not_implement_are_errors(path, key, value):
    raw = raw_config()
    _node(raw, path)[key] = value
    with pytest.raises(ValueError):
        parse_edge_config(dump(raw))


def test_holdout_must_match_research_yaml():
    with pytest.raises(ValueError, match="differs from research.yaml"):
        parse_edge_config(dump(raw_config()), research_holdout=date(2025, 1, 1))


def test_train_end_must_come_before_the_holdout():
    raw = raw_config()
    raw["volatility"]["train_end"] = date(2025, 10, 1)
    with pytest.raises(ValueError, match="before common.holdout_start"):
        parse_edge_config(dump(raw))
