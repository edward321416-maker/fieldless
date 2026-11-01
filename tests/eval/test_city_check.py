import json

import pytest

from eval import city_check
from pipeline import geo

ORIGIN = (47.6, -122.33)


def frame(idx=0):
    return {"idx": idx, "clip": 0, "image_id": f"img{idx}", "lat": ORIGIN[0], "lng": ORIGIN[1], "compass": 0.0,
            "captured_at": "2023-02-08T00:00:00Z"}


def row(issue_id, item, box, status="auto", label="yes"):
    return {"id": issue_id, "issue_type": item, "frame_idx": 0, "object_label": label, "review_status": status,
            "review_reasons": [], "corrections": [], "model_outputs": {"check": {"answer": label, "box": box}}}


def point(pid, bearing, meters, **props):
    lat, lng = geo.offset_point(ORIGIN[0], ORIGIN[1], bearing, meters)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"OBJECTID": pid, **props}}


def test_build_writes_the_designed_shape_per_item(tmp_path):
    layer = {"type": "FeatureCollection", "features": [point(1, 0, 10), point(2, 30, 10)]}
    (tmp_path / "poles.geojson").write_text(json.dumps(layer), encoding="utf-8")
    data = {"scan": {"id": 4}, "frames": [frame()], "entrances": [],
            "issues": [row(10, "pole", [480, 0, 520, 600]),                          # looks at point 1
                       row(11, "pole", [908, 0, 948, 600], status="rejected")]}      # looked at point 2
    layers = {"pole": {"layer": "poles.geojson", "name": "City poles", "inventory": True}}

    out = city_check.build(data, layers, tmp_path, measured_at="2026-10-28")

    assert out["scan_id"] == 4 and out["city"] == "Seattle" and out["measured_at"] == "2026-10-28"
    assert out["reviewed"] is True and out["method_url"].endswith("eval/city.py")
    pole = out["items"]["pole"]
    assert {k: pole[k] for k in ("layer", "found", "covered", "matched_ids", "rejected_at_city_point")} == {
        "layer": "City poles", "found": 1, "covered": 2, "matched_ids": [10], "rejected_at_city_point": [11]}


def test_build_names_the_city_records_each_finding_paired_with_and_the_ones_it_missed(tmp_path):
    """docs/specs/2026-10-29-city-fields-design.md 2: the record fields come along, so the export can write them."""
    layer = {"type": "FeatureCollection", "features": [point(1, 0, 10, UNITID="CBR-1", DETWARN_EXISTS="Y"),
                                                       point(2, 30, 10, UNITID="CBR-2", DETWARN_EXISTS="N")]}
    (tmp_path / "ramps.geojson").write_text(json.dumps(layer), encoding="utf-8")
    data = {"scan": {"id": 4}, "frames": [frame()], "entrances": [], "issues": [row(10, "ramp", [480, 0, 520, 600])]}
    layers = {"ramp": {"layer": "ramps.geojson", "name": "City ramps", "inventory": False, "truth_field": "DETWARN_EXISTS",
                       "truth_yes": "Y", "truth_no": "N", "id_fields": ["UNITID"]}}

    ramp = city_check.build(data, layers, tmp_path, measured_at="2026-10-29")["items"]["ramp"]

    assert ramp["id_fields"] == ["UNITID"]
    assert ramp["pairs"] == [{"issue_id": 10, "in_view": True, "dist_m": 10.0, "city": {"UNITID": "CBR-1"}}]
    assert len(ramp["missed"]) == 1 and ramp["missed"][0]["city"] == {"UNITID": "CBR-2"}
    assert {"lat", "lng"} <= set(ramp["missed"][0])
    assert ramp["answer_field"] == {"name": "DETWARN_EXISTS", "yes": "Y", "no": "N"}


def test_an_item_without_a_yes_no_field_writes_no_answer_field(tmp_path):
    (tmp_path / "poles.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    data = {"scan": {"id": 1}, "frames": [frame()], "entrances": [], "issues": []}
    pole = city_check.build(data, {"pole": {"layer": "poles.geojson", "name": "p", "inventory": True}}, tmp_path,
                            measured_at="2026-10-29")["items"]["pole"]
    assert "answer_field" not in pole and pole["pairs"] == [] and pole["missed"] == [] and pole["id_fields"] == []


def test_a_scan_with_rows_to_review_is_not_reviewed(tmp_path):
    (tmp_path / "poles.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                            encoding="utf-8")
    data = {"scan": {"id": 1}, "frames": [frame()], "entrances": [],
            "issues": [row(1, "pole", [], status="needs_review", label="unclear")]}
    out = city_check.build(data, {"pole": {"layer": "poles.geojson", "name": "p", "inventory": True}}, tmp_path,
                           measured_at="2026-10-28")
    assert out["reviewed"] is False and out["items"]["pole"]["found"] == 0


def test_seattle_layers_match_the_evaluation_script():
    assert set(city_check.SEATTLE) == {"utility_pole", "street_tree", "litter_can", "parking_pay_station",
                                       "curb_ramp_warning"}
    ramp = city_check.SEATTLE["curb_ramp_warning"]
    assert (ramp["inventory"], ramp["truth_field"], ramp["truth_yes"]) == (False, "DETWARN_EXISTS", "Y")
    assert city_check.SEATTLE["street_tree"]["installed_field"] == "PLANTED_DATE"


def test_the_seattle_file_names_each_layers_record_fields():
    city = city_check.load_city("seattle")
    assert city["city"] == "Seattle" and city["root"] == "seattle"
    assert city["items"]["utility_pole"]["id_fields"] == ["SCLNR", "ASSET_ID"]
    assert city["items"]["curb_ramp_warning"]["truth_no"] == "N"
    assert all(cfg["id_fields"] for cfg in city["items"].values())


def test_a_target_the_city_publishes_finds_its_layer_by_any_of_its_names():
    city = city_check.load_city("seattle")
    rack = city_check.extra_layer(city, "Bike Racks")
    assert rack["id"] == "bike_rack" and rack["layer"] == "assets/bikeracks.geojson"
    assert rack["id_fields"] == ["COMPKEY", "UNITID", "UNITDESC"] and rack["root"] == "seattle"
    assert rack["name"] == "SDOT bike racks"
    assert city_check.extra_layer(city, "bicycle parking rack")["id"] == "bike_rack"
    assert city_check.extra_layer(city, "public bench") is None
    assert city_check.extra_layer(city, "") is None


SEOUL = {"city": "Seoul", "items": {"street_tree": {
    "layer": "Seoul street trees", "found": 6, "covered": 23, "matched_ids": [658], "rejected_at_city_point": [660],
    "id_fields": ["자치구"], "pairs": [{"issue_id": 658, "city": {"자치구": "성동구"}}],
    "missed": [{"lat": 37.5, "lng": 127.0, "city": {"자치구": "성동구"}}]}}}


def test_seoul_keeps_counts_and_source_but_no_records():
    out = city_check.shareable(SEOUL)
    item = out["items"]["street_tree"]
    assert (item["found"], item["covered"], item["layer"]) == (6, 23, "Seoul street trees")
    assert item["pairs"] == [] and item["missed"] == [] and item["id_fields"] == [] and item["matched_ids"] == []
    assert item["rejected_at_city_point"] == []  # found by looking at a Seoul record's position
    assert out["counts_only"] is True and "KOGL Type 4" in out["source"]
    assert "성동구" not in str(out)


def test_shareable_leaves_the_comparison_it_was_given_whole():
    before = json.dumps(SEOUL, ensure_ascii=False)
    city_check.shareable(SEOUL)
    assert json.dumps(SEOUL, ensure_ascii=False) == before  # the file on disk and the offline run stay complete


def test_seattle_is_unchanged():
    check = {"city": "Seattle", "items": {"street_tree": {"found": 1, "covered": 2, "missed": [{"lat": 1}]}}}
    assert city_check.shareable(check) == check


def test_a_city_whose_list_goes_by_counts_is_not_listed_record_by_record():
    assert city_check.lists_records("Seattle") is True
    assert city_check.lists_records("Seoul") is False


@pytest.mark.parametrize("name", ["seattle", "seattle-ballard"])
def test_a_record_field_a_photo_can_settle_is_named_in_the_citys_file(name):
    items = city_check.load_city(name)["items"]
    compared = {k: v["compare"] for k, v in items.items() if "compare" in v}
    assert len(compared) == 2
    for compare in compared.values():
        assert {"field", "yes", "no", "ask", "says_yes", "says_no"} <= compare.keys()


@pytest.mark.parametrize("name", ["seattle", "seattle-ballard"])
def test_only_a_date_that_records_a_verification_is_marked_verified(name):
    items = city_check.load_city(name)["items"]
    assert sorted(k for k, v in items.items() if v.get("verified")) == ["curb_ramp_warning", "street_tree"]
