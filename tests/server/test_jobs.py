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


def test_a_running_scan_left_by_a_restart_is_picked_up_and_a_second_start_adds_no_thread(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    scan_id = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, scan_id, status="running")
    conn.close()
    ran = []
    worker = ScanWorker(path, tmp_path, run=ran.append)
    worker.start()
    first = worker._thread
    worker.start()
    assert worker._thread is first
    worker.join()
    worker.stop()
    assert ran == [scan_id]
