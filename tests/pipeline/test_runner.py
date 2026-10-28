import dataclasses
import json

import pytest

from pipeline import collect, config, db, entrance_detect, runner, sidewalk, survey_items
from pipeline.cost import CostCapReached, CostMeter
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
                                     "defect_checks": 0, "check_requests": 0}
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
    meter = runner.meter_for(conn, db.get_scan(conn, second), scan_cap_usd=6.0)  # a local run's own cap: the day still limits it
    assert meter.cap_usd == pytest.approx(1.0)
    meter = runner.meter_for(conn, db.get_scan(conn, first), scan_cap_usd=6.0)
    assert meter.cap_usd == pytest.approx(6.0)


def test_meter_for_keeps_scans_started_by_visitors_inside_the_public_budget(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    local = db.create_scan(conn, "mapillary", BBOX)  # a CLI scan: no address, never counted
    db.update_scan(conn, local, cost_usd=5.0)
    public = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, public, client_ip_hash="a", cost_usd=1.5)
    new = db.create_scan(conn, "mapillary", BBOX)
    db.update_scan(conn, new, client_ip_hash="b")
    assert db.public_cost(conn) == pytest.approx(1.5)
    assert runner.meter_for(conn, db.get_scan(conn, new), public_cap_usd=2.0).cap_usd == pytest.approx(0.5)
    assert runner.meter_for(conn, db.get_scan(conn, public), public_cap_usd=2.0).cap_usd == pytest.approx(2.0)
    assert runner.meter_for(conn, db.get_scan(conn, new)).cap_usd == pytest.approx(3.0)
    assert runner.meter_for(conn, db.get_scan(conn, local), public_cap_usd=2.0).cap_usd == pytest.approx(3.0)


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
    assert ("spark", "tactile_condition") not in api.calls  # the KR tactile check is gated off for US
    assert result["unavailable"]["defect_checks"] == 0


RAMP_NO = {"visible": True, "answer": "no", "box": [400, 600, 600, 900], "confidence": 0.8, "note": "no domes"}


def test_a_us_scan_checks_curb_ramps_one_row_per_ramp_with_evidence(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")

    def sam_ramp(prompt, media):
        if prompt == "curb cut":  # sampled photos 0, 2, 4: the ramp on 2 and, closer, on 4
            size = {"img2.jpg": 40, "img4.jpg": 80}.get(media.name)
            return text([line(0, [record("0", 0, 0, size, size, 160, 120, 0.9)])]) if size else ""
        return us_sam(prompt, media)

    def spark_ramp(schema_name, parts):
        return RAMP_NO if schema_name == "survey_check" else spark(schema_name, parts)

    result = runner.run_scan(make(FakeApi(spark=spark_ramp, sam=sam_ramp), us_scan))
    assert result["status"] == "done", result
    (ramp,) = [i for i in db.issues(conn, us_scan) if i["issue_type"] == "curb_ramp_warning"]
    assert (ramp["object_label"], ramp["frame_idx"], ramp["review_status"]) == ("no", 4, "needs_review")
    assert ramp["model_outputs"]["find"]["group_idxs"] == [2, 4]
    assert ramp["evidence"]["image"]  # the sidewalk evidence stage stored a blurred photo
    assert result["defects"] == 1 and result["unavailable"]["check_requests"] == 0


def test_rerunning_the_defects_stage_replaces_the_check_rows(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam_ramp(prompt, media):
        return found if prompt == "curb cut" and media.name == "img2.jpg" else us_sam(prompt, media)

    answers = iter([RAMP_NO, {**RAMP_NO, "answer": "yes"}])

    def spark_ramp(schema_name, parts):
        return next(answers) if schema_name == "survey_check" else spark(schema_name, parts)

    api = FakeApi(spark=spark_ramp, sam=sam_ramp)
    runner.run_scan(make(api, us_scan))
    db.set_stage(conn, us_scan, "defects", "pending")
    runner.run_scan(make(api, us_scan))
    assert [i["object_label"] for i in db.issues(conn, us_scan) if i["issue_type"] == "curb_ramp_warning"] == ["yes"]


TWO_CHECKS = '''
standard = "US"

[path]
surface = "sidewalk"
name = "the sidewalk walking path"
describe = "sidewalks"
objects = ["traffic cone"]
fixed = []

[[check]]
id = "first"
find = "curb cut"
ask = "FIRST question"
report_when = "no"
label_yes = "First yes"
label_no = "First no"

[[check]]
id = "second"
find = "bench"
ask = "SECOND question"
report_when = "no"
label_yes = "Second yes"
label_no = "Second no"
'''


def test_rerunning_one_check_keeps_the_other_checks_rows_and_reviews(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam_both(prompt, media):
        return found if prompt in ("curb cut", "bench") and media.name == "img2.jpg" else us_sam(prompt, media)

    answer = {"first": RAMP_NO, "second": RAMP_NO}

    def spark_both(schema_name, parts):
        if schema_name != "survey_check":
            return spark(schema_name, parts)
        which = "first" if "FIRST question" in parts[1]["text"] else "second"
        return answer[which]

    def rows():
        return {i["issue_type"]: i for i in db.issues(conn, us_scan) if i["issue_type"] in ("first", "second")}

    scan = make(FakeApi(spark=spark_both, sam=sam_both), us_scan)
    scan.standard = survey_items.parse(TWO_CHECKS, "two.toml")
    assert runner.run_scan(scan)["status"] == "done"
    before = rows()
    assert set(before) == {"first", "second"}
    correction = [{"field": "object_label", "from": "no", "to": "yes"}]
    db.update_issue(conn, before["first"]["id"], review_status="confirmed", object_label="yes", corrections=correction)

    answer["second"] = {**RAMP_NO, "answer": "yes"}
    api = FakeApi(spark=spark_both, sam=sam_both)
    again = make(api, us_scan)
    again.standard, again.only_checks = scan.standard, ("second",)
    db.set_stage(conn, us_scan, "defects", "pending")
    assert runner.run_scan(again)["status"] == "done"
    after = rows()
    assert (after["first"]["id"], after["first"]["review_status"], after["first"]["object_label"],
            after["first"]["corrections"]) == (before["first"]["id"], "confirmed", "yes", correction)
    assert (before["second"]["object_label"], after["second"]["object_label"]) == ("no", "yes")  # replaced
    assert after["second"]["evidence"]["image"] and after["first"]["evidence"] == before["first"]["evidence"]
    asked = {c[1] for c in api.calls if c[0] == "sam_image"}
    assert "bench" not in asked and "curb cut" not in asked  # SAM areas of both checks are reused from the first run
    assert api.calls.count(("spark", "survey_check")) == 1  # only the rerun check's object was asked about again
    assert json.loads(again.state("checks_unavailable").read_text(encoding="utf-8")).keys() == {"first", "second"}


def test_an_unknown_check_id_is_refused_before_anything_is_cleared(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    db.add_issue(conn, us_scan, {"issue_type": "curb_ramp_warning", "object_label": "no", "frame_idx": 0,
                                 "lat": 0.0, "lng": 0.0})
    scan = make(FakeApi(), us_scan)
    scan.standard, scan.only_checks = survey_items.load("US"), ("nonsense",)
    with pytest.raises(ValueError, match="nonsense"):
        runner.stage_defects(scan)
    assert [i["issue_type"] for i in db.issues(conn, us_scan)] == ["curb_ramp_warning"]


class DisagreeingOpen:
    def answer(self, check, photo):
        return {"visible": True, "answer": "yes", "box": [], "confidence": 0.6, "note": "domes"}


def test_a_second_model_that_disagrees_adds_the_models_disagree_reason(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam_ramp(prompt, media):
        return found if prompt == "curb cut" and media.name == "img2.jpg" else us_sam(prompt, media)

    def spark_ramp(schema_name, parts):
        return RAMP_NO if schema_name == "survey_check" else spark(schema_name, parts)

    scan = make(FakeApi(spark=spark_ramp, sam=sam_ramp), us_scan)
    scan.second_opinion = DisagreeingOpen()
    runner.run_scan(scan)
    (ramp,) = [i for i in db.issues(conn, us_scan) if i["issue_type"] == "curb_ramp_warning"]
    assert ramp["review_reasons"] == ["possible_defect", "models_disagree"]
    assert ramp["model_outputs"]["second_opinion"]["agree"] is False


def sam_calls(api, phrases):
    return [c[1:] for c in api.calls if c[0] == "sam_image" and c[1] in phrases]


def test_rerunning_the_defects_stage_does_not_ask_sam_again_unless_the_phrase_changed(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam_ramp(prompt, media):
        return found if prompt == "curb cut" and media.name == "img2.jpg" else us_sam(prompt, media)

    def spark_ramp(schema_name, parts):
        return RAMP_NO if schema_name == "survey_check" else spark(schema_name, parts)

    api = FakeApi(spark=spark_ramp, sam=sam_ramp)
    standard = survey_items.load("US")
    phrases = {c.find for c in standard.checks}
    scan = make(api, us_scan)
    scan.standard = standard
    runner.run_scan(scan)
    first = sam_calls(api, phrases)
    assert ("curb cut", "img2.jpg") in first and len(first) == 3 * len(phrases)  # sampled photos 0, 2, 4 per check
    saved = json.loads(scan.state("find_curb_ramp_warning").read_text(encoding="utf-8"))
    assert saved == {"phrase": "curb cut", "areas": {"0": 0, "2": 1600, "4": 0}}

    db.set_stage(conn, us_scan, "defects", "pending")
    again = make(api, us_scan)
    again.standard = standard
    runner.run_scan(again)
    assert sam_calls(api, phrases) == first  # the second run made no SAM request
    (ramp,) = [i for i in db.issues(conn, us_scan) if i["issue_type"] == "curb_ramp_warning"]
    assert ramp["frame_idx"] == 2  # and the row is the same as the first run's

    changed = dataclasses.replace(standard, checks=tuple(
        dataclasses.replace(c, find="dropped kerb") if c.id == "curb_ramp_warning" else c for c in standard.checks))
    db.set_stage(conn, us_scan, "defects", "pending")
    third = make(api, us_scan)
    third.standard = changed
    runner.run_scan(third)
    assert [c for c in sam_calls(api, {*phrases, "dropped kerb"}) if c not in first] == [
        ("dropped kerb", "img0.jpg"), ("dropped kerb", "img2.jpg"), ("dropped kerb", "img4.jpg")]  # only the new phrase
    assert json.loads(third.state("find_curb_ramp_warning").read_text(encoding="utf-8"))["phrase"] == "dropped kerb"


def test_a_failed_sam_request_is_not_saved_and_is_asked_again(setup):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    broken = {"on": True}

    def sam_ramp(prompt, media):
        if prompt == "curb cut" and media.name == "img4.jpg" and broken["on"]:
            raise RuntimeError("timeout")
        return found if prompt == "curb cut" and media.name == "img2.jpg" else us_sam(prompt, media)

    api = FakeApi(spark=lambda s, p: RAMP_NO if s == "survey_check" else spark(s, p), sam=sam_ramp)
    scan = make(api, us_scan)
    scan.standard = survey_items.load("US")
    runner.run_scan(scan)
    assert json.loads(scan.state("find_curb_ramp_warning").read_text(encoding="utf-8"))["areas"] == {"0": 0, "2": 1600}
    before = len(sam_calls(api, {"curb cut"}))
    broken["on"] = False
    db.set_stage(conn, us_scan, "defects", "pending")
    runner.run_scan(make(api, us_scan))
    assert sam_calls(api, {"curb cut"})[before:] == [("curb cut", "img4.jpg")]  # only the one that failed


def test_collect_takes_only_the_photos_the_cap_pays_for_with_the_standards_checks(setup, monkeypatch):
    _, _, make, _ = setup
    asked = []
    monkeypatch.setattr(runner.collect, "collect", lambda bbox, max_photos: asked.append(max_photos) or fake_frames())
    api = FakeApi()
    api.meter = CostMeter(cap_usd=3.0)
    scan = make(api)
    scan.standard = survey_items.load("US")
    runner.stage_collect(scan)
    assert asked == [config.photo_budget(scan.max_photos, checks=len(scan.standard.checks), cap_usd=3.0)]
    assert asked[0] < config.photo_budget(scan.max_photos)


def test_collecting_again_drops_the_saved_sam_areas(setup):
    _, _, make, _ = setup
    scan = make(FakeApi())
    scan.standard = survey_items.load("KR")
    scan.state("find_curb_ramp_warning").write_text("{}", encoding="utf-8")
    runner.stage_collect(scan)
    assert not scan.state("find_curb_ramp_warning").exists()



def test_the_checks_glimmer_judges_run_back_to_back_so_its_gpu_starts_once(setup, monkeypatch):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    order = []

    def run_check(api, check, clips, photos, by_idx, workers, known=None, judge=None, audit_seed=None, **kw):
        order.append(check.id)
        return [], {"find": [], "ask": []}

    monkeypatch.setattr(runner.survey_checks, "run_check", run_check)
    std = survey_items.load("US")
    glimmer = {"utility_pole", "litter_can"}  # a Spark check stands between them in the standard
    std = dataclasses.replace(std, checks=tuple(
        dataclasses.replace(c, judge="glimmer" if c.id in glimmer else "spark") for c in std.checks))
    scan = make(FakeApi(spark=spark, sam=us_sam), us_scan)
    scan.standard, scan.judge = std, object()
    assert runner.run_scan(scan)["status"] == "done"
    spark_order = [c.id for c in std.checks if c.id not in glimmer]
    assert order == spark_order + [c.id for c in std.checks if c.id in glimmer]


def test_the_judge_answers_only_the_checks_that_name_it_and_the_audit_is_seeded_by_the_scan(setup, monkeypatch):
    conn, _, make, _ = setup
    us_scan = db.create_scan(conn, "mapillary", SEATTLE, "US")
    seen = []

    def run_check(api, check, clips, photos, by_idx, workers, known=None, judge=None, audit_seed=None, **kw):
        seen.append((check.id, judge, audit_seed))
        return [], {"find": [], "ask": []}

    monkeypatch.setattr(runner.survey_checks, "run_check", run_check)
    std = survey_items.load("US")
    std = dataclasses.replace(std, checks=tuple(
        dataclasses.replace(c, judge="glimmer" if c.id == "utility_pole" else "spark") for c in std.checks))
    judge = object()
    scan = make(FakeApi(spark=spark, sam=us_sam), us_scan)
    scan.standard, scan.judge = std, judge
    assert runner.run_scan(scan)["status"] == "done"
    assert ("utility_pole", judge, us_scan) in seen
    assert all(j is None for cid, j, _ in seen if cid != "utility_pole")
