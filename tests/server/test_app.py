import pytest
from fastapi.testclient import TestClient

from pipeline import config, db
from server import limits
from server.app import create_app
from server.settings import Settings

BBOX = [126.9842, 37.5623, 126.9858, 37.5632]
PREVIEW = {"photos": 42, "sequences": 2, "captured_from": "2024-05-01T00:00:00Z",
           "captured_to": "2025-09-24T07:21:37Z", "est_cost_usd": 0.24}


class FakeWorker:
    def __init__(self):
        self.submitted = []

    def start(self):
        pass

    def stop(self):
        pass

    def submit(self, scan_id):
        self.submitted.append(scan_id)


@pytest.fixture
def make(tmp_path):
    def build(**overrides):
        settings = Settings(**{"static_dir": tmp_path / "no-web", **overrides})
        worker = FakeWorker()
        app = create_app(settings, worker=worker, preview=lambda bbox, max_photos: PREVIEW)
        return TestClient(app), worker
    return build


def store():
    return db.connect(config.run_dir("server") / "threshold.db")


def seed():
    conn = store()
    scan_id = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, scan_id, status="done", image_count=60, cost_usd=0.33, processing_seconds=120.0)
    evidence = {"image": f"scan{scan_id}_entrance1.jpg", "image_id": "123", "creator": "filipb",
                "captured_at": "2025-09-24T07:21:37Z", "link": "https://www.mapillary.com/app/?pKey=123",
                "license": "CC BY-SA 4.0", "blurred_regions": 0}
    stairs = db.add_entrance(conn, scan_id, {
        "frame_idx": 2, "place_name": "Cafe", "category": "cafe", "lat": 37.5627, "lng": 126.985, "step_count": 2,
        "stairs_detected": True, "sill_possible": False, "review_status": "auto", "evidence": evidence,
        "captured_at": "2025-09-24T07:21:37Z"})
    sill = db.add_entrance(conn, scan_id, {
        "frame_idx": 9, "place_name": "emart24", "category": "convenience store", "lat": 37.5628, "lng": 126.9851,
        "step_count": 1, "stairs_detected": False, "sill_possible": True, "review_status": "needs_review",
        "review_reasons": ["possible_sill"], "captured_at": "2021-01-01T00:00:00Z"})
    issue = db.add_issue(conn, scan_id, {
        "issue_type": "obstruction", "object_label": "electric scooter", "frame_start": 4, "frame_end": 6,
        "frame_idx": 5, "lat": 37.5626, "lng": 126.9849, "overlap_ratio": 0.6, "review_status": "auto",
        "captured_at": "2025-09-24T07:21:37Z"})
    broken = db.add_issue(conn, scan_id, {
        "issue_type": "missing_blocks", "frame_start": 11, "frame_end": 11, "frame_idx": 11, "lat": 37.5629,
        "lng": 126.9852, "review_status": "needs_review", "review_reasons": ["possible_defect"],
        "captured_at": "2025-09-24T07:21:37Z"})
    return scan_id, stairs, sill, issue, broken


def test_health_and_config(make):
    client, _ = make()
    assert client.get("/api/health").json() == {"ok": True, "forwarded_for_entries": 0}
    assert client.get("/api/health", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}).json()["forwarded_for_entries"] == 2
    assert client.get("/api/config").json() == {"invite_required": False, "max_bbox_deg2": 0.0001}
    client, _ = make(invite_code="abc")
    assert client.get("/api/config").json()["invite_required"] is True


def test_preview_checks_the_area(make):
    client, _ = make()
    assert client.post("/api/preview", json={"bbox": BBOX}).json() == PREVIEW
    too_big = client.post("/api/preview", json={"bbox": [126.9, 37.5, 127.0, 37.6]})
    assert too_big.status_code == 422 and "too large" in too_big.json()["detail"]
    assert client.post("/api/preview", json={"bbox": [126.99, 37.56, 126.98, 37.57]}).status_code == 422


def test_a_failed_preview_is_a_502_with_a_message(tmp_path):
    from pipeline.mapillary import MapillaryError

    def boom(bbox, max_photos):
        raise MapillaryError("Mapillary HTTP 500 for /images: split depth")

    client = TestClient(create_app(Settings(static_dir=tmp_path / "no-web"), worker=FakeWorker(), preview=boom))
    response = client.post("/api/preview", json={"bbox": BBOX})
    assert response.status_code == 502 and "Mapillary did not answer" in response.json()["detail"]


def test_no_survey_starts_when_todays_budget_is_used_up(make):
    client, worker = make()
    conn = store()
    spent = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, spent, status="done", cost_usd=config.DAILY_COST_CAP_USD)
    response = client.post("/api/scans", json={"bbox": BBOX})
    assert response.status_code == 503 and "budget" in response.json()["detail"]
    assert worker.submitted == []


def test_starting_a_scan_queues_it_and_keeps_only_a_hashed_address(make):
    client, worker = make()
    response = client.post("/api/scans", json={"bbox": BBOX})
    assert response.status_code == 202
    scan_id = response.json()["id"]
    assert worker.submitted == [scan_id]
    stored = db.get_scan(store(), scan_id)
    assert stored["status"] == "queued" and len(stored["client_ip_hash"]) == 32
    assert stored["client_ip_hash"] == limits.ip_hash("testclient", "threshold")  # the default salt in tests
    assert "client_ip_hash" not in client.get(f"/api/scans/{scan_id}").json()


def test_invite_code_is_required_when_set(make):
    client, _ = make(invite_code="abc")
    assert client.post("/api/scans", json={"bbox": BBOX}).status_code == 403
    assert client.post("/api/scans", json={"bbox": BBOX, "invite_code": "nope"}).status_code == 403
    assert client.post("/api/scans", json={"bbox": BBOX, "invite_code": "abc"}).status_code == 202


def test_three_scans_per_address_per_day(make):
    client, _ = make()
    for _ in range(3):
        assert client.post("/api/scans", json={"bbox": BBOX}).status_code == 202
    assert client.post("/api/scans", json={"bbox": BBOX}).status_code == 429
    other = client.post("/api/scans", json={"bbox": BBOX}, headers={"x-forwarded-for": "5.6.7.8"})
    assert other.status_code == 202


def test_scan_json(make):
    client, _ = make()
    scan_id, *_ = seed()
    body = client.get(f"/api/scans/{scan_id}").json()
    assert body["status"] == "done"
    assert body["counts"] == {"defects": 1, "obstructions": 1, "entrances": 2, "entrances_with_steps": 1,
                              "needs_review": 2}
    assert client.get("/api/scans/999").status_code == 404


def test_entrances_carry_their_step_label(make):
    client, _ = make()
    scan_id, stairs, sill, *_ = seed()
    rows = {e["id"]: e for e in client.get(f"/api/scans/{scan_id}/entrances").json()}
    assert rows[stairs]["label"] == {"icon": "▲", "text": "2 steps"}
    assert rows[stairs]["evidence"]["url"] == f"/api/evidence/scan{scan_id}_entrance1.jpg"
    assert rows[sill]["label"] == {"icon": "?", "text": "Possible sill"} and rows[sill]["old_photo"] is True
    assert "verdict" not in rows[stairs] and "has_ramp" not in rows[stairs]


def test_issues_list_durable_defects_first(make):
    client, _ = make()
    scan_id, _, _, issue, broken = seed()
    rows = client.get(f"/api/scans/{scan_id}/issues").json()
    assert [r["id"] for r in rows] == [broken, issue]
    assert rows[0]["label"] == {"icon": "✖", "text": "Tactile blocks missing"} and rows[0]["durable"] is True
    assert rows[1]["label"] == {"icon": "⛔", "text": "Blocked: electric scooter"}


def test_evidence_is_served_only_by_its_name(make):
    client, _ = make()
    folder = config.run_dir("server") / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "scan1_entrance1.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    (folder / "notes.txt").write_text("not a photo", encoding="utf-8")
    (folder / "secret.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    ok = client.get("/api/evidence/scan1_entrance1.jpg")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    assert client.get("/api/evidence/scan1_issue9.jpg").status_code == 404
    assert client.get("/api/evidence/threshold.db").status_code == 404
    assert client.get("/api/evidence/..%2Fthreshold.db").status_code == 404
    assert client.get("/api/evidence/notes.txt").status_code == 404
    assert client.get("/api/evidence/secret.jpg").status_code == 404  # exists, but the name is not an evidence name


def test_reviewing_an_entrance_fixes_it_and_adds_the_time(make):
    client, _ = make()
    scan_id, stairs, sill, *_ = seed()
    fixed = client.post(f"/api/entrances/{sill}/review",
                        json={"action": "correct", "seconds": 12.5, "step_count": 0}).json()
    assert fixed["review_status"] == "verified" and fixed["step_count"] == 0
    assert fixed["label"] == {"icon": "✓", "text": "No steps"}
    (entry,) = db.get_entrance(store(), sill)["corrections"]
    assert entry["before"] == {"step_count": 1} and entry["seconds"] == 12.5
    client.post(f"/api/entrances/{stairs}/review", json={"action": "confirm", "seconds": 3.0})
    assert db.get_scan(store(), scan_id)["review_seconds"] == 15.5
    again = client.post(f"/api/entrances/{stairs}/review", json={"action": "confirm", "seconds": 3600})
    assert again.status_code == 409  # an item is reviewed once, so review time cannot be inflated
    assert db.get_scan(store(), scan_id)["review_seconds"] == 15.5
    assert len(db.get_entrance(store(), stairs)["corrections"]) == 1
    assert client.post("/api/entrances/999/review", json={"action": "confirm", "seconds": 1}).status_code == 404
    assert client.post(f"/api/entrances/{stairs}/review", json={"action": "delete", "seconds": 1}).status_code == 422
    ramp = client.post(f"/api/entrances/{stairs}/review", json={"action": "correct", "seconds": 1, "has_ramp": True})
    assert ramp.status_code == 422  # spec revision 4: the step count is the only editable field


def test_a_rejected_issue_disappears_from_the_map(make):
    client, _ = make()
    scan_id, _, _, issue, broken = seed()
    assert client.post(f"/api/issues/{issue}/review", json={"action": "reject", "seconds": 2.0}).json()[
        "review_status"] == "rejected"
    assert [r["id"] for r in client.get(f"/api/scans/{scan_id}/issues").json()] == [broken]
    assert client.get(f"/api/scans/{scan_id}").json()["counts"]["obstructions"] == 0


def test_demo_lists_only_scans_that_exist(make):
    scan_id, *_ = seed()
    client, _ = make(demo_scans=((scan_id, "Myeongdong"), (999, "Gone")))
    assert client.get("/api/demo").json() == [{"scan_id": scan_id, "name": "Myeongdong", "bbox": BBOX}]


def test_the_built_web_app_is_served_when_present(make, tmp_path):
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Threshold</title>", encoding="utf-8")
    client, _ = make(static_dir=web)
    assert "Threshold" in client.get("/").text
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/docs").status_code == 404
    assert client.get("/api/health").json()["ok"] is True


def test_validation_errors_carry_a_text_detail(make):
    client, _ = make()
    response = client.post("/api/scans", json={"bbox": "not a list"})
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)
    bad = client.post("/api/issues/1/review", json={"action": "confirm", "seconds": 1, "extra": True})
    assert bad.status_code == 422 and isinstance(bad.json()["detail"], str)


def test_a_correction_needs_a_step_count(make):
    client, _ = make()
    _, _, sill, *_ = seed()
    response = client.post(f"/api/entrances/{sill}/review", json={"action": "correct", "seconds": 1})
    assert response.status_code == 422 and "step count" in response.json()["detail"]


def test_demo_surveys_are_read_only(make):
    scan_id, stairs, _, issue, _ = seed()
    client, _ = make(demo_scans=((scan_id, "Seongsu"),))
    assert client.post(f"/api/entrances/{stairs}/review", json={"action": "confirm", "seconds": 1}).status_code == 403
    assert client.post(f"/api/issues/{issue}/review", json={"action": "reject", "seconds": 1}).status_code == 403
    assert client.get(f"/api/scans/{scan_id}").json()["review_seconds"] == 0


def test_parallel_starts_from_one_address_stop_at_the_daily_limit(make):
    from concurrent.futures import ThreadPoolExecutor
    client, worker = make()
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: client.post("/api/scans", json={"bbox": BBOX}).status_code, range(8)))
    assert codes.count(202) == 3 and codes.count(429) == 5


class FakeStore:
    def __init__(self):
        self.restored = 0
        self.saves = 0

    def restore(self):
        self.restored += 1
        return False

    def request_save(self):
        self.saves += 1


def test_the_dataset_store_restores_at_start_and_saves_after_a_review(tmp_path):
    _, _, sill, _, broken = seed()
    store = FakeStore()
    app = create_app(Settings(static_dir=tmp_path / "no-web"), worker=FakeWorker(),
                     preview=lambda bbox, max_photos: PREVIEW, store=store)
    with TestClient(app) as client:
        assert store.restored == 1
        client.post(f"/api/entrances/{sill}/review", json={"action": "correct", "seconds": 1, "step_count": 0})
        client.post(f"/api/issues/{broken}/review", json={"action": "confirm", "seconds": 1})
    assert store.saves == 2


def test_a_new_scan_gets_the_standard_of_its_area(make):
    client, _ = make()
    seattle = client.post("/api/scans", json={"bbox": [-122.3230, 47.6130, -122.3170, 47.6175]}).json()["id"]
    seoul = client.post("/api/scans", json={"bbox": BBOX}).json()["id"]
    assert db.get_scan(store(), seattle)["jurisdiction"] == "US"
    assert db.get_scan(store(), seoul)["jurisdiction"] == "KR"


def seed_us():
    conn = store()
    scan_id = db.create_scan(conn, "mapillary", [-122.3230, 47.6130, -122.3170, 47.6175], "US")
    db.update_scan(conn, scan_id, status="done", image_count=200)
    unclear = db.add_issue(conn, scan_id, {
        "issue_type": "curb_ramp_warning", "object_label": "unclear", "frame_start": 2, "frame_end": 4,
        "frame_idx": 4, "lat": 47.614, "lng": -122.32, "review_status": "needs_review", "review_reasons": ["unclear"],
        "model_outputs": {"check": {"visible": True, "answer": "unclear", "box": [], "confidence": 0.4, "note": ""}},
        "captured_at": "2022-08-30T10:00:00Z"})
    cone = db.add_issue(conn, scan_id, {
        "issue_type": "obstruction", "object_label": "traffic cone", "frame_start": 6, "frame_end": 7,
        "frame_idx": 6, "lat": 47.615, "lng": -122.32, "review_status": "needs_review",
        "review_reasons": ["low_confidence"], "captured_at": "2022-08-30T10:00:05Z"})
    return scan_id, unclear, cone


def test_an_unclear_check_is_answered_by_the_reviewer(make):
    client, _ = make()
    scan_id, unclear, cone = seed_us()
    out = client.post(f"/api/issues/{unclear}/review", json={"action": "answer", "answer": "no", "seconds": 5}).json()
    assert out["check"]["answer"] == "no" and out["defect"] is True and out["review_status"] == "verified"
    assert out["label"] == {"icon": "✖", "text": "Curb ramp without detectable warning"}
    assert client.get(f"/api/scans/{scan_id}").json()["counts"]["defects"] == 1
    assert client.post(f"/api/issues/{cone}/review",
                       json={"action": "answer", "answer": "no", "seconds": 1}).status_code == 422  # not a check
    _, other, _ = seed_us()
    assert client.post(f"/api/issues/{other}/review", json={"action": "answer", "seconds": 1}).status_code == 422



def test_an_inventory_row_is_confirmed_with_yes_not_answered_no(make):
    client, _ = make()
    scan_id, _, _ = seed_us()
    conn = store()
    pole = db.add_issue(conn, scan_id, {"issue_type": "utility_pole", "object_label": "unclear", "frame_idx": 9,
                                        "lat": 47.617, "lng": -122.32, "review_status": "needs_review",
                                        "review_reasons": ["unclear"]})
    refused = client.post(f"/api/issues/{pole}/review", json={"action": "answer", "answer": "no", "seconds": 1})
    assert refused.status_code == 422 and "reject" in refused.json()["detail"]
    ok = client.post(f"/api/issues/{pole}/review", json={"action": "answer", "answer": "yes", "seconds": 1}).json()
    assert ok["check"]["answer"] == "yes" and ok["defect"] is False


def test_issues_of_a_us_scan_list_check_defects_first(make):
    client, _ = make()
    scan_id, unclear, cone = seed_us()
    conn = store()
    no_warning = db.add_issue(conn, scan_id, {"issue_type": "curb_ramp_warning", "object_label": "no",
                                              "frame_idx": 8, "lat": 47.616, "lng": -122.32,
                                              "review_status": "needs_review", "review_reasons": ["possible_defect"]})
    ids = [r["id"] for r in client.get(f"/api/scans/{scan_id}/issues").json()]
    assert ids == [no_warning, unclear, cone]
