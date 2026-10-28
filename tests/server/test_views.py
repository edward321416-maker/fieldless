from datetime import datetime, timezone

import pytest

from pipeline import survey_items
from server import views
from tests.pipeline.test_survey_items import INVENTORY as INVENTORY_TOML

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
US = survey_items.load("US")
EVIDENCE = {"image": "scan1_entrance4.jpg", "image_id": "123", "creator": "filipb",
            "captured_at": "2025-09-24T07:21:37Z", "link": "https://www.mapillary.com/app/?pKey=123",
            "license": "CC BY-SA 4.0", "blurred_regions": 1}


def entrance(**overrides):
    row = {"id": 4, "lat": 37.56, "lng": 126.98, "place_name": "Cafe", "category": "cafe", "step_count": 2,
           "stairs_detected": True, "sill_possible": False, "review_status": "auto", "review_reasons": [],
           "evidence": EVIDENCE, "captured_at": "2025-09-24T07:21:37Z"}
    return {**row, **overrides}


def issue(**overrides):
    row = {"id": 2, "lat": 37.5, "lng": 127.0, "issue_type": "obstruction", "object_label": "electric scooter",
           "frame_start": 4, "frame_end": 6, "review_status": "auto", "review_reasons": [], "evidence": {},
           "captured_at": "2021-03-01T00:00:00Z"}
    return {**row, **overrides}


def test_is_old_after_three_years():
    assert views.is_old("2021-01-01T00:00:00Z", NOW)
    assert not views.is_old("2025-09-24T07:21:37Z", NOW)
    assert not views.is_old(None, NOW)


def test_evidence_json_points_at_the_evidence_route_and_keeps_attribution():
    assert views.evidence_json(EVIDENCE) == {"url": "/api/evidence/scan1_entrance4.jpg", "creator": "filipb",
                                             "captured_at": "2025-09-24T07:21:37Z",
                                             "link": "https://www.mapillary.com/app/?pKey=123",
                                             "license": "CC BY-SA 4.0", "blurred": True}
    assert views.evidence_json({}) is None
    assert views.evidence_json({"error": "SamStreamError: boom"}) is None


@pytest.mark.parametrize("overrides, label", [
    ({}, {"icon": "▲", "text": "2 steps"}),
    ({"step_count": 1}, {"icon": "▲", "text": "1 step"}),
    ({"step_count": 0}, {"icon": "✓", "text": "No steps"}),
    ({"step_count": 1, "review_status": "needs_review", "review_reasons": ["possible_sill"]},
     {"icon": "?", "text": "Possible sill"}),
    ({"step_count": 1, "review_status": "verified", "review_reasons": ["possible_sill"]},
     {"icon": "▲", "text": "1 step"}),
    ({"step_count": None, "review_status": "needs_review", "review_reasons": ["analysis_failed"]},
     {"icon": "?", "text": "Not analysed"}),
])
def test_entrance_label(overrides, label):
    assert views.entrance_label(entrance(**overrides)) == label


def test_entrance_json():
    out = views.entrance_json(entrance(place_name=None), NOW)
    assert out["label"] == {"icon": "▲", "text": "2 steps"} and out["name"] == "" and out["old_photo"] is False
    assert out["stairs_detected"] is True and out["evidence"]["creator"] == "filipb"
    assert set(out) == {"id", "lat", "lng", "heading", "name", "category", "step_count", "stairs_detected",
                        "sill_possible", "label", "review_status", "review_reasons", "evidence", "captured_at",
                        "old_photo"}
    assert out["heading"] is None and views.entrance_json(entrance(), NOW, heading=271.5)["heading"] == 271.5


@pytest.mark.parametrize("overrides, label", [
    ({}, {"icon": "⛔", "text": "Blocked: electric scooter"}),
    ({"issue_type": "missing_blocks", "object_label": None}, {"icon": "✖", "text": "Tactile blocks missing"}),
    ({"issue_type": "cover_cut", "object_label": None}, {"icon": "◼", "text": "Tactile line cut by a cover"}),
    ({"issue_type": "fixed_obstruction", "object_label": "bollard"},
     {"icon": "▮", "text": "Fixed object on tactile paving: bollard"}),
    ({"issue_type": "fixed_obstruction", "object_label": None},
     {"icon": "▮", "text": "Fixed object on tactile paving: object"}),
])
def test_issue_label(overrides, label):
    assert views.issue_label(issue(**overrides)) == label


def test_an_issue_type_of_no_known_stage_is_labelled_by_its_name():
    old = issue(issue_type="old_check_item", object_label="no")
    assert views.issue_label(old, US) == {"icon": "•", "text": "old check item"}
    assert views.issue_label(old) == {"icon": "•", "text": "old check item"}


def test_issue_json_marks_durable_defects():
    one_off = views.issue_json(issue(), NOW)
    assert one_off["durable"] is False and one_off["old_photo"] is True and one_off["evidence"] is None
    bollard = views.issue_json(issue(issue_type="fixed_obstruction", object_label="bollard"), NOW)
    assert bollard["durable"] is True and bollard["label"]["icon"] == "▮"


def test_issue_order_puts_durable_defects_first():
    rows = [issue(id=1), issue(id=2, issue_type="cover_cut"), issue(id=3, issue_type="missing_blocks"), issue(id=4)]
    assert [r["id"] for r in sorted(rows, key=views.issue_order)] == [2, 3, 1, 4]


def test_scan_json_counts_defects_obstructions_and_items_to_review():
    scan = {"id": 1, "status": "done", "stage_status": {"collect": "done"}, "bbox": [0, 0, 1, 1], "image_count": 60,
            "captured_from": "a", "captured_to": "b", "cost_usd": 0.3, "processing_seconds": 100.0,
            "review_seconds": 5.0, "error": None}
    entrances = [entrance(), entrance(id=5, step_count=0, review_status="needs_review")]
    issues = [issue(issue_type="missing_blocks", review_status="needs_review"),
              issue(issue_type="fixed_obstruction", object_label="pole"), issue(review_status="rejected"), issue()]
    out = views.scan_json(scan, entrances, issues)
    assert out["counts"] == {"defects": 2, "obstructions": 1, "entrances": 2, "entrances_with_steps": 1,
                             "needs_review": 2, "field_check": 0, "verified": 0, "rejected": 1,
                             "dropped": 0}
    assert out["id"] == 1 and out["status"] == "done" and out["image_count"] == 60


def test_scan_json_carries_the_surveyed_km_and_the_cost_per_km():
    scan = {"id": 1, "status": "done", "stage_status": {}, "bbox": [0, 0, 1, 1], "image_count": 60,
            "captured_from": "a", "captured_to": "b", "cost_usd": 0.3, "processing_seconds": 100.0,
            "review_seconds": 5.0, "error": None}
    assert views.scan_json(scan, [], [], km=0.5)["usd_per_km"] == 0.6
    assert views.scan_json(scan, [], [])["usd_per_km"] is None


def test_surveyed_km_follows_each_walk_and_skips_gaps():
    frames = [
        {"clip": 0, "idx": 0, "lat": 37.5600, "lng": 126.9800},
        {"clip": 0, "idx": 1, "lat": 37.5601, "lng": 126.9800},  # about 11 m
        {"clip": 0, "idx": 2, "lat": 37.5701, "lng": 126.9800},  # about 1.1 km jump: a gap, not counted
        {"clip": 1, "idx": 3, "lat": 37.5600, "lng": 126.9900},
        {"clip": 1, "idx": 4, "lat": 37.5602, "lng": 126.9900},  # about 22 m
    ]
    assert views.surveyed_km(frames) == pytest.approx(0.033, abs=0.002)
    assert views.surveyed_km([]) == 0


def ramp(answer="no", **overrides):
    return issue(issue_type="curb_ramp_warning", object_label=answer, **overrides)


def test_check_rows_are_labelled_from_the_standard():
    assert views.issue_label(ramp("no"), US) == {"icon": "✖", "text": "Curb ramp without detectable warning"}
    assert views.issue_label(ramp("yes"), US) == {"icon": "✓", "text": "Curb ramp with detectable warning"}
    assert views.issue_label(ramp("unclear"), US) == {"icon": "?", "text": "Curb ramp: detectable warning unclear"}


def test_issue_json_carries_the_check_and_whether_it_is_a_defect():
    out = views.issue_json(ramp("no", review_status="needs_review"), NOW, US)
    assert out["defect"] is True and out["durable"] is True
    assert out["check"] == {"id": "curb_ramp_warning", "find": "curb cut", "target": "curb ramp",
                            "question": US.checks[0].ask, "answer": "no", "report_when": "no", "label_yes": "Curb ramp with detectable warning",
                            "label_no": "Curb ramp without detectable warning", "inventory": False,
                            "model": "Muse Spark"}
    assert views.issue_json(ramp("yes"), NOW, US)["defect"] is False
    assert views.issue_json(issue(), NOW)["check"] is None and views.issue_json(issue(), NOW)["defect"] is False
    assert views.issue_json(issue(), NOW)["second_opinion"] is None


def test_issue_json_carries_the_second_opinion_only_when_it_answered():
    answered = ramp("no", model_outputs={"second_opinion": {"model": "m", "answer": "yes", "agree": False}})
    assert views.issue_json(answered, NOW, US)["second_opinion"] == {"answer": "yes", "agree": False, "model": "m"}
    failed = ramp("no", model_outputs={"second_opinion": {"model": "m", "error": "boom"}})
    assert views.issue_json(failed, NOW, US)["second_opinion"] is None


def test_issue_order_puts_check_defects_first_then_checked_objects_then_obstructions():
    rows = [issue(id=1), ramp("yes", id=2), ramp("no", id=3), ramp("unclear", id=4)]
    assert [r["id"] for r in sorted(rows, key=lambda r: views.issue_order(r, US))] == [3, 2, 4, 1]


def test_scan_json_counts_by_the_scans_standard():
    scan = {"id": 2, "status": "done", "stage_status": {}, "bbox": [0, 0, 1, 1], "image_count": 200,
            "captured_from": "a", "captured_to": "b", "cost_usd": 2.5, "processing_seconds": 900.0,
            "review_seconds": 0.0, "error": None, "jurisdiction": "US"}
    issues = [ramp("no", review_status="needs_review"), ramp("yes"), ramp("yes"), ramp("unclear", review_status="needs_review"),
              ramp("no", review_status="rejected"), issue(object_label="traffic cone")]
    out = views.scan_json(scan, [], issues)
    assert out["standard"] == "US"
    assert out["counts"]["defects"] == 1 and out["counts"]["obstructions"] == 1 and out["counts"]["needs_review"] == 2
    ramp_check = survey_items.load("US").check("curb_ramp_warning")
    assert out["checks"][0] == {"id": "curb_ramp_warning", "find": "curb cut", "target": "curb ramp", "inventory": False,
                                "report_when": "no", "label_yes": "Curb ramp with detectable warning",
                                "label_no": "Curb ramp without detectable warning", "yes": 2, "no": 1, "unclear": 1,
                                "map_icon": ramp_check.map_icon, "question": ramp_check.question or ramp_check.ask}
    # the map's items: the standard's checks in order, then the stages' own (no KR tactile defects on a US scan)
    assert [(i["id"], i["kind"]) for i in out["items"]] == [
        ("curb_ramp_warning", "check"), ("utility_pole", "check"), ("street_tree", "check"), ("litter_can", "check"),
        ("parking_pay_station", "check"), ("obstruction", "stage"), ("entrance", "stage")]
    assert all(i["map_icon"] for i in out["items"] if i["kind"] == "stage") and "city_check" not in out
    assert views.scan_json(scan, [], issues, city_check={"scan_id": 1})["city_check"] == {"scan_id": 1}
    assert [(c["id"], c["inventory"], c["yes"]) for c in out["checks"][1:]] == [
        ("utility_pole", True, 0), ("street_tree", True, 0), ("litter_can", True, 0), ("parking_pay_station", True, 0)]


def test_inventory_rows_are_counted_as_found_and_never_as_defects(monkeypatch):
    light = survey_items.parse(INVENTORY_TOML, "us.toml")
    rows = [issue(issue_type="streetlight", object_label="yes"), issue(issue_type="streetlight", object_label="yes"),
            issue(issue_type="streetlight", object_label="unclear", review_status="needs_review")]
    assert all(not light.is_defect(r) for r in rows)
    assert views.issue_label(rows[0], light) == {"icon": "●", "text": "Streetlight pole"}
    assert views.issue_label(rows[2], light) == {"icon": "?", "text": "Streetlight pole: unclear from the photo"}
    assert views.check_json(rows[0], light)["inventory"] is True
    assert views.issue_json(rows[0], NOW, light)["defect"] is False
    monkeypatch.setattr(views, "standard_of", lambda scan: light)
    scan = {"id": 3, "status": "done", "stage_status": {}, "bbox": [0, 0, 1, 1], "image_count": 90,
            "captured_from": "a", "captured_to": "b", "cost_usd": 1.0, "processing_seconds": 60.0,
            "review_seconds": 0.0, "error": None, "jurisdiction": "US"}
    out = views.scan_json(scan, [], rows)
    assert out["counts"]["defects"] == 0 and out["counts"]["needs_review"] == 1
    (summary,) = out["checks"]
    assert summary["inventory"] is True and (summary["yes"], summary["no"], summary["unclear"]) == (2, 0, 1)


def test_a_scan_without_a_standard_is_korean():
    scan = {"id": 1, "status": "done", "stage_status": {}, "bbox": [0, 0, 1, 1], "image_count": 1,
            "captured_from": None, "captured_to": None, "cost_usd": 0.0, "processing_seconds": 0.0,
            "review_seconds": 0.0, "error": None}
    assert views.scan_json(scan, [], [])["standard"] == "KR" and views.scan_json(scan, [], [])["checks"] == []



def test_a_check_row_names_the_model_that_answered_and_an_audit_reads_like_a_second_opinion():
    std = survey_items.load("US")
    pole = {"issue_type": "utility_pole", "object_label": "yes",
            "model_outputs": {"check": {"answer": "yes", "model": "meta-models/Muse-Glimmer-30B"},
                              "audit": {"model": "muse-spark-1.3", "answer": "no", "agree": False}}}
    assert views.check_json(pole, std)["model"] == "Muse Glimmer (open weights, served on Modal)"
    assert views.second_json(pole) == {"answer": "no", "agree": False, "model": "Muse Spark"}
    ramp = {"issue_type": "curb_ramp_warning", "object_label": "no",
            "model_outputs": {"check": {"answer": "no"},
                              "second_opinion": {"model": "meta-models/muse-spark-open-1026", "answer": "no",
                                                 "agree": True}}}
    assert views.check_json(ramp, std)["model"] == "Muse Spark"  # rows from before the model was recorded
    assert views.second_json(ramp) == {"answer": "no", "agree": True, "model": "Muse Spark Open"}


def test_an_issue_carries_the_models_box_and_its_photos_heading():
    us = survey_items.load("US")
    row = {**issue(issue_type="curb_ramp_warning", object_label="no"),
           "model_outputs": {"check": {"visible": True, "answer": "no", "box": [100, 600, 300, 900]}}}
    out = views.issue_json(row, NOW, std=us, heading=93.5)
    assert out["box"] == [100.0, 600.0, 300.0, 900.0] and out["heading"] == 93.5
    for box in ([], None, [1, 2, 3], ["a", 1, 2, 3]):  # no box, or not one: the card says the model marked none
        bad = {**row, "model_outputs": {"check": {"visible": True, "answer": "no", "box": box}}}
        assert views.issue_json(bad, NOW, std=us)["box"] is None
    assert views.issue_json(issue(), NOW)["box"] is None and views.issue_json(issue(), NOW)["heading"] is None
