"""The raw source snapshot an Asset Check row carries (LedgerSource, docs/specs/2026-11-04-ledger-review-design.md)."""
import json

from eval import asset_check, ledger_review
from tests.eval import ledger_fixture as fx


def test_a_record_row_carries_the_raw_snapshot_of_its_city_record():
    check = fx.make_check()
    src = fx.row_of(check, "P1")["ledger_source"]
    props = fx.layers()["pole"][0]["properties"]
    assert src["version"] == 1
    assert src["layer"] == "poles.geojson"  # the safe basename, not the city's path or its display name
    assert (src["record_key"], src["record_id"]) == ("UNITID", "P1")
    assert src["properties_sha256"] == ledger_review.properties_sha256(props)
    assert src["editable_fields"]["IS_LIGHT"]["before"] == "YES"


def test_the_hash_is_over_the_canonical_sorted_json_of_all_original_properties():
    props = {"b": 1, "a": "café", "c": [1, 2]}
    canonical = json.dumps(props, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    import hashlib
    assert ledger_review.properties_sha256(props) == hashlib.sha256(canonical).hexdigest()
    assert ledger_review.properties_sha256({"c": [1, 2], "a": "café", "b": 1}) == ledger_review.properties_sha256(props)
    assert ledger_review.properties_sha256({**props, "extra": 0}) != ledger_review.properties_sha256(props)


def test_editable_fields_hold_the_raw_before_yes_and_no_not_the_cities_phrases():
    field = fx.row_of(fx.make_check(), "P1")["ledger_source"]["editable_fields"]["IS_LIGHT"]
    assert field == {"before": "YES", "yes": "YES", "no": "NO", "question": "Does the pole hold a lamp?",
                     "suggested": None}


def test_a_value_outside_the_yes_no_scheme_cannot_be_edited():
    src = fx.row_of(fx.make_check(), "P2")["ledger_source"]  # IS_LIGHT is "Maybe"
    assert src["editable_fields"] == {}


def test_an_item_without_a_compare_field_keeps_a_source_but_nothing_to_edit():
    for record in ("TRE-53943", "C1"):
        src = fx.row_of(fx.make_check(), record)["ledger_source"]
        assert src["editable_fields"] == {}


def test_the_trees_verification_date_is_never_editable_even_if_a_city_file_names_it():
    town = json.loads(json.dumps(fx.TOWN))
    town["items"]["tree"]["compare"] = {"field": "LAST_VERIFY_DATE", "yes": "1600000000000", "no": "1",
                                        "ask": "x"}
    src = fx.row_of(fx.make_check(town=town), "TRE-53943")["ledger_source"]
    assert src["editable_fields"] == {}


def test_ids_the_record_key_and_dates_are_never_editable():
    for field in ("UNITID", "CHECKED", "INSTALL_DATE", "VERIFICATION_DATE", "lat", "geometry"):
        town = json.loads(json.dumps(fx.TOWN))
        layer_map = fx.layers()
        layer_map["pole"][0]["properties"].setdefault(field, "YES")  # the id keeps its own value: it must still not edit
        value = layer_map["pole"][0]["properties"][field]
        town["items"]["pole"]["compare"] = {"field": field, "yes": value, "no": "NO", "ask": "x"}
        src = fx.row_of(fx.make_check(town=town, layer_map=layer_map), "P1")["ledger_source"]
        assert src["editable_fields"] == {}, field


def test_a_duplicated_city_id_leaves_nothing_editable():
    src = fx.row_of(fx.make_check(), "P3")["ledger_source"]
    assert src["editable_fields"] == {}


def test_an_unsafe_layer_name_gets_no_source():
    for bad in ("../poles.geojson", "assets/..", "poles.txt", "", "a b/.geojson"):
        town = json.loads(json.dumps(fx.TOWN))
        town["items"]["pole"]["layer"] = bad
        assert "ledger_source" not in fx.row_of(fx.make_check(town=town), "P1"), bad


def test_a_photo_that_contradicts_the_record_suggests_the_other_value_without_choosing_it():
    differs = {("pole", "P1"): {"city_says": "Holds a street light", "photo_shows": "Holds no street light"}}
    attributes = {("pole", "P1"): {"state": "different", "reason": ""}}
    check = fx.make_check(differs=differs, attributes=attributes)
    assert fx.row_of(check, "P1")["ledger_source"]["editable_fields"]["IS_LIGHT"]["suggested"] == "NO"
    other = fx.row_of(check, "P3")["ledger_source"]
    assert other["editable_fields"] == {}


def test_the_review_key_binds_scan_item_record_snapshot_photo_and_box():
    base = fx.row_of(fx.make_check(), "P1")["ledger_source"]["review_key"]
    assert base == fx.row_of(fx.make_check(), "P1")["ledger_source"]["review_key"]  # stable
    assert base != fx.row_of(fx.make_check(), "P2")["ledger_source"]["review_key"]
    edited = fx.layers()
    edited["pole"][0]["properties"]["NOTE"] = "changed since"
    assert base != fx.row_of(fx.make_check(layer_map=edited), "P1")["ledger_source"]["review_key"]
    moved_box = fx.data()["issues"]
    moved_box[0]["model_outputs"]["check"]["box"] = [9, 9, 9, 9]
    assert base != fx.row_of(fx.make_check(issues=moved_box), "P1")["ledger_source"]["review_key"]
    other_time = fx.data()["issues"]
    other_time[0]["evidence"]["captured_at"] = "2023-01-01T00:00:00Z"
    assert base != fx.row_of(fx.make_check(issues=other_time), "P1")["ledger_source"]["review_key"]
    other_photo = fx.data()["issues"]
    other_photo[0]["evidence"]["link"] = "https://www.mapillary.com/app/?pKey=1"
    assert base != fx.row_of(fx.make_check(issues=other_photo), "P1")["ledger_source"]["review_key"]


def test_a_record_the_photos_did_not_settle_has_a_source_and_no_photo_to_bind():
    src = fx.row_of(fx.make_check(), "C1")["ledger_source"]
    assert src["review_key"] and src["layer"] == "litter.geojson"


def test_an_object_that_is_not_on_the_citys_list_has_no_source():
    issues = fx.data()["issues"]
    out = asset_check.build({"scan": {"id": fx.SCAN}, "issues": issues}, {"items": {"pole": {
        "layer": "City poles", "id_fields": ["UNITID"], "pairs": [], "missed": []}}}, fx.TOWN,
        {"pole": fx.layers()["pole"]}, fx.STANDARD, {}, fx.TRUST)
    off_list = [r for r in out["rows"] if r["status"] == asset_check.NOT_LISTED]
    assert off_list and all("ledger_source" not in r for r in off_list)


def test_neighbouring_trees_are_warned_about_each_other_not_called_duplicates():
    check = fx.make_check()
    a = fx.row_of(check, "TRE-53943")["ledger_source"]["identity_warnings"]
    b = fx.row_of(check, "TRE-53948")["ledger_source"]["identity_warnings"]
    assert len(a) == 1 and "TRE-53948" in a[0] and len(b) == 1 and "TRE-53943" in b[0]
    for text in a + b:
        assert "duplicate" not in text.lower() and "same object" not in text.lower()
    assert fx.row_of(check, "TRE-90000")["ledger_source"]["identity_warnings"] == []  # 1 km away


def test_no_neighbour_warning_beyond_15_metres_or_10_seconds_or_for_another_item():
    far = fx.layers()
    far["tree"][1]["geometry"]["coordinates"][1] = 47.7 + 0.00015  # about 17 m
    assert fx.row_of(fx.make_check(layer_map=far), "TRE-53948")["ledger_source"]["identity_warnings"] == []
    late = fx.data()["issues"]
    late[4]["evidence"]["captured_at"] = "2022-08-30T10:00:11Z"  # 11 s after the other tree's photo
    check = fx.make_check(issues=late)
    assert fx.row_of(check, "TRE-53948")["ledger_source"]["identity_warnings"] == []
    mixed = fx.layers()
    mixed["pole"][0]["geometry"]["coordinates"] = [-122.3, 47.7]  # a pole on top of a tree is not a same-item pair
    assert fx.row_of(fx.make_check(layer_map=mixed), "TRE-53943")["ledger_source"]["identity_warnings"] \
        == fx.row_of(fx.make_check(), "TRE-53943")["ledger_source"]["identity_warnings"]


def test_the_boundary_is_inclusive_at_10_seconds_and_15_metres():
    edge = fx.data()["issues"]
    edge[4]["evidence"]["captured_at"] = "2022-08-30T10:00:10Z"
    assert len(fx.row_of(fx.make_check(issues=edge), "TRE-53948")["ledger_source"]["identity_warnings"]) == 1


def test_adding_the_source_changes_no_count_or_existing_column():
    check = fx.make_check()
    stripped = [{k: v for k, v in r.items() if k != "ledger_source"} for r in check["rows"]]
    assert all(set(r) >= {"item", "city_record", "status", "action"} for r in stripped)
    assert sum(check["actions"].values()) == len(check["rows"])
    json.dumps(check)  # the app and the saved file serialise it


def test_default_decisions_do_not_exist_in_the_source():
    src = fx.row_of(fx.make_check(), "P1")["ledger_source"]
    assert not {"decision", "identity", "reviewer", "reviewed_at", "approved"} & set(src)
