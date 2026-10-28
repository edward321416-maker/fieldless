import json

import pytest

from eval import glimmer_ledger as ledger


def test_an_estimate_counts_busy_time_cold_start_and_the_idle_minutes_before_scale_down():
    assert ledger.estimate_usd(60, 180) == pytest.approx((60 + 180 + 120) * 0.001097)


def test_runs_append_and_add_up(tmp_path):
    path = tmp_path / "ledger.jsonl"
    assert ledger.total_usd(path) == 0.0
    first = ledger.append(path, "smoke", busy_s=30, cold_start_s=200)
    ledger.append(path, "replay", busy_s=600)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["what"] for r in rows] == ["smoke", "replay"] and rows[0] == first
    assert first["usd_est"] == pytest.approx(ledger.estimate_usd(30, 200), abs=1e-4)
    assert ledger.total_usd(path) == pytest.approx(ledger.estimate_usd(30, 200) + ledger.estimate_usd(600), abs=1e-3)


def test_the_development_cap_stops_a_run_before_it_starts(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.check_budget(path, planned_usd=29.0)
    path.write_text(json.dumps({"what": "x", "usd_est": 29.5}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="30"):
        ledger.check_budget(path, planned_usd=1.0)


def test_the_ledger_lives_outside_the_repo_next_to_the_runs():
    assert ledger.LEDGER.name == "glimmer_gpu_ledger.jsonl" and ledger.LEDGER.parent.name == "data"
