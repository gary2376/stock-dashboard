import copy
import json
from pathlib import Path

import pytest

import score_guards

CASES = json.loads((Path(__file__).parent / "golden" / "guards.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["ticker"] for c in CASES])
def test_apply_guards_matches_frozen_behaviour(case):
    data = copy.deepcopy(case["data"])
    caps = score_guards.apply_guards(data, case["fund"], case["dossier"], case["pct_from_high"], case["health"])
    assert data["scores"] == case["expected"]["scores"]
    assert data["score_reasons"] == case["expected"]["score_reasons"]  # incl. the "N分：" prefix rewrite
    assert caps == case["expected"]["caps"]


def test_golden_covers_fired_and_unfired():
    assert any(c["expected"]["caps"] for c in CASES) and any(not c["expected"]["caps"] for c in CASES)
