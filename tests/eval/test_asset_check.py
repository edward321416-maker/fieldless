import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from eval import asset_check, record_check
from pipeline import config, evidence

ID_FIELDS = ["UNITID"]
TOWN = {"city": "Testville", "items": {
    "pole": {"record_field": "UNITID", "checked_field": "CHECKED", "verified": True, "place_field": "PLACE",
             "condition_field": "COND"},
    "can": {"record_field": "UNITID"}}}
FAR = {"lat": 1.0, "lng": 2.0}


def layer(*records):
    return [{"properties": r, "geometry": {"type": "Point", "coordinates": [10.0 + n, 20.0 + n]}}
            for n, r in enumerate(records)]


LAYERS = {"pole": layer({"UNITID": "P1", "CHECKED": "2020-05-01", "PLACE": "1 Main St", "COND": "GOOD "},
                        {"UNITID": "P2", "CHECKED": 1727740800000},  # 2024-10, epoch milliseconds
                        {"UNITID": "P3", "CHECKED": ""}, {"UNITID": "P4"}, {"UNITID": "P5"}),
          "can": layer({"UNITID": "C1"})}
CHECK = {"items": {
    "pole": {"layer": "City poles", "id_fields": ID_FIELDS,
             "pairs": [{"issue_id": 1, "in_view": True, "city": {"UNITID": "P1"}},
                       {"issue_id": 2, "in_view": False, "city": {"UNITID": "P5"}}],
             "missed": [{"city": {"UNITID": "P2"}, **FAR}, {"city": {"UNITID": "P3"}, **FAR}, {"city": {"UNITID": "P4"}, **FAR}]},
    "can": {"layer": "City cans", "id_fields": ID_FIELDS, "pairs": [],
            "missed": [{"city": {"UNITID": "C1"}, **FAR}]}}}


def issue(id_, item, label="yes", status="auto"):
    return {"id": id_, "issue_type": item, "object_label": label, "review_status": status, "lat": 5.0, "lng": 6.0,
            "model_outputs": {"check": {"box": [1, 2, 3, 4]}},
            "evidence": {"image": f"scan1_issue{id_}_9.jpg", "captured_at": "2022-08-30T00:00:00Z", "creator": "a"}}


DATA = {"scan": {"id": 1}, "issues": [issue(1, "pole"), issue(2, "pole"), issue(3, "pole"), issue(4, "can", "unclear"),
                                      issue(5, "can", status="rejected")]}
STANDARD = SimpleNamespace(check=lambda item: SimpleNamespace(target={"pole": "city pole", "can": "public can"}[item]))
NEWER = {"how": asset_check.BY_PANORAMA, "box": [5, 6, 7, 8],
         "evidence": {"image": "scan1_look0_7.jpg", "captured_at": "2025-07-11T00:00:00Z"}}


def result(looks=None, labels=None):
    trust = {"controls": [{"photos": "x", "asked": 10, "both_yes": 1}], "looked_at": 2, "sent_back": 1}
    return asset_check.build(DATA, CHECK, TOWN, LAYERS, STANDARD, looks or {}, trust, labels)


def row(out, record):
    return next(r for r in out["rows"] if r["city_record"] == record)


def row_of(status, unsure="", newer="yes"):
    return {"status": status, "models_unsure": unsure, "photo_newer_than_city_date": newer}


def test_a_record_the_photos_did_not_settle_is_a_visit():
    assert asset_check.action_of(row_of(asset_check.NOT_DETERMINED), None, None, True) == asset_check.VISIT


def test_a_seen_record_nobody_is_sure_of_is_a_visit():
    assert asset_check.action_of(row_of(asset_check.SEEN, "yes"), None, None, True) == asset_check.VISIT
    assert asset_check.action_of(row_of(asset_check.SEEN), {"value": "unclear"}, None, True) == asset_check.VISIT
    assert asset_check.action_of(row_of(asset_check.SEEN), {"value": "no"}, None, True) == asset_check.VISIT


def test_a_seen_record_with_a_verified_date_older_than_the_photo_gets_a_new_date():
    assert asset_check.action_of(row_of(asset_check.SEEN), {"value": "yes"}, None, True) == asset_check.DATE
    assert asset_check.action_of(row_of(asset_check.SEEN), None, None, True) == asset_check.DATE


def test_a_date_that_is_not_a_verification_is_left_alone():
    assert asset_check.action_of(row_of(asset_check.SEEN), None, None, False) == asset_check.SAME
    assert asset_check.action_of(row_of(asset_check.SEEN, newer=""), None, None, True) == asset_check.SAME


def test_a_list_date_that_is_no_verification_is_not_shown_as_the_citys_date():
    out = result_without_dates()
    assert row(out, "P1")["city_date"] == "" and row(out, "P1")["photo_newer_than_city_date"] == ""


def test_a_record_the_photo_contradicts_is_to_correct_unless_nobody_is_sure_of_it():
    differs = {"city_says": "No", "photo_shows": "Yes"}
    assert asset_check.action_of(row_of(asset_check.SEEN), None, differs, True) == asset_check.FIX
    assert asset_check.action_of(row_of(asset_check.SEEN, "yes"), None, differs, True) == asset_check.VISIT


def test_an_object_off_the_lists():
    off = row_of(asset_check.NOT_LISTED, newer="")
    assert asset_check.action_of(off, {"value": "yes"}, None, False) == asset_check.COMPARE
    assert asset_check.action_of(off, None, None, False) == asset_check.COMPARE
    assert asset_check.action_of(off, {"value": "unclear"}, None, False) == asset_check.COMPARE
    assert asset_check.action_of(row_of(asset_check.NOT_LISTED, "yes", ""), None, None, False) == asset_check.COMPARE
    assert asset_check.action_of(off, {"value": "no"}, None, False) is None
    assert asset_check.action_of(off, {"value": "yes", "dup_of": 3}, None, False) is None


def test_every_row_has_one_action_and_the_counts_add_up():
    out = result({asset_check.record_key({"UNITID": "P2"}): NEWER})
    assert all(r["action"] for r in out["rows"])
    assert sum(out["actions"].values()) == len(out["rows"])
    records = [r for r in out["rows"] if r["city_record"]]
    assert len(records) == out["totals"]["in_view"]
    visits = sum(r["action"] == asset_check.VISIT for r in records)
    assert out["no_visit"] == len(records) - visits
    assert out["actions"][asset_check.VISIT] == visits  # a visit is a city record: one number on every surface
    assert sum(sum(i["actions"].values()) for i in out["items"]) == len(out["rows"])


def test_a_row_names_no_finding_by_its_local_id():
    # a finding's id changes when a survey goes into the live data, and this file goes up as it is
    assert not [r for r in result()["rows"] if "issue_id" in r]


def test_an_object_the_assistant_found_wrong_or_repeated_leaves_the_list_and_is_counted():
    labels = {"issues": {"3": {"item": "pole", "value": "yes", "dup_of": 1}, "4": {"item": "can", "value": "no"}}}
    out = result(labels=labels)
    assert out["left_out"] == {"repeats": 1, "wrong": 1}
    assert not [r for r in out["rows"] if r["status"] == asset_check.NOT_LISTED]
    assert out["totals"]["not_listed"] == 2  # what the photos held, whatever is left out of the list


def test_a_new_date_names_the_citys_field_and_never_fills_it():
    out = result({asset_check.record_key({"UNITID": "P2"}): NEWER})
    r = row(out, "P2")
    assert r["action"] == asset_check.DATE
    assert r["city_says"] == "CHECKED 2024-10"
    assert r["photo_shows"] == "Seen in a photo of 2025-07-11"
    assert r["city_date"] == "2024-10"


def test_a_record_to_correct_says_both_sides():
    differs = {("pole", "P2"): {"city_says": "No light", "photo_shows": "A light"}}
    trust = {"controls": [], "looked_at": 0, "sent_back": 0}
    looks = {asset_check.record_key({"UNITID": "P2"}): NEWER}
    out = asset_check.build(DATA, CHECK, TOWN, LAYERS, STANDARD, looks, trust, None, differs)
    r = row(out, "P2")
    assert (r["action"], r["city_says"], r["photo_shows"]) == (asset_check.FIX, "No light", "A light")


def test_a_list_with_nothing_in_view_still_counts_as_compared():
    check = {**CHECK, "items": {**CHECK["items"], "can": {**CHECK["items"]["can"], "pairs": [], "missed": []}}}
    data = {**DATA, "issues": [i for i in DATA["issues"] if i["issue_type"] != "can"]}
    out = asset_check.build(data, check, TOWN, LAYERS, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0})
    can = next(i for i in out["items"] if i["item"] == "can")
    assert (can["asset"], can["in_view"], can["not_listed"]) == ("Public can", 0, 0)


def test_every_record_in_view_has_exactly_one_row():
    out = result({asset_check.record_key({"UNITID": "P2"}): NEWER})
    records = [r["city_record"] for r in out["rows"] if r["city_record"]]
    assert sorted(records) == ["C1", "P1", "P2", "P3", "P4"]
    assert out["totals"] == {"in_view": 5, "seen": 2, "seen_by": {"Survey": 1, "Second look, panorama": 1},
                             "not_determined": 3, "not_listed": 2, "not_listed_unsure": 1, "seen_unsure": 0, "newer": 2}


def test_a_seen_record_the_models_were_unsure_of_is_counted_as_such():
    data = {**DATA, "issues": [issue(1, "pole", "unclear"), *DATA["issues"][1:]]}
    trust = {"controls": [], "looked_at": 0, "sent_back": 0}
    out = asset_check.build(data, CHECK, TOWN, LAYERS, STANDARD, {}, trust)
    assert out["totals"]["seen_unsure"] == 1 and row(out, "P1")["models_unsure"] == "yes"


def test_a_record_with_no_look_stays_not_determined_and_keeps_the_citys_position():
    r = row(result(), "P3")
    assert r["status"] == asset_check.NOT_DETERMINED and r["evidence"] is None and r["seen_in_photo_on"] == ""
    assert (r["lng"], r["lat"]) == (12.0, 22.0) and r["position_source"] == "City record"


def test_a_later_look_gives_the_row_its_photo_date_and_box():
    r = row(result({asset_check.record_key({"UNITID": "P2"}): NEWER}), "P2")
    assert r["status"] == asset_check.SEEN and r["how_seen"] == asset_check.BY_PANORAMA
    assert r["seen_in_photo_on"] == "2025-07-11" and r["box"] == [5, 6, 7, 8]
    assert r["city_date"] == "2024-10" and r["photo_newer_than_city_date"] == "yes"


def test_a_photo_older_than_the_citys_date_is_not_called_newer():
    looks = {asset_check.record_key({"UNITID": "P2"}): {**NEWER, "evidence": {"captured_at": "2022-08-30T00:00:00Z"}}}
    assert row(result(looks), "P2")["photo_newer_than_city_date"] == ""


def test_a_city_list_without_a_date_has_nothing_to_compare():
    r = row(result({asset_check.record_key({"UNITID": "C1"}): NEWER}), "C1")
    assert r["city_date"] == "" and r["photo_newer_than_city_date"] == ""


def test_an_object_not_on_the_list_carries_the_cameras_position_and_says_so():
    rows = [r for r in result()["rows"] if r["status"] == asset_check.NOT_LISTED]
    assert [r["asset"] for r in rows] == ["City pole", "Public can"]
    assert rows[0]["position_accuracy"] == "Unknown (within 50 ft)" and rows[0]["city_record"] == ""
    assert rows[1]["models_unsure"] == "yes"


def test_a_finding_paired_outside_the_view_is_on_neither_side_and_is_counted():
    out = result()
    assert out["trust"]["outside_view"] == 1
    assert "P5" not in [r["city_record"] for r in out["rows"]]
    assert "scan1_issue2_9.jpg" not in [(r["evidence"] or {}).get("image") for r in out["rows"]]


def test_a_rejected_finding_is_not_a_row():
    assert "scan1_issue5_9.jpg" not in [(r["evidence"] or {}).get("image") for r in result()["rows"]]


def test_each_items_counts_stand_on_their_own():
    pole, can = result()["items"]
    assert (pole["asset"], pole["city_list"], pole["in_view"], pole["seen"]) == ("City pole", "City poles", 4, 1)
    assert (can["in_view"], can["not_determined"], can["not_listed"]) == (1, 1, 1)


def test_the_city_fields_are_kept_in_the_citys_words():
    r = row(result(), "P1")
    assert r["city_location"] == "1 Main St" and r["city_condition"] == "GOOD" and r["city_list"] == "City poles"


def test_the_assistants_check_of_the_findings_is_carried_when_there_is_one():
    labels = {"issues": {"1": {"item": "pole", "value": "yes"}, "2": {"item": "pole", "value": "no"},
                         "3": {"item": "pole", "value": "unclear"}}}
    assert result(labels=labels)["trust"]["findings"] == {"n": 3, "yes": 1, "no": 1, "unclear": 1}
    assert "findings" not in result()["trust"]


@pytest.mark.parametrize("value, expected", [(1727740800000, "2024-10"), ("2020-05-01", "2020-05"), ("", ""), (None, ""), ("  ", "")])
def test_city_dates_become_months(value, expected):
    assert asset_check.month(value) == expected


def write_looks(run: Path, cache: Path) -> None:
    (run / "second_look_scan1.json").write_text(json.dumps({
        "summary": {"controls": 20, "controls_both_yes": 0},
        "spots": [{"kind": "record", "city": {"UNITID": "P2"}, "spark": {"answer": "yes", "visible": True, "box": [1, 2, 3, 4]},
                   "open": {"answer": "yes", "visible": True},
                   "view": {"image_id": "11", "captured_at": "2022-08-30T00:00:00Z", "dist_m": 14.2}},
                  {"kind": "record", "city": {"UNITID": "P3"}, "spark": {"answer": "yes"}, "open": {"answer": "no"},
                   "view": {"image_id": "12", "captured_at": "2022-08-30T00:00:00Z"}},
                  {"kind": "control", "spark": {"answer": "yes"}, "open": {"answer": "yes"},
                   "view": {"image_id": "13", "captured_at": "2022-08-30T00:00:00Z"}}]}), encoding="utf-8")
    view = {"image_id": "21", "captured_at": "2025-07-11T00:00:00Z", "creator": "kev", "yaw": 12.4}
    (run / "pano_look_scan1.json").write_text(json.dumps({
        "summary": {"controls": 10, "controls_seen": 1},
        "jobs": [{"kind": "record", "city": {"UNITID": "P4"}, "answers": [
                     {"view": view, "spark": {"answer": "yes", "box": [5, 6, 7, 8]}, "open": {"answer": "yes"}}]},
                 {"kind": "record", "city": {"UNITID": "C1"}, "answers": [
                     {"view": {**view, "image_id": "22"}, "spark": {"answer": "yes", "box": [5, 6, 7, 8]}, "open": {"answer": "yes"}}]}]}),
        encoding="utf-8")
    (run / "look_review_scan1.json").write_text(json.dumps({"looked_at": 3, "newer_control_note": "It was a real pole.", "sent_back": [
        {"item": "can", "record": "C1", "image_id": "22", "why": "the box sits on something else"}]}), encoding="utf-8")


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_check.config, "photo_cache", lambda: tmp_path / "photos")
    (tmp_path / "photos").mkdir()
    write_looks(tmp_path, tmp_path / "photos")
    return tmp_path


FRAMES = [{"image_id": "11", "creator": "bea"}]


def test_each_record_the_second_look_asked_about_has_its_photo(run):
    near = asset_check.read_nearby(run, 1, FRAMES)
    assert near[asset_check.record_key({"UNITID": "P2"})] == {
        "link": "https://www.mapillary.com/app/?pKey=11", "creator": "bea", "captured_at": "2022-08-30T00:00:00Z",
        "license": "CC BY-SA 4.0", "dist_m": 14.2}
    assert asset_check.record_key({"UNITID": "P3"}) in near
    assert len(near) == 2  # a control spot is no record


def test_no_second_look_is_no_photo(tmp_path):
    assert asset_check.read_nearby(tmp_path, 1, []) == {}


def nearby_result(**records):
    photo = {"link": "l", "creator": "bea", "captured_at": "2022-08-30T00:00:00Z", "license": "CC BY-SA 4.0", "dist_m": 14.2}
    near = {asset_check.record_key({"UNITID": r}): {**photo, "dist_m": d} for r, d in records.items()}
    return asset_check.build(DATA, CHECK, TOWN, LAYERS, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0},
                             nearby=near)


BOXED = {**DATA, "scan": {"id": 1, "bbox": [9.5, 19.5, 13.5, 23.5]}}  # holds P1..P4 and C1, not P5 (14, 24)


def boxed():
    return asset_check.build(BOXED, CHECK, TOWN, LAYERS, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0})


def test_every_city_record_in_the_box_is_counted_reached_or_not():
    out = boxed()
    assert out["totals"]["in_area"] == out["totals"]["in_view"] + len(out["unreached"])
    assert [i["in_area"] for i in out["items"]] == [4, 1]


def test_a_record_in_the_box_the_photos_had_not_in_view_is_unreached():
    out = boxed()
    assert out["unreached"] == []  # every record in this box is in view or paired: P1 in view, P2-P4 and C1 missed
    near = {**LAYERS, "pole": [*LAYERS["pole"], {"properties": {"UNITID": "P9"}, "geometry": {"type": "Point", "coordinates": [12.5, 22.5]}}]}
    out = asset_check.build(BOXED, CHECK, TOWN, near, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0})
    assert out["unreached"] == [["pole", 12.5, 22.5, ["P9"]]]
    assert out["unreached_fields"] == {"pole": ["UNITID"], "can": ["UNITID"]}
    assert out["totals"]["in_area"] == out["totals"]["in_view"] + 1


def test_a_record_paired_outside_the_view_and_outside_the_box_is_not_counted():
    assert all(r[0] != "pole" or (r[1], r[2]) != (14.0, 24.0) for r in boxed()["unreached"])


def test_an_unreached_record_carries_only_its_city_ids_as_text():
    near = {**LAYERS, "pole": [*LAYERS["pole"], {"properties": {"UNITID": 1384731.0, "PLACE": "1 Main St", "COND": "GOOD"},
                                                 "geometry": {"type": "Point", "coordinates": [12.5, 22.5]}},
                               {"properties": {"PLACE": "2 Main St"}, "geometry": {"type": "Point", "coordinates": [12.6, 22.6]}}]}
    out = asset_check.build(BOXED, CHECK, TOWN, near, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0})
    assert [entry[3] for entry in out["unreached"]] == [["1384731"], [""]]  # no place, no condition, a missing id is blank


def test_ids_are_written_as_the_app_writes_them():
    assert [asset_check.id_text(v) for v in (None, 7.0, 7.5, 12, "TRE-1")] == ["", "7", "7.5", "12", "TRE-1"]


def test_a_survey_without_a_box_says_nothing_of_its_reach():
    out = result()
    assert "unreached" not in out and "unreached_fields" not in out and "in_area" not in out["totals"]


def test_a_record_the_photos_did_not_settle_carries_a_photo_from_nearby():
    out = nearby_result(P2=14.2)
    assert row(out, "P2")["nearby_photo"]["dist_m"] == 14.2
    assert row(out, "P2")["status"] == asset_check.NOT_DETERMINED and row(out, "P2")["evidence"] is None


def test_a_photo_from_nearby_settles_nothing():
    plain, near = result(), nearby_result(P2=14.2, P3=3.0, P4=9.0, C1=2.0)
    assert (near["no_visit"], near["actions"], near["totals"]) == (plain["no_visit"], plain["actions"], plain["totals"])


def test_a_seen_record_has_its_own_photo_and_no_other():
    assert "nearby_photo" not in row(nearby_result(P1=4.0), "P1")


def test_the_list_counts_the_visits_that_have_a_photo():
    visits = sum(r["action"] == asset_check.VISIT for r in result()["rows"])
    assert result()["visits_with_photo"] == 0
    assert nearby_result(P2=14.2, P3=3.0)["visits_with_photo"] == 2
    assert nearby_result(P2=1.0, P3=1.0, P4=1.0, C1=1.0)["visits_with_photo"] == visits


def test_the_list_says_how_much_newer_the_photos_are_than_the_citys_dates():
    assert result()["date_gap_years"] == 2.2  # P1: the city's 2020-05, a photo of 2022-08
    assert "date_gap_years" not in result_without_dates()


def result_without_dates():
    town = {**TOWN, "items": {**TOWN["items"], "pole": {**TOWN["items"]["pole"], "verified": False}}}
    return asset_check.build(DATA, CHECK, town, LAYERS, STANDARD, {}, {"controls": [], "looked_at": 0, "sent_back": 0})


def test_only_records_both_models_saw_come_back_from_the_looks(run):
    looks, _ = asset_check.read_looks(run, 1, FRAMES)
    assert set(looks) == {asset_check.record_key({"UNITID": "P2"}), asset_check.record_key({"UNITID": "P4"})}


def test_a_look_sent_back_after_the_check_by_eye_does_not_count(run):
    looks, trust = asset_check.read_looks(run, 1, FRAMES)
    assert asset_check.record_key({"UNITID": "C1"}) not in looks
    assert trust["looked_at"] == 3 and trust["sent_back"] == 1


def test_the_looks_say_how_their_control_spots_went(run):
    _, trust = asset_check.read_looks(run, 1, FRAMES)
    assert trust["controls"] == [{"photos": "Survey-date photos", "asked": 20, "both_yes": 0},
                                 {"photos": "Panoramas", "asked": 10, "both_yes": 1, "note": "It was a real pole."}]


def test_a_look_names_its_photo_and_its_creator(run):
    looks, _ = asset_check.read_looks(run, 1, FRAMES)
    flat = looks[asset_check.record_key({"UNITID": "P2"})]
    pano = looks[asset_check.record_key({"UNITID": "P4"})]
    assert flat["view"]["creator"] == "bea" and flat["photo"] == run / "photos" / "11.jpg"
    assert pano["view"]["creator"] == "kev" and pano["photo"] == run / "pano_views" / "21_12.jpg"


def test_no_later_looks_at_all_is_an_empty_answer(tmp_path):
    assert asset_check.read_looks(tmp_path, 1, []) == ({}, {"controls": [], "looked_at": 0, "sent_back": 0})


class FakeSam:
    def __init__(self):
        self.asked = []

    def sam_image(self, concept, photo):
        self.asked.append((concept, Path(photo).name))
        return {"objects": []}


def blurrable(tmp_path):
    photo = tmp_path / "21.jpg"
    cv2.imwrite(str(photo), np.zeros((60, 80, 3), np.uint8))
    return {"a": {"how": asset_check.BY_LOOK, "photo": photo, "box": None,
                  "view": {"image_id": "21", "creator": "kev", "captured_at": "2025-07-11T00:00:00Z"}},
            "b": {"how": asset_check.BY_LOOK, "photo": photo, "box": None,
                  "view": {"image_id": "21", "creator": "kev", "captured_at": "2025-07-11T00:00:00Z"}}}


@pytest.fixture
def no_segments(monkeypatch):
    monkeypatch.setattr(evidence, "parse_away", lambda *a, **k: [])


def test_two_records_in_one_photo_share_one_blurred_photo(tmp_path, no_segments):
    looks, sam = blurrable(tmp_path), FakeSam()
    asset_check.blur_looks(looks, tmp_path, 1, api=sam)
    assert (tmp_path / "evidence" / "scan1_look0_21.jpg").is_file()
    assert len(sam.asked) == len(evidence.BLUR_CONCEPTS)
    assert looks["a"]["evidence"] == looks["b"]["evidence"]
    assert looks["a"]["evidence"]["link"] == evidence.mapillary_link("21")
    assert looks["a"]["evidence"]["creator"] == "kev" and looks["a"]["evidence"]["license"] == evidence.LICENSE


def test_a_photo_already_blurred_is_not_sent_again(tmp_path, no_segments):
    asset_check.blur_looks(blurrable(tmp_path), tmp_path, 1, api=FakeSam())
    again = FakeSam()
    looks = blurrable(tmp_path)
    asset_check.blur_looks(looks, tmp_path, 1, api=again)
    assert again.asked == [] and looks["a"]["evidence"]["image"] == "scan1_look0_21.jpg"


def test_a_failed_blur_leaves_no_photo(tmp_path):
    class Broken:
        def sam_image(self, concept, photo):
            raise RuntimeError("no answer")

    with pytest.raises(RuntimeError):
        asset_check.blur_looks(blurrable(tmp_path), tmp_path, 1, api=Broken())
    assert not (tmp_path / "evidence").exists()


def test_what_a_blur_found_is_kept_for_the_next_run(tmp_path, no_segments):
    asset_check.blur_looks(blurrable(tmp_path), tmp_path, 1, api=FakeSam())
    looks = blurrable(tmp_path)
    asset_check.blur_looks(looks, tmp_path, 1, api=FakeSam())
    assert looks["a"]["evidence"]["blurred_regions"] == 0


def test_a_citys_own_credit_for_its_records_comes_along():
    trust = {"controls": [], "looked_at": 0, "sent_back": 0}
    town = {**TOWN, "credit": "City records: Testville Open Data."}
    assert asset_check.build(DATA, CHECK, town, LAYERS, STANDARD, {}, trust)["credit"] == "City records: Testville Open Data."
    assert "credit" not in asset_check.build(DATA, CHECK, TOWN, LAYERS, STANDARD, {}, trust)


def test_an_object_nobody_is_sure_of_stays_to_compare_and_says_so():
    out = result(labels={"issues": {"3": {"item": "pole", "value": "unclear"}}})
    unsure = [r for r in out["rows"] if r["action"] == asset_check.COMPARE]
    assert len(unsure) == 2 and out["compare_unsure"] == 2
    assert {r["photo_shows"] for r in unsure} == {asset_check.NOT_SETTLED}


def test_an_object_everyone_is_sure_of_has_nothing_to_add():
    out = result(labels={"issues": {"3": {"item": "pole", "value": "yes"}}})
    sure = next(r for r in out["rows"] if r["asset"] == "City pole" and r["status"] == asset_check.NOT_LISTED)
    assert sure["photo_shows"] == "" and out["compare_unsure"] == 1  # the unsure can is still counted


def test_the_findings_count_only_the_rows_the_models_alone_settled():
    data = {**DATA, "issues": [*DATA["issues"], issue(6, "pole", status="needs_review")],
            "entrances": [{"id": 7, "review_status": "auto"}, {"id": 8, "review_status": "field_check"}]}
    labels = {"issues": {"1": {"item": "pole", "value": "yes"}, "5": {"item": "can", "value": "no"},
                         "6": {"item": "pole", "value": "unclear"}},
              "entrances": {"7": {"value": "yes"}, "8": {"value": "unclear"}}}  # 5 is rejected, 6 and 8 a person looked at
    trust = {"controls": [], "looked_at": 0, "sent_back": 0}
    out = asset_check.build(data, CHECK, TOWN, LAYERS, STANDARD, {}, trust, labels)
    assert out["trust"]["findings"] == {"n": 2, "yes": 2, "no": 0, "unclear": 0}


def test_a_judgement_of_rows_that_are_no_longer_the_models_own_says_nothing():
    labels = {"issues": {"5": {"item": "can", "value": "no"}}}  # issue 5 is rejected
    assert "findings" not in result(labels=labels)["trust"]


def test_what_the_assistant_said_of_a_row_still_decides_the_rows_action_whatever_its_status():
    data = {**DATA, "issues": [*DATA["issues"][:3], {**issue(4, "can", "yes", status="field_check")}]}
    labels = {"issues": {"4": {"item": "can", "value": "unclear"}}}
    trust = {"controls": [], "looked_at": 0, "sent_back": 0}
    out = asset_check.build(data, CHECK, TOWN, LAYERS, STANDARD, {}, trust, labels)
    assert next(r for r in out["rows"] if r["status"] == asset_check.NOT_LISTED and r["asset"] == "Public can")["photo_shows"] \
        == asset_check.NOT_SETTLED
    assert "findings" not in out["trust"]  # but it is not a finding the models made on their own


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_later_looks_cost_what_their_files_say(tmp_path):
    write_json(tmp_path / "second_look_scan1.json", {"cost_usd": 1.1})
    write_json(tmp_path / "second_look_pilot_scan1.json", {"summary": {"cost_usd": 0.25}})
    write_json(tmp_path / "pano_look_scan1.json", {"cost_usd": 0.75})
    write_json(tmp_path / "record_check_scan1.json", {"cost_usd": 0.5})
    write_json(tmp_path / "second_look_scan2.json", {"cost_usd": 9.0})  # another survey's
    (tmp_path / "evidence").mkdir()
    for name in ("scan1_look0_7.jpg", "scan1_look1_8.jpg", "scan2_look0_9.jpg", "scan1_issue1_9.jpg"):
        (tmp_path / "evidence" / name).write_bytes(b"jpg")  # two photos of scan 1's looks; the others are not
    costs = asset_check.second_look_costs(tmp_path, 1)
    assert costs["second_look"] == pytest.approx(1.1) and costs["sample"] == pytest.approx(0.25)
    assert costs["panoramas"] == pytest.approx(0.75) and costs["record_check"] == pytest.approx(0.5)
    assert costs["blur"] == pytest.approx(2 * 2 * config.SAM_PER_IMAGE)  # two SAM requests a photo
    assert costs["total"] == pytest.approx(1.1 + 0.25 + 0.75 + 0.5 + costs["blur"])


def test_a_survey_with_no_later_looks_has_nothing_more_to_pay_for(tmp_path):
    assert asset_check.second_look_costs(tmp_path, 1)["total"] == 0


def test_the_cost_of_the_looks_goes_into_the_file_for_the_page_and_the_app(tmp_path):
    trust = {"controls": [], "looked_at": 0, "sent_back": 0, **asset_check.cost_trust({"second_look": 1.0, "total": 1.5})}
    assert trust["second_looks_usd"] == 1.5 and trust["second_looks"]["second_look"] == 1.0
    assert asset_check.cost_trust({"total": 0}) == {}


# --- an object seen is not an attribute checked ---------------------------------------------------------------------

LIT = {"field": "LIT", "yes": "Y", "no": "N", "ask": "Is it lit?", "says_yes": "Lit", "says_no": "Not lit"}
TOWN_LIT = {**TOWN, "items": {**TOWN["items"], "pole": {**TOWN["items"]["pole"], "compare": LIT}}}
LAYERS_LIT = {**LAYERS, "pole": layer({"UNITID": "P1", "CHECKED": "2020-05-01", "PLACE": "1 Main St", "COND": "GOOD ", "LIT": "Y"},
                                      {"UNITID": "P2", "CHECKED": 1727740800000, "LIT": "N"},
                                      {"UNITID": "P3", "CHECKED": "", "LIT": "Y"}, {"UNITID": "P4"}, {"UNITID": "P5"})}
BOTH_SEEN = {asset_check.record_key({"UNITID": "P2"}): NEWER}
TRUST = {"controls": [], "looked_at": 0, "sent_back": 0}


def build_lit(attributes=None, town=TOWN_LIT, layers=LAYERS_LIT, differs=None, looks=BOTH_SEEN):
    return asset_check.build(DATA, CHECK, town, layers, STANDARD, looks, TRUST, None, differs, None, attributes)


def test_a_seen_record_whose_attribute_was_not_checked_is_not_a_record_that_needs_no_visit():
    out = build_lit()  # no record check was run: nothing was asked about the pole's light
    for record in ("P1", "P2"):
        r = row(out, record)
        assert r["status"] == asset_check.SEEN and r["action"] == asset_check.VISIT
        assert (r["attribute_field"], r["attribute_state"]) == ("LIT", "not_checked")
    assert out["no_visit"] == 0 and out["desk_review_candidates"] == 0


def test_the_record_that_is_there_keeps_its_presence_and_dates_while_its_attribute_is_open():
    out = build_lit({("pole", "P2"): {"state": "unsettled", "reason": "unclear"}})
    r = row(out, "P2")
    assert r["action"] == asset_check.VISIT and r["status"] == asset_check.SEEN and r["how_seen"] == asset_check.BY_PANORAMA
    assert (r["city_date"], r["seen_in_photo_on"], r["photo_newer_than_city_date"]) == ("2024-10", "2025-07-11", "yes")
    assert r["attribute_state"] == "unsettled" and r["attribute_field"] == "LIT"
    assert "Not lit" in r["attribute_note"]  # the city's side of what is open
    assert "newer photo or a field check" in r["visit_reason"] and "Not lit" in r["visit_reason"]
    assert r["city_says"] == "" and r["photo_shows"] == ""  # nothing is claimed about either side


def test_a_record_whose_attribute_the_photos_agree_with_can_still_get_a_date_or_stay_as_it_is():
    out = build_lit({("pole", "P1"): {"state": "agree", "reason": ""}, ("pole", "P2"): {"state": "agree", "reason": ""}})
    assert row(out, "P1")["action"] == asset_check.DATE and row(out, "P2")["action"] == asset_check.DATE
    assert row(out, "P1")["attribute_state"] == "agree" and not row(out, "P1").get("visit_reason")
    assert out["no_visit"] == 2 == out["desk_review_candidates"]


def test_an_attribute_the_models_both_found_different_is_to_correct_and_says_so():
    differs = {("pole", "P2"): {"city_says": "Not lit", "photo_shows": "Lit"}}
    out = build_lit({("pole", "P2"): {"state": "different", "reason": ""}}, differs=differs)
    r = row(out, "P2")
    assert (r["action"], r["attribute_state"], r["city_says"], r["photo_shows"]) == (asset_check.FIX, "different", "Not lit", "Lit")


def test_a_difference_without_a_saved_state_still_counts_as_different():
    differs = {("pole", "P2"): {"city_says": "Not lit", "photo_shows": "Lit"}}
    r = row(build_lit({("pole", "P1"): {"state": "agree", "reason": ""}}, differs=differs), "P2")
    assert (r["action"], r["attribute_state"]) == (asset_check.FIX, "different")


def test_the_two_reasons_a_record_was_never_asked_about_are_told_apart():
    blank = {**LAYERS_LIT, "pole": layer({"UNITID": "P1", "CHECKED": "2020-05-01", "LIT": ""},
                                         {"UNITID": "P2", "CHECKED": 1727740800000, "LIT": "N"},
                                         {"UNITID": "P3"}, {"UNITID": "P4"}, {"UNITID": "P5"})}
    out = build_lit(layers=blank)
    assert row(out, "P1")["attribute_state"] == "not_checked" and "no value" in row(out, "P1")["attribute_note"]
    assert row(out, "P2")["attribute_state"] == "not_checked" and "was not asked" in row(out, "P2")["attribute_note"]


def test_an_item_with_no_attribute_to_check_is_judged_as_it_was():
    out = result()  # TOWN has no compare for any item
    assert all("attribute_state" not in r and "attribute_field" not in r for r in out["rows"])
    assert "attribute_checks" not in out
    assert row(result(BOTH_SEEN), "P2")["action"] == asset_check.DATE


def test_only_a_visit_row_says_why_it_needs_a_newer_photo_or_a_field_check():
    out = result()
    for r in out["rows"]:
        assert bool(r.get("visit_reason")) == (r["action"] == asset_check.VISIT)
    assert "No photo settled" in row(out, "P3")["visit_reason"]


def test_what_is_still_open_is_counted_whole_and_per_item():
    out = build_lit({("pole", "P1"): {"state": "agree", "reason": ""}, ("pole", "P2"): {"state": "unsettled", "reason": "unclear"}})
    assert out["attribute_checks"] == {"required": 2, "agree": 1, "different": 0, "unsettled": 1, "not_checked": 0,
                                       "open_by": {"unclear": 1}}
    assert out["visits_attribute_unresolved"] == 1
    pole = next(i for i in out["items"] if i["item"] == "pole")
    assert pole["attribute_checks"]["required"] == 2 and "attribute_checks" not in next(i for i in out["items"] if i["item"] == "can")
    assert out["no_visit"] == out["desk_review_candidates"] == 1  # the key stays; the new key says what it counts


def test_the_visit_count_still_adds_up_with_the_photo_covered_records():
    out = build_lit({("pole", "P1"): {"state": "unsettled", "reason": "unclear"}})
    records = [r for r in out["rows"] if r["city_record"]]
    assert len(records) == out["totals"]["in_view"]
    assert out["actions"][asset_check.VISIT] == sum(r["action"] == asset_check.VISIT for r in records)
    assert out["no_visit"] == len(records) - out["actions"][asset_check.VISIT]


def test_the_action_of_a_seen_record_waits_for_its_attribute_but_not_for_its_presence():
    seen, yes = row_of(asset_check.SEEN), {"value": "yes"}
    assert asset_check.action_of(seen, yes, None, True, {"state": "agree"}) == asset_check.DATE
    assert asset_check.action_of(seen, yes, None, False, {"state": "agree"}) == asset_check.SAME
    for state in ("unsettled", "not_checked"):
        assert asset_check.action_of(seen, yes, None, True, {"state": state}) == asset_check.VISIT
        assert asset_check.action_of(seen, yes, None, False, {"state": state}) == asset_check.VISIT
    assert asset_check.action_of(seen, yes, {"city_says": "a", "photo_shows": "b"}, True, {"state": "different"}) == asset_check.FIX
    off = row_of(asset_check.NOT_LISTED, newer="")
    assert asset_check.action_of(off, yes, None, False, {"state": "unsettled"}) == asset_check.COMPARE


def test_offline_never_blurs_and_says_which_photos_still_need_it(tmp_path):
    with pytest.raises(SystemExit, match="not blurred yet"):
        asset_check.blur_looks(blurrable(tmp_path), tmp_path, 1, offline=True)  # no api, no key: nothing may be asked
    assert not (tmp_path / "evidence").exists() and not (tmp_path / "look_evidence_scan1.json").exists()


def test_offline_reads_the_photos_already_blurred_and_asks_for_nothing(tmp_path, no_segments):
    asset_check.blur_looks(blurrable(tmp_path), tmp_path, 1, api=FakeSam())
    looks = blurrable(tmp_path)
    assert asset_check.blur_looks(looks, tmp_path, 1, offline=True) == 0.0
    assert looks["a"]["evidence"]["image"] == "scan1_look0_21.jpg"


def test_a_visit_waiting_on_its_attribute_says_what_it_would_be_if_the_attribute_agreed():
    out = build_lit()
    assert row(out, "P1")["action_if_attribute_agrees"] == asset_check.DATE
    assert row(out, "P2")["action_if_attribute_agrees"] == asset_check.DATE
    unsettled = build_lit({("pole", "P2"): {"state": "unsettled", "reason": "unclear"}})
    assert row(unsettled, "P2")["action_if_attribute_agrees"] == asset_check.DATE
    agreed = build_lit({("pole", "P2"): {"state": "agree", "reason": ""}})
    assert "action_if_attribute_agrees" not in row(agreed, "P2")  # nothing is waiting
    assert "action_if_attribute_agrees" not in row(build_lit(), "P3")  # the photos did not settle the record at all


def test_the_first_asset_check_still_leaves_the_seen_records_for_the_record_check_to_ask_about():
    # the order of a run is Asset Check, Record Check, Asset Check: the first must not hide the rows the second asks
    first = build_lit()
    layers = {"pole": {r["UNITID"]: r for r in (f["properties"] for f in LAYERS_LIT["pole"])}}
    jobs = record_check.jobs_for(first, TOWN_LIT, layers)
    assert [(j["record"], j["city_value"]) for j in jobs] == [("P1", "yes"), ("P2", "no")]
