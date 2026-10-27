from datetime import datetime, timezone

import pytest

from server import views

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
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
                                             "license": "CC BY-SA 4.0"}
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
    assert set(out) == {"id", "lat", "lng", "name", "category", "step_count", "stairs_detected", "sill_possible",
                        "label", "review_status", "review_reasons", "evidence", "captured_at", "old_photo"}


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
                             "needs_review": 2}
    assert out["id"] == 1 and out["status"] == "done" and out["image_count"] == 60
