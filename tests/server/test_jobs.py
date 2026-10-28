from pipeline import db
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
