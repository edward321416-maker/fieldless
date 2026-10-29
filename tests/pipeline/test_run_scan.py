import json

import pytest

from pipeline import config, db, run_scan


def test_preview_is_priced_for_the_standard_and_the_given_cap(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(run_scan.collect, "preview",
                        lambda bbox, max_photos, checks=0: seen.append((max_photos, checks)) or {"photos": 1})
    assert run_scan.main(["--preview", "--bbox", "-122.323,47.613,-122.317,47.6175", "--cap-usd", "6"]) == 0
    assert run_scan.main(["--preview", "--bbox", "-122.323,47.613,-122.317,47.6175", "--standard", "KR"]) == 0
    us = len(run_scan.survey_items.load("US").checks)
    assert seen == [(run_scan.config.photo_budget(400, checks=us, cap_usd=6.0), us),
                    (run_scan.config.photo_budget(400), 0)]


def test_preview_prints_the_estimate_without_spending(monkeypatch, capsys):
    monkeypatch.setattr(run_scan.collect, "preview",
                        lambda bbox, max_photos, checks=0: {"photos": 12, "est_cost_usd": 0.07})
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


def test_muse_glimmer_judges_only_a_run_that_asks_for_it(monkeypatch):
    from pipeline import second_opinion
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    with pytest.raises(SystemExit):  # --glimmer without the server's address
        run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "g", "--glimmer"])
    monkeypatch.setenv("GLIMMER_URL", "https://me--fieldless-glimmer-serve.modal.run")
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "g"])
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "g", "--glimmer"])
    assert captured[0].judge is None  # the address alone is not enough: a small scan stays on Muse Spark
    assert captured[1].judge.model == second_opinion.GLIMMER_MODEL


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


def test_cap_usd_raises_this_scans_cap_for_a_local_run(monkeypatch):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "cap", "--cap-usd", "6"])
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "cap"])
    assert captured[0].api.meter.cap_usd == 6.0
    assert captured[1].api.meter.cap_usd == config.SCAN_COST_CAP_USD
    with pytest.raises(SystemExit):
        run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "cap", "--cap-usd", "0"])
    with pytest.raises(SystemExit):
        run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "cap", "--cap-usd", "lots"])


def test_the_command_refuses_to_start_a_scan_inside_a_worker_process(tmp_path, monkeypatch):
    from pipeline import db, runner
    monkeypatch.setattr(runner.multiprocessing, "parent_process", lambda: object())

    with pytest.raises(runner.InWorkerProcess):
        run_scan.main(["--run", "worker", "--bbox", "126.97,37.55,126.99,37.57"])

    assert not (config.run_dir("worker") / "threshold.db").exists() or db.connect(
        config.run_dir("worker") / "threshold.db").execute("SELECT COUNT(*) FROM scan").fetchone()[0] == 0


def test_a_second_scan_in_the_same_run_folder_stops_at_once():
    with run_scan.one_scan_at_a_time(config.run_dir("busy")):
        with pytest.raises(run_scan.ScanAlreadyRunning):
            run_scan.main(["--run", "busy", "--bbox", "126.97,37.55,126.99,37.57"])
    assert not (config.run_dir("busy") / "threshold.db").exists()  # stopped before anything was made


def test_a_command_line_scan_saves_its_spend_as_it_goes(monkeypatch):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    run_scan.main(["--bbox", "-122.3230,47.6130,-122.3170,47.6175", "--run", "spend"])
    (scan,) = captured

    scan.api.meter.reserve(0.3)
    scan.api.meter.settle(0.3, 0.3)  # a request that cost $0.30, as a killed run would leave it

    conn = db.connect(config.run_dir("spend") / "threshold.db")
    assert db.get_scan(conn, scan.scan_id)["cost_usd"] == pytest.approx(0.3)


def test_photos_of_starts_a_new_scan_on_the_same_photos_without_collecting(monkeypatch):
    captured = []
    monkeypatch.setattr(run_scan, "run_scan", fake_run(captured))
    conn = db.connect(config.run_dir("same") / "threshold.db")
    first = db.create_scan(conn, "mapillary", [-122.323, 47.613, -122.317, 47.6175], "US")
    frames = [{"idx": i, "clip": 0, "image_id": f"9{i}", "sequence_id": "s", "lat": 47.614, "lng": -122.32,
               "compass": 0.0, "captured_at": "2022-08-30T10:00:00Z", "creator": "a", "width": 2048, "height": 1152}
              for i in range(3)]
    db.add_frames(conn, first, frames)
    db.update_scan(conn, first, image_count=3, captured_from="2022-08-30T10:00:00Z", captured_to="2022-08-30T10:00:00Z",
                   sequence_ids=["s"])

    run_scan.main(["--run", "same", "--photos-of", str(first)])

    (scan,) = captured
    new = db.get_scan(conn, scan.scan_id)
    assert scan.scan_id != first and (new["bbox"], new["jurisdiction"], new["image_count"]) == (
        [-122.323, 47.613, -122.317, 47.6175], "US", 3)
    assert new["stage_status"] == {"collect": "done"}
    assert [f["image_id"] for f in db.frames(conn, scan.scan_id)] == ["90", "91", "92"]
    with pytest.raises(SystemExit):
        run_scan.main(["--run", "same", "--photos-of", str(first), "--bbox", "1,2,3,4"])
