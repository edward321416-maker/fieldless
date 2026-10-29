import pytest

from deploy.merge_scans import merge_scans, remap_city_check
from pipeline import db

BBOX = [127.05, 37.54, 127.06, 37.55]


def issue(n):
    return {"issue_type": "street_tree", "frame_idx": n, "lat": 37.545, "lng": 127.055, "review_status": "auto",
            "review_reasons": [], "object_label": "yes", "model_outputs": {}, "evidence": {"image": f"scan1_issue{n}.jpg"}}


def test_a_survey_is_replaced_in_the_live_database_and_everything_else_there_stays(tmp_path):
    local = db.connect(tmp_path / "local.db")
    seoul = db.create_scan(local, "mapillary", BBOX, "KR")
    db.add_frames(local, seoul, [{"idx": 0, "clip": 0, "image_id": "9", "sequence_id": "s", "lat": 37.545,
                                  "lng": 127.055, "compass": 0.0, "captured_at": "2025-01-01T00:00:00Z",
                                  "creator": "c", "width": 2048, "height": 1152}])
    for n in range(3):
        db.add_issue(local, seoul, issue(n))
    db.update_scan(local, seoul, status="done", cost_usd=1.9)
    db.set_report_text(local, seoul, {"summary": "new"}, "muse-spark")
    local.close()

    live = db.connect(tmp_path / "live.db")
    old = db.create_scan(live, "mapillary", BBOX, "KR")  # the same survey as seeded before: id 1
    db.add_issue(live, old, issue(9))
    visitor = db.create_scan(live, "mapillary", BBOX, "US")
    taken = [db.add_issue(live, visitor, issue(n)) for n in range(4)]  # ids the local rows also use
    run = db.create_item_run(live, old, "bench", client_ip_hash="h")
    live.close()

    written = merge_scans(tmp_path / "local.db", tmp_path / "live.db", [seoul])

    assert written[seoul]["counts"] == {"frame": 1, "entrance": 0, "sidewalk_issue": 3, "dropped_pick": 0,
                                        "report_text": 1}
    live = db.connect(tmp_path / "live.db")
    assert db.get_scan(live, seoul)["cost_usd"] == pytest.approx(1.9) and db.get_scan(live, seoul)["status"] == "done"
    rows = db.issues(live, seoul)
    assert [r["evidence"]["image"] for r in rows] == ["scan1_issue0.jpg", "scan1_issue1.jpg", "scan1_issue2.jpg"]
    assert not {r["id"] for r in rows} & set(taken)  # new ids: the visitor's rows keep theirs
    assert [r["id"] for r in db.issues(live, visitor)] == taken
    assert db.item_run(live, run)["scan_id"] == seoul  # a visitor's item run on the survey stays
    assert db.report_text(live, seoul) == {"summary": "new"} and len(db.frames(live, seoul)) == 1
    new = written[seoul]["ids"]["sidewalk_issue"]
    assert sorted(new.values()) == [r["id"] for r in rows]
    check = {"scan_id": seoul, "items": {"street_tree": {
        "found": 2, "covered": 5, "matched_ids": [1, 3], "rejected_at_city_point": [2],
        "pairs": [{"issue_id": 1, "dist_m": 4.0}, {"issue_id": 3, "dist_m": 6.0}], "missed": []}}}
    moved = remap_city_check(check, new)["items"]["street_tree"]
    assert moved["matched_ids"] == sorted([new[1], new[3]]) and moved["rejected_at_city_point"] == [new[2]]
    assert [p["issue_id"] for p in moved["pairs"]] == [new[1], new[3]] and moved["found"] == 2
    assert set(moved["matched_ids"]) <= {r["id"] for r in rows}  # every id names a finding of this survey
    with pytest.raises(KeyError):
        remap_city_check({"items": {"street_tree": {"matched_ids": [42]}}}, new)
    with pytest.raises(KeyError):
        merge_scans(tmp_path / "local.db", tmp_path / "live.db", [99])
