"""Case 2: the daily cap, the visitors' cap or the item cap is reached, before a start or in the middle of a run."""
import dataclasses

import pytest

from pipeline import collect, config, db, item_runs, runner, survey_items
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import RunAborted
from server import jobs
from server.jobs import ScanWorker
from tests.pipeline.fakes import FakeApi
from tests.pipeline.test_runner import fake_frames, sam, spark
from tests.resilience.harness import SEATTLE, Container

VISITOR = "a" * 32
CHECK = survey_items.Check(id="agent_bench", find="bench", target="bench", report_when="", label_yes="", label_no="",
                           label_unclear="", ask="", inventory=True, find_by="photos")
LONG_AGO = "2026-01-01T00:00:00Z"  # counts toward the visitors' cap, not today's


def walk(photos: int = 30) -> list[dict]:
    return [{"id": f"p{i}", "sequence": "s", "lat": 47.6100 + i * 2e-5, "lng": -122.3190, "compass": 90.0,
             "captured_at": "2025-09-24T10:00:00Z", "creator": "c", "thumb_2048_url": "u"} for i in range(photos)]


@pytest.fixture
def mapillary_has_photos(monkeypatch):
    monkeypatch.setattr(collect, "candidate_sequences", lambda bbox: [walk()])
    monkeypatch.setattr(collect, "fetch_photo", lambda rec: (160, 120))


def scan_with_budget_left(tmp_path, left_usd: float, daily: bool) -> tuple:
    """A visitor's queued survey, when this much is left of the day's budget or of the visitors' $40."""
    path = tmp_path / "t.db"
    conn = db.connect(path)
    spent = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, spent, status="done", client_ip_hash=VISITOR, created_at=db.now() if daily else LONG_AGO,
                   cost_usd=(config.DAILY_COST_CAP_USD if daily else 40.0) - left_usd)
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, scan_id, client_ip_hash=VISITOR)
    return conn, path, scan_id


@pytest.mark.parametrize("left_usd", [0.0, 0.20], ids=["nothing left", "twenty cents left"])
@pytest.mark.parametrize("daily", [True, False], ids=["daily cap", "visitor cap"])
def test_a_survey_that_reaches_the_front_of_the_queue_without_budget_says_so(tmp_path, mapillary_has_photos, left_usd,
                                                                             daily):
    conn, path, scan_id = scan_with_budget_left(tmp_path, left_usd, daily)
    worker = ScanWorker(path, tmp_path, public_cap_usd=40.0)
    worker._run_pipeline(scan_id)
    scan = db.get_scan(conn, scan_id)
    conn.close()
    assert scan["status"] == "stopped_cost_cap", scan["error"]


class SpendingApi(FakeApi):
    """A fake MetaApi that reserves and settles on the real meter, as MetaApi does."""

    def __init__(self, meter: CostMeter, per_call_usd: float = 0.05):
        super().__init__(spark=spark, sam=sam)
        self.meter, self.per_call_usd = meter, per_call_usd

    def _charge(self) -> None:
        self.meter.reserve(self.per_call_usd)
        self.meter.settle(self.per_call_usd, self.per_call_usd)

    def spark_json(self, parts, schema_name, schema, estimate_usd=0.0):
        self._charge()
        return super().spark_json(parts, schema_name, schema, estimate_usd)

    def spark_video_json(self, prompt, file_id, schema_name, schema):
        self._charge()
        return super().spark_video_json(prompt, file_id, schema_name, schema)

    def sam_image(self, prompt, image):
        self._charge()
        return super().sam_image(prompt, image)

    def sam_video(self, prompt, file_id, frames):
        self._charge()
        return super().sam_video(prompt, file_id, frames)


def test_the_cap_in_the_middle_of_a_survey_leaves_a_final_state_saved_spend_and_a_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.collect, "collect", lambda bbox, max_photos: fake_frames())
    monkeypatch.setattr(runner.entrance_read, "original_photo", collect.photo_path)
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: SpendingApi(meter))
    path = tmp_path / "t.db"
    conn = db.connect(path)
    other = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, other, status="done", cost_usd=config.DAILY_COST_CAP_USD - 0.4)  # $0.40 left today
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    backups = []
    worker = ScanWorker(path, tmp_path, on_done=backups.append)
    worker.start()
    worker.submit(scan_id)
    worker.submit(scan_id + 1000)  # a job after it: the worker goes on
    worker.join()
    worker.stop()
    scan = db.get_scan(conn, scan_id)
    assert scan["status"] == "stopped_cost_cap" and "cost cap" in scan["error"]
    assert 0 < scan["cost_usd"] <= 0.4 + 1e-9
    assert "running" not in scan["stage_status"].values()
    assert db.daily_cost(conn, db.now()[:10]) <= config.DAILY_COST_CAP_USD + 1e-9
    assert scan_id in backups
    conn.close()


def test_a_cap_during_the_evidence_stage_keeps_the_photos_already_paid_for(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    frames = fake_frames()
    db.add_frames(conn, scan_id, frames)
    for frame in frames[:4]:
        db.add_issue(conn, scan_id, {"issue_type": "obstruction", "frame_idx": frame["idx"], "review_status": "auto"})
    calls = []

    def capped(prompt, media):
        calls.append(prompt)
        if len(calls) > 5:  # person and license plate for two rows, then half of the third
            raise CostCapReached("cost cap $3.00")
        return ""

    scan = runner.Scan(conn, scan_id, FakeApi(sam=capped), tmp_path / "run", SEATTLE, workers=1)
    scan.frames = frames
    with pytest.raises(CostCapReached):
        runner.stage_evidence(scan, kinds=("issue",))
    on_disk = sorted((tmp_path / "run" / "evidence").glob("*.jpg"))
    linked = [r for r in db.issues(conn, scan_id) if (r["evidence"] or {}).get("image")]
    conn.close()
    assert len(on_disk) == 2
    assert len(linked) == 2


def test_a_queue_left_from_yesterday_does_not_spend_on_todays_budget(tmp_path, monkeypatch):
    """daily_cost counts a job on the day it was created, so a queued job run a day later would escape the day's cap."""
    monkeypatch.setattr(jobs, "run_scan", lambda scan: pytest.fail("the survey ran"))
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, scan_id, created_at=LONG_AGO)
    done = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, done, status="done")
    run_id = db.create_item_run(conn, done, "bench")
    conn.execute("UPDATE item_run SET created_at = ? WHERE id = ?", (LONG_AGO, run_id))
    conn.commit()
    worker = ScanWorker(tmp_path / "t.db", tmp_path, items=object())
    worker._run_pipeline(scan_id)
    worker._run_item(run_id)
    scan, run = db.get_scan(conn, scan_id), db.item_run(conn, run_id)
    conn.close()
    assert (scan["status"], run["status"]) == ("failed", "failed")
    assert "UTC day" in scan["error"] and "UTC day" in run["error"]


def test_a_job_asks_for_a_backup_when_it_starts_and_as_it_spends(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: SpendingApi(meter))

    def spends_sixty_cents(scan):
        for _ in range(12):
            scan.api._charge()

    monkeypatch.setattr(jobs, "run_scan", spends_sixty_cents)
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    conn.close()
    asked = []
    worker = ScanWorker(tmp_path / "t.db", tmp_path, checkpoint=lambda: asked.append(1))
    worker.start()  # picks up the queued survey
    worker.join()
    worker.stop()
    assert len(asked) == 3  # at the start, at $0.25 and at $0.50


def test_a_full_queue_turns_a_survey_away_with_a_sentence_and_no_row(tmp_path, monkeypatch):
    live = Container(tmp_path, "disk-1", monkeypatch, max_queued=1)
    assert live.client.post("/api/scans", json={"bbox": SEATTLE}).status_code == 202  # nothing reads the queue yet
    reply = live.client.post("/api/scans", json={"bbox": SEATTLE})
    assert reply.status_code == 503 and "waiting" in reply.json()["detail"]
    with live.conn() as conn:
        assert len(db.list_scans(conn)) == 1


def test_the_report_sentences_of_a_finished_survey_count_toward_the_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: SpendingApi(meter))

    def finished(scan):
        scan.api._charge()  # $0.05 of pipeline calls, saved
        db.update_scan(scan.conn, scan.scan_id, status="done", cost_usd=scan.api.meter.spent_usd)

    monkeypatch.setattr(jobs, "run_scan", finished)
    monkeypatch.setattr(jobs.report, "make_text", lambda conn, run_dir, scan_id, api: api.meter.settle(0.0, 0.03))
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    ScanWorker(tmp_path / "t.db", tmp_path)._run_pipeline(scan_id)
    cost = db.get_scan(conn, scan_id)["cost_usd"]
    conn.close()
    assert cost == pytest.approx(0.08)


class ItemsStoppedByTheBreaker:
    def start(self, api, photos, target, on_progress):
        api.meter.spent_usd += 0.04
        raise RunAborted("5 requests in a row failed")


def test_an_item_run_stopped_by_the_circuit_breaker_keeps_its_spend(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, scan_id, status="done")
    photos = item_runs.ScanPhotos(scan_id, SEATTLE, "US", [{"idx": 0, "clip": 0}], {0: tmp_path / "0.jpg"})
    monkeypatch.setattr(item_runs, "scan_photos", lambda conn, scan_id: photos)
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: dataclasses.make_dataclass("Api", ["meter"])(meter))
    run_id = db.create_item_run(conn, scan_id, "bench")
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=lambda s: None, items=ItemsStoppedByTheBreaker())
    worker.start()
    worker.submit_item(run_id)
    worker.join()
    worker.stop()
    run = db.item_run(conn, run_id)
    conn.close()
    assert run["status"] == "failed" and "in a row" in run["error"]
    assert run["cost_usd"] == pytest.approx(0.04)


class CityCheckFails:
    def start(self, api, photos, target, on_progress):
        return {"status": "ready", "check": CHECK, "test": {}}

    def city_check(self, photos, check, rows):
        raise OSError("the city file is not on this disk")


def test_a_city_check_that_fails_keeps_the_items_paid_rows(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.update_scan(conn, scan_id, status="done")
    photos = item_runs.ScanPhotos(scan_id, SEATTLE, "US", [{"idx": 0, "clip": 0}], {0: tmp_path / "0.jpg"})
    monkeypatch.setattr(item_runs, "scan_photos", lambda conn, scan_id: photos)
    monkeypatch.setattr(item_runs, "run_item_on_scan", lambda *a, **k: [{"id": "item1-1"}])
    monkeypatch.setattr(jobs, "MetaApi", lambda log, meter: dataclasses.make_dataclass("Api", ["meter"])(meter))
    run_id = db.create_item_run(conn, scan_id, "bench")
    worker = ScanWorker(tmp_path / "t.db", tmp_path, run=lambda s: None, items=CityCheckFails())
    worker._run_item(run_id)
    run = db.item_run(conn, run_id)
    conn.close()
    assert run["status"] == "done" and run["rows"] == [{"id": "item1-1"}]


def test_an_item_run_the_cap_stopped_part_way_does_not_report_the_photos_it_reached_as_the_survey(tmp_path, monkeypatch):
    stopped = CostCapReached("cost cap $1.00")
    monkeypatch.setattr(item_runs.survey_checks, "run_check", lambda *a, **k: ([{"frame_idx": 0}], {"stopped": stopped}))
    photos = item_runs.ScanPhotos(1, SEATTLE, "US", [{"idx": 0, "clip": 0}], {0: tmp_path / "0.jpg"})
    with pytest.raises(CostCapReached):
        item_runs.run_item_on_scan(None, photos, CHECK, tmp_path, 1)


def test_a_cap_during_the_items_keeps_the_rows_already_answered(tmp_path, monkeypatch):
    """Muse Spark answered the first two photos of a check when the cap came: those answers are paid for."""
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
    frames = fake_frames()
    db.add_frames(conn, scan_id, frames)
    answers = []

    def capped(parts, schema_name, schema):
        answers.append(schema_name)
        if len(answers) > 2:
            raise CostCapReached("cost cap $3.00")
        return {"visible": True, "answer": "yes", "confidence": 0.9, "note": "a"}

    api = FakeApi(sam=lambda prompt, media: "")
    api.spark_json = lambda parts, schema_name, schema, estimate_usd=0.0: capped(parts, schema_name, schema)
    scan = runner.Scan(conn, scan_id, api, tmp_path / "run", SEATTLE, workers=1)
    scan.frames = frames
    picks = [{"idx": f["idx"], "area": 900, "group": [f["idx"]]} for f in frames[:4]]
    monkeypatch.setattr(runner.survey_checks, "find_areas",
                        lambda api, check, idxs, photos, workers, known: {p["idx"]: p["area"] for p in picks})
    monkeypatch.setattr(runner.survey_checks, "groups", lambda areas, idxs: [p["group"] for p in picks])
    monkeypatch.setattr(runner.survey_checks, "merge_near", lambda found, by_idx: found)
    only = runner.survey_items.load("US").checks[3]  # found on photos, not on a video
    assert only.find_by == "photos"
    scan.standard = runner.survey_items.load("US")
    scan.only_checks = (only.id,)
    with pytest.raises(CostCapReached):
        runner.stage_defects(scan)
    rows = db.issues(conn, scan_id)
    conn.close()
    assert [r["issue_type"] for r in rows] == [only.id, only.id]


@pytest.mark.parametrize("cap, message", [(True, "Today's survey budget is used up"),
                                          (False, "The live demo's survey budget is used up")])
def test_an_item_is_refused_with_a_message_when_the_budget_is_used_up(tmp_path, monkeypatch, cap, message):
    live = Container(tmp_path, "disk-1", monkeypatch, item_invite_code="code").boot()
    with live.conn() as conn:
        scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
        db.update_scan(conn, scan_id, status="done", client_ip_hash=VISITOR, cost_usd=10.0 if cap else 40.0,
                       created_at=db.now() if cap else LONG_AGO)
    reply = live.client.post(f"/api/scans/{scan_id}/items", json={"target": "bench", "invite_code": "code"})
    assert reply.status_code == 503 and message in reply.json()["detail"]
    live.worker.stop()


def test_a_survey_is_refused_with_a_message_and_no_row_when_the_budget_is_used_up(tmp_path, monkeypatch):
    live = Container(tmp_path, "disk-1", monkeypatch).boot()
    with live.conn() as conn:
        scan_id = db.create_scan(conn, "mapillary", SEATTLE, "US")
        db.update_scan(conn, scan_id, status="done", client_ip_hash=VISITOR, cost_usd=40.0, created_at=LONG_AGO)
    reply = live.client.post("/api/scans", json={"bbox": SEATTLE})
    assert reply.status_code == 503 and "the finished surveys stay open" in reply.json()["detail"]
    assert live.client.get(f"/api/scans/{scan_id}").status_code == 200
    with live.conn() as conn:
        assert len(db.list_scans(conn)) == 1
    live.worker.stop()
