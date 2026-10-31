import csv
import json
from dataclasses import replace

from assist import export
from pipeline import survey_items
from tests.assist import survey_scan
from tests.assist.survey_scan import STANDARD


def by_row(feats):
    return {f["properties"]["row_id"]: f for f in feats}


def subset(props: dict, expected: dict) -> dict:
    return {k: props[k] for k in expected}


def test_features_leave_out_rejected_rows_and_name_every_photo():
    data = survey_scan.load()

    feats = export.features(data, STANDARD)

    rows = by_row(feats)
    assert len(feats) == 7 and "issue4" not in rows  # issue4 was rejected by a person
    assert len(export.features(data, STANDARD, include_rejected=True)) == 8
    for f in feats:
        p = f["properties"]
        assert p["creator"] and p["license"] == "CC BY-SA 4.0" and p["photo_id"]
        assert p["photo_url"] == f"https://www.mapillary.com/app/?pKey={p['photo_id']}"
        assert p["scan_id"] == data["scan"]["id"] and set(p) == set(export.PROPERTIES)
    confirmed = rows["issue1"]
    assert confirmed["geometry"] == {"type": "Point", "coordinates": [-122.3, 47.6001]}
    expected = {"item": "curb_ramp_warning", "answer": "no", "label": "Curb ramp without detectable warning",
                "defect": True, "review_status": "verified", "creator": "alice", "photo_id": "101",
                "captured_at": "2025-06-02T10:00:00Z", "evidence": f"evidence/scan{data['scan']['id']}_issue1.jpg"}
    assert subset(confirmed["properties"], expected) == expected


def test_defect_follows_report_when_and_never_marks_inventory_or_entrances():
    feats = by_row(export.features(survey_scan.load(), STANDARD))

    defect = {row: f["properties"]["defect"] for row, f in feats.items()}

    assert defect == {"issue1": True, "issue2": False, "issue3": True, "issue5": False, "issue6": False,
                      "issue7": False, "entrance1": False}
    assert feats["issue5"]["properties"]["label"] == "Streetlight pole"
    assert feats["issue6"]["properties"]["label"] == "Streetlight pole: unclear from the photo"
    assert feats["issue7"]["properties"]["label"] == "Blocked: traffic cone"  # the app's own label
    expected = {"item": "entrance", "answer": 2, "label": "2 steps", "creator": "dave"}
    assert subset(feats["entrance1"]["properties"], expected) == expected


def test_an_inventory_item_is_never_a_defect_even_with_a_report_when():
    pole = replace(survey_scan.POLE, report_when="yes")
    standard = replace(STANDARD, checks=(survey_scan.RAMP, pole))

    feats = by_row(export.features(survey_scan.load(), standard))

    assert feats["issue5"]["properties"]["answer"] == "yes" and feats["issue5"]["properties"]["defect"] is False


def test_a_check_the_standard_does_not_hold_is_labelled_by_its_type_and_is_no_defect():
    feats = by_row(export.features(survey_scan.load(), survey_items.load("KR")))

    assert feats["issue1"]["properties"]["label"] == "curb ramp warning"  # server.views' wording
    assert feats["issue1"]["properties"]["defect"] is False


def test_write_makes_one_geojson_and_one_csv_per_item(tmp_path):
    data = survey_scan.load()
    scan_id = data["scan"]["id"]

    paths = export.write(data, STANDARD, tmp_path / "out")

    collection = json.loads(paths["geojson"].read_text(encoding="utf-8"))
    assert paths["geojson"].name == f"survey_scan{scan_id}.geojson"
    assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 7
    assert {item: p.name for item, p in paths["csv"].items()} == {
        item: f"survey_scan{scan_id}_{item}.csv"
        for item in ("curb_ramp_warning", "streetlight_pole", "obstruction", "entrance")}
    with open(paths["csv"]["curb_ramp_warning"], encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        table = list(reader)
    assert reader.fieldnames == ["lat", "lng", *export.PROPERTIES]
    assert [(r["lat"], r["lng"], r["answer"], r["defect"]) for r in table] == [
        ("47.6001", "-122.3", "no", "true"), ("47.6002", "-122.3", "yes", "false"),
        ("47.6003", "-122.3", "no", "true")]
    assert table[1]["evidence"] == "" and table[0]["creator"] == "alice"


def test_every_row_says_its_review_status_in_words_and_a_site_visit_row_is_kept():
    data = survey_scan.load()
    row = next(r for r in data["issues"] if r["review_status"] == "needs_review")
    row["review_status"] = "field_check"

    feats = by_row(export.features(data, STANDARD))

    assert feats[f"issue{row['id']}"]["properties"]["status"] == "Check on site"
    assert feats["issue1"]["properties"]["status"] == "Confirmed by a person"
    assert all(f["properties"]["status"] in export.STATUS_TEXT.values() for f in feats.values())


def test_finds_the_models_set_aside_get_their_own_csv_with_the_photo_credit(tmp_path):
    data = survey_scan.load()
    data["dropped"] = [{"issue_type": "curb_ramp_warning", "frame_idx": 2, "lat": 47.6002, "lng": -122.3,
                        "captured_at": "2025-06-03T10:00:00Z", "audited": 0,
                        "model_outputs": {"check": {"visible": False, "answer": "unclear", "note": "a driveway"}}}]

    paths = export.write(data, STANDARD, tmp_path)

    with open(paths["dropped"], encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["item"] == "curb_ramp_warning" and rows[0]["photo_id"] == "102"
    assert rows[0]["creator"] == "carol" and rows[0]["license"] == "CC BY-SA 4.0" and rows[0]["audited"] == "false"
    assert rows[0]["model_note"] == "a driveway"
    assert "dropped" not in export.write(survey_scan.load(), STANDARD, tmp_path / "none")  # nothing set aside


def test_load_scan_reads_set_aside_finds_and_an_older_database_has_none(tmp_path):
    from eval import store
    import sqlite3
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.close()
    with store.open_ro(old) as c:
        assert store.dropped(c, 1) == []


CITY = {"city": "Seattle", "items": {
    "curb_ramp_warning": {"layer": "SDOT curb ramps", "id_fields": ["COMPKEY", "UNITID"],
                          "answer_field": {"name": "DETWARN_EXISTS", "yes": "Y", "no": "N"},
                          "pairs": [{"issue_id": 1, "in_view": True, "dist_m": 8.0,
                                     "city": {"COMPKEY": 805801, "UNITID": "CBR-1"}},
                                    {"issue_id": 2, "in_view": False, "dist_m": 26.0,
                                     "city": {"COMPKEY": 805802, "UNITID": "CBR-2"}}],
                          "missed": [{"lat": 47.61, "lng": -122.31, "city": {"COMPKEY": 805803, "UNITID": "CBR-3"}}]},
    "streetlight_pole": {"layer": "City Light poles", "id_fields": ["SCLNR"], "pairs": [], "missed": []}}}


def test_with_a_city_check_each_finding_carries_the_citys_own_record_fields():
    """docs/specs/2026-10-29-city-fields-design.md 3: a finding names the record it paired with, in the city's fields."""
    data = {**survey_scan.load(), "city_check": CITY}

    rows = by_row(export.features(data, STANDARD))

    ramp = rows["issue1"]["properties"]
    assert subset(ramp, {"city_layer": 1, "city_match": 1, "COMPKEY": 1, "UNITID": 1, "DETWARN_EXISTS": 1}) == {
        "city_layer": "SDOT curb ramps", "city_match": "In the city's list", "COMPKEY": 805801, "UNITID": "CBR-1",
        "DETWARN_EXISTS": "N"}  # the survey's answer "no" in the city's own value
    far = rows["issue2"]["properties"]
    assert (far["city_match"], far["UNITID"], far["DETWARN_EXISTS"]) == (
        "In the city's list (beyond the photo's view)", "CBR-2", "Y")
    alone = rows["issue3"]["properties"]
    assert (alone["city_match"], alone["COMPKEY"], alone["UNITID"]) == ("Not paired with the city's list", None, None)
    unclear = rows["issue6"]["properties"]  # a pole the models could not settle: no city value is made up
    assert unclear["city_match"] == "Not paired with the city's list" and unclear["SCLNR"] is None
    assert "DETWARN_EXISTS" not in unclear
    assert "city_layer" not in rows["issue7"]["properties"]  # an item the city has no layer for


def test_an_unclear_answer_leaves_the_citys_yes_no_field_empty():
    data = survey_scan.load()
    ramp = next(r for r in data["issues"] if r["id"] == 3)
    ramp["object_label"] = "unclear"
    data["city_check"] = CITY
    assert by_row(export.features(data, STANDARD))["issue3"]["properties"]["DETWARN_EXISTS"] is None


def test_the_city_records_the_photos_showed_but_the_survey_did_not_find_get_a_csv_to_check(tmp_path):
    data = {**survey_scan.load(), "city_check": CITY}
    scan_id = data["scan"]["id"]

    paths = export.write(data, STANDARD, tmp_path)

    with open(paths["csv"]["curb_ramp_warning"], encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        table = list(reader)
    assert reader.fieldnames == ["lat", "lng", *export.PROPERTIES, "city_layer", "city_match", "COMPKEY", "UNITID",
                                 "DETWARN_EXISTS"]
    assert table[0]["UNITID"] == "CBR-1" and table[0]["DETWARN_EXISTS"] == "N"
    check = paths["city_records"]["curb_ramp_warning"]
    assert check.name == f"survey_scan{scan_id}_curb_ramp_warning_city_records_to_check.csv"
    with open(check, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"lat": "47.61", "lng": "-122.31", "city_layer": "SDOT curb ramps", "COMPKEY": "805803",
                     "UNITID": "CBR-3", "note": export.RECORD_TO_CHECK}]
    assert "streetlight_pole" not in paths["city_records"]  # nothing missed: no file


def test_without_a_city_check_the_files_are_as_before(tmp_path):
    paths = export.write(survey_scan.load(), STANDARD, tmp_path)
    with open(paths["csv"]["curb_ramp_warning"], encoding="utf-8-sig", newline="") as f:
        assert csv.DictReader(f).fieldnames == ["lat", "lng", *export.PROPERTIES]
    assert paths["city_records"] == {}


def test_text_that_starts_like_a_formula_stays_text_in_a_spreadsheet():
    assert [export._cell(v) for v in ("=HYPERLINK(1)", "+1", "-1", "@x", "\tx", "\rx")] == [
        "'=HYPERLINK(1)", "'+1", "'-1", "'@x", "'\tx", "'\rx"]
    assert [export._cell(v) for v in ("Pike St", -1.5, 3, None, True)] == ["Pike St", "-1.5", "3", "", "true"]
