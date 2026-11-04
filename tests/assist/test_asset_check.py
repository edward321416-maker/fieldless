import csv
import io
import json

from assist import asset_check

EV = {"image": "scan1_issue1_9.jpg", "creator": "bea", "captured_at": "2022-08-30T00:00:00Z",
      "link": "https://www.mapillary.com/app/?pKey=9", "license": "CC BY-SA 4.0", "blurred_regions": 2}


def row(**over):
    return {"item": "pole", "asset": "City pole", "city_list": "City poles", "city_record": "P1", "city_location": "1 Main St",
            "status": "Seen in photo", "how_seen": "Survey", "models_unsure": "", "city_date": "2020-05",
            "seen_in_photo_on": "2022-08-30", "photo_newer_than_city_date": "yes", "city_condition": "GOOD", "lat": 1.5,
            "lng": 2.5, "position_source": "City record", "position_accuracy": "City record", "box": [100, 200, 300, 400],
            "evidence": EV, "action": "Update the date", "city_says": "CHECKED 2020-05",
            "photo_shows": "Seen in a photo of 2022-08-30", **over}


ROWS = [row(), row(asset="Public can", how_seen="Second look, panorama", city_record="C1", city_date="",
               photo_newer_than_city_date="", evidence={**EV, "image": "scan1_look0_7.jpg"}, action="No change",
               city_says="", photo_shows=""),
        row(asset="Street tree", status="Not determined", how_seen="", city_record="T1", seen_in_photo_on="",
            photo_newer_than_city_date="", evidence=None, box=None, action="Visit on site", city_says="", photo_shows=""),
        row(asset="Street tree", status="Not on the city list", how_seen="", city_list="", city_record="", models_unsure="yes",
            city_date="", photo_newer_than_city_date="", position_accuracy="Unknown (within 50 ft)",
            evidence={**EV, "image": "scan1_issue3_9.jpg"}, action="Compare with the list", city_says="",
            photo_shows="Not settled what this is")]
ACTIONS = {"Update the date": 1, "No change": 1, "Visit on site": 1, "Compare with the list": 1}
TOTALS = {"in_view": 3, "seen": 2, "seen_by": {"Survey": 1}, "not_determined": 1, "not_listed": 1, "not_listed_unsure": 1,
          "seen_unsure": 1, "newer": 1}
CHECK = {"scan_id": 1, "city": "Testville", "rows": ROWS, "totals": TOTALS, "actions": ACTIONS, "no_visit": 2,
         "compare_unsure": 1, "left_out": {"repeats": 6, "wrong": 1},
         "items": [{"item": "pole", "asset": "City pole", "city_list": "City poles", **TOTALS, "actions": ACTIONS}],
         "trust": {"photo_months": ["2022-08", "2025-07"], "controls": [{"photos": "Panoramas", "asked": 10, "both_yes": 1, "note": "It was a real pole."}],
                   "looked_at": 25, "sent_back": 3, "outside_view": 6, "findings": {"n": 88, "yes": 73, "no": 4, "unclear": 11}}}


def lines(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_the_table_has_one_line_per_row_with_the_photos_attribution():
    table = lines(asset_check.table_csv(CHECK))
    assert len(table) == 4 and list(table[0]) == [k for k, _ in asset_check.COLUMNS]
    assert table[0]["photo_url"] == EV["link"] and table[0]["photo_by"] == "bea" and table[0]["license"] == EV["license"]
    assert table[0]["evidence_file"] == "evidence/scan1_issue1_9.jpg"


def test_a_row_without_a_photo_leaves_the_photo_cells_empty():
    third = lines(asset_check.table_csv(CHECK))[2]
    assert third["status"] == "Not determined" and third["photo_url"] == "" and third["evidence_file"] == ""


NEARBY = {"link": "https://www.mapillary.com/app/?pKey=5", "creator": "kev", "captured_at": "2026-07-01T00:00:00Z",
          "license": "CC BY-SA 4.0", "dist_m": 14.2}
VISIT_WITH_PHOTO = {**CHECK, "rows": [ROWS[0], {**ROWS[2], "nearby_photo": NEARBY}, *ROWS[1:2], *ROWS[3:]],
                    "visits_with_photo": 1, "date_gap_years": 8.2}


def test_a_visit_gives_the_nearby_photo_in_its_own_cells():
    near = next(r for r in lines(asset_check.table_csv(VISIT_WITH_PHOTO)) if r["city_record"] == "T1")
    assert near["nearby_photo_link"] == NEARBY["link"] and near["nearby_photo_m"] == "14.2"
    assert near["nearby_photo_by"] == "kev" and near["nearby_photo_license"] == "CC BY-SA 4.0"
    assert near["photo_url"] == "" and near["evidence_file"] == ""  # it settled nothing, so it is no evidence
    assert near["status"] == "Not determined"


def test_a_row_without_a_nearby_photo_leaves_those_cells_empty():
    first = lines(asset_check.table_csv(VISIT_WITH_PHOTO))[0]
    assert (first["nearby_photo_link"], first["nearby_photo_m"], first["nearby_photo_by"]) == ("", "", "")


def test_the_new_cells_come_last_so_the_earlier_columns_do_not_move():
    names = [k for k, _ in asset_check.COLUMNS]
    assert names.index("evidence_file") == len(names) - 17
    assert names[-16:-12] == ["nearby_photo_link", "nearby_photo_m", "nearby_photo_by", "nearby_photo_license"]
    assert names[-12:-7] == ["attribute_field", "attribute_state", "attribute_note", "action_if_attribute_agrees",
                             "visit_reason"]
    assert names[-7:] == ["identity_state", "identity_reason", "competing_record_ids", "evidence_quality",
                          "evidence_quality_reason", "action_before_quality_gate", "action_label"]


def test_the_app_gets_the_visits_with_a_photo_and_the_date_gap_with_the_counts():
    summary = asset_check.summary(VISIT_WITH_PHOTO)
    assert (summary["visits_with_photo"], summary["date_gap_years"]) == (1, 8.2)
    assert "rows" not in summary


def test_a_list_made_before_these_counts_still_loads():
    summary = asset_check.summary(CHECK)
    assert "visits_with_photo" not in summary and "date_gap_years" not in summary


def test_the_page_asks_for_no_icon_and_writes_large_counts_as_the_app_does():
    page = asset_check.document({**CHECK, "totals": {**TOTALS, "in_area": 3292}, "unreached": []})
    assert '<link rel="icon" href="data:,">' in page
    assert "Of 3,292 city records" in page and "The other 3,289 had no survey photo in view" in page


def test_the_page_says_how_many_records_in_the_box_the_photos_reached():
    reach = {**CHECK, "totals": {**TOTALS, "in_area": 40}, "unreached": [["pole", 1.0, 2.0]] * 37}
    page = asset_check.document(reach)
    assert "Of 40 city records in the survey&#x27;s box or its photos&#x27; view, the photos reached 3: 2 are candidates for desk review " \
        "and 1 needs a newer photo or a field check." in page
    assert "The other 37 had no survey photo in view. This list says nothing about them." in page


def test_the_app_gets_the_unreached_records_with_the_rows_not_with_the_counts():
    reach = {**CHECK, "totals": {**TOTALS, "in_area": 40}, "unreached": [["pole", 1.0, 2.0, ["P9"]]],
             "unreached_fields": {"pole": ["UNITID"]}}
    assert "unreached" not in asset_check.summary(reach) and "unreached_fields" not in asset_check.summary(reach)
    assert asset_check.for_app(reach)["unreached"] == [["pole", 1.0, 2.0, ["P9"]]]
    assert asset_check.for_app(reach)["unreached_fields"] == {"pole": ["UNITID"]}
    assert (asset_check.for_app(CHECK)["unreached"], asset_check.for_app(CHECK)["unreached_fields"]) == ([], {})


def test_the_page_says_what_the_nearby_photo_is_and_credits_it():
    page = asset_check.document(VISIT_WITH_PHOTO)
    assert "taken 14 m from the record&#x27;s position" in page or "taken 14 m from the record's position" in page
    assert "It may not show the record." in page
    assert NEARBY["link"] in page and "Photo by kev" in page


def test_text_that_starts_like_a_formula_stays_text():
    check = {**CHECK, "rows": [row(city_location="=HYPERLINK(1)")]}
    assert lines(asset_check.table_csv(check))[0]["city_location"] == "'=HYPERLINK(1)"


def test_the_geojson_puts_each_row_at_its_position():
    features = json.loads(asset_check.table_geojson(CHECK))["features"]
    assert len(features) == 4 and features[0]["geometry"] == {"type": "Point", "coordinates": [2.5, 1.5]}
    assert features[3]["properties"]["position_accuracy"] == "Unknown (within 50 ft)"


def test_the_app_gets_the_counts_apart_from_the_rows():
    assert set(asset_check.summary(CHECK)) == {"city", "items", "totals", "trust", "actions", "no_visit", "left_out",
                                               "compare_unsure"}


def test_the_app_gets_each_photo_at_the_servers_address():
    rows = asset_check.for_app(CHECK)["rows"]
    assert rows[0]["evidence"]["url"] == "/api/evidence/scan1_issue1_9.jpg" and rows[2]["evidence"] is None


def test_the_photos_are_named_once_each():
    assert asset_check.photos(CHECK) == {"scan1_issue1_9.jpg", "scan1_look0_7.jpg", "scan1_issue3_9.jpg"}


def test_the_page_opens_with_the_visits_and_is_labelled_as_not_the_citys():
    page = asset_check.document(CHECK, area="Capitol Hill", km=0.94, photo_count=181)
    assert "<title>Capitol Hill Record Update List</title>" in page
    assert "It is not a Testville city document, and the city has not reviewed it." in page
    assert "Of 3 city records in view of the photos, 2 are candidates for desk review and 1 needs a newer photo or a field check." in page
    assert "0.94 km of walked streets" in page


def test_the_table_starts_with_what_to_do():
    table = lines(asset_check.table_csv(CHECK))
    assert list(table[0])[0] == "action" and table[0]["action"] == "Update the date"
    assert table[0]["city_says"] == "CHECKED 2020-05" and table[0]["photo_shows"] == "Seen in a photo of 2022-08-30"
    assert table[0]["city_date"] == "2020-05" and table[0]["seen_in_photo_on"] == "2022-08-30"  # each date in its own cell


def test_each_action_with_rows_has_its_line_and_none_without():
    page = asset_check.document(CHECK)
    assert 'id="next"' in page and "Review newer photo evidence" in page and "Visit on site" in page
    assert "Compare with the list" in page and "Correct the record" not in page


def test_a_visit_is_a_city_record_and_an_unsure_object_is_counted_under_compare():
    page = asset_check.document(CHECK)
    assert "1 city record the photos did not settle" in page
    assert "The models or the AI assistant could not settle 1 of these." in page


def test_a_record_to_correct_shows_both_sides_first():
    rows = [row(asset="Curb ramp", action="Correct the record", city_says="No light", photo_shows="A light"), *ROWS]
    check = {**CHECK, "rows": rows, "actions": {**ACTIONS, "Correct the record": 1}}
    page = asset_check.document(check)
    assert page.index("Correct the record") < page.index("Review newer photo evidence")
    assert asset_check.examples(check)[0]["action"] == "Correct the record"
    assert "No light" in page and "A light" in page


def test_the_page_says_what_was_left_out_and_how_records_were_compared():
    trust = {**CHECK["trust"], "record_check": {"asked": 20, "agree": 15, "differ": 1, "unsettled": 4, "sent_back": 1}}
    page = asset_check.document({**CHECK, "trust": trust})
    assert "6 repeats and 1 wrong object" in page
    assert "Asked about 20 seen records" in page and "agreed with the city on 15" in page
    assert "disagreed with it on 1" in page and "sent 1 back" in page


def test_a_row_is_a_suggestion():
    assert "It changes nothing in the city's records" in asset_check.document(CHECK)


def test_every_photo_on_the_page_has_its_credit():
    page = asset_check.document(CHECK)
    shown = page.count("<figure>")
    assert shown == 2 and page.count("View on Mapillary") == shown and page.count("Photo by bea") == shown
    assert page.count(">CC BY-SA 4.0</a>. Edited: ") == shown
    assert page.count('href="https://creativecommons.org/licenses/by-sa/4.0/"') >= shown


def test_a_view_cut_from_a_panorama_says_so():
    page = asset_check.document(CHECK)
    assert "Edited: cut from a 360-degree photo, people and plates blurred, resized." in page
    assert "Edited: people and plates blurred, resized." in page  # the survey's own photo was not cut


def test_a_photo_with_nothing_to_blur_does_not_say_it_was_blurred():
    check = {**CHECK, "rows": [row(evidence={**EV, "blurred_regions": 0})]}
    assert "Edited: resized." in asset_check.document(check) and "plates blurred" not in asset_check.document(check)


def test_photos_come_from_where_the_page_is_told():
    assert 'src="../../../evidence/scan1_issue1_9.jpg"' in asset_check.document(CHECK, photo_base="../../../evidence/")


def test_the_examples_start_with_the_most_common_kind_of_row():
    many = {**CHECK, "rows": [*ROWS, row(city_record="P9", action="Correct the record", city_says="x", photo_shows="y")],
            "actions": {**ACTIONS, "Correct the record": 1, "No change": 5}}
    assert [r["action"] for r in asset_check.examples(many)][0] == "No change"


def test_the_objects_off_the_lists_are_counted_as_the_table_counts_them():
    page = asset_check.document({**CHECK, "totals": {**TOTALS, "not_listed": 9}})
    assert "The 1 objects not on the lists" in page or "The 1 object" in page or " 1 objects not on the lists" in page
    assert "9 objects not on the lists" not in page


def test_the_examples_are_of_different_assets():
    shown = asset_check.examples(CHECK)
    assert len({r["asset"] for r in shown}) == len(shown) and len(shown) >= 2


def test_an_example_is_never_one_the_models_were_unsure_of():
    assert all(r["models_unsure"] != "yes" for r in asset_check.examples(CHECK))


def test_the_page_says_how_the_seen_records_came_to_be_seen():
    totals = {**TOTALS, "seen_by": {"Survey": 45, "Second look": 9, "Second look, panorama": 16}}
    assert asset_check.seen_how(totals) == "45 by the survey, 9 on a second look, 16 on a second look in panoramas"
    assert asset_check.seen_how({**TOTALS, "seen_by": {"Survey": 2}}) == "2 by the survey"


def test_the_page_says_how_far_to_trust_it():
    page = asset_check.document(CHECK)
    assert "An AI assistant checked 88 findings against their photos: 73 right, 4 wrong and 11 could not be told." in page
    assert "10 spots with no city record were asked the same way. Two models agreed on an object at 1 of them. It was a real pole." in page
    assert "looked at the 25 records two models had settled and sent 3 back to Not determined" in page
    assert "6 findings paired with a city record outside the photo's view are counted on neither side." in page
    assert "August 2022 and July 2025" in page


def test_the_page_never_gets_ahead_of_the_citys_decision():
    page = asset_check.document(CHECK).lower()
    for word in ("add to db", "add_to_city_db", "verified", "confirmed", "missing record", "patch", "approved", "passed"):
        assert word not in page


def test_the_page_says_how_rows_were_paired_and_where_the_models_were_unsure():
    page = asset_check.document(CHECK)
    assert "by the photo's position and viewing direction, not by a model comparing the two" in page
    assert "The models were unsure of 1 of the 2 seen records." in page


def test_a_check_without_later_looks_still_makes_a_page():
    check = {**CHECK, "trust": {"photo_months": ["2022-08"], "controls": [], "looked_at": 0, "sent_back": 0, "outside_view": 0}}
    page = asset_check.document(check)
    assert "Control spots" not in page and "Second looks." not in page and "Left out." not in page


def test_write_makes_the_three_files(tmp_path):
    asset_check.write(CHECK, tmp_path, area="Capitol Hill")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["update_list.csv", "update_list.geojson", "update_list.html"]
    assert 'src="evidence/scan1_issue1_9.jpg"' in (tmp_path / "update_list.html").read_text(encoding="utf-8")


def test_a_citys_own_credit_stands_in_for_the_plain_one():
    credit = "City records: Testville Open Data, downloaded Nov 1, 2026."
    check = {**CHECK, "credit": credit}
    assert credit in asset_check.document(check) and "Testville open data." not in asset_check.document(check)
    assert asset_check.summary(check)["credit"] == credit
    assert "City data: Testville open data." in asset_check.document(CHECK)


def test_the_page_says_what_the_second_looks_cost_beside_the_surveys_own_cost():
    check = {**CHECK, "trust": {**CHECK["trust"], "second_looks_usd": 2.3028}}
    page = asset_check.document(check, survey_cost=3.3274)
    assert "<strong>Cost.</strong> The survey cost $3.33. The second looks behind this list, run separately, cost $2.30 more." in page
    assert "The second looks behind this list, run separately, cost $2.30." in asset_check.document(check)
    assert "behind this list" not in asset_check.document(CHECK, survey_cost=3.3274)


def test_the_survey_cost_reaches_the_page_in_the_zip(tmp_path):
    check = {**CHECK, "trust": {**CHECK["trust"], "second_looks_usd": 2.3028}}
    asset_check.write(check, tmp_path, survey_cost=3.3274)
    assert "The survey cost $3.33." in (tmp_path / "update_list.html").read_text(encoding="utf-8")


# --- an object seen is not an attribute checked: what the table, the app and the page say about it ----------------

OPEN = row(asset="Utility pole", city_record="P7", action="Visit on site", city_says="", photo_shows="",
           attribute_field="IS_STREETLIGHT", attribute_state="unsettled",
           attribute_note="Holds a street light: the top of the pole is cut off in the photo",
           action_if_attribute_agrees="Update the date",
           visit_reason="The record is there in a photo, but whether it Holds a street light is not settled. "
                        "A newer photo or a field check is needed.")
WITH_ATTRIBUTES = {**CHECK, "rows": [*ROWS, OPEN], "actions": {**ACTIONS, "Visit on site": 2}, "no_visit": 2,
                   "desk_review_candidates": 2, "visits_attribute_unresolved": 1,
                   "attribute_checks": {"required": 3, "agree": 2, "different": 0, "unsettled": 1, "not_checked": 0,
                                        "open_by": {"unclear": 1}}}


def test_the_table_and_the_geojson_say_which_attribute_is_open_and_why_the_row_is_a_visit():
    csv_row = lines(asset_check.table_csv(WITH_ATTRIBUTES))[-1]
    assert csv_row["action"] == "Visit on site" and csv_row["status"] == "Seen in photo"
    assert (csv_row["attribute_field"], csv_row["attribute_state"]) == ("IS_STREETLIGHT", "unsettled")
    assert "cut off" in csv_row["attribute_note"] and "newer photo or a field check" in csv_row["visit_reason"]
    props = json.loads(asset_check.table_geojson(WITH_ATTRIBUTES))["features"][-1]["properties"]
    assert props["attribute_state"] == "unsettled" and props["attribute_field"] == "IS_STREETLIGHT"
    assert props["visit_reason"] == OPEN["visit_reason"] and "lat" not in props
    assert props["action_if_attribute_agrees"] == "Update the date" and csv_row["action_if_attribute_agrees"] == "Update the date"


def test_a_row_made_before_the_attribute_cells_has_them_empty():
    first = lines(asset_check.table_csv(CHECK))[0]
    cells = ("attribute_field", "attribute_state", "attribute_note", "action_if_attribute_agrees", "visit_reason")
    assert [first[c] for c in cells] == [""] * 5


def test_the_app_gets_the_new_counts_and_an_old_list_still_loads():
    summary = asset_check.summary(WITH_ATTRIBUTES)
    assert summary["desk_review_candidates"] == 2 and summary["visits_attribute_unresolved"] == 1
    assert summary["attribute_checks"]["unsettled"] == 1 and summary["no_visit"] == 2  # the old key stays
    assert asset_check.for_app(WITH_ATTRIBUTES)["rows"][-1]["attribute_state"] == "unsettled"
    old = asset_check.summary(CHECK)
    assert not {"desk_review_candidates", "attribute_checks", "visits_attribute_unresolved"} & set(old)


def test_the_page_never_says_a_record_needs_no_site_visit():
    for check in (CHECK, WITH_ATTRIBUTES):
        page = asset_check.document(check)
        assert "no site visit" not in page.lower()
        assert "candidates for desk review" in page
        assert "not a measured count of avoided site visits" in page


def test_the_visit_action_says_a_newer_photo_or_a_field_check_is_needed():
    assert "newer photo or a field check" in asset_check.ACTIONS["Visit on site"]
    assert "site visit" not in asset_check.ACTIONS["Visit on site"]


def test_the_page_says_how_many_seen_records_have_an_attribute_the_photos_did_not_settle():
    page = asset_check.document(WITH_ATTRIBUTES)
    assert "Seen in a photo is not the same as checked" in page
    assert "1 of the 3 seen records with an attribute to check" in page


def test_a_list_without_attribute_counts_does_not_mention_them():
    assert "Seen in a photo is not the same as checked" not in asset_check.document(CHECK)


def test_the_page_calls_the_comparison_a_model_agreement_prototype_not_an_independent_evaluation():
    trust = {**CHECK["trust"], "record_check": {"asked": 20, "agree": 15, "differ": 1, "unsettled": 4, "sent_back": 1}}
    page = asset_check.document({**CHECK, "trust": trust})
    assert "model agreement" in page and "prototype" in page and "not an independent evaluation" in page


def test_the_examples_never_show_a_visit_row_as_a_settled_record():
    assert all(r["action"] != "Visit on site" for r in asset_check.examples(WITH_ATTRIBUTES))


# --- evidence quality before candidate counts: the same numbers on every surface ----------------------------------

HELD_ID = row(asset="Street tree", city_record="TRE-1", action="Visit on site", city_says="", photo_shows="",
              identity_state="unresolved", competing_record_ids=["TRE-2", "TRE-3"],
              identity_reason="TRE-1 and TRE-2 are 9.8 m apart and photographed 7 s apart; the photos cannot tell which "
                              "record this is. A desk identity check or a new photo may resolve it; nothing is merged.",
              evidence_quality="not_reviewed", evidence_quality_reason="This source has not been reviewed.",
              quality_gate={"action_before": "Update the date", "causes": ["identity_unresolved"], "model_answer": {}},
              visit_reason="Identity not settled. A desk identity check or a new photo may resolve it.")
HELD_AUDIT = row(asset="Curb ramp", city_record="CBR-1", action="Visit on site", city_says="", photo_shows="",
                 identity_state="not_flagged", competing_record_ids=[], identity_reason="",
                 evidence_quality="unclear", evidence_quality_reason="AI visual audit, not independent ground truth: x",
                 attribute_state="unsettled",
                 quality_gate={"action_before": "Update the date", "causes": ["audit_hold"], "model_answer": {}})
GATE = {"identity": {"unresolved_rows": 3, "moved_out_of_candidates": 1, "already_visit": 2,
                     "groups": [["TRE-1", "TRE-2", "TRE-3"]], "rule": {"max_metres": 15.0, "max_seconds": 10.0}},
        "evidence_quality": {"unclear": 1, "not_reviewed": 4,
                             "policy": {"policy_id": "ai-visual-audit-holds", "holds_in_policy": 2, "applied": 1,
                                        "provenance": "AI visual audit, not independent ground truth"}},
        "moved_out_of_candidates": 2, "candidates_before_gate": 4,
        "by_cause": {"identity_only": 1, "hold_only": 1, "identity_and_hold": 0},
        "visit_causes": {"primary": {"identity_unresolved": 3, "audit_hold": 1}, "overlap": {"identity_and_attribute": 0}}}
GATED = {**CHECK, "rows": [*ROWS, HELD_ID, HELD_AUDIT], "actions": {**ACTIONS, "Visit on site": 3},
         "desk_review_candidates": 2, "quality_gate": GATE}


def test_the_table_and_the_geojson_carry_the_identity_and_quality_cells():
    csv_row = next(r for r in lines(asset_check.table_csv(GATED)) if r["city_record"] == "TRE-1")
    assert (csv_row["identity_state"], csv_row["competing_record_ids"]) == ("unresolved", "TRE-2; TRE-3")
    assert "nothing is merged" in csv_row["identity_reason"] and csv_row["evidence_quality"] == "not_reviewed"
    assert csv_row["action_before_quality_gate"] == "Update the date" and csv_row["action"] == "Visit on site"
    held = next(r for r in lines(asset_check.table_csv(GATED)) if r["city_record"] == "CBR-1")
    assert held["evidence_quality"] == "unclear" and held["identity_state"] == "not_flagged"
    props = next(f["properties"] for f in json.loads(asset_check.table_geojson(GATED))["features"]
                 if f["properties"]["city_record"] == "TRE-1")
    assert props["competing_record_ids"] == "TRE-2; TRE-3" and props["identity_state"] == "unresolved"


def test_a_row_made_before_the_quality_fields_has_them_empty():
    first = lines(asset_check.table_csv(CHECK))[0]
    cells = ("identity_state", "identity_reason", "competing_record_ids", "evidence_quality",
             "evidence_quality_reason", "action_before_quality_gate")
    assert [first[c] for c in cells] == [""] * 6  # absence of a flag says nothing; the table does not invent one


def test_the_app_gets_the_gate_with_the_counts_and_an_old_list_still_loads():
    assert asset_check.summary(GATED)["quality_gate"] == GATE
    assert "quality_gate" not in asset_check.summary(CHECK)
    assert "rows" not in asset_check.summary(GATED)


def test_the_page_states_how_many_were_held_back_and_why_with_the_same_numbers():
    page = asset_check.document(GATED)
    assert "candidates for desk review" in page
    assert "Held before counting" in page
    assert "2 city records that would have been candidates" in page
    assert "1 whose identity the photos cannot settle" in page and "1 held by an AI visual audit" in page
    assert "not independent ground truth" in page
    assert "none is merged or called a duplicate" in page


def test_the_page_never_counts_an_overlap_twice():
    both = {"identity_and_hold": 1, "identity_only": 1, "hold_only": 1}
    gate = {**GATE, "by_cause": both, "moved_out_of_candidates": 3}
    page = asset_check.document({**GATED, "quality_gate": gate})
    assert "3 city records that would have been candidates" in page
    assert "1 of them on both grounds" in page


def test_a_list_with_no_gate_says_nothing_about_one():
    assert "Held before counting" not in asset_check.document(CHECK)


# --- the date action is called Review newer photo evidence for people; the machine value stays ------------------------

def test_people_read_review_newer_photo_evidence_and_machines_still_read_the_old_action():
    table = lines(asset_check.table_csv(CHECK))
    assert table[0]["action"] == "Update the date" and table[0]["action_label"] == "Review newer photo evidence"
    assert [r["action_label"] for r in table[1:]] == ["No change", "Visit on site", "Compare with the list"]
    props = json.loads(asset_check.table_geojson(CHECK))["features"][0]["properties"]
    assert props["action"] == "Update the date" and props["action_label"] == "Review newer photo evidence"
    assert asset_check.label_of("Correct the record") == "Correct the record"


def test_the_page_never_shows_the_old_name_of_the_date_action():
    page = asset_check.document(WITH_ATTRIBUTES)
    body = page.split("<main>", 1)[1]
    visible = body.replace("action_if_attribute_agrees", "")  # a machine column name, described below
    assert "Review newer photo evidence" in visible
    for old in ("<strong>Update the date</strong>", ">Update the date<", "chip seen\">Update the date"):
        assert old not in visible
    assert "Update the date" not in page.split('<section aria-labelledby="file">')[0]
