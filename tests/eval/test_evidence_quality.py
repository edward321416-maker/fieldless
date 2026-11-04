"""Evidence quality before candidate counts: the identity gate for same-item records that the photos cannot tell
apart, and the source-bound AI audit hold policy. Nothing here calls a model, and nothing here says anything is verified."""
import copy
import json

import pytest

from eval import asset_check, attribute_state, evidence_quality, ledger_review
from tests.eval import ledger_fixture as fx

AGREE = {"state": attribute_state.AGREE, "reason": ""}
UNRESOLVED, NOT_FLAGGED, UNCLEAR, NOT_REVIEWED = "unresolved", "not_flagged", "unclear", "not_reviewed"
VISIT, DATE = asset_check.VISIT, asset_check.DATE
ID_FIELDS = ("identity_state", "identity_reason", "competing_record_ids", "evidence_quality", "evidence_quality_reason")


def build(issues=None, layer_map=None, attributes=None, policy=None, differs=None):
    data = fx.data()
    if issues is not None:
        data["issues"] = issues
    kwargs = {} if policy is None else {"quality_policy": policy}
    return asset_check.build(data, fx.check_file(), fx.TOWN, layer_map or fx.layers(), fx.STANDARD, {}, fx.TRUST, None,
                             differs, None, attributes, **kwargs)


def empty_policy():
    return {"schema_version": 1, "policy_id": "test-holds", "provenance": evidence_quality.PROVENANCE,
            "audit": {"id": "test-audit", "date": "2026-11-04"}, "holds": []}


def held(check, record, reason="The detail cannot be read at this size.", **over):
    """A policy that holds the exact source `record` has in `check`."""
    hold = evidence_quality.make_hold(fx.row_of(check, record), "IS_LIGHT", reason, "agree", fx.SCAN, **over)
    return {**empty_policy(), "holds": [hold]}


# --- the identity gate ----------------------------------------------------------------------------------------------

def test_the_tree_pair_stays_two_records_and_neither_is_a_settled_candidate():
    check = build()
    a, b = fx.row_of(check, "TRE-53943"), fx.row_of(check, "TRE-53948")
    assert a["ledger_source"]["review_key"] != b["ledger_source"]["review_key"]  # not merged
    for mine, theirs in ((a, "TRE-53948"), (b, "TRE-53943")):
        assert mine["identity_state"] == UNRESOLVED
        assert mine["competing_record_ids"] == [theirs]
        assert mine["action"] == VISIT
        assert "identity" in mine["identity_reason"].lower() and theirs in mine["identity_reason"]
        assert "new photo" in mine["visit_reason"] and "identity" in mine["visit_reason"].lower()
        assert mine["city_says"] == mine["photo_shows"] == ""
        assert "nothing is merged" in mine["identity_reason"]


def test_the_original_model_answer_of_a_moved_row_is_kept_beside_the_new_action():
    row = fx.row_of(build(), "TRE-53943")
    assert row["quality_gate"]["action_before"] == DATE
    assert row["quality_gate"]["causes"] == ["identity_unresolved"]
    assert row["status"] == asset_check.SEEN and row["seen_in_photo_on"] == "2022-08-30"  # provenance untouched
    assert row["evidence"]["link"].endswith("94")


def test_a_record_with_no_close_neighbor_stays_a_candidate_and_is_not_flagged():
    row = fx.row_of(build(), "TRE-90000")
    assert row["identity_state"] == NOT_FLAGGED and row["competing_record_ids"] == [] and row["identity_reason"] == ""
    assert row["action"] == DATE and "quality_gate" not in row


def test_the_gate_applies_before_every_total():
    check = build()
    rows = [r for r in check["rows"] if r["city_record"]]
    assert check["desk_review_candidates"] == check["no_visit"] == sum(r["action"] != VISIT for r in rows)
    assert check["actions"][DATE] == 1  # TRE-90000 only; the two TRE rows moved
    tree = next(i for i in check["items"] if i["item"] == "tree")
    assert tree["actions"] == {VISIT: 2, DATE: 1}
    assert sum(check["actions"].values()) == len(check["rows"])
    gate = check["quality_gate"]
    assert gate["identity"]["unresolved_rows"] == 2 and gate["identity"]["moved_out_of_candidates"] == 2
    assert gate["candidates_before_gate"] == check["no_visit"] + gate["moved_out_of_candidates"] == check["no_visit"] + 2


def test_an_unresolved_row_that_already_was_a_visit_stays_a_visit_with_its_own_reason():
    # the pole pair: both sit at the same spot, and with no attribute check both are visits already
    layer_map = fx.layers()
    layer_map["pole"][1]["geometry"]["coordinates"] = list(layer_map["pole"][0]["geometry"]["coordinates"])
    layer_map["pole"][1]["geometry"]["coordinates"][1] += 0.00005
    check = build(layer_map=layer_map)
    a, b = fx.row_of(check, "P1"), fx.row_of(check, "P2")
    assert a["identity_state"] == b["identity_state"] == UNRESOLVED
    assert a["action"] == b["action"] == VISIT
    assert "quality_gate" not in a  # nothing was taken out of the candidates
    assert check["quality_gate"]["identity"]["already_visit"] >= 2


def test_the_gate_is_generic_to_any_item_and_not_to_two_known_ids():
    layer_map = fx.layers()
    layer_map["can"].append(fx.feature({"UNITID": "C2"}, 47.8 + 0.00006, -122.3))
    data = fx.data()
    data["issues"] += [fx.issue(7, "can", "2023-01-01T00:00:00Z"), fx.issue(8, "can", "2023-01-01T00:00:03Z")]
    cf = fx.check_file()
    cf["items"]["can"]["missed"], cf["items"]["can"]["pairs"] = [], [
        {"issue_id": 7, "in_view": True, "city": {"UNITID": "C1"}}, {"issue_id": 8, "in_view": True, "city": {"UNITID": "C2"}}]
    town = copy.deepcopy(fx.TOWN)
    town["items"]["can"].update(checked_field="CHECKED", verified=True)
    for f in layer_map["can"]:
        f["properties"]["CHECKED"] = "2020-01-01"
    check = asset_check.build(data, cf, town, layer_map, fx.STANDARD, {}, fx.TRUST)
    assert {r["city_record"]: r["identity_state"] for r in check["rows"] if r["item"] == "can"} == {
        "C1": UNRESOLVED, "C2": UNRESOLVED}


@pytest.mark.parametrize("name,change", [
    ("different items at the same spot and time", "item"),
    ("farther apart than the distance gate", "distance"),
    ("photographed farther apart in time than the time gate", "time")])
def test_records_outside_the_gate_are_not_flagged(name, change):
    layer_map, issues = fx.layers(), fx.data()["issues"]
    if change == "item":
        # a pole at the tree's spot, photographed at the same second
        layer_map["pole"][1]["geometry"]["coordinates"] = list(layer_map["tree"][0]["geometry"]["coordinates"])
        issues[1]["evidence"]["captured_at"] = issues[3]["evidence"]["captured_at"]
        for f in layer_map["tree"][1:]:
            f["geometry"]["coordinates"][1] += 0.05  # the tree pair is no longer close
    elif change == "distance":
        layer_map["tree"][1]["geometry"]["coordinates"][1] += 0.0005  # about 55 m
    elif change == "time":
        issues[4]["evidence"]["captured_at"] = "2022-08-30T10:00:30Z"
    check = build(issues=issues, layer_map=layer_map)
    flagged = [r["city_record"] for r in check["rows"] if r.get("identity_state") == UNRESOLVED]
    assert flagged == []
    assert check["quality_gate"]["identity"]["unresolved_rows"] == 0
    assert {r["action"] for r in check["rows"] if r["item"] == "tree"} == {DATE}  # trees stay candidates


def test_two_rows_of_one_record_are_never_each_others_neighbor():
    rows = [r for r in fx.make_check()["rows"] if r["item"] == "tree"]
    twin = copy.deepcopy(rows[0])
    twin["ledger_source"]["review_key"] = "f" * 64
    assert ledger_review.neighbor_pairs([rows[0], twin]) == {rows[0]["ledger_source"]["review_key"]: [], "f" * 64: []}


def test_exactly_at_the_gate_is_flagged_and_just_past_it_is_not():
    assert ledger_review.NEIGHBOR_M == 15.0 and ledger_review.NEIGHBOR_S == 10.0
    layer_map, issues = fx.layers(), fx.data()["issues"]
    issues[4]["evidence"]["captured_at"] = "2022-08-30T10:00:10Z"  # exactly 10 s
    assert fx.row_of(build(issues=issues), "TRE-53948")["identity_state"] == UNRESOLVED
    issues[4]["evidence"]["captured_at"] = "2022-08-30T10:00:11Z"  # 11 s
    assert fx.row_of(build(issues=issues), "TRE-53948")["identity_state"] == NOT_FLAGGED


def test_warning_pairs_say_who_is_near_and_the_warning_text_is_unchanged():
    rows = fx.make_check()["rows"]
    pairs = ledger_review.neighbor_pairs([r for r in rows if r["item"] == "tree"])
    key = fx.row_of(fx.make_check(), "TRE-53943")["ledger_source"]["review_key"]
    [near] = pairs[key]
    assert near["record_id"] == "TRE-53948" and near["metres"] < ledger_review.NEIGHBOR_M and near["seconds"] == 7
    assert fx.row_of(fx.make_check(), "TRE-53943")["ledger_source"]["identity_warnings"] == [
        "Record TRE-53948 of the same item is 7.8 m away and was photographed 7 s from this one: check these are two different objects."]


def test_the_raw_city_data_and_the_review_keys_are_not_touched():
    layer_map = fx.layers()
    before = copy.deepcopy(layer_map)
    build(layer_map=layer_map)
    assert layer_map == before
    keys = lambda c: [r["ledger_source"]["review_key"] for r in c["rows"] if r.get("ledger_source")]  # noqa: E731
    assert keys(build()) == keys(fx.make_check())  # the gate adds no input to a snapshot


# --- the audit hold policy -------------------------------------------------------------------------------------------

def agreed():
    return {("pole", "P1"): AGREE, ("pole", "P2"): AGREE, ("pole", "P3"): AGREE}


def test_without_a_hold_a_row_is_not_reviewed_never_verified():
    row = fx.row_of(build(attributes=agreed()), "P1")
    assert row["evidence_quality"] == NOT_REVIEWED and row["action"] == DATE and row["attribute_state"] == "agree"
    assert "verified" not in row["evidence_quality_reason"].lower().replace("not verified", "")
    assert "not been reviewed" in row["evidence_quality_reason"]


def test_every_city_record_row_carries_the_quality_fields_and_listed_objects_do_not():
    check = build(attributes=agreed())
    for r in check["rows"]:
        if r["city_record"]:
            assert all(k in r for k in ID_FIELDS)
            assert r["identity_state"] in (UNRESOLVED, NOT_FLAGGED) and r["evidence_quality"] in (UNCLEAR, NOT_REVIEWED)
        else:
            assert not any(k in r for k in ID_FIELDS)


def test_a_hold_binds_the_exact_source_and_leaves_the_models_answer_alone():
    base = build(attributes=agreed())
    check = build(attributes=agreed(), policy=held(base, "P1"))
    row = fx.row_of(check, "P1")
    assert row["evidence_quality"] == UNCLEAR and "cannot be read" in row["evidence_quality_reason"]
    assert row["attribute_state"] == "unsettled" and row["action"] == VISIT
    assert "action_if_attribute_agrees" not in row  # no conditional candidate left
    assert row["quality_gate"]["model_answer"]["attribute_state"] == "agree"
    assert row["quality_gate"]["action_before"] == DATE and row["quality_gate"]["causes"] == ["audit_hold"]
    assert "AI visual audit" in row["visit_reason"] and "not independent ground truth" in row["visit_reason"]
    assert fx.row_of(check, "P2")["evidence_quality"] == NOT_REVIEWED  # no other row inherits it
    assert fx.row_of(check, "P2")["action"] == DATE
    assert check["attribute_checks"]["open_by"]["audit_hold"] == 1
    assert check["quality_gate"]["evidence_quality"]["unclear"] == 1
    assert check["desk_review_candidates"] == base["desk_review_candidates"] - 1


def test_a_hold_does_not_survive_a_changed_source():
    base = build(attributes=agreed())
    policy = held(base, "P1")
    newer = fx.data()["issues"]
    newer[0]["evidence"]["captured_at"] = "2023-01-02T10:00:00Z"  # another photo date: another review_key
    moved = build(issues=newer, attributes=agreed(), policy=policy)
    row = fx.row_of(moved, "P1")
    assert row["ledger_source"]["review_key"] != policy["holds"][0]["review_key"]
    assert row["evidence_quality"] == NOT_REVIEWED and row["action"] == DATE and row["attribute_state"] == "agree"
    assert "quality_gate" not in row
    changed = fx.layers()
    changed["pole"][0]["properties"]["NOTE"] = "edited by the city"  # the city's record changed
    row = fx.row_of(build(layer_map=changed, attributes=agreed(), policy=policy), "P1")
    assert row["evidence_quality"] == NOT_REVIEWED and row["action"] == DATE
    box = fx.data()["issues"]
    box[0]["model_outputs"]["check"]["box"] = [9, 9, 9, 9]
    assert fx.row_of(build(issues=box, attributes=agreed(), policy=policy), "P1")["evidence_quality"] == NOT_REVIEWED


@pytest.mark.parametrize("field,value", [("properties_sha256", "0" * 64), ("position_sha256", "0" * 64),
                                         ("record_id", "P2"), ("item", "tree"), ("layer", "other.geojson")])
def test_a_hold_whose_key_names_one_source_but_whose_values_name_another_is_refused_and_ignored(field, value):
    base = build(attributes=agreed())
    policy = held(base, "P1")
    policy["holds"][0][field] = value
    with pytest.raises(ValueError, match="review_key"):
        build(attributes=agreed(), policy=policy)  # a half-bound hold is a malformed policy: none of it applies
    assert evidence_quality.find_hold(fx.row_of(base, "P1"), policy, AGREE, "IS_LIGHT") is None  # and never binds a row


def test_a_hold_for_another_field_is_ignored():
    base = build(attributes=agreed())
    policy = held(base, "P1")
    policy["holds"][0]["field"] = "OTHER_FIELD"
    row = fx.row_of(build(attributes=agreed(), policy=policy), "P1")
    assert row["evidence_quality"] == NOT_REVIEWED and row["action"] == DATE


def test_a_hold_is_for_one_model_answer_not_for_whatever_the_models_say_next():
    base = build(attributes=agreed())
    policy = held(base, "P1")
    differs = {("pole", "P1"): {"city_says": "Holds a street light", "photo_shows": "Holds no street light"}}
    row = fx.row_of(build(attributes={("pole", "P1"): {"state": "different", "reason": ""}}, policy=policy,
                          differs=differs), "P1")
    assert row["evidence_quality"] == NOT_REVIEWED and row["action"] == asset_check.FIX


def test_a_hold_and_an_identity_warning_on_one_row_count_once():
    layer_map = fx.layers()
    layer_map["pole"][1]["geometry"]["coordinates"] = [layer_map["pole"][0]["geometry"]["coordinates"][0],
                                                       layer_map["pole"][0]["geometry"]["coordinates"][1] + 0.00005]
    base = build(layer_map=layer_map, attributes=agreed())
    assert fx.row_of(base, "P1")["action"] == VISIT  # both identity-flagged poles are moved, and so are the two trees
    gate = base["quality_gate"]
    assert gate["identity"]["moved_out_of_candidates"] == gate["moved_out_of_candidates"] == 4
    assert gate["by_cause"] == {"identity_only": 4, "hold_only": 0, "identity_and_hold": 0}
    check = build(layer_map=layer_map, attributes=agreed(), policy=held(base, "P1"))
    row = fx.row_of(check, "P1")
    assert row["quality_gate"]["causes"] == ["identity_unresolved", "audit_hold"]
    assert row["quality_gate"]["action_before"] == DATE
    gate = check["quality_gate"]
    assert gate["moved_out_of_candidates"] == 4  # P1 is one row, moved once, however many grounds it has
    assert gate["by_cause"] == {"identity_only": 3, "hold_only": 0, "identity_and_hold": 1}
    assert gate["identity"]["moved_out_of_candidates"] == 4
    assert gate["evidence_quality"]["unclear"] == 1
    assert gate["visit_causes"]["overlap"]["identity_and_attribute"] == 1
    assert check["desk_review_candidates"] == base["desk_review_candidates"]  # nothing new was taken out
    assert gate["candidates_before_gate"] == check["desk_review_candidates"] + 4


def test_the_visit_reasons_add_up_to_the_visits_without_counting_an_overlap_twice():
    check = build(attributes={("pole", "P1"): {"state": "unsettled", "reason": "unclear"}, **agreed()})
    visits = [r for r in check["rows"] if r["city_record"] and r["action"] == VISIT]
    primary = check["quality_gate"]["visit_causes"]["primary"]
    assert sum(primary.values()) == len(visits)
    assert all(r["visit_causes"] for r in visits)


# --- the checked-in policy --------------------------------------------------------------------------------------------

AUDIT = {"CBR-9290": "447f0a79fa5c54526dc1282af4b8354eea3c5e9020b49dd4019cd546c70d8c60",
         "CBR-30448": "b5d1eb80091f4cba71962e21ce44f1ba8bed63453a57c4ba30db4d0e546f0c17"}


def test_the_checked_in_policy_holds_the_two_audited_ramp_sources_and_nothing_else():
    policy = evidence_quality.load_policy()
    assert policy["provenance"] == "AI visual audit, not independent ground truth"
    assert {h["record_id"]: h["review_key"] for h in policy["holds"]} == AUDIT
    for hold in policy["holds"]:
        assert (hold["scan_id"], hold["item"], hold["layer"], hold["field"]) == (
            30, "curb_ramp_warning", "curb_ramps.geojson", "DETWARN_EXISTS")
        assert hold["model_attribute_state"] == "agree"
        assert hold["photo"]["link"].startswith("https://www.mapillary.com/app/?pKey=")
        assert hold["photo"]["captured_at"] and hold["photo"]["creator"] and hold["photo"]["license"] == "CC BY-SA 4.0"
        assert "not independent ground truth" in hold["provenance"] and hold["reason"]
        assert "wrong" not in hold["reason"].lower() or "not a finding" in hold["reason"].lower()


def test_each_checked_in_hold_rebuilds_its_own_review_key():
    for hold in evidence_quality.load_policy()["holds"]:
        assert evidence_quality.hold_review_key(hold) == hold["review_key"]


def test_the_policy_is_not_a_conditional_in_the_candidate_code():
    import inspect
    source = inspect.getsource(asset_check)
    assert "CBR-9290" not in source and "CBR-30448" not in source and "TRE-53943" not in source


def test_a_hold_in_the_default_policy_holds_the_exact_snapshot_it_names():
    """The audited row, rebuilt from the audit's own binding values, is held; with one value changed it is not."""
    hold = copy.deepcopy(evidence_quality.load_policy()["holds"][0])
    row = {"item": hold["item"], "city_record": hold["record_id"],
           "evidence": {"link": hold["photo"]["link"], "captured_at": hold["photo"]["captured_at"]}, "box": hold["box"],
           "ledger_source": {"layer": hold["layer"], "record_key": hold["record_key"], "record_id": hold["record_id"],
                             "properties_sha256": hold["properties_sha256"], "position_sha256": hold["position_sha256"],
                             "review_key": hold["review_key"], "editable_fields": {hold["field"]: {}}}}
    policy = evidence_quality.load_policy()
    assert evidence_quality.find_hold(row, policy, {"state": "agree"}, hold["field"]) == hold
    row["ledger_source"]["review_key"] = "0" * 64
    assert evidence_quality.find_hold(row, policy, {"state": "agree"}, hold["field"]) is None


@pytest.mark.parametrize("mutate,problem", [
    (lambda p: p.update(schema_version=2), "schema_version"),
    (lambda p: p.update(provenance="verified"), "provenance"),
    (lambda p: p["holds"].append(p["holds"][0]), "duplicate"),
    (lambda p: p["holds"][0].pop("reason"), "reason"),
    (lambda p: p["holds"][0].update(review_key="1" * 64), "review_key"),
    (lambda p: p["holds"][0].update(model_attribute_state="maybe"), "model_attribute_state"),
    (lambda p: p["holds"][0].update(extra="x"), "unknown key")])
def test_a_malformed_policy_is_refused_not_half_applied(mutate, problem):
    policy = copy.deepcopy(evidence_quality.load_policy())
    mutate(policy)
    with pytest.raises(ValueError, match=problem):
        evidence_quality.check_policy(policy)


def test_the_default_policy_file_is_valid_json_in_the_repository():
    path = evidence_quality.DEFAULT_POLICY
    assert path.is_file() and json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_every_surface_reads_the_same_gated_counts():
    """The same built check through the CSV, the GeoJSON, the app's summary and the page: one number of candidates."""
    import csv
    import io

    from assist import asset_check as assist_page
    base = build(attributes=agreed())
    check = build(attributes=agreed(), policy=held(base, "P1"))
    table = list(csv.DictReader(io.StringIO(assist_page.table_csv(check).decode("utf-8-sig"))))
    in_csv = sum(bool(r["city_record"]) and r["action"] != VISIT for r in table)
    in_geojson = sum(bool(f["properties"]["city_record"]) and f["properties"]["action"] != VISIT
                     for f in json.loads(assist_page.table_geojson(check))["features"])
    summary = assist_page.summary(check)
    assert in_csv == in_geojson == check["no_visit"] == summary["no_visit"] == summary["desk_review_candidates"]
    assert summary["quality_gate"]["candidates_before_gate"] == in_csv + summary["quality_gate"]["moved_out_of_candidates"]
    moved_in_csv = sum(r["action_before_quality_gate"] not in ("", VISIT) for r in table)
    assert moved_in_csv == summary["quality_gate"]["moved_out_of_candidates"]
    page = assist_page.document(check)
    assert f"{check['no_visit']} of {check['totals']['in_view']} city records are candidates" in page
    assert f"{summary['quality_gate']['moved_out_of_candidates']} city records that would have been candidates" in page
