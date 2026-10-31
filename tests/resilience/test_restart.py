"""Case 1: the container restarts. SQLite lives on the container's own disk, so a new container has only what the last
backup put on the Volume."""
import shutil

import pytest
from fastapi.testclient import TestClient

from pipeline import db
from server import store as store_module
from server.store import DB_NAME
from tests.resilience.harness import DEMO_ID, SEATTLE, Container, Hanging, backed_up_scans, wait_for


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A running container with the example survey backed up."""
    first = Container(tmp_path, "disk-1", monkeypatch)
    first.seed_finished_survey()
    return first.boot()


def cut_off_a_survey(live, tmp_path, monkeypatch):
    """A visitor's survey has spent $2.00 when the container dies; a new one starts from the Volume."""
    job = Hanging(2.0, live.db_path)
    live.worker._run = job
    scan_id = live.client.post("/api/scans", json={"bbox": SEATTLE}).json()["id"]
    assert job.started.wait(5)
    live.store.save_now()  # what the checkpoint asks for (test_a_job_asks_for_a_backup_when_it_starts_and_as_it_spends)
    job.release()
    return scan_id, Container(tmp_path, "disk-2", monkeypatch).boot()


def test_a_survey_cut_off_by_a_restart_ends_as_a_failed_survey_the_visitor_can_see(live, tmp_path, monkeypatch):
    scan_id, new = cut_off_a_survey(live, tmp_path, monkeypatch)
    reply = new.client.get(f"/api/scans/{scan_id}")
    assert reply.status_code == 200 and reply.json()["status"] == "failed"


def test_the_spend_of_a_survey_cut_off_by_a_restart_still_counts_toward_the_caps(live, tmp_path, monkeypatch):
    _, new = cut_off_a_survey(live, tmp_path, monkeypatch)
    with new.conn() as conn:
        assert db.public_cost(conn) == pytest.approx(2.0)
        assert db.daily_cost(conn, db.now()[:10]) == pytest.approx(3.5)


def test_a_new_survey_never_takes_the_id_of_one_a_restart_lost(live, tmp_path, monkeypatch):
    scan_id, new = cut_off_a_survey(live, tmp_path, monkeypatch)
    new.worker._run = lambda job_id: None
    assert new.client.post("/api/scans", json={"bbox": SEATTLE}).json()["id"] != scan_id


def test_an_item_run_cut_off_by_a_restart_ends_as_a_failed_run(tmp_path, monkeypatch):
    live = Container(tmp_path, "disk-1", monkeypatch, item_invite_code="code")
    live.seed_finished_survey()
    live.boot()
    job = Hanging(0.5, live.db_path, update=db.update_item_run)
    real_run_item = live.worker._run_item
    live.worker._run_item = job
    run_id = live.client.post(f"/api/scans/{DEMO_ID}/items", json={"target": "bench", "invite_code": "code"}).json()["id"]
    assert job.started.wait(5)
    live.store.save_now()
    job.release()
    live.worker._run_item = real_run_item
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert new.client.get(f"/api/items/{run_id}").status_code == 200


def test_what_a_backup_caught_mid_run_comes_back_in_a_final_state_and_the_worker_is_back(live, tmp_path, monkeypatch):
    """A review's backup can catch a survey running, an item running and a survey waiting."""
    ran = []
    with live.conn() as conn:
        running = db.create_scan(conn, "mapillary", SEATTLE, "US")
        db.update_scan(conn, running, status="running", cost_usd=0.7)
        db.set_stage(conn, running, "sidewalk", "running")
        waiting = db.create_scan(conn, "mapillary", SEATTLE, "US")
        item = db.create_item_run(conn, DEMO_ID, "bench")
        db.update_item_run(conn, item, status="running", cost_usd=0.2)
    live.store.save_now()

    new = Container(tmp_path, "disk-2", monkeypatch)
    new.worker._run = ran.append
    new.boot()
    new.worker.join()
    assert new.worker._thread.is_alive()
    assert ran == [waiting]  # queued work is picked up again
    with new.conn() as conn:
        cut = db.get_scan(conn, running)
        assert (cut["status"], cut["cost_usd"]) == ("failed", 0.7) and "restart" in cut["error"]
        assert cut["stage_status"]["sidewalk"] == "stopped"
        assert db.item_run(conn, item)["status"] == "failed"
        assert not db.busy(conn) or db.get_scan(conn, waiting)["status"] == "queued"  # the fake job leaves it queued
    assert new.client.get(f"/api/scans/{DEMO_ID}").json()["status"] == "done"
    new.worker.stop()


def test_a_graceful_shutdown_saves_what_is_still_waiting_for_its_backup(tmp_path, monkeypatch):
    live = Container(tmp_path, "disk-1", monkeypatch)
    live.seed_finished_survey()

    def finish(scan_id):
        with live.conn() as conn:
            db.update_scan(conn, scan_id, status="done", cost_usd=1.0)

    live.worker._run = finish
    with TestClient(live.app) as client:
        scan_id = client.post("/api/scans", json={"bbox": SEATTLE}).json()["id"]
        live.worker.join()
    assert scan_id in backed_up_scans(live.volume)  # the row is on the Volume, in whatever state it reached


def test_a_leftover_part_file_on_the_volume_is_ignored(live, tmp_path, monkeypatch):
    """A restart in the last step of a backup leaves threshold.db.part: the backup before it is whole and is restored."""
    (live.volume / f"{DB_NAME}.part").write_bytes(b"half a database")
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert new.client.get(f"/api/scans/{DEMO_ID}").json()["status"] == "done"
    new.worker.stop()


def test_half_written_side_files_do_not_stop_a_finished_survey(live, tmp_path, monkeypatch):
    (live.volume / f"city_check_scan{DEMO_ID}.json").write_text('{"scan_id": 1, "ite', encoding="utf-8")
    (live.volume / f"call_stats_scan{DEMO_ID}.json").write_text("[not json", encoding="utf-8")
    (live.volume / "calls_glimmer.jsonl").write_text('{"kind": "glimmer", "ts"', encoding="utf-8")
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert new.client.get(f"/api/scans/{DEMO_ID}").status_code == 200
    assert new.client.get(f"/api/scans/{DEMO_ID}/calls").status_code == 404
    new.worker.stop()


def half_of(path):
    data = path.read_bytes()
    path.write_bytes(data[:len(data) // 2])


def test_a_half_written_database_on_the_volume_does_not_keep_the_site_down(tmp_path, monkeypatch):
    first = Container(tmp_path, "disk-1", monkeypatch)
    first.seed_finished_survey()
    half_of(first.volume / DB_NAME)
    new = Container(tmp_path, "disk-2", monkeypatch)
    with TestClient(new.app) as client:
        assert client.get("/api/health").status_code == 200


def fail_reads_of(monkeypatch, name):
    real = shutil.copy2

    def copy(src, dst, **kwargs):
        if str(src).endswith(name) and "volume" in str(src):
            raise OSError("Input/output error")
        return real(src, dst, **kwargs)

    monkeypatch.setattr(store_module.shutil, "copy2", copy)
    return lambda: monkeypatch.setattr(store_module.shutil, "copy2", real)


def test_a_volume_read_error_at_start_never_costs_the_backup(live, tmp_path, monkeypatch):
    restore_ok = fail_reads_of(monkeypatch, DB_NAME)
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    restore_ok()
    assert new.client.get("/api/demo").json() == []  # the first screen has nothing to open
    new.worker._run = lambda job_id: None
    new.client.post("/api/scans", json={"bbox": SEATTLE})
    new.worker.join()
    new.store.save_now()  # what on_done does 20 s after any survey ends
    new.worker.stop()
    # the visitor's new survey took id 1, so /api/demo would now open it as the example
    assert backed_up_scans(live.volume)[DEMO_ID] == "done"


def test_a_read_error_part_way_through_a_restore_is_tried_again_and_the_photos_come_back(live, tmp_path, monkeypatch):
    real, failed = shutil.copy2, []

    def copy(src, dst, **kwargs):
        if "evidence" in str(src) and "volume" in str(src) and not failed:
            failed.append(src)
            raise OSError("Input/output error")
        return real(src, dst, **kwargs)

    monkeypatch.setattr(store_module.shutil, "copy2", copy)
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert failed
    assert new.client.get(f"/api/scans/{DEMO_ID}").status_code == 200
    assert new.client.get("/api/evidence/scan1_issue1_123.jpg").status_code == 200


def test_a_photo_the_volume_cannot_give_back_does_not_cost_the_surveys(live, tmp_path, monkeypatch):
    real = shutil.copy2

    def copy(src, dst, **kwargs):
        if "evidence" in str(src) and "volume" in str(src):
            raise OSError("Input/output error")
        return real(src, dst, **kwargs)

    monkeypatch.setattr(store_module.shutil, "copy2", copy)
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert new.client.get(f"/api/scans/{DEMO_ID}").json()["status"] == "done"
    assert new.client.get("/api/evidence/scan1_issue1_123.jpg").status_code == 404
    assert new.client.get("/api/health").json()["ok"] is True


def test_a_half_written_database_falls_back_to_the_backup_before_it(tmp_path, monkeypatch):
    first = Container(tmp_path, "disk-1", monkeypatch)
    first.seed_finished_survey()
    first.store.save_now()  # the seed's copy is now the one before the last
    half_of(first.volume / DB_NAME)
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert new.client.get(f"/api/scans/{DEMO_ID}").json()["status"] == "done"
    assert new.store.restore_failed is False
    new.worker.stop()


def test_a_server_that_could_not_read_its_backup_starts_no_paid_work(tmp_path, monkeypatch):
    """Its empty database knows nothing of the day's spend, and what it ran could not be saved."""
    first = Container(tmp_path, "disk-1", monkeypatch)
    first.seed_finished_survey()
    half_of(first.volume / DB_NAME)
    new = Container(tmp_path, "disk-2", monkeypatch, item_invite_code="code").boot()
    assert new.client.get("/api/health").json()["ok"] is False
    assert new.client.post("/api/scans", json={"bbox": SEATTLE}).status_code == 503
    with new.conn() as conn:
        scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
        db.update_scan(conn, scan_id, status="done")
    reply = new.client.post(f"/api/scans/{scan_id}/items", json={"target": "bench", "invite_code": "code"})
    assert reply.status_code == 503 and "paused" in reply.json()["detail"]
    new.worker.stop()


def test_a_restart_during_a_backup_never_leaves_part_of_a_photo_under_its_name(live, monkeypatch):
    real = shutil.copy2
    kept = live.volume / "evidence" / "scan1_issue1_123.jpg"
    whole = kept.read_bytes()
    (live.run_dir / "evidence" / "scan1_issue2_456.jpg").write_bytes(whole)

    def killed_half_way(src, dst, **kwargs):
        if "volume" not in str(dst):
            return real(src, dst, **kwargs)
        with open(dst, "wb") as f:
            f.write(whole[:len(whole) // 2])
        raise OSError("the container stopped here")

    monkeypatch.setattr(store_module.shutil, "copy2", killed_half_way)
    with pytest.raises(OSError):
        live.store.save_now()
    assert kept.read_bytes() == whole
    assert not (live.volume / "evidence" / "scan1_issue2_456.jpg").exists()


def test_a_photo_cut_short_on_the_volume_is_written_again_by_the_next_backup(live):
    kept = live.volume / "evidence" / "scan1_issue1_123.jpg"
    half_of(kept)
    live.store.save_now()
    assert kept.read_bytes() == (live.run_dir / "evidence" / kept.name).read_bytes()


def test_a_backup_writes_only_the_photos_the_volume_does_not_have(live, monkeypatch):
    real, written = shutil.copy2, []

    def counting(src, dst, **kwargs):
        if "evidence" in str(dst):
            written.append(str(dst))
        return real(src, dst, **kwargs)

    monkeypatch.setattr(store_module.shutil, "copy2", counting)
    live.store.save_now()
    assert written == []  # the photos are linked into the staging folder, and the Volume has them already


def test_the_app_keeps_its_data_on_the_volume_through_ordinary_restarts(live, tmp_path, monkeypatch):
    """The plain case: a finished survey, its photo and its address survive a restart."""
    new = Container(tmp_path, "disk-2", monkeypatch).boot()
    assert wait_for(lambda: new.client.get("/api/demo").json() == [{"scan_id": DEMO_ID, "name": "Example",
                                                                     "bbox": SEATTLE}])
    assert new.client.get("/api/evidence/scan1_issue1_123.jpg").status_code == 200
    new.worker.stop()
