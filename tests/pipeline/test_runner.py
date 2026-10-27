import json

import pytest

from pipeline import collect, config, db, entrance_detect, runner, sidewalk
from pipeline.cost import CostCapReached
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text

N = 6
VIDEO = (1280, 960)  # video frame size images_to_mp4 gives for 160 x 120 photos
BBOX = [126.97, 37.55, 126.99, 37.57]
READING = {"step_count": 2, "has_step_or_sill": True, "step_box": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},
           "confidence": 0.9, "notes": "two steps"}


def fake_frames():
    frames = []
    for i in range(N):
        synthetic_photo(collect.photo_path(f"img{i}"), (160, 120), seed=i)
        frames.append({"idx": i, "clip": 0, "image_id": f"img{i}", "sequence_id": "seq", "lat": 37.56 + i * 2e-5,
                       "lng": 126.98, "compass": 90.0, "captured_at": f"2025-09-24T10:00:0{i}Z",
                       "creator": "tester", "width": 160, "height": 120})
    return frames


def spark(schema_name, parts):
    if schema_name == "street_scan":
        return {"entrances": [{"t": 2, "side": "left", "name": "Cafe", "category": "cafe",
                               "has_visible_steps": True}], "obstacle_types": ["traffic cone"]}
    if schema_name == "entrance_read":
        return READING
    if schema_name == "event_check":
        return {"blocked": True, "object": "traffic cone", "damage_visible": False, "confidence": 0.9, "notes": ""}
    if schema_name == "tactile_condition":  # S7: intact paving
        return {"tactile_visible": True, "line_end": "none", "damage": "none", "box": [], "confidence": 0.9,
                "note": ""}
    raise AssertionError(schema_name)


def sam(prompt, media):
    if prompt == "stairs":  # the 5-photo window around photo 2: stairs on its middle frame
        return text([line(2, [record("0", 80, 160, 240, 320, *VIDEO, 0.8)])])
    if prompt == "yellow tactile paving":  # a strip along the bottom of every photo
        return text([line(0, [record("0", 0, 100, 160, 120, 160, 120, 0.9)])])
    if prompt == "traffic cone":  # standing on the strip in photos 1-3 of the chunk video
        return text([line(f, [record("0", 0, 800, 160, 960, *VIDEO, 0.9)]) for f in (1, 2, 3)])
    return ""  # electric scooter, bicycle, person, license plate


@pytest.fixture
def setup(tmp_path, monkeypatch):
    calls = {"collect": 0}

    def fake_collect(bbox, max_photos):
        calls["collect"] += 1
        return fake_frames()

    monkeypatch.setattr(runner.collect, "collect", fake_collect)
    monkeypatch.setattr(runner.entrance_read, "original_photo", collect.photo_path)  # no network
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", BBOX)

    def make(api, sid=None):
        return runner.Scan(conn, sid or scan_id, api, tmp_path / "run", BBOX, workers=2)

    return conn, scan_id, make, calls


def test_a_scan_runs_every_stage_and_stores_entrances_issues_and_evidence(setup):
    conn, scan_id, make, _ = setup
    scan = make(FakeApi(spark=spark, sam=sam))
    result = runner.run_scan(scan)
    assert result["status"] == "done", result
    assert set(result["stage_status"].values()) == {"done"}
    (ent,) = db.entrances(conn, scan_id)
    assert (ent["frame_idx"], ent["step_count"], ent["stairs_detected"], ent["review_status"]) == (2, 2, True, "auto")
    assert ent["sill_possible"] is False  # SAM found stairs, so no "possible sill"
    assert ent["rise_cm_low"] is None and ent["has_ramp"] is None  # spec revision 4: steps only
    assert ent["location_source"] == "image_offset"
    (issue,) = db.issues(conn, scan_id)
    assert (issue["issue_type"], issue["object_label"], issue["frame_start"], issue["frame_end"],
            issue["review_status"]) == ("obstruction", "traffic cone", 1, 3, "auto")
    assert list(result["stage_status"]) == list(runner.STAGES)
    assert result["unavailable"] == {"scene_chunks": 0, "tactile_photos": 0, "obstacle_requests": 0,
                                     "defect_checks": 0}
    assert (scan.work / "entrance0" / "stairs_0000_0004.mp4").exists()  # each entrance has its own folder
    assert json.loads(scan.state("defects_checks").read_text(encoding="utf-8"))[0]["candidate"] == "sample"
    for row in (ent, issue):
        assert (scan.run_dir / "evidence" / row["evidence"]["image"]).exists()
        assert row["evidence"]["license"] == "CC BY-SA 4.0"
    stored = db.get_scan(conn, scan_id)
    assert stored["image_count"] == N and stored["captured_from"] == "2025-09-24T10:00:00Z"


def test_the_defects_stage_checks_tactile_run_edges_and_stores_possible_defects(setup):
    conn, scan_id, make, _ = setup

    def sam_edge(prompt, media):
        if prompt == "yellow tactile paving" and media.name == "img0.jpg":
            return ""  # no tactile paving in the first photo, so photo 2 starts a run
        return sam(prompt, media)

    def spark_cover(schema_name, parts):
        if schema_name == "tactile_condition":
            return {"tactile_visible": True, "line_end": "none", "damage": "cut_by_cover", "box": [600, 500, 900, 650],
                    "confidence": 0.8, "note": "a manhole cover sits in the line"}
        return spark(schema_name, parts)

    result = runner.run_scan(make(FakeApi(spark=spark_cover, sam=sam_edge)))
    assert result["status"] == "done", result
    assert result["defects"] == 1
    (cover,) = [i for i in db.issues(conn, scan_id) if i["issue_type"] == "cover_cut"]
    assert (cover["frame_idx"], cover["review_status"], cover["review_reasons"]) == (2, "needs_review",
                                                                                    ["possible_defect"])
    assert cover["evidence"]["image"]  # the evidence stage stored a blurred photo for it


def test_a_fixed_object_on_the_tactile_paving_is_a_durable_defect(setup):
    conn, scan_id, make, _ = setup

    def sam_bollard(prompt, media):
        if prompt == "bollard":
            return sam("traffic cone", media)  # stands on the strip in photos 1-3
        if prompt == "traffic cone":
            return ""
        return sam(prompt, media)

    result = runner.run_scan(make(FakeApi(spark=spark, sam=sam_bollard)))
    assert result["status"] == "done", result
    assert [(i["issue_type"], i["object_label"]) for i in db.issues(conn, scan_id)] == [
        ("fixed_obstruction", "bollard")]
    assert result["defects"] == 1


def test_a_failed_sidewalk_stage_fails_defects_too_and_both_rerun(setup, monkeypatch):
    conn, scan_id, make, _ = setup
    real = sidewalk.surface_segs

    def broken(*args):
        raise RuntimeError("tactile bug")

    monkeypatch.setattr(runner.sidewalk, "surface_segs", broken)
    first = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert first["stage_status"]["sidewalk"].startswith("failed")
    assert first["stage_status"]["defects"].startswith("failed")  # no tactile flags to read
    assert first["stage_status"]["entrances"] == "done"
    monkeypatch.setattr(runner.sidewalk, "surface_segs", real)
    second = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert second["status"] == "done", second
    assert len(db.issues(conn, scan_id)) == 1 and len(db.entrances(conn, scan_id)) == 1


def test_a_failed_entrance_stage_does_not_stop_the_sidewalk_and_resumes_alone(setup, monkeypatch):
    conn, scan_id, make, calls = setup
    real_merge = entrance_detect.merge_candidates

    def broken(cands, frames_by_idx):
        raise RuntimeError("merge bug")

    monkeypatch.setattr(runner.entrance_detect, "merge_candidates", broken)
    first = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert first["status"] == "failed" and "merge bug" in first["error"]
    assert first["stage_status"]["entrances"].startswith("failed")
    assert first["stage_status"]["sidewalk"] == "done" and first["issues"] == 1

    monkeypatch.setattr(runner.entrance_detect, "merge_candidates", real_merge)
    second = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert second["status"] == "done", second
    assert calls["collect"] == 1  # collect was not run again
    (ent,) = db.entrances(conn, scan_id)
    assert ent["evidence"]["image"]  # evidence ran again for the new entrance
    assert len(db.issues(conn, scan_id)) == 1  # sidewalk was not run again


def test_the_cost_cap_stops_the_scan_where_it_is(setup):
    _, _, make, _ = setup

    def capped(prompt, media):
        if prompt == "stairs":
            raise CostCapReached("cost cap $3.00")
        return sam(prompt, media)

    result = runner.run_scan(make(FakeApi(spark=spark, sam=capped)))
    assert result["status"] == "stopped_cost_cap"
    assert result["stage_status"]["entrances"] == "stopped"
    assert result["stage_status"]["sidewalk"] == "done"  # the main layer ran first
    assert "evidence" not in result["stage_status"]
    assert result["stage_status"]["sidewalk_evidence"] == "done"  # the main layer kept its evidence photos
    assert result["cost_usd"] == 0.0  # FakeApi's meter spends nothing, but the value is saved per stage


def test_an_area_without_photos_fails_at_collect(setup, monkeypatch):
    _, _, make, _ = setup
    monkeypatch.setattr(runner.collect, "collect", lambda bbox, max_photos: [])
    result = runner.run_scan(make(FakeApi()))
    assert result["status"] == "failed" and "no recent pedestrian photos" in result["error"]
    assert result["stage_status"]["collect"].startswith("failed")


def test_meter_for_leaves_room_only_for_what_is_left_of_the_day(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    first = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, first, cost_usd=9.0)
    second = db.create_scan(conn, "mapillary", BBOX)
    meter = runner.meter_for(conn, db.get_scan(conn, second))
    assert meter.cap_usd == pytest.approx(1.0) and meter.spent_usd == 0
    meter = runner.meter_for(conn, db.get_scan(conn, first))
    assert meter.cap_usd == pytest.approx(3.0) and meter.spent_usd == pytest.approx(9.0)


def test_a_failed_evidence_photo_is_a_row_error_and_is_retried_on_the_next_run(setup, monkeypatch):
    conn, scan_id, make, _ = setup

    def no_download(image_id):
        raise RuntimeError("download failed")

    monkeypatch.setattr(runner.entrance_read, "original_photo", no_download)
    first = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert first["status"] == "failed" and first["stage_status"]["evidence"].startswith("failed")
    (ent,) = db.entrances(conn, scan_id)
    assert "error" in ent["evidence"]
    (issue,) = db.issues(conn, scan_id)
    assert issue["evidence"]["image"]  # the issue's photo needed no download, so it was stored
    monkeypatch.setattr(runner.entrance_read, "original_photo", collect.photo_path)
    second = runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    assert second["status"] == "done", second
    (ent,) = db.entrances(conn, scan_id)
    assert ent["evidence"]["image"]


def test_a_scan_never_takes_more_photos_than_its_budget(tmp_path):
    assert runner.Scan(None, 1, FakeApi(), tmp_path, BBOX, max_photos=999).max_photos == config.photo_budget()
    assert runner.Scan(None, 1, FakeApi(), tmp_path, BBOX, max_photos=999).max_photos <= 400


def test_a_scan_stopped_at_its_cap_is_not_resumed_and_keeps_its_rows(setup):
    conn, scan_id, make, _ = setup
    runner.run_scan(make(FakeApi(spark=spark, sam=sam)))
    db.update_scan(conn, scan_id, status="stopped_cost_cap")
    api = FakeApi(spark=spark, sam=sam)
    api.meter = runner.CostMeter(cap_usd=3.0, spent_usd=2.95)
    result = runner.run_scan(make(api))
    assert result["status"] == "stopped_cost_cap" and "already reached" in result["error"]
    assert api.calls == [] and len(db.issues(conn, scan_id)) == 1


def test_run_aborted_marks_the_scan_failed_and_keeps_the_spend(setup):
    _, _, make, _ = setup

    def aborting(prompt, media):
        if prompt == "stairs":
            raise runner.RunAborted("5 failed requests in a row")
        return sam(prompt, media)

    api = FakeApi(spark=spark, sam=aborting)
    api.meter.spent_usd = 0.42
    result = runner.run_scan(make(api))
    assert result["status"] == "failed" and "5 failed requests" in result["error"]
    assert result["stage_status"]["entrances"] == "stopped" and result["cost_usd"] == 0.42


SEATTLE = [-122.3230, 47.6130, -122.3170, 47.6175]


def us_sam(prompt, media):
    """The US walking path is the same strip the KR tests use for tactile paving; nothing else is found."""
    if prompt == "sidewalk":
        return sam("yellow tactile paving", media)
    if prompt == "yellow tactile paving":
        return ""
    return sam(prompt, media)


def test_a_us_scan_tracks_objects_on_the_sidewalk_and_reports_no_fixed_ones(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")

    def spark_pole(schema_name, parts):
        if schema_name == "street_scan":
            return {"entrances": [], "obstacle_types": ["utility pole"]}
        if schema_name == "tactile_condition":
            raise AssertionError("the KR tactile check never runs on a US scan")
        return spark(schema_name, parts)

    def sam_pole(prompt, media):
        if prompt == "utility pole":
            return us_sam("traffic cone", media)  # stands on the sidewalk in photos 1-3, like the cone
        return us_sam(prompt, media)

    api = FakeApi(spark=spark_pole, sam=sam_pole)
    result = runner.run_scan(make(api, us_scan))
    assert result["status"] == "done", result
    assert [(i["issue_type"], i["object_label"]) for i in db.issues(conn, us_scan)] == [("obstruction", "traffic cone")]
    asked = {c[1] for c in api.calls if c[0] == "sam_image"}
    assert "sidewalk" in asked and "yellow tactile paving" not in asked
