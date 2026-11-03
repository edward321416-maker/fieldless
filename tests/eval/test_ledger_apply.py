"""eval.ledger_apply: validated changes become a NEW copy of the city's GeoJSON folder, or nothing is written."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from eval import ledger_apply, ledger_review
from eval.ledger_review import LedgerError
from tests.eval import ledger_fixture as fx

COLLECTIONS = {"assets/poles.geojson": "pole", "assets/trees.geojson": "tree", "litter.geojson": "can"}


def write_input(root: Path, layers=None, edit=None) -> Path:
    """The city's GeoJSON folder: the fixture's layers, one untouched layer and a file that is not a layer."""
    layers = layers or fx.layers()
    for rel, item in COLLECTIONS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"type": "FeatureCollection", "name": item, "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
               "features": layers[item]}
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "other.geojson").write_bytes(b'{"type":"FeatureCollection","features":[]}\n')
    (root / "notes.txt").write_text("not a layer", encoding="utf-8")
    if edit:
        edit(root)
    return root


def digest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def city(tmp_path):
    return write_input(tmp_path / "city")


CHECK = fx.make_check()


def journal_of(*records, check=CHECK, **over):
    return fx.journal(*[fx.decision(check, r, **over) for r in records])


def run(check, journal, city, out, town=fx.TOWN, **kw):
    """ledger_apply.apply with the fixture's city file as the trusted one, unless told otherwise."""
    return ledger_apply.apply(check, journal, city, out, town=town, **kw)


def reject(city, tmp_path, check, journal, fragment, out=None, town=fx.TOWN):
    out = out or tmp_path / "out"
    before_in, before_parent = digest(city), sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(LedgerError) as raised:
        ledger_apply.apply(check, journal, city, out, town=town)
    assert fragment in " | ".join(raised.value.problems), raised.value.problems
    assert digest(city) == before_in  # the original folder is untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == before_parent  # nothing written, not even a staging folder
    assert not out.exists()


def retarget(check, record, **over):
    """A check whose row for `record` has other source values and a review key that matches them (a hostile or old file)."""
    check = copy.deepcopy(check)
    row = fx.row_of(check, record)
    src = {**row["ledger_source"], **over}
    src["review_key"] = ledger_review.review_key(
        check["scan_id"], row["item"], src["layer"], src["record_key"], src["record_id"], src["properties_sha256"],
        row.get("evidence"), row.get("box"), src.get("position_sha256"))
    row["ledger_source"] = src
    return check


def test_a_change_is_applied_to_a_new_copy_and_the_original_folder_stays_as_it_was(city, tmp_path):
    out = tmp_path / "out"
    before = digest(city)
    audit = run(CHECK, journal_of("P1"), city, out)
    assert digest(city) == before
    changed = load(out / "assets" / "poles.geojson")
    original = load(city / "assets" / "poles.geojson")
    expected = copy.deepcopy(original)
    expected["features"][0]["properties"]["IS_LIGHT"] = "NO"
    assert changed == expected  # that one property, nothing else, in that layer
    assert list(changed["features"][0]["properties"]) == list(original["features"][0]["properties"])  # key order
    assert changed["features"][0]["properties"]["NOTE"] == "caf\u00e9"
    assert (out / "assets" / "trees.geojson").read_bytes() == (city / "assets" / "trees.geojson").read_bytes()
    assert (out / "litter.geojson").read_bytes() == (city / "litter.geojson").read_bytes()
    assert not (out / "other.geojson").exists()  # only the city file's layers are copied
    assert not (out / "notes.txt").exists()  # only the layers are copied
    assert audit["applied"] == 1 and audit["changes"][0]["after"] == "NO"


def test_the_audit_names_what_changed_who_said_so_and_what_it_does_not_mean(city, tmp_path):
    out = tmp_path / "out"
    run(CHECK, fx.journal(fx.decision(CHECK, "P1"), fx.decision(
        CHECK, "TRE-90000", decision="observe", field=None, proposed_value=None),
        fx.decision(CHECK, "P3", decision="hold", field=None, proposed_value=None)), city, out)
    audit = load(out / "_ledger_review" / "audit.json")
    assert audit["schema_version"] == 1 and audit["scan_id"] == fx.SCAN
    [change] = audit["changes"]
    assert (change["layer"], change["record_id"], change["field"], change["before"], change["after"]) \
        == ("poles.geojson", "P1", "IS_LIGHT", "YES", "NO")
    assert change["reviewer"] == "A. Tester" and "Self-asserted" in change["reviewer_assertion"]
    assert [o["record_id"] for o in audit["observations"]] == ["TRE-90000"]
    assert [h["record_id"] for h in audit["holds"]] == ["P3"]
    assert "No city system was written" in audit["notice"]
    files = audit["files"]
    assert files["assets/poles.geojson"]["changed"] is True and files["assets/trees.geojson"]["changed"] is False
    assert files["assets/poles.geojson"]["input_sha256"] != files["assets/poles.geojson"]["output_sha256"]
    assert files["litter.geojson"]["input_sha256"] == files["litter.geojson"]["output_sha256"]
    assert str(tmp_path) not in json.dumps(audit)  # no local path in the audit
    assert (out / "_ledger_review" / "changes.csv").read_text(encoding="utf-8").count("\n") >= 2
    assert (out / "_ledger_review" / "observations.csv").is_file()
    assert load(out / "_ledger_review" / "journal.json")["decisions"][0]["record_id"] == "P1"


def test_an_observation_or_a_hold_alone_changes_no_layer(city, tmp_path):
    out = tmp_path / "out"
    obs = fx.decision(CHECK, "TRE-90000", decision="observe", field=None, proposed_value=None)
    run(CHECK, fx.journal(obs), city, out)
    layers = {k: v for k, v in digest(out).items() if k.endswith(".geojson")}  # the city file's layers only
    assert layers == {k: v for k, v in digest(city).items() if k.endswith(".geojson") and k != "other.geojson"}
    assert load(out / "_ledger_review" / "audit.json")["applied"] == 0


def test_numbers_other_members_and_unicode_of_a_layer_survive_the_rewrite(tmp_path):
    layers = fx.layers()
    layers["pole"][0]["properties"].update(BIG=12345678901234567890, TINY=1e-07, KEEP=1.10, KO="\ud55c\uae00 \U0001f600")
    city = write_input(tmp_path / "city", layers)
    check = fx.make_check(layer_map=layers)
    run(check, fx.journal(fx.decision(check, "P1")), city, tmp_path / "out")
    changed = load(tmp_path / "out" / "assets" / "poles.geojson")
    props = changed["features"][0]["properties"]
    assert (props["BIG"], props["TINY"], props["KEEP"], props["KO"]) == (12345678901234567890, 1e-07, 1.10, "\ud55c\uae00 \U0001f600")
    assert changed["crs"] == load(city / "assets" / "poles.geojson")["crs"]


def test_a_record_edited_in_the_ledger_since_the_export_is_refused(city, tmp_path):
    def edit(root):
        path = root / "assets" / "poles.geojson"
        doc = load(path)
        doc["features"][0]["properties"]["NOTE"] = "edited by the city after the export"
        path.write_text(json.dumps(doc), encoding="utf-8")
    write_input(city, edit=edit)
    reject(city, tmp_path, CHECK, journal_of("P1"), "properties changed since")


def test_a_changed_value_is_refused_even_when_a_hand_edited_check_repeats_the_new_hash(city, tmp_path):
    def edit(root):
        path = root / "assets" / "poles.geojson"
        doc = load(path)
        doc["features"][0]["properties"]["IS_LIGHT"] = "NO"
        path.write_text(json.dumps(doc), encoding="utf-8")
    write_input(city, edit=edit)
    new_sha = ledger_review.properties_sha256(load(city / "assets" / "poles.geojson")["features"][0]["properties"])
    check = retarget(CHECK, "P1", properties_sha256=new_sha)  # the hash matches, the recorded `before` is stale
    reject(city, tmp_path, check, journal_of("P1", check=check), "before")


def test_a_record_missing_from_the_ledger_is_refused(city, tmp_path):
    def edit(root):
        path = root / "assets" / "poles.geojson"
        doc = load(path)
        doc["features"] = doc["features"][1:]
        path.write_text(json.dumps(doc), encoding="utf-8")
    write_input(city, edit=edit)
    reject(city, tmp_path, CHECK, journal_of("P1"), "0 records")


def test_a_record_id_that_appears_twice_in_the_ledger_is_refused(city, tmp_path):
    def edit(root):
        path = root / "assets" / "poles.geojson"
        doc = load(path)
        doc["features"].append(copy.deepcopy(doc["features"][0]))
        path.write_text(json.dumps(doc), encoding="utf-8")
    write_input(city, edit=edit)
    reject(city, tmp_path, CHECK, journal_of("P1"), "2 records")


@pytest.mark.parametrize("layer", ["../evil.geojson", "assets/poles.geojson", "C:/x.geojson", "poles.txt", "", "a\\b.geojson"])
def test_an_unsafe_layer_name_in_a_check_never_reaches_the_filesystem(city, tmp_path, layer):
    check = retarget(CHECK, "P1", layer=layer)
    reject(city, tmp_path, check, journal_of("P1", check=check), "unsafe layer")


def observe(check, record):
    return fx.journal(fx.decision(check, record, decision="observe", field=None, proposed_value=None))


def test_a_layer_file_missing_or_found_twice_is_refused(city, tmp_path):
    gone = retarget(CHECK, "P1", layer="gone.geojson")
    reject(city, tmp_path, gone, journal_of("P1", check=gone), "not one of the city's layers")
    # an observation alone needs no city file: its layer is then found by its name in the folder
    gone_obs = retarget(CHECK, "TRE-90000", layer="gone.geojson")
    reject(city, tmp_path, gone_obs, observe(gone_obs, "TRE-90000"), "no layer file", town=None)
    (city / "assets" / "copy").mkdir()
    (city / "assets" / "copy" / "trees.geojson").write_bytes((city / "assets" / "trees.geojson").read_bytes())
    reject(city, tmp_path, CHECK, observe(CHECK, "TRE-90000"), "more than one layer file", town=None)


def test_a_layer_that_is_not_a_feature_collection_or_has_nan_is_refused(city, tmp_path):
    (city / "assets" / "poles.geojson").write_text('{"type": "Feature"}', encoding="utf-8")
    reject(city, tmp_path, CHECK, journal_of("P1"), "not a FeatureCollection")
    (city / "assets" / "poles.geojson").write_text('{"type":"FeatureCollection","features":[{"properties":{"UNITID":"P1","X":NaN}}]}',
                                                  encoding="utf-8")
    reject(city, tmp_path, CHECK, journal_of("P1"), "not valid JSON")


def test_an_existing_output_is_never_overwritten(city, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(LedgerError) as raised:
        run(CHECK, journal_of("P1"), city, out)
    assert "already exists" in " ".join(raised.value.problems)
    assert [p.name for p in out.iterdir()] == ["keep.txt"] and (out / "keep.txt").read_text(encoding="utf-8") == "mine"
    empty = tmp_path / "empty"
    empty.mkdir()
    reject_existing = pytest.raises(LedgerError)
    with reject_existing:
        run(CHECK, journal_of("P1"), city, empty)
    plain_file = tmp_path / "afile"
    plain_file.write_text("x", encoding="utf-8")
    with pytest.raises(LedgerError):
        run(CHECK, journal_of("P1"), city, plain_file)


def test_the_output_cannot_be_the_input_inside_it_or_around_it(city, tmp_path):
    for out in (city, city / "revised", tmp_path):
        with pytest.raises(LedgerError) as raised:
            run(CHECK, journal_of("P1"), city, out)
        assert "input folder" in " ".join(raised.value.problems) or "already exists" in " ".join(raised.value.problems)
    assert not (city / "revised").exists()


def test_one_bad_change_among_good_ones_writes_nothing(city, tmp_path):
    check = retarget(CHECK, "TRE-90000", properties_sha256="0" * 64)
    journal = fx.journal(fx.decision(CHECK, "P1"), fx.decision(check, "TRE-90000", decision="observe", field=None,
                                                                proposed_value=None))
    reject(city, tmp_path, check, journal, "properties changed since")


def test_an_invalid_journal_writes_nothing(city, tmp_path):
    reject(city, tmp_path, CHECK, journal_of("P1", identity="uncertain"), "identity must be confirmed")
    reject(city, tmp_path, CHECK, fx.journal(), "nothing to apply")


def test_a_journal_for_another_scan_writes_nothing(city, tmp_path):
    reject(city, tmp_path, CHECK, fx.journal(fx.decision(CHECK, "P1"), scan_id=99), "scan_id")


def test_the_cli_applies_and_reports_and_a_rejection_exits_2_without_writing(city, tmp_path, capsys, monkeypatch):
    check_path, journal_path = tmp_path / "asset_check.json", tmp_path / "journal.json"
    check_path.write_text(json.dumps(CHECK), encoding="utf-8")
    journal_path.write_text(json.dumps(journal_of("P1")), encoding="utf-8")
    out = tmp_path / "revised"
    args = ["--check", str(check_path), "--journal", str(journal_path), "--input-dir", str(city), "--output-dir", str(out)]
    assert ledger_apply.main(args) == 2  # a change is never applied without a trusted city file
    assert "--city" in capsys.readouterr().err and not out.exists()
    monkeypatch.setattr(ledger_apply, "load_city", lambda name: copy.deepcopy(fx.TOWN))
    args += ["--city", "testville"]
    assert ledger_apply.main(args) == 0
    assert "1 change" in capsys.readouterr().out and (out / "_ledger_review" / "audit.json").is_file()
    assert ledger_apply.main(args) == 2  # the output now exists
    assert "already exists" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ledger_apply.main(["--check", str(bad), "--journal", str(journal_path), "--input-dir", str(city),
                              "--output-dir", str(tmp_path / "other")]) == 2
    assert not (tmp_path / "other").exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_a_layer_that_is_a_link_out_of_the_folder_is_refused(city, tmp_path):
    outside = tmp_path / "outside.geojson"
    outside.write_bytes((city / "assets" / "poles.geojson").read_bytes())
    (city / "assets" / "poles.geojson").unlink()
    try:
        os.symlink(outside, city / "assets" / "poles.geojson")
    except OSError:
        pytest.skip("symlinks need privileges here")
    reject(city, tmp_path, CHECK, journal_of("P1"), "inside the input folder")
    reject(city, tmp_path, CHECK, observe(CHECK, "P1"), "outside the input folder", town=None)


def decoy(root: Path):
    """Another area's copy of the poles layer, same file name, other records."""
    (root / "other_area").mkdir()
    doc = load(root / "assets" / "poles.geojson")
    doc["features"][0]["properties"]["IS_LIGHT"] = "NO"
    (root / "other_area" / "poles.geojson").write_text(json.dumps(doc), encoding="utf-8")


def test_a_layer_name_found_in_several_folders_needs_the_city_file_to_say_which(city, tmp_path):
    decoy(city)
    reject(city, tmp_path, CHECK, observe(CHECK, "P1"), "more than one layer file", town=None)
    out = tmp_path / "out"
    audit = run(CHECK, journal_of("P1"), city, out)
    assert audit["applied"] == 1
    assert load(out / "assets" / "poles.geojson")["features"][0]["properties"]["IS_LIGHT"] == "NO"
    assert sorted(digest(out)) == sorted(["assets/poles.geojson", "assets/trees.geojson", "litter.geojson",
                                          "_ledger_review/audit.json", "_ledger_review/changes.csv",
                                          "_ledger_review/journal.json", "_ledger_review/observations.csv"])
    assert not (out / "other_area").exists()  # only the city's configured layers are copied


def test_a_layer_the_city_file_does_not_name_or_names_outside_the_folder_is_refused(city, tmp_path):
    no_pole = {"items": {k: v for k, v in fx.TOWN["items"].items() if k != "pole"}}
    reject(city, tmp_path, CHECK, journal_of("P1"), "not an item of the city file", town=no_pole)
    for bad in ("../city2/poles.geojson", "/etc/poles.geojson", "assets/../../poles.geojson", "assets/gone.geojson"):
        town = copy.deepcopy(fx.TOWN)
        town["items"]["pole"]["layer"] = bad
        reject(city, tmp_path, CHECK, journal_of("P1"), "layer", town=town)


def test_layer_paths_are_read_from_a_city_file_and_skip_a_basename_two_items_share():
    town = {"items": {"a": {"layer": "assets/poles.geojson"}, "b": {"layer": "x/trees.geojson"},
                      "c": {"layer": "y/trees.geojson"}, "d": {"layer": ""}}}
    assert ledger_apply.layer_paths(town) == {"poles.geojson": "assets/poles.geojson", "trees.geojson": None}


def test_the_cli_takes_the_city_file_to_tell_same_named_layers_apart(city, tmp_path, monkeypatch, capsys):
    decoy(city)
    monkeypatch.setattr(ledger_apply, "load_city", lambda name: copy.deepcopy(fx.TOWN))
    check_path, journal_path = tmp_path / "c.json", tmp_path / "j.json"
    check_path.write_text(json.dumps(CHECK), encoding="utf-8")
    journal_path.write_text(json.dumps(journal_of("P1")), encoding="utf-8")
    base = ["--check", str(check_path), "--journal", str(journal_path), "--input-dir", str(city)]
    assert ledger_apply.main([*base, "--output-dir", str(tmp_path / "no_city")]) == 2
    assert "--city" in capsys.readouterr().err and not (tmp_path / "no_city").exists()
    assert ledger_apply.main([*base, "--city", "testville", "--output-dir", str(tmp_path / "with_city")]) == 0


def move_geometry(root: Path, rel="assets/poles.geojson", index=0, dlng=0.00002):
    path = root / rel
    doc = load(path)
    doc["features"][index]["geometry"]["coordinates"][0] += dlng  # about 1.5 m: properties stay equal
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_record_moved_in_the_ledger_with_equal_properties_is_refused(city, tmp_path):
    move_geometry(city)
    reject(city, tmp_path, CHECK, journal_of("P1"), "location changed since")


def test_an_observation_of_a_record_that_moved_is_refused_too(city, tmp_path):
    move_geometry(city, "assets/trees.geojson", 2)
    obs = fx.decision(CHECK, "TRE-90000", decision="observe", field=None, proposed_value=None)
    reject(city, tmp_path, CHECK, fx.journal(obs), "location changed since")


def test_a_record_that_lost_its_point_is_refused(city, tmp_path):
    path = city / "assets" / "poles.geojson"
    doc = load(path)
    doc["features"][0]["geometry"] = None
    path.write_text(json.dumps(doc), encoding="utf-8")
    reject(city, tmp_path, CHECK, journal_of("P1"), "location changed since")


def test_a_source_made_before_positions_were_hashed_is_still_applied_on_its_properties_alone(city, tmp_path):
    check = copy.deepcopy(CHECK)
    row = fx.row_of(check, "P1")
    src = row["ledger_source"]
    del src["position_sha256"]
    src["review_key"] = ledger_review.review_key(check["scan_id"], row["item"], src["layer"], src["record_key"],
                                                 src["record_id"], src["properties_sha256"], row["evidence"], row["box"])
    assert run(check, journal_of("P1", check=check), city, tmp_path / "out")["applied"] == 1


# --- the whitelist comes from the city's own file, never from the downloaded check -------------------------------------

def test_a_hand_edited_check_cannot_offer_a_field_the_city_file_does_not_name(city, tmp_path):
    check = copy.deepcopy(CHECK)
    row = fx.row_of(check, "P1")
    row["ledger_source"]["editable_fields"]["NOTE"] = {"before": "caf\u00e9", "yes": "caf\u00e9", "no": "X", "question": "",
                                                       "suggested": None}
    journal = fx.journal(fx.decision(check, "P1", field="NOTE", proposed_value="X"))
    assert ledger_review.validate_journal(check, journal)  # the journal check alone trusts the check ...
    reject(city, tmp_path, check, journal, "configured")  # ... the application does not


def test_a_hand_edited_check_cannot_mint_other_yes_no_values(city, tmp_path):
    check = copy.deepcopy(CHECK)
    fx.row_of(check, "P1")["ledger_source"]["editable_fields"]["IS_LIGHT"].update(no="BROKEN")
    reject(city, tmp_path, check, fx.journal(fx.decision(check, "P1", proposed_value="BROKEN")), "yes/no")


def test_a_change_aimed_through_another_id_key_or_layer_is_refused(city, tmp_path):
    by_note = retarget(CHECK, "P1", record_key="NOTE", record_id="caf\u00e9")
    reject(city, tmp_path, by_note, journal_of("P1", check=by_note), "record key")
    other_layer = retarget(CHECK, "P1", layer="trees.geojson")
    reject(city, tmp_path, other_layer, journal_of("P1", check=other_layer), "layer")


def test_a_date_field_named_by_a_city_file_is_still_refused(city, tmp_path):
    town = copy.deepcopy(fx.TOWN)
    town["items"]["pole"]["compare"]["field"] = "LAST_VERIFY_DATE"
    check = copy.deepcopy(CHECK)
    row = fx.row_of(check, "P1")
    row["ledger_source"]["editable_fields"] = {"LAST_VERIFY_DATE": {"before": "YES", "yes": "YES", "no": "NO", "question": "",
                                                                   "suggested": None}}
    reject(city, tmp_path, check, fx.journal(fx.decision(check, "P1", field="LAST_VERIFY_DATE")), "not an editable field")
    changes = [{"item": "pole", "layer": "poles.geojson", "record_key": "UNITID", "record_id": "P1", "decision": "change",
                "field": "LAST_VERIFY_DATE", "before": "YES", "after": "NO"}]
    assert "protected" in " ".join(ledger_review.city_problems(changes, town))


def test_a_package_with_changes_needs_the_trusted_city_file(city, tmp_path):
    reject(city, tmp_path, CHECK, journal_of("P1"), "city file", town=None)
    reject(city, tmp_path, CHECK, journal_of("P1"), "city file", town={})


def test_observations_and_holds_alone_need_no_city_file(city, tmp_path):
    out = tmp_path / "out"
    journal = fx.journal(fx.decision(CHECK, "TRE-90000", decision="observe", field=None, proposed_value=None),
                         fx.decision(CHECK, "P3", decision="hold", field=None, proposed_value=None))
    audit = ledger_apply.apply(CHECK, journal, city, out)
    assert (audit["applied"], audit["observed"], audit["held"]) == (0, 1, 1)
    assert audit["whitelist"]["checked_against_city_file"] is False


def test_the_audit_says_the_whitelist_was_the_city_files(city, tmp_path):
    audit = run(CHECK, journal_of("P1"), city, tmp_path / "out")
    assert audit["whitelist"] == {"checked_against_city_file": True, "city": "Testville"}


def test_the_configured_fields_of_the_shipped_city_files_pass_and_nothing_else_does():
    from eval.city_check import load_city
    town = load_city("seattle-ballard")
    ok = [{"item": "utility_pole", "layer": "poles.geojson", "record_key": "ASSET_ID", "record_id": "1", "decision": "change",
           "field": "IS_STREETLIGHT", "before": "YES", "after": "NO"},
          {"item": "curb_ramp_warning", "layer": "curb_ramps.geojson", "record_key": "UNITID", "record_id": "2",
           "decision": "change", "field": "DETWARN_EXISTS", "before": "N", "after": "Y"}]
    assert ledger_review.city_problems(ok, town) == []
    for over, fragment in (({"field": "CONDITION"}, "configured"), ({"field": "VERIFICATION_DATE"}, "configured"),
                           ({"after": "MAYBE"}, "yes/no"), ({"before": "MAYBE"}, "yes/no"), ({"after": "YES"}, "no change"),
                           ({"record_key": "COMPKEY"}, "record key"), ({"layer": "street_trees.geojson"}, "layer"),
                           ({"item": "street_tree"}, "item")):
        assert fragment in " ".join(ledger_review.city_problems([{**ok[0], **over}], town)), over


@pytest.mark.parametrize("name", ["../seattle", "seattle/../nyc", "C:\\x", "", "SEATTLE", "seattle.toml", "a b"])
def test_the_cli_takes_only_the_name_of_a_shipped_city_file(city, tmp_path, capsys, name):
    check_path, journal_path = tmp_path / "c.json", tmp_path / "j.json"
    check_path.write_text(json.dumps(CHECK), encoding="utf-8")
    journal_path.write_text(json.dumps(journal_of("P1")), encoding="utf-8")
    out = tmp_path / "out"
    assert ledger_apply.main(["--check", str(check_path), "--journal", str(journal_path), "--input-dir", str(city),
                              "--output-dir", str(out), "--city", name]) == 2
    assert "city" in capsys.readouterr().err and not out.exists()


def test_a_city_file_that_is_not_shipped_is_refused_before_anything_is_written(city, tmp_path, capsys):
    check_path, journal_path = tmp_path / "c.json", tmp_path / "j.json"
    check_path.write_text(json.dumps(CHECK), encoding="utf-8")
    journal_path.write_text(json.dumps(journal_of("P1")), encoding="utf-8")
    assert ledger_apply.main(["--check", str(check_path), "--journal", str(journal_path), "--input-dir", str(city),
                              "--output-dir", str(tmp_path / "out"), "--city", "atlantis"]) == 2
    assert "atlantis" in capsys.readouterr().err and not (tmp_path / "out").exists()
