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
        if scan_id == b:
            raise RuntimeError("boom")

    worker = ScanWorker(path, tmp_path, run=run)
    worker.start()
    worker.join()
    worker.stop()
    assert ran == [a, b]
    failed = db.get_scan(conn, b)
    assert failed["status"] == "failed" and "boom" in failed["error"]


def test_submitted_scans_run(tmp_path):
    ran = []
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=ran.append)
    worker.start()
    worker.submit(7)
    worker.submit(8)
    worker.join()
    worker.stop()
    assert ran == [7, 8]
