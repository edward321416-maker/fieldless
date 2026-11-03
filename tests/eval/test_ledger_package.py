"""eval.ledger_package: the one portable review package (source + journal + manifest + convenience CSV files).
The source and journal are the only inputs; the CSV strings and the manifest are checked against what they are rebuilt from,
never trusted. Nothing in a package is an approval."""
import copy
import json
from pathlib import Path

import pytest

from eval import ledger_package, ledger_review
from eval.ledger_review import LedgerError
from tests.eval import ledger_fixture as fx

CHECK = fx.make_check()
JOURNAL = fx.journal(fx.decision(CHECK, "P1"))
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ledger_interop"


def package(check=CHECK, journal=JOURNAL):
    return ledger_package.build_package(check, journal)


def refused(value, fragment):
    with pytest.raises(LedgerError) as raised:
        ledger_package.read_package(value)
    assert fragment in " | ".join(raised.value.problems), raised.value.problems


def test_the_package_has_exactly_the_documented_shape():
    pkg = package()
    assert list(pkg) == ["schema_version", "kind", "scan_id", "source", "journal", "manifest", "files"]
    assert pkg["schema_version"] == 1 and pkg["kind"] == "fieldless-ledger-review" and pkg["scan_id"] == fx.SCAN
    assert pkg["source"] == CHECK and pkg["journal"] == JOURNAL
    assert pkg["manifest"] == {"notice": "Prepared only; not applied to an official ledger.",
                               "reviewer_assertion": "Self-asserted; not authenticated municipal approval."}
    assert list(pkg["files"]) == ["changes_csv", "observations_csv"]


def test_the_convenience_files_are_the_two_csv_files_rebuilt_from_source_and_journal():
    pkg = package()
    assert pkg["files"]["changes_csv"] == ledger_review.changes_csv(CHECK, JOURNAL)
    assert pkg["files"]["observations_csv"] == ledger_review.observations_csv(CHECK, JOURNAL)
    assert pkg["files"]["changes_csv"].startswith("﻿") and "P1" in pkg["files"]["changes_csv"]


def test_a_package_reads_back_to_the_same_source_and_journal():
    check, journal = ledger_package.read_package(json.loads(json.dumps(package())))
    assert check == CHECK and journal == JOURNAL


def test_a_package_is_refused_when_its_journal_is_not_valid_for_its_source():
    pkg = package()
    pkg["journal"]["decisions"][0]["identity"] = "uncertain"
    refused(pkg, "identity must be confirmed")


def test_a_package_is_not_built_from_an_invalid_journal():
    bad = fx.journal(fx.decision(CHECK, "P1", proposed_value="MAYBE"))
    with pytest.raises(LedgerError):
        ledger_package.build_package(CHECK, bad)


@pytest.mark.parametrize("name", ["changes_csv", "observations_csv"])
def test_a_convenience_csv_that_differs_from_the_rebuilt_one_is_refused(name):
    pkg = package()
    pkg["files"][name] += "P9,forged,row\r\n"
    refused(pkg, name)


def test_a_forged_csv_row_cannot_add_a_change_the_journal_does_not_have():
    pkg = package()
    pkg["files"]["changes_csv"] = pkg["files"]["changes_csv"].replace("P1", "P2")
    refused(pkg, "changes_csv")


@pytest.mark.parametrize("field", ["notice", "reviewer_assertion"])
def test_a_false_manifest_is_refused(field):
    pkg = package()
    pkg["manifest"][field] = "Approved by the city."
    refused(pkg, "manifest")


def test_a_manifest_with_another_key_or_a_missing_one_is_refused():
    pkg = package()
    pkg["manifest"]["approved"] = True
    refused(pkg, "manifest")
    pkg = package()
    del pkg["manifest"]["notice"]
    refused(pkg, "manifest")


def test_the_manifest_and_files_are_optional_but_never_replace_the_inputs():
    pkg = package()
    del pkg["manifest"], pkg["files"]
    assert ledger_package.read_package(pkg) == (CHECK, JOURNAL)


@pytest.mark.parametrize("over,fragment", [
    ({"kind": "something-else"}, "kind"),
    ({"schema_version": 2}, "schema_version"),
    ({"schema_version": True}, "schema_version"),
    ({"schema_version": 1.5}, "schema_version"),
    ({"scan_id": fx.SCAN + 1}, "scan_id"),
    ({"scan_id": True}, "scan_id"),
    ({"approved": True}, "unknown key"),
    ({"source": []}, "source"),
    ({"journal": "x"}, "journal"),
    ({"files": []}, "files"),
    ({"files": {"changes_csv": 1, "observations_csv": ""}}, "changes_csv"),
    ({"files": {"changes_csv": "", "observations_csv": "", "zip": ""}}, "files"),
])
def test_a_malformed_package_is_refused(over, fragment):
    refused({**package(), **over}, fragment)


@pytest.mark.parametrize("key", ["source", "journal", "kind", "schema_version", "scan_id"])
def test_a_package_missing_a_required_key_is_refused(key):
    pkg = package()
    del pkg[key]
    refused(pkg, key)


@pytest.mark.parametrize("value", [None, [], "text", 3])
def test_a_package_that_is_not_an_object_is_refused(value):
    refused(value, "not an object")


def test_the_scan_of_the_journal_must_be_the_scan_of_the_source():
    pkg = package()
    pkg["journal"]["scan_id"] = fx.SCAN + 1
    refused(pkg, "scan_id")


def test_a_source_edited_after_it_was_made_is_refused():
    pkg = package()
    pkg["source"] = copy.deepcopy(pkg["source"])
    row = fx.row_of(pkg["source"], "P1")
    row["ledger_source"]["properties_sha256"] = "0" * 64
    refused(pkg, "review keys")


def test_a_package_cannot_switch_to_another_scan_by_editing_only_the_source_id():
    pkg = package()
    pkg["source"] = {**pkg["source"], "scan_id": 8}
    refused(pkg, "scan_id")


def test_a_package_with_no_decisions_is_still_a_valid_package():
    check, journal = ledger_package.read_package(package(CHECK, fx.journal()))
    assert journal["decisions"] == []


def test_package_text_is_utf8_json_that_reads_back(tmp_path):
    text = ledger_package.package_text(package())
    assert text.endswith("\n") and not text.startswith("﻿")
    path = tmp_path / "p.json"
    path.write_bytes(text.encode("utf-8"))
    assert ledger_package.load_package(path) == (CHECK, JOURNAL)


def test_load_package_reads_a_file_with_a_bom_and_refuses_bad_json_nan_and_huge_files(tmp_path):
    path = tmp_path / "p.json"
    path.write_bytes(b"\xef\xbb\xbf" + ledger_package.package_text(package()).encode("utf-8"))
    assert ledger_package.load_package(path) == (CHECK, JOURNAL)
    for body, fragment in ((b"{not json", "not valid JSON"), (b'{"schema_version": NaN}', "not valid JSON"),
                           (b"\xff\xfe", "not valid JSON")):
        path.write_bytes(body)
        with pytest.raises(LedgerError) as raised:
            ledger_package.load_package(path)
        assert fragment in " | ".join(raised.value.problems)
    with pytest.raises(LedgerError) as raised:
        ledger_package.load_package(tmp_path / "missing.json")
    assert "cannot read" in " | ".join(raised.value.problems)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (ledger_package.MAX_BYTES + 1))
    with pytest.raises(LedgerError) as raised:
        ledger_package.load_package(big)
    assert "too large" in " | ".join(raised.value.problems)


def test_a_duplicate_key_in_the_json_text_is_refused(tmp_path):
    text = ledger_package.package_text(package()).replace('"kind": "fieldless-ledger-review"',
                                                          '"kind": "fieldless-ledger-review", "kind": "x"', 1)
    path = tmp_path / "p.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LedgerError) as raised:
        ledger_package.load_package(path)
    assert "duplicate" in " | ".join(raised.value.problems)


# --- TypeScript and Python read and write the same package (web/src/ledgerPackage.test.ts reads the same file) -----------

def test_the_shared_fixture_package_is_what_python_builds_from_the_shared_check_and_journal():
    check = json.loads((FIXTURE / "check.json").read_text(encoding="utf-8"))
    journal = json.loads((FIXTURE / "journal.json").read_text(encoding="utf-8"))
    shared = json.loads((FIXTURE / "review-package.json").read_text(encoding="utf-8"))
    assert ledger_package.build_package(check, journal) == shared
    assert ledger_package.read_package(shared) == (check, journal)
    # the convenience files of the fixture are the byte-checked ones of the interop test
    assert shared["files"]["changes_csv"].encode("utf-8") == (FIXTURE / "changes.csv").read_bytes()
    assert shared["files"]["observations_csv"].encode("utf-8") == (FIXTURE / "observations.csv").read_bytes()
