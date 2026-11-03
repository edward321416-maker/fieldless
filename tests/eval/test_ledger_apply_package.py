"""eval.ledger_apply --package and --dry-run: one package file is the alternative to --check plus --journal, and a dry run
prints what would be applied after every check of a real run, without creating anything."""
import copy
import json
from pathlib import Path

import pytest

from eval import ledger_apply, ledger_package
from tests.eval import ledger_fixture as fx
from tests.eval.test_ledger_apply import digest, move_geometry, write_input

CHECK = fx.make_check()
JOURNAL = fx.journal(fx.decision(CHECK, "P1"), fx.decision(CHECK, "TRE-53943", decision="observe", field=None,
                                                             proposed_value=None, neighbors_checked=True))


@pytest.fixture
def city(tmp_path):
    return write_input(tmp_path / "city")


@pytest.fixture(autouse=True)
def trusted(monkeypatch):
    monkeypatch.setattr(ledger_apply, "load_city", lambda name: copy.deepcopy(fx.TOWN))


def files(tmp_path, check=CHECK, journal=JOURNAL, pkg=None):
    paths = {"check": tmp_path / "check.json", "journal": tmp_path / "journal.json", "package": tmp_path / "package.json"}
    paths["check"].write_text(json.dumps(check), encoding="utf-8")
    paths["journal"].write_text(json.dumps(journal), encoding="utf-8")
    paths["package"].write_text(ledger_package.package_text(pkg or ledger_package.build_package(check, journal)), encoding="utf-8")
    return paths


def listing(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def sans_time(audit_path: Path) -> dict:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.pop("applied_at")
    return audit


def test_a_package_applies_exactly_as_check_and_journal_do(city, tmp_path, capsys):
    p = files(tmp_path)
    one, two = tmp_path / "from_package", tmp_path / "from_pair"
    assert ledger_apply.main(["--package", str(p["package"]), "--input-dir", str(city), "--output-dir", str(one),
                              "--city", "testville"]) == 0
    assert ledger_apply.main(["--check", str(p["check"]), "--journal", str(p["journal"]), "--input-dir", str(city),
                              "--output-dir", str(two), "--city", "testville"]) == 0
    assert "1 change" in capsys.readouterr().out
    a, b = digest(one), digest(two)
    audit = "_ledger_review/audit.json"
    assert {k: v for k, v in a.items() if k != audit} == {k: v for k, v in b.items() if k != audit}
    assert sans_time(one / audit) == sans_time(two / audit)
    layer = json.loads((one / "assets/poles.geojson").read_text(encoding="utf-8"))
    assert layer["features"][0]["properties"]["IS_LIGHT"] == "NO"


@pytest.mark.parametrize("extra", [["--check", "c.json"], ["--journal", "j.json"], ["--check", "c.json", "--journal", "j.json"]])
def test_mixing_package_with_check_or_journal_is_refused_and_writes_nothing(city, tmp_path, capsys, extra):
    p = files(tmp_path)
    out = tmp_path / "out"
    before = listing(tmp_path)
    args = ["--package", str(p["package"]), *extra, "--input-dir", str(city), "--output-dir", str(out), "--city", "testville"]
    assert ledger_apply.main(args) == 2
    err = capsys.readouterr().err
    assert "--package" in err and ("--check" in err or "--journal" in err)
    assert listing(tmp_path) == before and not out.exists()


@pytest.mark.parametrize("given", [[], ["check"], ["journal"]])
def test_without_a_package_both_check_and_journal_are_needed(city, tmp_path, capsys, given):
    p = files(tmp_path)
    args = [a for name in given for a in (f"--{name}", str(p[name]))]
    before = listing(tmp_path)
    assert ledger_apply.main([*args, "--input-dir", str(city), "--output-dir", str(tmp_path / "out"), "--city", "testville"]) == 2
    assert "--package" in capsys.readouterr().err
    assert listing(tmp_path) == before


def test_a_tampered_or_malformed_package_exits_2_and_writes_nothing(city, tmp_path, capsys):
    pkg = ledger_package.build_package(CHECK, JOURNAL)
    pkg["files"]["changes_csv"] = pkg["files"]["changes_csv"].replace("P1", "P2")
    bad = files(tmp_path, pkg=pkg)["package"]
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    for path in (bad, broken, tmp_path / "missing.json"):
        out = tmp_path / "out"
        before = listing(tmp_path)
        assert ledger_apply.main(["--package", str(path), "--input-dir", str(city), "--output-dir", str(out),
                                  "--city", "testville"]) == 2
        assert "REFUSED" in capsys.readouterr().err
        assert listing(tmp_path) == before


# --- the dry run -------------------------------------------------------------------------------------------------------

def dry(city, out, p, *extra, via="package"):
    source = ["--package", str(p["package"])] if via == "package" else ["--check", str(p["check"]), "--journal", str(p["journal"])]
    return ledger_apply.main([*source, "--input-dir", str(city), "--output-dir", str(out), "--city", "testville", "--dry-run", *extra])


@pytest.mark.parametrize("via", ["package", "pair"])
def test_a_dry_run_prints_the_plan_and_creates_nothing_at_all(city, tmp_path, capsys, via):
    p = files(tmp_path)
    out = tmp_path / "not" / "yet" / "revised"
    before, before_city = listing(tmp_path), digest(city)
    assert dry(city, out, p, via=via) == 0
    printed = capsys.readouterr().out
    assert "DRY RUN" in printed and "nothing was written" in printed
    assert "P1" in printed and "IS_LIGHT" in printed and "YES" in printed and "NO" in printed
    assert "1 change" in printed and "1 observation" in printed
    assert listing(tmp_path) == before and digest(city) == before_city
    assert not (tmp_path / "not").exists() and not out.exists()


def test_a_dry_run_leaves_no_staging_folder_next_to_the_output(city, tmp_path):
    p = files(tmp_path)
    dry(city, tmp_path / "revised", p)
    assert not [n for n in listing(tmp_path) if "staging" in n]


def test_a_dry_run_that_passes_predicts_a_real_run_that_passes(city, tmp_path):
    p = files(tmp_path)
    assert dry(city, tmp_path / "revised", p) == 0
    assert ledger_apply.main(["--package", str(p["package"]), "--input-dir", str(city), "--output-dir", str(tmp_path / "revised"),
                              "--city", "testville"]) == 0


def edited_properties(root):  # the record changed in the ledger since the export
    path = root / "assets/poles.geojson"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["features"][0]["properties"]["NOTE"] = "changed since the export"
    path.write_text(json.dumps(doc), encoding="utf-8")


def moved_record(root):
    move_geometry(root)


def missing_layer(root):
    (root / "assets/poles.geojson").unlink()


@pytest.mark.parametrize("edit,fragment", [(edited_properties, "properties changed"), (moved_record, "location changed"),
                                           (missing_layer, "not a file inside the input folder")])
def test_a_dry_run_makes_every_source_check_of_a_real_run(tmp_path, capsys, edit, fragment):
    city = write_input(tmp_path / "city", edit=edit)
    p = files(tmp_path)
    before = listing(tmp_path)
    assert dry(city, tmp_path / "revised", p) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and fragment in err
    assert listing(tmp_path) == before


def test_a_dry_run_refuses_an_existing_output_and_a_change_without_a_city_file(city, tmp_path, capsys):
    p = files(tmp_path)
    taken = tmp_path / "taken"
    taken.mkdir()
    assert dry(city, taken, p) == 2
    assert "already exists" in capsys.readouterr().err
    assert ledger_apply.main(["--package", str(p["package"]), "--input-dir", str(city), "--output-dir", str(tmp_path / "o"),
                              "--dry-run"]) == 2
    assert "--city" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_a_dry_run_refuses_a_city_name_that_is_a_path(city, tmp_path):
    p = files(tmp_path)
    assert ledger_apply.main(["--package", str(p["package"]), "--input-dir", str(city), "--output-dir", str(tmp_path / "o"),
                              "--city", "../evil", "--dry-run"]) == 2
    assert not (tmp_path / "o").exists()


def test_the_dry_run_function_returns_the_plan_without_writing(city, tmp_path):
    out = tmp_path / "a" / "b"
    plan = ledger_apply.dry_run(CHECK, JOURNAL, city, out, town=fx.TOWN)
    assert plan["applied"] == 1 and plan["observed"] == 1 and plan["held"] == 0 and plan["dry_run"] is True
    assert plan["changes"][0]["record_id"] == "P1" and plan["changes"][0]["output_file"] == "assets/poles.geojson"
    assert not (tmp_path / "a").exists()


def test_the_dry_run_function_raises_the_same_refusals_as_apply(city, tmp_path):
    with pytest.raises(ledger_apply.LedgerError):
        ledger_apply.dry_run(CHECK, JOURNAL, city, tmp_path / "out", town=None)  # a change needs the city file
    assert not (tmp_path / "out").exists()
