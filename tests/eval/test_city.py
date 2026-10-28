import json

import pytest

from eval import city
from pipeline import config, db, geo

ORIGIN = (47.6, -122.33)
ITEM = "streetlight_pole"
PHOTO_AT = "2023-02-08T00:00:00Z"
MS_2020 = 1577836800000  # 2020-01-01
MS_2024 = 1704067200000  # 2024-01-01


def frame(idx=0, at=ORIGIN, compass=0.0, captured_at=PHOTO_AT):
    return {"idx": idx, "clip": 0, "image_id": f"img{idx}", "lat": at[0], "lng": at[1], "compass": compass,
            "captured_at": captured_at}


def box_at(cx):
    """A box whose centre is cx on the 0-1000 scale across the image."""
    return [cx - 20, 300, cx + 20, 700]


def row(issue_id, frame_idx=0, box=(), label="yes", status="auto", corrections=(), item=ITEM):
    return {"id": issue_id, "issue_type": item, "frame_idx": frame_idx, "object_label": label,
            "review_status": status, "review_reasons": [], "corrections": list(corrections),
            "model_outputs": {"check": {"answer": label, "box": list(box)}}}


def point(pid, bearing, meters, at=ORIGIN, **props):
    lat, lng = geo.offset_point(at[0], at[1], bearing, meters)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"OBJECTID": pid, **props}}


def layer(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def run(rows, points, frames=None, **kw):
    return city.evaluate(rows, frames or [frame()], points, ITEM, id_field="OBJECTID", **kw)


def pairs(result):
    return {m["issue_id"]: m["point_id"] for m in result["matched"]}


def test_row_direction_adds_the_box_offset_to_the_compass_and_wraps():
    assert city.row_direction(row(1, box=box_at(500)), frame(compass=90.0)) == pytest.approx(90.0)
    assert city.row_direction(row(1, box=box_at(1000)), frame(compass=350.0)) == pytest.approx(25.0)
    assert city.row_direction(row(1, box=[]), frame()) is None
    assert city.row_direction(row(1, box=box_at(500)), frame(compass=None)) is None


def test_box_on_the_left_or_right_picks_the_pole_on_that_side_of_the_corner():
    # Two poles 15 m away on the two sides of a crossing, 50 degrees apart as seen from the photo.
    corner = layer(point(10, -25, 15), point(11, 25, 15))
    left, right = run([row(1, box=box_at(140))], corner), run([row(1, box=box_at(860))], corner)
    assert pairs(left) == {1: 10} and pairs(right) == {1: 11}
    assert left["matched"][0]["offset_deg"] < 1 and left["matched"][0]["dist_m"] == pytest.approx(15, abs=0.1)


def test_pairs_are_one_to_one_and_the_best_score_chooses_first():
    # Row 2 looks straight at P (score 0.33) and takes it before row 3 (0.51); row 1 looks straight at Q; row 3 is
    # left with both points used, so it does not share one: it repeats a found point.
    rows = [row(1, box=box_at(600)), row(2, box=box_at(500)), row(3, box=box_at(450))]
    r = run(rows, layer(point("P", 0, 10), point("Q", 7, 12)))
    assert pairs(r) == {1: "Q", 2: "P"} and r["rows_repeat"] == [3] and r["rows_unmatched"] == []


def test_a_row_without_a_box_matches_by_distance_alone_within_12_m():
    near, far = run([row(1)], layer(point(10, 120, 10))), run([row(1)], layer(point(10, 0, 20)))
    assert pairs(near) == {1: 10} and near["matched"][0]["offset_deg"] is None
    assert pairs(far) == {} and far["rows_unmatched"] == [1]


def test_a_row_with_a_direction_chooses_before_one_without():
    # Both rows can reach the pole; the boxless row is closer in metres, but its unknown angle scores as max_deg.
    frames = [frame(0), frame(1, at=geo.offset_point(*ORIGIN, 180, 4))]
    r = run([row(1, frame_idx=0), row(2, frame_idx=1, box=box_at(500))], layer(point(10, 0, 8)), frames)
    assert pairs(r) == {2: 10} and r["rows_repeat"] == [1] and r["rows_unmatched"] == []


def test_rejected_rows_and_other_items_are_ignored():
    rows = [row(1, box=box_at(500), status="rejected"), row(2, box=box_at(500), item="litter_can")]
    r = run(rows, layer(point(10, 0, 10)))
    assert r["rows"] == 0 and r["matched"] == [] and r["missed_covered"] == [10]


def test_inventory_mode_keeps_only_found_things():
    rows = [row(1, box=box_at(500), label="no"), row(2, box=box_at(860), label="yes"),
            row(3, box=box_at(140), label="unclear")]
    points = layer(point(10, 0, 10), point(11, 25, 10), point(12, -25, 10))
    assert run(rows, points)["rows"] == 3
    r = run(rows, points, inventory=True)
    assert r["rows"] == 2 and pairs(r) == {2: 11, 3: 12} and r["missed_covered"] == [10]


def test_inventory_mode_reads_the_reviewer_answer():
    reviewed = row(1, box=box_at(500), label="yes", status="verified",
                   corrections=[{"action": "answer", "answer": "no", "before": "yes", "seconds": 3.0}])
    assert run([reviewed], layer(point(10, 0, 10)), inventory=True)["rows"] == 0


def test_only_points_a_photo_could_show_can_be_missed():
    # Behind the camera, outside the view, or beyond cover_m: not photographed, never a miss.
    points = layer(point("front", 0, 10), point("behind", 180, 10), point("side", 60, 10), point("far", 0, 25),
                   point("elsewhere", 0, 5000))
    r = run([], points)
    assert r["missed_covered"] == ["front"] and r["covered"] == 1 and r["not_photographed"] == 4
    assert r["points_in_layer"] == 5 and r["found_share"] == 0.0


def test_a_photo_without_a_compass_covers_nothing():
    r = run([], layer(point(10, 0, 5)), frames=[frame(compass=None)])
    assert r["missed_covered"] == [] and r["not_photographed"] == 1 and r["found_share"] is None


def test_a_found_point_counts_as_photographed_so_the_share_stays_at_most_one():
    # 28 m away and 45 degrees right of the compass: outside the coverage cone, but the row's box shows it.
    r = run([row(1, box=box_at(1000))], layer(point(10, 45, 28)))
    assert pairs(r) == {1: 10} and r["covered"] == 1 and r["found_share"] == 1.0 and r["not_photographed"] == 0


def test_points_installed_after_the_photo_are_neither_found_nor_missed():
    points = layer(point("new_found", 0, 10, INSTALLED=MS_2024), point("new_missed", 20, 15, INSTALLED=MS_2024),
                   point("old_missed", -20, 15, INSTALLED=MS_2020), point("no_date", -30, 12))
    r = run([row(1, box=box_at(500))], points, installed_field="INSTALLED")
    assert r["matched"] == [] and r["missed_covered"] == ["old_missed", "no_date"]
    after = {a["point_id"]: a for a in r["installed_after_photo"]}
    assert set(after) == {"new_found", "new_missed"} and after["new_found"]["issue_id"] == 1
    assert after["new_missed"]["issue_id"] is None
    assert after["new_found"]["installed"].startswith("2024-01-01") and after["new_found"]["photo"] == PHOTO_AT
    assert r["covered"] == 2 and r["found_share"] == 0.0 and r["rows_unmatched"] == []
    assert r["points_in_layer"] == r["covered"] + len(r["installed_after_photo"]) + r["not_photographed"]


def test_a_missed_point_is_judged_on_its_latest_photo():
    # Installed in 2024: a 2023 photo could not show it, but a 2025 photo of the same spot could.
    frames = [frame(0), frame(1, captured_at="2025-06-01T00:00:00Z")]
    r = run([], layer(point(10, 0, 10, INSTALLED=MS_2024)), frames, installed_field="INSTALLED")
    assert r["missed_covered"] == [10] and r["installed_after_photo"] == []


def test_installed_dates_must_be_epoch_milliseconds():
    with pytest.raises(ValueError, match="epoch milliseconds"):
        run([], layer(point(10, 0, 10, INSTALLED="2024-01-01")), installed_field="INSTALLED")


def test_answers_agree_or_differ_with_the_city_and_a_reviewer_answer_wins():
    corners = [(47.6 + 0.001 * i, -122.33) for i in range(5)]
    frames = [frame(i, at=c) for i, c in enumerate(corners)]
    rows = [row(1, frame_idx=0, box=box_at(500), label="yes"),
            row(2, frame_idx=1, box=box_at(500), label="no"),
            row(3, frame_idx=2, box=box_at(500), label="unclear"),
            row(4, frame_idx=3, box=box_at(500), label="no", status="verified",
                corrections=[{"action": "answer", "answer": "yes", "before": "no", "seconds": 4.0}]),
            row(5, frame_idx=4, box=box_at(500), label="yes")]
    city_values = ["Y", "Y", "N", "Y", None]
    points = layer(*[point(10 + i, 0, 10, at=c, DETWARN_EXISTS=v)
                     for i, (c, v) in enumerate(zip(corners, city_values))])
    r = run(rows, points, frames, truth_field="DETWARN_EXISTS", truth_yes="Y")
    assert (r["agree"], r["disagree"], r["unclear"], r["city_unknown"]) == (2, 1, 1, 1)
    by_row = {m["issue_id"]: m for m in r["matched"]}
    assert by_row[4]["row_answer"] == "yes" and by_row[2]["city_value"] == "yes" and by_row[5]["city_value"] is None
    assert "agree" not in run(rows, points, frames)


def test_points_come_from_point_features_only():
    features = [point(10, 0, 10), {"type": "Feature", "geometry": None, "properties": {"OBJECTID": 11}},
                {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                 "properties": {"OBJECTID": 12}}]
    assert [p["id"] for p in city.load_points(layer(*features), "OBJECTID")] == [10]
    assert [p["id"] for p in city.load_points(features)] == [0]
    with pytest.raises(ValueError, match="OBJECTID"):
        city.load_points([point(None, 0, 10)], "OBJECTID")


def test_a_row_that_can_only_reach_a_found_point_repeats_it_and_is_not_outside_the_record():
    # group_m cuts one walk past a pole into pieces; two neighbouring pieces can both pick that pole. The second row
    # is a repeat, not a thing the city never recorded. A row that reaches no city point at all is outside it.
    frames = [frame(0), frame(1, at=geo.offset_point(*ORIGIN, 0, 5))]
    rows = [row(1, frame_idx=0, box=box_at(500)), row(2, frame_idx=1, box=box_at(500)),
            row(3, frame_idx=0, box=box_at(1000))]
    r = run(rows, layer(point(10, 0, 15)), frames)
    assert pairs(r) == {2: 10} and r["rows_repeat"] == [1] and r["rows_unmatched"] == [3]
    text = city.to_markdown(r)
    assert "| Rows repeating a found city point | 1 |" in text and "| Rows not in the city record | 1 |" in text
    assert "Repeats a found city point (two photos of one thing): rows [1]" in text


def test_a_row_that_reaches_a_point_installed_after_its_photo_is_a_repeat_too():
    frames = [frame(0), frame(1, at=geo.offset_point(*ORIGIN, 0, 5))]
    rows = [row(1, frame_idx=0, box=box_at(500)), row(2, frame_idx=1, box=box_at(500))]
    r = run(rows, layer(point(10, 0, 15, INSTALL=MS_2024)), frames, installed_field="INSTALL")
    assert [a["issue_id"] for a in r["installed_after_photo"]] == [2]
    assert r["rows_repeat"] == [1] and r["rows_unmatched"] == []


def test_markdown_never_calls_a_row_outside_the_city_record_wrong():
    result = run([row(1, box=box_at(500)), row(2, box=box_at(860))], layer(point(10, 0, 10), point(11, -25, 10)))
    text = city.to_markdown(result)
    assert "| Found | 1 (0.5 of photographed) |" in text
    assert "Not in the city record (new, moved, or a false find): rows [2]" in text
    assert "Photographed but not found: city points [11]" in text and "wrong" not in text.lower()


def test_cli_writes_the_json_next_to_the_run_and_prints_markdown(tmp_path, capsys):
    conn = db.connect(config.run_dir("r") / "threshold.db")
    scan_id = db.create_scan(conn, "test", None)
    db.add_frames(conn, scan_id, [
        {"idx": 0, "clip": 0, "image_id": "img0", "sequence_id": "s", "lat": ORIGIN[0], "lng": ORIGIN[1],
         "compass": 0.0, "captured_at": PHOTO_AT, "creator": "u", "width": 2048, "height": 1536}])
    found = db.add_issue(conn, scan_id, {"issue_type": "curb_ramp_warning", "frame_idx": 0, "frame_start": 0,
                                         "frame_end": 0, "object_label": "yes", "review_status": "auto",
                                         "model_outputs": {"check": {"answer": "yes", "box": box_at(500)}}})
    db.add_issue(conn, scan_id, {"issue_type": "curb_ramp_warning", "frame_idx": 0, "object_label": "no",
                                 "review_status": "rejected", "model_outputs": {"check": {"box": box_at(860)}}})
    conn.close()
    path = tmp_path / "ramps.geojson"
    path.write_text(json.dumps(layer(point(7, 0, 12, DETWARN_EXISTS="Y"), point(8, -25, 10, DETWARN_EXISTS="N"))),
                    encoding="utf-8")

    out = city.main(["--run", "r", "--scan", str(scan_id), "--item", "curb_ramp_warning", "--layer", str(path),
                     "--id-field", "OBJECTID", "--truth-field", "DETWARN_EXISTS", "--truth-yes", "Y"])

    result = json.loads(out.read_text(encoding="utf-8"))
    assert out == config.run_dir("r") / f"city_curb_ramp_warning_scan{scan_id}.json"
    assert result["scan_id"] == scan_id and result["layer"] == "ramps.geojson" and result["rows"] == 1
    assert pairs(result) == {found: 7} and result["missed_covered"] == [8] and result["agree"] == 1
    printed = capsys.readouterr().out
    assert f"City record: curb_ramp_warning, scan {scan_id}" in printed
    assert "| Answer agrees with the city record | 1 |" in printed


def test_cli_wants_truth_field_and_truth_yes_together(tmp_path):
    with pytest.raises(SystemExit):
        city.main(["--run", "r", "--scan", "1", "--item", ITEM, "--layer", str(tmp_path / "x.geojson"),
                   "--truth-field", "DETWARN_EXISTS"])


def test_where_keeps_only_points_with_the_listed_values():
    # Seattle's pole layer holds plain utility poles too; only IS_STREETLIGHT=YES are streetlights.
    features = [point(1, 0, 10, IS_STREETLIGHT="YES"), point(2, 30, 10, IS_STREETLIGHT="NO"), point(3, 330, 10)]
    r = run([row(1, box=box_at(500))], layer(*features), where={"IS_STREETLIGHT": "YES"})
    assert r["points_in_layer"] == 1 and pairs(r) == {1: 1} and r["params"]["where"] == {"IS_STREETLIGHT": "YES"}


def test_cli_where_takes_key_value_pairs(tmp_path):
    with pytest.raises(SystemExit):
        city.main(["--run", "r", "--scan", "1", "--item", ITEM, "--layer", str(tmp_path / "x.geojson"),
                   "--where", "IS_STREETLIGHT"])
    assert city.parse_where(["IS_STREETLIGHT=YES", "OWNER=SCL"]) == {"IS_STREETLIGHT": "YES", "OWNER": "SCL"}


def test_a_rejected_row_that_looked_at_an_unfound_city_point_is_listed_for_a_site_visit():
    # Three city points ahead: one a kept row found, one a rejected row looked at, one only a boxless rejected row is near.
    pts = layer(point(10, 0, 10), point(11, 30, 10), point(12, 180, 5))
    rows = [row(1, box=box_at(500)),                                  # kept: finds point 10 straight ahead
            row(2, box=box_at(500 + 30 / 70 * 1000), status="rejected"),  # looked at point 11
            row(3, box=box_at(500), status="rejected"),               # looked at point 10, already found
            row(4, box=[], status="rejected")]                        # no direction: 5 m from point 12

    r = run(rows, pts)

    assert pairs(r) == {1: 10}
    assert [(d["issue_id"], d["point_id"]) for d in r["rejected_at_city_point"]] == [(2, 11)]
    assert 11 in r["missed_covered"]  # still a miss: the record and the person disagree, neither wins
    assert "rows [2], city points [11]" in city.to_markdown(r)
