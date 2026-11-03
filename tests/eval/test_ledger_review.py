"""validate_journal and the two exports: what a reviewer's journal may turn into, checked against the Asset Check."""
import copy
import csv
import io
import json

import pytest

from eval import ledger_review
from eval.ledger_review import LedgerError
from tests.eval import ledger_fixture as fx

CHECK = fx.make_check()


def reject(check, journal, fragment):
    with pytest.raises(LedgerError) as raised:
        ledger_review.validate_journal(check, journal)
    assert fragment in " | ".join(raised.value.problems), raised.value.problems


def rows_of(text):
    return list(csv.reader(io.StringIO(text, newline="")))


def test_a_complete_change_of_the_configured_field_is_approved_with_its_raw_values():
    [change] = ledger_review.validate_journal(CHECK, fx.journal(fx.decision(CHECK, "P1")))
    assert (change["layer"], change["record_key"], change["record_id"]) == ("poles.geojson", "UNITID", "P1")
    assert (change["field"], change["before"], change["after"]) == ("IS_LIGHT", "YES", "NO")
    assert change["properties_sha256"] == fx.row_of(CHECK, "P1")["ledger_source"]["properties_sha256"]
    assert change["photo_observed_on"] == "2022-08-30"
    assert change["reviewer"] == "A. Tester" and "self-asserted" in change["reviewer_assertion"].lower()


def test_nothing_is_approved_by_default():
    assert ledger_review.validate_journal(CHECK, fx.journal()) == []
    assert ledger_review.observations(CHECK, fx.journal()) == []
    assert rows_of(ledger_review.changes_csv(CHECK, fx.journal()))[1:] == []


def test_a_historical_observation_is_recorded_apart_from_any_change():
    obs = fx.decision(CHECK, "TRE-90000", decision="observe", field=None, proposed_value=None)
    journal = fx.journal(obs)
    assert ledger_review.validate_journal(CHECK, journal) == []
    [seen] = ledger_review.observations(CHECK, journal)
    assert seen["photo_observed_on"] == "2022-08-30" and seen["record_id"] == "TRE-90000"
    assert seen["photo_link"].startswith("https://www.mapillary.com/") and seen["photo_by"] == "photographer"
    assert seen["photo_license"] == "CC BY-SA 4.0"
    assert "LAST_VERIFY_DATE" not in json.dumps(seen)  # a photo date is not the city's verification date
    header, *body = rows_of(ledger_review.observations_csv(CHECK, journal))
    assert len(body) == 1 and body[0][header.index("record_id")] == "TRE-90000"
    assert rows_of(ledger_review.changes_csv(CHECK, journal))[1:] == []


def test_a_hold_is_kept_out_of_both_exports():
    held = fx.decision(CHECK, "P1", decision="hold", field=None, proposed_value=None, identity="uncertain",
                       evidence="unclear")
    journal = fx.journal(held)
    assert ledger_review.validate_journal(CHECK, journal) == []
    assert ledger_review.observations(CHECK, journal) == []
    assert [h["record_id"] for h in ledger_review.holds(CHECK, journal)] == ["P1"]
    assert rows_of(ledger_review.changes_csv(CHECK, journal))[1:] == []
    assert rows_of(ledger_review.observations_csv(CHECK, journal))[1:] == []


@pytest.mark.parametrize("name,over,fragment", [
    ("identity uncertain", {"identity": "uncertain"}, "identity must be confirmed"),
    ("identity different object", {"identity": "different"}, "identity must be confirmed"),
    ("evidence unclear", {"evidence": "unclear"}, "evidence must be clear"),
    ("unknown identity word", {"identity": "yes"}, "identity"),
    ("unknown decision word", {"decision": "apply"}, "decision"),
    ("no reviewer", {"reviewer": ""}, "reviewer is required"),
    ("blank reviewer", {"reviewer": "   "}, "reviewer is required"),
    ("reviewer not text", {"reviewer": 7}, "reviewer is required"),
    ("no reason", {"note": ""}, "note is required"),
    ("blank reason", {"note": " \n "}, "note is required"),
    ("no time", {"reviewed_at": ""}, "reviewed_at"),
    ("time not a time", {"reviewed_at": "yesterday"}, "reviewed_at"),
    ("time without zone", {"reviewed_at": "2026-11-04T09:30:00"}, "reviewed_at"),
    ("time is a number", {"reviewed_at": 1730000000}, "reviewed_at"),
    ("same before and after", {"proposed_value": "YES"}, "no change"),
    ("value not raw yes/no", {"proposed_value": "yes"}, "not an allowed value"),
    ("value invented", {"proposed_value": "MAYBE"}, "not an allowed value"),
    ("no value", {"proposed_value": None}, "not an allowed value"),
    ("no field", {"field": None}, "not an editable field"),
    ("date field", {"field": "CHECKED", "proposed_value": "2026-11-04"}, "not an editable field"),
    ("id field", {"field": "UNITID", "proposed_value": "P9"}, "not an editable field"),
    ("geometry", {"field": "geometry", "proposed_value": "x"}, "not an editable field"),
    ("arbitrary field", {"field": "NOTE", "proposed_value": "x"}, "not an editable field"),
    ("field with trailing space", {"field": "IS_LIGHT "}, "not an editable field"),
    ("field not text", {"field": ["IS_LIGHT"]}, "not an editable field"),
    ("record id of another row", {"record_id": "P2"}, "does not match its row"),
    ("item of another row", {"item": "tree"}, "does not match its row"),
    ("unknown key", {"path": "../escape.geojson"}, "unknown key"),
    ("neighbors flag not a boolean", {"neighbors_checked": "yes"}, "neighbors_checked"),
    ("unknown review key", {"review_key": "0" * 64}, "unknown review_key"),
    ("no review key", {"review_key": None}, "unknown review_key"),
])
def test_an_invalid_change_rejects_the_whole_journal(name, over, fragment):
    good = fx.decision(CHECK, "P1")
    reject(CHECK, fx.journal(fx.decision(CHECK, "TRE-90000", decision="hold", field=None, proposed_value=None),
                             {**good, **over}), fragment)


def test_a_change_needs_a_photo_and_a_source_snapshot():
    # the pole nobody photographed has a source but no photo, and a record with no snapshot at all is unknown
    check = fx.make_check()
    row = fx.row_of(check, "C1")
    change = {**fx.decision(check, "C1"), "field": "IS_LIGHT"}
    reject(check, fx.journal(change), "no photo")
    legacy = copy.deepcopy(check)
    del fx.row_of(legacy, "P1")["ledger_source"]
    reject(legacy, fx.journal(fx.decision(check, "P1")), "unknown review_key")
    assert row["evidence"] is None


def test_observing_needs_a_confirmed_identity_and_a_photo():
    check = fx.make_check()
    reject(check, fx.journal(fx.decision(check, "C1", decision="observe", field=None, proposed_value=None)), "no photo")
    unsure = fx.decision(check, "TRE-90000", decision="observe", identity="uncertain", field=None, proposed_value=None)
    reject(check, fx.journal(unsure), "identity must be confirmed")


def test_the_neighbour_warning_must_be_confirmed_for_a_change_and_an_observation():
    town = json.loads(json.dumps(fx.TOWN))
    town["items"]["tree"]["compare"] = {"field": "HAS_GUARD", "yes": "Y", "no": "N", "ask": "Is there a tree guard?"}
    layers = fx.layers()
    for feature in layers["tree"]:
        feature["properties"]["HAS_GUARD"] = "N"
    check = fx.make_check(town=town, layer_map=layers)
    assert fx.row_of(check, "TRE-53943")["ledger_source"]["identity_warnings"]  # TRE-53943 and TRE-53948 are 8 m apart
    change = fx.decision(check, "TRE-53943", field="HAS_GUARD", proposed_value="Y")
    reject(check, fx.journal(change), "neighbors_checked")
    reject(check, fx.journal({**change, "decision": "observe", "field": None, "proposed_value": None}), "neighbors_checked")
    [done] = ledger_review.validate_journal(check, fx.journal({**change, "neighbors_checked": True}))
    assert done["neighbors_checked"] is True and done["record_id"] == "TRE-53943"
    # the warning asks a question; it does not merge the two: both records can be changed once each is confirmed
    both = fx.journal({**change, "neighbors_checked": True},
                      fx.decision(check, "TRE-53948", field="HAS_GUARD", proposed_value="Y", neighbors_checked=True))
    assert [c["record_id"] for c in ledger_review.validate_journal(check, both)] == ["TRE-53943", "TRE-53948"]


def test_a_neighbour_warning_removed_from_the_check_is_still_found_by_the_validator():
    town = json.loads(json.dumps(fx.TOWN))
    town["items"]["tree"]["compare"] = {"field": "HAS_GUARD", "yes": "Y", "no": "N", "ask": "x"}
    layers = fx.layers()
    for feature in layers["tree"]:
        feature["properties"]["HAS_GUARD"] = "N"
    check = fx.make_check(town=town, layer_map=layers)
    change = fx.decision(check, "TRE-53943", field="HAS_GUARD", proposed_value="Y")
    fx.row_of(check, "TRE-53943")["ledger_source"]["identity_warnings"] = []
    reject(check, fx.journal(change), "neighbors_checked")


def test_a_journal_of_another_scan_or_schema_is_rejected():
    good = fx.decision(CHECK, "P1")
    reject(CHECK, fx.journal(good, scan_id=fx.SCAN + 1), "scan_id")
    reject(CHECK, {**fx.journal(good), "schema_version": 2}, "schema_version")
    reject(CHECK, {**fx.journal(good), "schema_version": True}, "schema_version")
    reject(CHECK, {**fx.journal(good), "extra": 1}, "unknown key")
    reject(CHECK, {"schema_version": 1, "scan_id": fx.SCAN}, "decisions")
    reject(CHECK, {"schema_version": 1, "scan_id": fx.SCAN, "decisions": "x"}, "decisions")
    reject(CHECK, {"schema_version": 1, "scan_id": fx.SCAN, "decisions": ["x"]}, "decision")
    reject(CHECK, [], "journal")


def test_a_duplicate_decision_for_one_review_key_rejects_the_package():
    one = fx.decision(CHECK, "P1")
    reject(CHECK, fx.journal(one, copy.deepcopy(one)), "duplicate review_key")
    reject(CHECK, fx.journal(one, {**one, "decision": "hold", "field": None, "proposed_value": None}),
           "duplicate review_key")


def test_two_rows_changing_one_record_field_conflict():
    check = copy.deepcopy(CHECK)
    twin = copy.deepcopy(fx.row_of(check, "P1"))
    twin["evidence"]["captured_at"] = "2023-01-01T00:00:00Z"
    twin["ledger_source"]["review_key"] = ledger_review.review_key(
        fx.SCAN, twin["item"], "poles.geojson", "UNITID", "P1", twin["ledger_source"]["properties_sha256"],
        twin["evidence"], twin["box"])
    check["rows"].append(twin)
    a = fx.decision(check, "P1")
    b = {**a, "review_key": twin["ledger_source"]["review_key"], "proposed_value": "NO"}
    reject(check, fx.journal(a, b), "conflicting")


def test_a_journal_exported_before_the_ledger_record_or_photo_changed_is_stale():
    old = fx.journal(fx.decision(CHECK, "P1"))
    edited = fx.layers()
    edited["pole"][0]["properties"]["NOTE"] = "edited since the export"
    reject(fx.make_check(layer_map=edited), old, "unknown review_key")
    other_box = fx.data()["issues"]
    other_box[0]["model_outputs"]["check"]["box"] = [9, 9, 9, 9]
    reject(fx.make_check(issues=other_box), old, "unknown review_key")


def test_a_check_whose_rows_no_longer_match_their_review_keys_is_refused():
    check = copy.deepcopy(CHECK)
    fx.row_of(check, "P1")["box"] = [9, 9, 9, 9]
    reject(check, fx.journal(fx.decision(CHECK, "P1")), "check does not match")


def test_the_validator_accepts_the_apps_json_form_of_the_check():
    from assist import asset_check as page
    app_form = json.loads(json.dumps(page.for_app(CHECK)))
    [change] = ledger_review.validate_journal(app_form, fx.journal(fx.decision(CHECK, "P1")))
    assert change["after"] == "NO"


def test_every_problem_is_listed_not_just_the_first():
    bad = [fx.decision(CHECK, "P1", reviewer=""), fx.decision(CHECK, "TRE-90000", note="", decision="hold",
                                                                 field=None, proposed_value=None)]
    with pytest.raises(LedgerError) as raised:
        ledger_review.validate_journal(CHECK, fx.journal(*bad))
    assert len(raised.value.problems) == 2


FORMULAS = ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1", "\r=1", "\n=1", "=HYPERLINK(\"http://x\",\"y\")"]


@pytest.mark.parametrize("text", FORMULAS)
def test_csv_cells_that_start_like_a_formula_are_made_text(text):
    change = fx.decision(CHECK, "P1", note=text, reviewer=f"{text} Rev")
    header, row = rows_of(ledger_review.changes_csv(CHECK, fx.journal(change)))
    note = row[header.index("note")]
    assert note.startswith("'") and note[1:] == text
    assert not row[header.index("reviewer")][0] in "=+-@\t\r\n"


def test_csv_keeps_quotes_commas_newlines_and_unicode_and_the_exact_before_after():
    note = 'He said "hi", then left\nnext line é한글 \U0001f600 =not a formula in the middle'
    change = fx.decision(CHECK, "P1", note=note, reviewer="Zoë, \"Z\"")
    header, row = rows_of(ledger_review.changes_csv(CHECK, fx.journal(change)))
    cells = dict(zip(header, row))
    assert cells["note"] == note and cells["reviewer"] == 'Zoë, "Z"'
    assert (cells["field"], cells["before"], cells["after"]) == ("IS_LIGHT", "YES", "NO")
    assert cells["record_id"] == "P1" and cells["layer"] == "poles.geojson"
    assert cells["reviewer_assertion"].startswith("Self-asserted")
    assert {"review_key", "properties_sha256", "photo_observed_on", "photo_link", "photo_by", "photo_license",
            "reviewed_at", "neighbors_checked", "scan_id", "item"} <= set(header)


def test_a_formula_in_a_raw_city_value_is_defanged_in_the_csv_but_kept_exact_in_the_change():
    layers = fx.layers()
    town = json.loads(json.dumps(fx.TOWN))
    town["items"]["pole"]["compare"].update(yes="=YES", no="-NO")
    layers["pole"][0]["properties"]["IS_LIGHT"] = "=YES"
    check = fx.make_check(town=town, layer_map=layers)
    journal = fx.journal(fx.decision(check, "P1", proposed_value="-NO"))
    [change] = ledger_review.validate_journal(check, journal)
    assert (change["before"], change["after"]) == ("=YES", "-NO")
    header, row = rows_of(ledger_review.changes_csv(check, journal))
    assert (row[header.index("before")], row[header.index("after")]) == ("'=YES", "'-NO")


@pytest.mark.parametrize("field", ["LAST_VERIFY_DATE", "VERIFICATION_DATE", "DATEUPDATED", "UNITID", "OBJECTID", "lat"])
def test_a_check_edited_to_offer_a_date_or_id_field_still_cannot_change_it(field):
    check = copy.deepcopy(CHECK)
    row = fx.row_of(check, "TRE-90000")
    row["ledger_source"]["editable_fields"] = {field: {"before": "1", "yes": "1", "no": "2", "question": "x",
                                                       "suggested": None}}
    change = fx.decision(check, "TRE-90000", field=field, proposed_value="2")
    reject(check, fx.journal(change), "not an editable field")
