import json

import pytest

from pipeline import config, db, run_scan


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


def test_a_new_scan_takes_its_standard_from_the_area_or_the_flag(monkeypatch, capsys):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    assert run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "std"]) == 0
    assert run_scan.main(["--bbox", "126.98,37.56,126.99,37.565", "--run", "std"]) == 0
    assert run_scan.main(["--bbox", "126.98,37.56,126.99,37.565", "--run", "std", "--standard", "US"]) == 0
    conn = db.connect(config.run_dir("std") / "threshold.db")
    assert [db.get_scan(conn, s.scan_id)["jurisdiction"] for s in captured] == ["US", "KR", "US"]


def test_rerun_marks_one_stage_pending_and_needs_resume(monkeypatch, capsys):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "again"])
    conn = db.connect(config.run_dir("again") / "threshold.db")
    scan_id = captured[0].scan_id
    db.set_stage(conn, scan_id, "defects", "done")
    assert run_scan.main(["--resume", str(scan_id), "--run", "again", "--rerun", "defects"]) == 0
    assert db.get_scan(conn, scan_id)["stage_status"]["defects"] == "pending"
    with pytest.raises(SystemExit):
        run_scan.main(["--bbox", "126.98,37.56,126.99,37.565", "--rerun", "defects"])
    with pytest.raises(SystemExit):
        run_scan.main(["--resume", str(scan_id), "--run", "again", "--rerun", "nonsense"])


def test_check_needs_rerun_defects_and_a_check_of_the_standard(monkeypatch):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "only"])
    scan_id = captured[0].scan_id
    with pytest.raises(SystemExit):  # no --rerun
        run_scan.main(["--resume", str(scan_id), "--run", "only", "--check", "curb_ramp_warning"])
    with pytest.raises(SystemExit):  # another stage
        run_scan.main(["--resume", str(scan_id), "--run", "only", "--rerun", "sidewalk", "--check",
                       "curb_ramp_warning"])
    with pytest.raises(SystemExit):  # not a check of the scan's standard
        run_scan.main(["--resume", str(scan_id), "--run", "only", "--rerun", "defects", "--check", "nonsense"])
    assert len(captured) == 1
    conn = db.connect(config.run_dir("only") / "threshold.db")
    assert db.get_scan(conn, scan_id)["stage_status"].get("defects") != "pending"  # nothing was changed
    assert run_scan.main(["--resume", str(scan_id), "--run", "only", "--rerun", "defects", "--check",
                          "curb_ramp_warning"]) == 0
    assert captured[1].only_checks == ("curb_ramp_warning",)
