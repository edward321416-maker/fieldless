import dataclasses

import pytest

from pipeline import db, item_runs
from pipeline.cost import CostCapReached
from pipeline.survey_items import Check
from server import jobs
from server.jobs import ScanWorker

BBOX = [126.9842, 37.5623, 126.9858, 37.5632]


def test_waiting_scans_are_picked_up_at_start_oldest_first_and_crashes_are_marked(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    a = db.create_scan(conn, "mapillary", BBOX)
    b = db.create_scan(conn, "mapillary", BBOX)
    done = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, done, status="done")
    ran = []

    def run(scan_id):
        ran.append(scan_id)
        if scan_id == a:
            raise RuntimeError("boom")

    worker = ScanWorker(path, tmp_path, run=run)
    worker.start()
    worker.join()
    worker.stop()
    assert ran == [a, b]  # b still ran after a crashed
    failed = db.get_scan(conn, a)
    assert failed["status"] == "failed" and "boom" in failed["error"]
    conn.close()


def test_submitted_scans_run(tmp_path):
    ran = []
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=ran.append)
    worker.start()
    worker.submit(7)
    worker.submit(8)
    worker.join()
    worker.stop()
    assert ran == [7, 8]


def test_a_failure_to_mark_a_crash_does_not_stop_the_worker(tmp_path, monkeypatch):
    ran = []

    def run(scan_id):
        ran.append(scan_id)
        raise RuntimeError("boom")

    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=run)
    monkeypatch.setattr("server.jobs.db.get_scan", lambda conn, scan_id: (_ for _ in ()).throw(OSError("disk full")))
    worker.start()
    worker.submit(1)
    worker.submit(2)
    worker.join()
    worker.stop()
    assert ran == [1, 2]


def test_a_scan_a_restart_cut_off_is_marked_failed_not_rerun_and_a_second_start_adds_no_thread(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    cut = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, cut, status="running", cost_usd=0.4)
    db.set_stage(conn, cut, "collect", "done")
    db.set_stage(conn, cut, "chunks", "running")
    waiting = db.create_scan(conn, "mapillary", BBOX)
    ran = []
    worker = ScanWorker(path, tmp_path, run=ran.append)
    worker.start()
    first = worker._thread
    worker.start()
    assert worker._thread is first
    worker.join()
    worker.stop()
    assert ran == [waiting]
    row = db.get_scan(conn, cut)
    assert row["status"] == "failed" and "restart" in row["error"] and row["cost_usd"] == 0.4
    assert row["stage_status"] == {"collect": "done", "chunks": "stopped"}
    conn.close()


def test_spend_is_saved_in_steps_and_never_goes_back(tmp_path):
    from server.jobs import spend_saver
    path = tmp_path / "t.db"
    conn = db.connect(path)
    scan_id = db.create_scan(conn, "mapillary", BBOX)
    save = spend_saver(path, scan_id, 0.0)
    save(0.01)
    assert db.get_scan(conn, scan_id)["cost_usd"] == 0
    save(0.06)
    assert db.get_scan(conn, scan_id)["cost_usd"] == 0.06
    save(0.05)  # an earlier total arriving late
    assert db.get_scan(conn, scan_id)["cost_usd"] == 0.06
    conn.close()


def test_a_base_exception_from_a_scan_does_not_stop_the_worker(tmp_path):
    from pipeline.meta_api import RunAborted
    ran = []

    def run(scan_id):
        ran.append(scan_id)
        if scan_id == 1:
            raise RunAborted("stop")

    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=run)
    worker.start()
    worker.submit(1)
    worker.submit(2)
    worker.join()
    worker.stop()
    assert ran == [1, 2]


def test_the_after_scan_hook_runs_after_every_scan_even_a_crash(tmp_path):
    done = []

    def run(scan_id):
        if scan_id == 1:
            raise RuntimeError("boom")

    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=run, on_done=done.append)
    worker.start()
    worker.submit(1)
    worker.submit(2)
    worker.join()
    worker.stop()
    assert done == [1, 2]



def test_the_worker_uses_glimmer_only_while_todays_gpu_estimate_is_under_its_cap(tmp_path, monkeypatch):
    import json as _json
    import time as _time
    from pipeline import second_opinion
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=lambda scan_id: None)
    assert worker.judge() is None  # no GLIMMER_URL
    monkeypatch.setenv("GLIMMER_URL", "https://me--fieldless-glimmer-serve.modal.run")
    assert worker.judge().model == second_opinion.GLIMMER_MODEL
    hours = second_opinion.GLIMMER_DAILY_USD / second_opinion.GPU_USD_PER_HOUR + 0.1
    (tmp_path / "calls_glimmer.jsonl").write_text(
        _json.dumps({"kind": "glimmer", "ts": _time.time() - 60, "seconds": hours * 3600}) + "\n",
        encoding="utf-8")
    assert worker.judge() is None


def test_the_worker_stops_using_glimmer_once_its_total_gpu_estimate_reaches_the_cap(tmp_path, monkeypatch):
    import json as _json
    import time as _time
    from pipeline import second_opinion
    monkeypatch.setenv("GLIMMER_URL", "https://me--fieldless-glimmer-serve.modal.run")
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=lambda scan_id: None, glimmer_total_usd=1.0)
    assert worker.judge() is not None
    hours = 1.0 / second_opinion.GPU_USD_PER_HOUR  # a day ago: under today's cap, but the total reaches $1
    (tmp_path / "calls_glimmer.jsonl").write_text(
        _json.dumps({"kind": "glimmer", "ts": _time.time() - 2 * 86_400, "seconds": hours * 3600}) + "\n",
        encoding="utf-8")
    assert worker.judge() is None
    assert ScanWorker(tmp_path / "t.db", tmp_path, run=lambda scan_id: None).judge() is not None  # no total cap
    assert ScanWorker(tmp_path / "t.db", tmp_path, run=lambda scan_id: None,
                      glimmer_daily_usd=0.0).judge() is None  # a lower daily cap


def test_a_finished_scan_gets_its_report_sentences_and_a_failure_only_logs(caplog):
    from pipeline import config
    from tests.assist import survey_scan
    from tests.assist.test_report import GOOD, NOTES
    from tests.pipeline.fakes import FakeApi

    scan_id = survey_scan.build("jobs")
    run_dir = config.run_dir("jobs")
    worker = ScanWorker(run_dir / "threshold.db", run_dir)
    conn = db.connect(run_dir / "threshold.db")

    worker._write_report_text(conn, scan_id, FakeApi(spark=lambda name, parts: GOOD))
    assert db.report_text(conn, scan_id) == NOTES

    def broken(name, parts):
        raise RuntimeError("model down")

    worker._write_report_text(conn, scan_id, FakeApi(spark=broken))  # does not raise
    assert db.report_text(conn, scan_id) == NOTES and "report text failed" in caplog.text


# --- item runs (plan 12) ------------------------------------------------------------------------------------------

HYDRANT = Check(id="agent_fire_hydrant", find="fire hydrant", target="fire hydrant", ask="Is it a fire hydrant?",
                report_when="yes", label_yes="Fire hydrant", label_no="Not a fire hydrant",
                label_unclear="Fire hydrant unclear", inventory=True, group_m=15.0)


class FakeItems:
    """Stands in for assist.custom_item: asks a person about photos 3 and 7, then is ready."""

    def __init__(self, start=None):
        self.calls = []
        self._start = start

    def start(self, api, photos, target, on_progress):
        self.calls.append(("start", target, sorted(photos.paths)))
        if self._start:
            return self._start(api)
        on_progress("Muse Spark wrote a draft")
        return {"status": "labels_needed", "photos": [3, 7], "state": {"phrases": ["fire hydrant"]}}

    def resume(self, api, photos, state, labels, on_progress):
        self.calls.append(("resume", state, labels))
        on_progress("SAM 'fire hydrant': found 1 of 1")
        return {"status": "ready", "check": HYDRANT, "test": {"labels": "person", "found": [1, 1]}}

    def city_check(self, photos, check, rows):
        self.calls.append(("city", check.id, len(rows)))
        return {"layer": "Hydrants", "found": 1, "covered": 2}


@pytest.fixture
def item_env(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    scan_id = db.create_scan(conn, "mapillary", BBOX, "US")
    db.update_scan(conn, scan_id, status="done")
    photos = item_runs.ScanPhotos(scan_id, BBOX, "US", [{"idx": i, "clip": 0} for i in range(9)],
                                  {i: tmp_path / f"{i}.jpg" for i in range(9)})
    monkeypatch.setattr(item_runs, "scan_photos", lambda conn, scan_id: photos)
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: dataclasses.make_dataclass("Api", ["meter"])(meter))

    def run_on_scan(api, photos, check, run_dir, run_id, on_progress):
        on_progress("Surveying all 9 photos of the scan for fire hydrant")
        api.meter.spent_usd += 0.2
        return [{"id": f"item{run_id}-1", "item_run": run_id, "issue_type": check.id, "frame_idx": 3}]

    monkeypatch.setattr(item_runs, "run_item_on_scan", run_on_scan)
    yield path, conn, scan_id
    conn.close()


def test_an_item_run_waits_for_a_persons_answers_then_runs_on_the_scan(tmp_path, item_env):
    path, conn, scan_id = item_env
    items, done = FakeItems(), []
    worker = ScanWorker(path, tmp_path, run=lambda s: None, items=items, on_done=done.append)
    worker.start()
    run_id = db.create_item_run(conn, scan_id, "fire hydrant")
    worker.submit_item(run_id)
    worker.join()
    run = db.item_run(conn, run_id)
    assert (run["status"], run["label_photos"], run["state"]) == ("labels_needed", [3, 7],
                                                                  {"phrases": ["fire hydrant"]})

    db.update_item_run(conn, run_id, labels={"3": "yes", "7": "no"}, status="queued")
    worker.submit_item(run_id)
    worker.join()
    worker.stop()
    run = db.item_run(conn, run_id)
    assert run["status"] == "done" and run["error"] is None
    assert run["check_def"]["id"] == "agent_fire_hydrant" and run["rows"][0]["id"] == f"item{run_id}-1"
    assert run["test"] == {"labels": "person", "found": [1, 1], "city": {"layer": "Hydrants", "found": 1, "covered": 2}}
    assert run["progress"] == ["Muse Spark wrote a draft", "SAM 'fire hydrant': found 1 of 1",
                               "Surveying all 9 photos of the scan for fire hydrant"]
    assert run["cost_usd"] == pytest.approx(0.2)
    assert items.calls[1] == ("resume", {"phrases": ["fire hydrant"]}, {3: "yes", 7: "no"})
    assert done == [scan_id, scan_id]  # the store saves after each step
    assert db.issues(conn, scan_id) == []


def test_an_item_run_that_fails_its_test_or_its_cap_says_why(tmp_path, item_env):
    path, conn, scan_id = item_env

    def no_wording(api):
        return {"status": "failed", "reason": "No reliable wording found", "test": {"found": [1, 5]}}

    def over_cap(api):
        api.meter.spent_usd = 1.0
        raise CostCapReached("cap")

    for start, error in ((no_wording, "No reliable wording found"),
                         (over_cap, "The run stopped at its spending limit ($1.00)")):
        worker = ScanWorker(path, tmp_path, run=lambda s: None, items=FakeItems(start))
        worker.start()
        run_id = db.create_item_run(conn, scan_id, "fire hydrant")
        worker.submit_item(run_id)
        worker.join()
        worker.stop()
        run = db.item_run(conn, run_id)
        assert (run["status"], run["error"]) == ("failed", error)
    assert run["cost_usd"] == pytest.approx(1.0)


def test_an_item_run_without_photos_fails_and_a_restart_fails_running_runs_and_requeues_queued_ones(
        tmp_path, item_env, monkeypatch):
    path, conn, scan_id = item_env
    cut = db.create_item_run(conn, scan_id, "bench")
    db.update_item_run(conn, cut, status="running")
    waiting = db.create_item_run(conn, scan_id, "bike rack")
    monkeypatch.setattr(item_runs, "scan_photos",
                        lambda conn, scan_id: item_runs.ScanPhotos(scan_id, BBOX, "US", [], {}))
    worker = ScanWorker(path, tmp_path, run=lambda s: None, items=FakeItems())
    worker.start()
    worker.join()
    worker.stop()
    assert db.item_run(conn, cut)["status"] == "failed" and "restart" in db.item_run(conn, cut)["error"]
    assert (db.item_run(conn, waiting)["status"], db.item_run(conn, waiting)["error"]) == (
        "failed", "The survey's photos could not be downloaded from Mapillary")
