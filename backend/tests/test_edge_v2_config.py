from pathlib import Path

import pytest

from candly.research.edge_v2_config import SECTIONS, edge_v2_section, edge_v3_section, load_edge_v2

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "edge_search_v2.yaml"


def test_registered_file_has_every_section():
    raw = load_edge_v2(REPO_CONFIG)
    assert set(raw) == SECTIONS
    assert raw["family"]["fdr_q"] == 0.10
    assert edge_v2_section("intraday_momentum", REPO_CONFIG)["name"] == "im_v1"


def test_unknown_section_is_rejected(tmp_path):
    bad = tmp_path / "edge_search_v2.yaml"
    bad.write_text(REPO_CONFIG.read_text(encoding="utf-8") + "\nextra: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extra"):
        load_edge_v2(bad)


def test_v3_file_has_the_point_in_time_retest():
    spec = edge_v3_section("xs_pit", REPO_CONFIG.with_name("edge_search_v3.yaml"))
    assert spec["name"] == "xs_v2" and spec["base"] == "xs_v1"
    assert spec["horizons_days"] == [5, 20]
