import json

import pytest
from fastapi.testclient import TestClient

from pipeline import config, db, mapillary, survey_items
from pipeline.runner import RESUME_MIN_USD
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

    def submit_item(self, run_id):
        self.submitted.append(("item", run_id))


@pytest.fixture
def make(tmp_path):
    def build(**overrides):
        settings = Settings(**{"static_dir": tmp_path / "no-web", **overrides})
        worker = FakeWorker()
        app = create_app(settings, worker=worker, preview=lambda bbox, max_photos, checks=0: PREVIEW)
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
    health = client.get("/api/health").json()
    assert health["ok"] is True and health["forwarded_for_entries"] == 0 and health["mask_decoding"]
    assert client.get("/api/health", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}).json()["forwarded_for_entries"] == 2
    assert client.get("/api/config").json() == {"invite_required": False, "max_bbox_deg2": 0.0001, "items": True,
                                                "make_items": False, "coverage_tiles": True, "report": True}
    client, _ = make(invite_code="abc")
    assert client.get("/api/config").json()["invite_required"] is True


def test_preview_checks_the_area(make):
    client, _ = make()
    body = client.post("/api/preview", json={"bbox": BBOX}).json()
    assert {k: body[k] for k in PREVIEW} == PREVIEW
    assert body["standard"] == "KR" and body["cap_usd"] == config.SCAN_COST_CAP_USD
    assert [i["id"] for i in body["items"]][-2:] == ["obstruction", "entrance"]  # what the survey would look for
    too_big = client.post("/api/preview", json={"bbox": [126.9, 37.5, 127.0, 37.6]})
    assert too_big.status_code == 422 and "too large" in too_big.json()["detail"]
    assert client.post("/api/preview", json={"bbox": [126.99, 37.56, 126.98, 37.57]}).status_code == 422


def test_a_failed_preview_is_a_502_with_a_message(tmp_path):
    from pipeline.mapillary import MapillaryError

    def boom(bbox, max_photos, checks=0):
        raise MapillaryError("Mapillary HTTP 500 for /images: split depth")

    client = TestClient(create_app(Settings(static_dir=tmp_path / "no-web"), worker=FakeWorker(), preview=boom))
    response = client.post("/api/preview", json={"bbox": BBOX})
    assert response.status_code == 502 and "Mapillary did not answer" in response.json()["detail"]


def test_no_survey_starts_when_the_public_budget_is_used_up(make):
    client, worker = make(public_cost_cap_usd=2.0)
    conn = store()
    local = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, local, status="done", cost_usd=1.9)  # a CLI scan does not count
    assert client.post("/api/scans", json={"bbox": BBOX}).status_code == 202
    visitor = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, visitor, status="done", cost_usd=2.0 - RESUME_MIN_USD / 2, client_ip_hash="x")
    response = client.post("/api/scans", json={"bbox": BBOX})
    assert response.status_code == 503 and "finished surveys stay open" in response.json()["detail"]


def test_the_worker_gets_the_public_budget(tmp_path):
    app = create_app(Settings(static_dir=tmp_path / "no-web", public_cost_cap_usd=7.0),
                     preview=lambda bbox, max_photos, checks=0: PREVIEW)
    assert app.state.worker.public_cap_usd == 7.0


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
                              "needs_review": 2, "field_check": 0, "verified": 0, "rejected": 0,
                              "dropped": 0}
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
    (folder / "scan1_issue2_4001.jpg").write_bytes(b"\xff\xd8\xffjpeg")  # named with the photo's id (runner)
    ok = client.get("/api/evidence/scan1_entrance1.jpg")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    assert client.get("/api/evidence/scan1_issue2_4001.jpg").status_code == 200
    assert client.get("/api/evidence/scan1_issue2_x.jpg").status_code == 404
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
                     preview=lambda bbox, max_photos, checks=0: PREVIEW, store=store)
    with TestClient(app) as client:
        assert store.restored == 1
        client.post(f"/api/entrances/{sill}/review", json={"action": "correct", "seconds": 1, "step_count": 0})
        client.post(f"/api/issues/{broken}/review", json={"action": "confirm", "seconds": 1})
    assert store.saves == 2


def test_a_preview_is_priced_and_budgeted_for_the_checks_of_its_areas_standard(tmp_path):
    seen = []

    def fake(bbox, max_photos, checks=0):
        seen.append((max_photos, checks))
        return PREVIEW

    client = TestClient(create_app(Settings(static_dir=tmp_path / "no-web"), worker=FakeWorker(), preview=fake))
    client.post("/api/preview", json={"bbox": [-122.3230, 47.6130, -122.3170, 47.6175]})
    client.post("/api/preview", json={"bbox": BBOX})
    us, kr = (len(survey_items.load(code).checks) for code in ("US", "KR"))
    assert seen == [(config.photo_budget(checks=us), us), (config.photo_budget(checks=kr), kr)]


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


def test_a_finished_survey_downloads_as_one_zip_of_report_geojson_csv_and_evidence(make):
    import io
    import zipfile
    scan_id, *_ = seed()
    photos = config.run_dir("server") / "evidence"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / f"scan{scan_id}_entrance1.jpg").write_bytes(b"jpg")  # the seeded Cafe entrance's evidence
    client, _ = make(demo_scans=((scan_id, "Myeongdong"),))
    response = client.get(f"/api/scans/{scan_id}/files.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert f'filename="fieldless-survey-{scan_id}.zip"' in response.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(z.namelist())
    assert {"report.html", "report.md", f"survey_scan{scan_id}.geojson", f"evidence/scan{scan_id}_entrance1.jpg"} <= names
    assert any(n.endswith(".csv") for n in names)
    assert "Myeongdong" in z.read("report.html").decode("utf-8")  # the demo survey's name titles the report
    assert z.read(f"evidence/scan{scan_id}_entrance1.jpg") == b"jpg"


def test_survey_files_wait_for_the_survey_to_finish(make):
    client, _ = make()
    assert client.get("/api/scans/999/files.zip").status_code == 404
    conn = store()
    scan_id = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, scan_id, status="running")
    assert client.get(f"/api/scans/{scan_id}/files.zip").status_code == 409


def test_cant_tell_from_the_photo_keeps_the_item_for_a_site_visit_and_counts_the_time(make):
    client, _ = make()
    scan_id, _, sill, _, broken = seed()
    issue = client.post(f"/api/issues/{broken}/review", json={"action": "field_check", "seconds": 7.0}).json()
    assert issue["review_status"] == "field_check"
    assert broken in [r["id"] for r in client.get(f"/api/scans/{scan_id}/issues").json()]  # still on the map
    entrance = client.post(f"/api/entrances/{sill}/review", json={"action": "field_check", "seconds": 5.0}).json()
    assert entrance["review_status"] == "field_check"
    assert entrance["label"] == {"icon": "?", "text": "Possible sill"}  # nobody settled it
    counts = client.get(f"/api/scans/{scan_id}").json()["counts"]
    assert counts["needs_review"] == 0 and counts["field_check"] == 2
    assert db.get_scan(store(), scan_id)["review_seconds"] == 12.0
    again = client.post(f"/api/issues/{broken}/review", json={"action": "confirm", "seconds": 1.0})
    assert again.status_code == 409  # a person has answered it



def test_walks_headings_and_the_city_check_file(make):
    client, _ = make()
    scan_id, *_ = seed()
    conn = store()
    db.add_frames(conn, scan_id, [
        {"idx": i, "clip": clip, "image_id": f"img{i}", "sequence_id": "s", "lat": 37.5623 + i / 1e4,
         "lng": 126.985, "compass": 90.0 + i, "captured_at": "2025-09-24T07:21:37Z", "creator": "c",
         "width": 2048, "height": 1536} for i, clip in [(2, 0), (5, 0), (9, 1), (11, 1)]])
    walks = client.get(f"/api/scans/{scan_id}/walks").json()
    assert [w["clip"] for w in walks] == [0, 1] and walks[0]["points"][0] == pytest.approx([126.985, 37.5625])
    assert sorted(r["heading"] for r in client.get(f"/api/scans/{scan_id}/issues").json()) == [95.0, 101.0]
    assert sorted(e["heading"] for e in client.get(f"/api/scans/{scan_id}/entrances").json()) == [92.0, 99.0]
    assert "city_check" not in client.get(f"/api/scans/{scan_id}").json()
    path = config.run_dir("server") / f"city_check_scan{scan_id}.json"
    path.write_text("{not json", encoding="utf-8")
    assert "city_check" not in client.get(f"/api/scans/{scan_id}").json()  # a broken file is left out
    path.write_text('{"scan_id": %d, "items": {}}' % scan_id, encoding="utf-8")
    assert client.get(f"/api/scans/{scan_id}").json()["city_check"] == {"scan_id": scan_id, "items": {}}
    assert client.get("/api/scans/999/walks").status_code == 404


def test_undo_takes_back_a_decision_and_its_seconds_for_a_minute(make):
    client, _ = make()
    scan_id, stairs, sill, _, broken = seed()
    client.post(f"/api/issues/{broken}/review", json={"action": "reject", "seconds": 8.0})
    assert client.get(f"/api/scans/{scan_id}/issues").json()[-1]["id"] != broken  # gone from the map
    back = client.post(f"/api/issues/{broken}/review/undo").json()
    assert back["review_status"] == "needs_review" and db.get_issue(store(), broken)["corrections"] == []
    assert db.get_scan(store(), scan_id)["review_seconds"] == 0.0
    assert client.post(f"/api/issues/{broken}/review/undo").status_code == 409  # nothing left to take back
    client.post(f"/api/entrances/{sill}/review", json={"action": "correct", "seconds": 5.0, "step_count": 0})
    back = client.post(f"/api/entrances/{sill}/review/undo").json()
    assert back["step_count"] == 1 and back["review_status"] == "needs_review"
    client.post(f"/api/entrances/{stairs}/review", json={"action": "confirm", "seconds": 3.0})
    conn = store()
    row = db.get_entrance(conn, stairs)
    db.update_entrance(conn, stairs, corrections=[{**row["corrections"][0], "at": "2020-01-01T00:00:00Z"}])
    assert client.post(f"/api/entrances/{stairs}/review/undo").status_code == 409  # too late
    assert db.get_scan(store(), scan_id)["review_seconds"] == 3.0
    demo_client, _ = make(demo_scans=((scan_id, "Demo"),))
    assert demo_client.post(f"/api/entrances/{stairs}/review/undo").status_code == 403


def test_coverage_tiles_are_passed_through_gzipped_kept_for_an_hour_and_refused_outside_the_zooms(tmp_path):
    asked = []

    def tile(z, x, y):
        asked.append((z, x, y))
        if (x, y) == (9, 9):
            raise mapillary.MapillaryError("down")
        return None if (x, y) == (0, 0) else b"\x1a\x02pbf"

    client = TestClient(create_app(Settings(static_dir=tmp_path / "no-web"), worker=FakeWorker(),
                                   preview=lambda *a, **k: PREVIEW, tile=tile))
    ok = client.get("/api/mapillary/tiles/13/1312/2860")  # the test client takes gzip and unpacks it
    assert ok.status_code == 200 and ok.content == b"\x1a\x02pbf" and ok.headers["content-encoding"] == "gzip"
    assert ok.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    plain = client.get("/api/mapillary/tiles/13/1312/2860", headers={"Accept-Encoding": "identity"})
    assert plain.content == b"\x1a\x02pbf" and "content-encoding" not in plain.headers
    assert asked == [(13, 1312, 2860)]  # kept: Mapillary was asked once
    assert client.get("/api/mapillary/tiles/13/0/0").status_code == 204  # Mapillary has no tile there
    assert client.get("/api/mapillary/tiles/13/9/9").status_code == 502
    assert client.get("/api/mapillary/tiles/14/0/0").status_code == 404  # zoom 14 adds every photo: 10x the size
    assert client.get("/api/mapillary/tiles/6/64/0").status_code == 404  # x beyond 2**z
    off = TestClient(create_app(Settings(static_dir=tmp_path / "no-web", coverage_tiles=False), worker=FakeWorker(),
                                preview=lambda *a, **k: PREVIEW, tile=tile))
    assert off.get("/api/config").json()["coverage_tiles"] is False
    assert off.get("/api/mapillary/tiles/13/1312/2860").status_code == 404


def finished_scan(status="done"):
    conn = store()
    scan_id = db.create_scan(conn, "mapillary", [-122.32, 47.61, -122.31, 47.62], jurisdiction="US")
    db.add_frames(conn, scan_id, [{"idx": 0, "clip": 0, "image_id": "900", "sequence_id": "s", "lat": 47.615,
                                   "lng": -122.315, "compass": 0.0, "captured_at": "2025-06-01T10:00:00Z",
                                   "creator": "alice", "width": 2048, "height": 1152}])
    db.add_issue(conn, scan_id, {"issue_type": "utility_pole", "object_label": "yes", "frame_start": 0,
                                 "frame_end": 0, "frame_idx": 0, "lat": 47.615, "lng": -122.315,
                                 "review_status": "auto", "captured_at": "2025-06-01T10:00:00Z",
                                 "evidence": {"image": f"scan{scan_id}_issue1_900.jpg", "image_id": "900",
                                              "creator": "alice", "captured_at": "2025-06-01T10:00:00Z",
                                              "link": "https://www.mapillary.com/app/?pKey=900",
                                              "license": "CC BY-SA 4.0", "blurred_regions": 0}})
    db.update_scan(conn, scan_id, status=status, image_count=1)
    return conn, scan_id


def test_the_survey_report_page_is_the_document_with_the_saved_sentences(make):
    client, _ = make()
    conn, scan_id = finished_scan()
    db.set_report_text(conn, scan_id, {"summary": "Most poles were found.", "items": {}, "priorities": []}, "m")

    page = client.get(f"/api/scans/{scan_id}/report")

    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Street Survey Report" in page.text and "Most poles were found." in page.text
    assert f'src="../../evidence/scan{scan_id}_issue1_900.jpg"' in page.text
    assert client.get("/api/scans/9999/report").status_code == 404
    _, running = finished_scan(status="running")
    assert client.get(f"/api/scans/{running}/report").status_code == 409
    assert client.get("/api/config").json()["report"] is True  # the web shows its report link


def test_the_files_zip_report_carries_the_saved_sentences(make):
    import io
    import zipfile
    client, _ = make()
    conn, scan_id = finished_scan()
    db.set_report_text(conn, scan_id, {"summary": "Most poles were found.", "items": {}, "priorities": []}, "m")

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/scans/{scan_id}/files.zip").content))

    page = z.read("report.html").decode("utf-8")
    assert "Most poles were found." in page and f'src="evidence/scan{scan_id}_issue1_900.jpg"' in page


# --- survey items the agent writes (plan 12) -----------------------------------------------------------------------

HYDRANT = {"id": "agent_fire_hydrant", "find": "fire hydrant", "target": "fire hydrant", "ask": "Is it a fire hydrant?",
           "report_when": "yes", "label_yes": "Fire hydrant", "label_no": "Not a fire hydrant",
           "label_unclear": "Fire hydrant unclear", "inventory": True, "group_m": 15.0}


def item_scan(status="done"):
    conn = store()
    scan_id = db.create_scan(conn, "mapillary", [-122.33, 47.61, -122.32, 47.62], "US")
    db.add_frames(conn, scan_id, [{"idx": i, "clip": 0, "image_id": f"88{i}", "sequence_id": "s", "lat": 47.614,
                                   "lng": -122.32, "compass": 90.0, "captured_at": "2022-08-30T10:00:00Z",
                                   "creator": "walker", "width": 2048, "height": 1152} for i in range(9)])
    db.update_scan(conn, scan_id, status=status)
    return conn, scan_id


def test_making_an_item_needs_the_servers_item_code_a_finished_scan_and_a_free_worker(make):
    conn, scan_id = item_scan()
    client, worker = make()
    assert client.get("/api/config").json()["make_items"] is False
    assert client.post(f"/api/scans/{scan_id}/items", json={"target": "fire hydrant"}).status_code == 403

    client, worker = make(item_invite_code="hydrant")
    assert client.get("/api/config").json()["make_items"] is True
    wrong = client.post(f"/api/scans/{scan_id}/items", json={"target": "fire hydrant", "invite_code": "x"})
    assert wrong.status_code == 403 and wrong.json()["detail"] == "That invite code is not the right one"
    none = client.post(f"/api/scans/{scan_id}/items", json={"target": "fire hydrant"})
    assert none.status_code == 403 and none.json()["detail"] == "Making a survey item needs an invite code"
    assert client.post(f"/api/scans/{scan_id}/items", json={"target": "x", "invite_code": "hydrant"}).status_code == 422
    _, running = item_scan(status="running")
    assert client.post(f"/api/scans/{running}/items",
                       json={"target": "fire hydrant", "invite_code": "hydrant"}).status_code == 409
    db.update_scan(conn, running, status="done")

    made = client.post(f"/api/scans/{scan_id}/items", json={"target": "  fire   hydrant ", "invite_code": "hydrant"})
    assert made.status_code == 202
    run_id = made.json()["id"]
    assert worker.submitted == [("item", run_id)]
    run = db.item_run(conn, run_id)
    assert (run["scan_id"], run["target"], run["status"]) == (scan_id, "fire hydrant", "queued")
    assert run["client_ip_hash"]  # a visitor's run counts toward the visitors' cap
    busy = client.post(f"/api/scans/{scan_id}/items", json={"target": "bench", "invite_code": "hydrant"})
    assert busy.status_code == 409  # one at a time
    conn.close()


def test_an_item_waiting_for_answers_shows_its_photos_and_takes_one_answer_per_photo(make):
    conn, scan_id = item_scan()
    run_id = db.create_item_run(conn, scan_id, "fire hydrant")
    db.update_item_run(conn, run_id, status="labels_needed", label_photos=[3, 7], progress=["Muse Spark wrote a draft"])
    collect_path = config.photo_cache() / "883.jpg"
    collect_path.write_bytes(b"\xff\xd8 photo")
    client, worker = make()

    item = client.get(f"/api/items/{run_id}").json()
    assert (item["status"], item["progress"], item["item"], item["issues"]) == (
        "labels_needed", ["Muse Spark wrote a draft"], None, [])
    assert [p["idx"] for p in item["photos"]] == [3, 7]
    assert item["photos"][0] == {"idx": 3, "url": f"/api/items/{run_id}/photos/3.jpg", "image_id": "883",
                                 "creator": "walker", "captured_at": "2022-08-30T10:00:00Z",
                                 "link": "https://www.mapillary.com/app/?pKey=883"}
    assert client.get(f"/api/items/{run_id}/photos/3.jpg").content == b"\xff\xd8 photo"
    assert client.get(f"/api/items/{run_id}/photos/4.jpg").status_code == 404  # not one it asks about
    assert client.get(f"/api/items/{run_id}/photos/7.jpg").status_code == 404  # not on this disk

    assert client.post(f"/api/items/{run_id}/labels", json={"labels": {"3": "yes"}}).status_code == 422
    assert client.post(f"/api/items/{run_id}/labels", json={"labels": {"3": "yes", "7": "maybe"}}).status_code == 422
    assert client.post(f"/api/items/{run_id}/labels", json={"labels": {"3": "yes", "7": "unclear"}}).status_code == 202
    run = db.item_run(conn, run_id)
    assert (run["status"], run["labels"]) == ("queued", {"3": "yes", "7": "unclear"})
    assert worker.submitted == [("item", run_id)]
    assert client.post(f"/api/items/{run_id}/labels", json={"labels": {"3": "no", "7": "no"}}).status_code == 409
    assert client.get(f"/api/items/{run_id}/photos/3.jpg").status_code == 404  # answered: no longer shown
    assert client.get("/api/items/999999").status_code == 404
    conn.close()


def test_a_finished_item_shows_its_rows_as_the_scans_own_and_leaves_the_scan_as_it_was(make):
    conn, scan_id = item_scan()
    client, _ = make()
    before = client.get(f"/api/scans/{scan_id}").json()
    run_id = db.create_item_run(conn, scan_id, "fire hydrant")
    name = f"scan{scan_id}_item{run_id}-1_882.jpg"
    (config.run_dir("server") / "evidence").mkdir(exist_ok=True)
    (config.run_dir("server") / "evidence" / name).write_bytes(b"jpg")
    row = {"id": f"item{run_id}-1", "scan_id": scan_id, "item_run": run_id, "issue_type": "agent_fire_hydrant",
           "object_label": "yes", "frame_start": 1, "frame_end": 3, "frame_idx": 2, "lat": 47.614, "lng": -122.32,
           "confidence": 0.9, "review_status": "auto", "review_reasons": [], "captured_at": "2022-08-30T10:00:00Z",
           "model_outputs": {"check": {"answer": "yes", "note": "a red hydrant", "box": [1, 2, 3, 4]},
                             "find": {"phrase": "fire hydrant", "mask_area": 900, "group_idxs": [1, 2, 3]}},
           "evidence": {"image": name, "image_id": "882", "creator": "walker", "captured_at": "2022-08-30T10:00:00Z",
                        "link": "https://www.mapillary.com/app/?pKey=882", "license": "CC BY-SA 4.0",
                        "blurred_regions": 0}}
    db.update_item_run(conn, run_id, status="done", check_def=HYDRANT, rows=[row], cost_usd=0.41234,
                       test={"labels": "city", "found": [5, 6], "city": {"layer": "Hydrants", "found": 1, "covered": 2}})
    failed = db.create_item_run(conn, scan_id, "zzz")
    db.update_item_run(conn, failed, status="failed", error="No reliable wording found")

    item = client.get(f"/api/items/{run_id}").json()
    assert item["item"]["id"] == "agent_fire_hydrant" and item["item"]["added_by"] == "agent"
    assert (item["item"]["yes"], item["item"]["question"], item["cost_usd"]) == (1, "Is it a fire hydrant?", 0.4123)
    (issue,) = item["issues"]
    assert (issue["id"], issue["item_run"], issue["issue_type"], issue["heading"]) == (
        f"item{run_id}-1", run_id, "agent_fire_hydrant", 90.0)
    assert issue["check"]["target"] == "fire hydrant" and issue["check"]["answer"] == "yes"
    assert issue["evidence"]["url"] == f"/api/evidence/{name}"
    photo = client.get(issue["evidence"]["url"])
    assert photo.content == b"jpg" and photo.headers["cache-control"] == "public, max-age=86400"
    assert photo.headers["content-type"] == "image/jpeg"
    assert item["test"]["city"] == {"layer": "Hydrants", "found": 1, "covered": 2}
    assert [r["id"] for r in client.get(f"/api/scans/{scan_id}/items").json()] == [run_id]  # finished runs only
    assert client.get(f"/api/scans/{scan_id}").json() == before
    conn.close()



def test_a_surveys_call_record_is_served_when_one_was_made(make):
    conn, scan_id = item_scan()
    client, _ = make()
    assert client.get(f"/api/scans/{scan_id}/calls").status_code == 404
    record = {"scan_id": scan_id, "stages": [{"stage": "items", "model": "Muse Spark", "calls": 3}], "total": {"calls": 3}}
    (config.run_dir("server") / f"call_stats_scan{scan_id}.json").write_text(json.dumps(record), encoding="utf-8")
    assert client.get(f"/api/scans/{scan_id}/calls").json() == record
    (config.run_dir("server") / f"call_stats_scan{scan_id}.json").write_text("{broken", encoding="utf-8")
    assert client.get(f"/api/scans/{scan_id}/calls").status_code == 404
    conn.close()
