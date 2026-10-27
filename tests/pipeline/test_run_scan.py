import json

import pytest

from pipeline import run_scan


def test_preview_prints_the_estimate_without_spending(monkeypatch, capsys):
    monkeypatch.setattr(run_scan.collect, "preview", lambda bbox, max_photos: {"photos": 12, "est_cost_usd": 0.07})
    assert run_scan.main(["--preview", "--bbox", "126.98,37.56,126.99,37.565"]) == 0
    assert json.loads(capsys.readouterr().out)["photos"] == 12


def test_a_scan_needs_a_bbox_or_a_resume_id():
    with pytest.raises(SystemExit):
        run_scan.main([])
    with pytest.raises(SystemExit):
        run_scan.main(["--bbox", "1,2,3"])
