import json

import pytest

import analysis_profile as ap
import generate_analysis as ga
import meta_screen
import score_guards


@pytest.fixture
def use_profile(tmp_path, monkeypatch):
    def _use(profile_json=None, rules=None):
        d = tmp_path / "p"
        d.mkdir(exist_ok=True)
        if profile_json is not None:
            (d / "profile.json").write_text(profile_json if isinstance(profile_json, str) else json.dumps(profile_json))
        if rules is not None:
            (d / "rules.md").write_text(rules)
        monkeypatch.setenv("STOCK_DASHBOARD_PROFILE", str(d))
        ap._load.cache_clear()
        return ap.profile()
    yield _use
    ap._load.cache_clear()


def test_default_profile_is_clean():
    p = ap.profile()
    assert p["signal_style"] == "state" and p["warnings"] == [] and p["rules"] == ""
    assert p["technical"]["stop_max_pct"] == 7.0 and p["guards"]["loss_cap"] == 40


def test_override_known_keys_ignores_unknown_and_wrong_types(use_profile):
    p = use_profile({"signal_style": "action", "technical": {"stop_max_pct": 5, "rr_min": "x", "bogus": 1},
                     "guards": {"loss_cap": True}, "evil": {}})
    assert p["signal_style"] == "action" and p["technical"]["stop_max_pct"] == 5
    assert p["technical"]["rr_min"] == 3.0 and p["guards"]["loss_cap"] == 40  # wrong type -> default
    assert len(p["warnings"]) == 4


@pytest.mark.parametrize("content", ["{not json", "[]"])
def test_broken_profile_falls_back_with_warning(use_profile, content):
    p = use_profile(content)
    assert p["technical"]["stop_max_pct"] == 7.0 and any("讀取 profile 失敗" in w for w in p["warnings"])


def test_missing_profile_dir_does_not_crash(monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_PROFILE", "/nonexistent/zzz")
    ap._load.cache_clear()
    try:
        assert ap.profile()["warnings"]
    finally:
        ap._load.cache_clear()


def test_invalid_signal_style_resets(use_profile):
    p = use_profile({"signal_style": "yolo"})
    assert p["signal_style"] == "state" and p["warnings"]


def test_rules_md_comments_stripped_and_truncated(use_profile):
    p = use_profile({}, rules="<!-- hi\nthere -->\nkeep this")
    assert p["rules"] == "keep this"
    p = use_profile({}, rules="x" * 9000)
    assert len(p["rules"]) == ap.MAX_RULES_CHARS and p["warnings"]


def test_guard_threshold_comes_from_profile(use_profile):
    fund = {"profit_margins": -0.1}

    def cap():
        d = {"scores": {"fundamentals": 90, "valuation": 90, "news": 50, "market": 50},
             "score_reasons": {}}
        score_guards.apply_guards(d, fund, None, None, {})
        return d["scores"]["fundamentals"]

    assert cap() == 40
    use_profile({"guards": {"loss_cap": 25}})
    assert cap() == 25


def test_technical_reason_uses_profile_thresholds(use_profile):
    result = {"final_pass": True, "stage2": True, "edge_count": 3, "categories": {}, "risk": 4.0, "rr": 4.5}
    dossier = {"daily": {}, "price": 10}
    base = meta_screen.technical_reason(result, dossier)
    assert "≤7%" in base and "≥3:1" in base
    use_profile({"technical": {"stop_max_pct": 5, "rr_min": 2}})
    out = meta_screen.technical_reason(result, dossier)
    assert "≤5%" in out and "≥2:1" in out


def test_signal_style_switches_prompt_wording():
    assert "操作訊號：買進／賣出／續抱／觀望" in ga._schema_for("action")
    state = ga._schema_for("state")
    assert "技術狀態判讀" in state and "操作訊號：買進" not in state
