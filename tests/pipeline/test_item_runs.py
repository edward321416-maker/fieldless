import dataclasses

import pytest

from pipeline import collect, config, db, item_runs
from pipeline.survey_items import Check
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text

HYDRANT = Check(id="agent_fire_hydrant", find="fire hydrant", target="fire hydrant",
                ask="Is the marked object a fire hydrant?", report_when="yes", label_yes="Fire hydrant",
                label_no="Not a fire hydrant", label_unclear="Fire hydrant unclear", inventory=True, group_m=15.0)
YES = {"visible": True, "answer": "yes", "box": [100, 600, 300, 900], "confidence": 0.9, "note": "a red hydrant"}
NO = {"visible": True, "answer": "no", "box": [100, 600, 300, 900], "confidence": 0.8, "note": "a bollard"}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path / "data"))
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def scan_with_photos(conn, n=9, step_m=5.0):
    scan_id = db.create_scan(conn, "mapillary", [-122.33, 47.61, -122.32, 47.62], "US")
    frames = [{"idx": i, "clip": 0, "image_id": f"77{i}", "sequence_id": "s", "lat": 47.6130 + i * step_m / 111_320,
               "lng": -122.32, "compass": 0.0, "captured_at": f"2022-08-30T10:00:{i:02d}Z", "creator": "walker",
               "width": 160, "height": 120} for i in range(n)]
    db.add_frames(conn, scan_id, frames)
    db.update_scan(conn, scan_id, status="done")
    return scan_id, frames


def test_scan_photos_downloads_the_missing_photos_and_leaves_out_the_ones_it_cannot_get(conn):
    scan_id, frames = scan_with_photos(conn, n=4)
    asked = []

    def fetch(rec):
        asked.append(rec["id"])
        if rec["id"] == "772":
            return None
        synthetic_photo(collect.photo_path(rec["id"]), seed=int(rec["id"]))
        return (160, 120)

    photos = item_runs.scan_photos(conn, scan_id, workers=2, fetch=fetch)

    assert sorted(asked) == ["770", "771", "772", "773"]
    assert [f["idx"] for f in photos.frames] == [0, 1, 3] and sorted(photos.paths) == [0, 1, 3]
    assert photos.paths[3] == collect.photo_path("773") and photos.paths[3].exists()
    assert (photos.scan_id, photos.standard, photos.clips()) == (scan_id, "US", {0: [0, 1, 3]})


def test_the_item_runs_on_every_photo_and_its_rows_carry_their_run_and_evidence(conn, tmp_path):
    scan_id, frames = scan_with_photos(conn)
    paths = {f["idx"]: synthetic_photo(tmp_path / "p" / f"{f['idx']}.jpg", seed=f["idx"]) for f in frames}
    photos = item_runs.ScanPhotos(scan_id, [0, 0, 1, 1], "US", frames, paths)
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    answers = iter([YES, NO])

    def sam(prompt, media):
        return found if prompt == "fire hydrant" and media.name in ("2.jpg", "8.jpg") else ""

    lines = []
    rows = item_runs.run_item_on_scan(FakeApi(spark=lambda schema, parts: next(answers), sam=sam), photos, HYDRANT,
                                      tmp_path / "run", run_id=5, workers=1, on_progress=lines.append)

    assert [(r["id"], r["item_run"], r["scan_id"], r["issue_type"], r["frame_idx"], r["object_label"])
            for r in rows] == [("item5-1", 5, scan_id, "agent_fire_hydrant", 2, "yes")]
    record_ = rows[0]["evidence"]
    assert record_["image"] == f"scan{scan_id}_item5-1_772.jpg" and record_["creator"] == "walker"
    assert (tmp_path / "run" / "evidence" / record_["image"]).exists()
    assert lines == ["Surveying all 9 photos of the scan for fire hydrant",
                     "SAM 3.1 found 2 possible fire hydrant; Muse Spark kept 1"]
    assert db.issues(conn, scan_id) == []  # the scan itself is not changed


def test_an_item_found_by_video_is_refused(conn, tmp_path):
    scan_id, frames = scan_with_photos(conn, n=2)
    photos = item_runs.ScanPhotos(scan_id, [0, 0, 1, 1], "US", frames, {})
    with pytest.raises(ValueError, match="found on photos"):
        item_runs.run_item_on_scan(FakeApi(), photos, dataclasses.replace(HYDRANT, find_by="video"), tmp_path, 1)


def test_a_run_is_capped_at_a_dollar_within_what_the_day_and_the_visitors_have_left(conn, monkeypatch):
    scan_id, _ = scan_with_photos(conn, n=1)
    db.update_scan(conn, scan_id, cost_usd=9.6)  # a scan created today
    run_id = db.create_item_run(conn, scan_id, "bench", client_ip_hash="h")
    db.update_item_run(conn, run_id, cost_usd=0.1)  # spent before a pause for a person's answers
    meter = item_runs.meter_for_item(conn, db.item_run(conn, run_id))
    assert meter.cap_usd == pytest.approx(config.DAILY_COST_CAP_USD - 9.6) and meter.spent_usd == pytest.approx(0.1)

    db.update_scan(conn, scan_id, cost_usd=0.0)
    assert item_runs.meter_for_item(conn, db.item_run(conn, run_id)).cap_usd == item_runs.ITEM_COST_CAP_USD
    other = db.create_item_run(conn, scan_id, "bike rack", client_ip_hash="g")
    db.update_item_run(conn, other, cost_usd=39.5, created_at="2026-10-01T00:00:00Z")  # a visitor, another day
    capped = item_runs.meter_for_item(conn, db.item_run(conn, run_id), public_cap_usd=40.0)
    assert capped.cap_usd == pytest.approx(0.5)
