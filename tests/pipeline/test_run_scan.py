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


def test_bad_numbers_and_unknown_scans_are_refused():
    with pytest.raises(SystemExit):
        run_scan.main(["--bbox", "126.98,37.56,126.99,37.565", "--tactile-every", "0"])
    with pytest.raises(SystemExit):
        run_scan.main(["--resume", "999", "--run", "refused"])


def fake_run(captured):
    def run(scan):
        captured.append(scan)
        return {"status": "done", "scan_id": scan.scan_id}
    return run


def test_the_cli_turns_on_the_second_opinion_only_with_a_baseten_key(monkeypatch):
    from pipeline import second_opinion
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "open"])
    monkeypatch.setenv("BASETEN_API_KEY", "test-not-a-real-key")
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "open"])
    assert captured[0].second_opinion is None
    assert isinstance(captured[1].second_opinion, second_opinion.OpenModel)
