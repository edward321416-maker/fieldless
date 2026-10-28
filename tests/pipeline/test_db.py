import sqlite3

import pytest

from pipeline import db


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def frame(i, clip=0):
    return {"idx": i, "clip": clip, "image_id": f"img{i}", "sequence_id": "seq", "lat": 37.5, "lng": 127.0,
            "compass": 90.0, "captured_at": "2025-09-24T10:00:00Z", "creator": "someone", "width": 2048,
            "height": 1536}


def test_a_new_scan_is_queued_with_its_bbox(conn):
    scan_id = db.create_scan(conn, "mapillary", [126.98, 37.56, 126.99, 37.57])
    scan = db.get_scan(conn, scan_id)
    assert scan["status"] == "queued"
    assert scan["bbox"] == [126.98, 37.56, 126.99, 37.57]
    assert scan["stage_status"] == {}
    assert scan["cost_usd"] == 0


def test_missing_scan_raises_key_error(conn):
    with pytest.raises(KeyError):
        db.get_scan(conn, 99)


def test_set_stage_keeps_the_other_stages(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.set_stage(conn, scan_id, "collect", "done")
    db.set_stage(conn, scan_id, "chunks", "running")
    assert db.get_scan(conn, scan_id)["stage_status"] == {"collect": "done", "chunks": "running"}


def test_frames_round_trip_in_index_order(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.add_frames(conn, scan_id, [frame(1), frame(0)])
    rows = db.frames(conn, scan_id)
    assert [r["idx"] for r in rows] == [0, 1]
    assert rows[0] == frame(0)


def test_a_failed_frame_insert_leaves_no_rows_behind(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    with pytest.raises(sqlite3.IntegrityError):
        db.add_frames(conn, scan_id, [frame(0), frame(1), frame(1)])
    assert db.frames(conn, scan_id) == []
    db.update_scan(conn, scan_id, status="running")
    assert db.frames(conn, scan_id) == []


def test_clear_rows_removes_frames_of_one_scan_only(conn):
    a = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    b = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.add_frames(conn, a, [frame(0), frame(1)])
    db.add_frames(conn, b, [frame(0)])
    db.clear_rows(conn, a, "frame")
    assert db.frames(conn, a) == []
    assert [r["idx"] for r in db.frames(conn, b)] == [0]


def test_entrance_json_and_bool_columns_round_trip(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    entrance_id = db.add_entrance(conn, scan_id, {
        "frame_idx": 3, "place_name": "Cafe", "step_count": 2, "stairs_detected": True, "has_ramp": False,
        "review_reasons": ["count_mismatch"], "model_outputs": {"reading": {"step_count": 2}}})
    db.update_entrance(conn, entrance_id, evidence={"image": "e.jpg"})
    (row,) = db.entrances(conn, scan_id)
    assert row["stairs_detected"] is True and row["has_ramp"] is False and row["sill_possible"] is None
    assert row["review_reasons"] == ["count_mismatch"]
    assert row["model_outputs"] == {"reading": {"step_count": 2}}
    assert row["evidence"] == {"image": "e.jpg"}
    assert row["review_status"] == "auto"


def test_issues_and_clear_rows(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.add_issue(conn, scan_id, {"issue_type": "obstruction", "object_label": "bicycle", "frame_start": 1,
                                 "frame_end": 3})
    assert db.issues(conn, scan_id)[0]["object_label"] == "bicycle"
    db.clear_rows(conn, scan_id, "sidewalk_issue")
    assert db.issues(conn, scan_id) == []
    with pytest.raises(ValueError):
        db.clear_rows(conn, scan_id, "scan")


def test_daily_cost_sums_scans_created_that_day(conn):
    a = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    b = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.update_scan(conn, a, cost_usd=1.25)
    db.update_scan(conn, b, cost_usd=0.5)
    assert db.daily_cost(conn, db.now()[:10]) == pytest.approx(1.75)
    assert db.daily_cost(conn, "1999-01-01") == 0


def test_review_seconds_add_up(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 1, 1])
    db.add_review_seconds(conn, scan_id, 2.5)
    db.add_review_seconds(conn, scan_id, 4.0)
    assert db.get_scan(conn, scan_id)["review_seconds"] == 6.5


def test_clear_issues_deletes_only_the_given_types_of_one_scan(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    other = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    for issue_type in ("obstruction", "fixed_obstruction", "missing_blocks", "cover_cut"):
        db.add_issue(conn, scan_id, {"issue_type": issue_type})
    db.add_issue(conn, other, {"issue_type": "cover_cut"})
    db.clear_issues(conn, scan_id, ("missing_blocks", "cover_cut"))
    assert [i["issue_type"] for i in db.issues(conn, scan_id)] == ["obstruction", "fixed_obstruction"]
    assert [i["issue_type"] for i in db.issues(conn, other)] == ["cover_cut"]


def test_unknown_column_names_are_refused_before_any_sql(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    entrance_id = db.add_entrance(conn, scan_id, {"frame_idx": 1})
    with pytest.raises(ValueError, match="unknown entrance columns"):
        db.update_entrance(conn, entrance_id, **{"step_count = 0, review_status": "x"})
    with pytest.raises(ValueError, match="unknown sidewalk_issue columns"):
        db.add_issue(conn, scan_id, {"issue_type": "obstruction", "colour": "red"})
    assert db.entrances(conn, scan_id)[0]["review_status"] == "auto"


def test_get_entrance_and_issue(conn):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    entrance_id = db.add_entrance(conn, scan_id, {"frame_idx": 1, "place_name": "A"})
    issue_id = db.add_issue(conn, scan_id, {"issue_type": "obstruction", "object_label": "bicycle"})
    assert db.get_entrance(conn, entrance_id)["place_name"] == "A"
    assert db.get_issue(conn, issue_id)["object_label"] == "bicycle"
    with pytest.raises(KeyError):
        db.get_entrance(conn, 999)
    with pytest.raises(KeyError):
        db.get_issue(conn, 999)


def test_list_scans_newest_first_and_scans_since(conn):
    a = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    b = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    db.update_scan(conn, a, client_ip_hash="h1")
    db.update_scan(conn, b, client_ip_hash="h1")
    assert [s["id"] for s in db.list_scans(conn)] == [b, a]
    assert db.scans_since(conn, "h1", "2000-01-01T00:00:00Z") == 2
    assert db.scans_since(conn, "h1", "2999-01-01T00:00:00Z") == 0
    assert db.scans_since(conn, "other", "2000-01-01T00:00:00Z") == 0


def test_dropped_picks_are_kept_per_scan_and_cleared_with_their_check(conn, tmp_path):
    scan_id = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    other = db.create_scan(conn, "mapillary", [0, 0, 0.01, 0.01])
    pick = {"issue_type": "curb_ramp_warning", "frame_idx": 4, "frame_start": 2, "frame_end": 4, "lat": 47.61,
            "lng": -122.32, "captured_at": "2022-08-30T10:00:00Z", "audited": True,
            "model_outputs": {"check": {"visible": False, "answer": "unclear"}}}
    db.add_dropped(conn, scan_id, pick)
    db.add_dropped(conn, scan_id, {**pick, "issue_type": "litter_can", "audited": False})
    db.add_dropped(conn, other, pick)
    (first, second) = db.dropped(conn, scan_id)
    assert first["audited"] is True and first["model_outputs"]["check"]["visible"] is False
    db.clear_issues(conn, scan_id, ("curb_ramp_warning",))
    assert [d["issue_type"] for d in db.dropped(conn, scan_id)] == ["litter_can"]
    assert len(db.dropped(conn, other)) == 1
    old = tmp_path / "old.db"  # a database made before the table existed gets it on connect
    plain = sqlite3.connect(old)
    plain.execute("CREATE TABLE scan (id INTEGER PRIMARY KEY)")
    plain.close()
    assert db.dropped(db.connect(old), 1) == []


def test_report_text_is_kept_per_scan_and_a_newer_one_replaces_it(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    scan_id = db.create_scan(conn, "test", [0, 0, 1, 1])
    assert db.report_text(conn, scan_id) is None
    db.set_report_text(conn, scan_id, {"summary": "first"}, "muse-spark-1.3")
    db.set_report_text(conn, scan_id, {"summary": "second", "items": {"a": "b"}}, "muse-spark-1.3")
    assert db.report_text(conn, scan_id) == {"summary": "second", "items": {"a": "b"}}


def test_report_text_reads_none_from_a_database_made_before_the_table(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE scan (id INTEGER PRIMARY KEY)")
    old.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert db.report_text(ro, 1) is None  # a read-only copy of an older database has no report_text table
    ro.close()
