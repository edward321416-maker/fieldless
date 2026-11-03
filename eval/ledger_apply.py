"""Apply a reviewer's validated ledger changes to a NEW copy of a city's GeoJSON folder.

    python -m eval.ledger_apply --check ledger-review-scan30-source.json --journal ledger-review-scan30-journal.json \\
        --input-dir data/seattle/ballard --output-dir data/seattle-ballard-revised --city seattle-ballard

--check is the Asset Check of the survey (the file eval.asset_check wrote, or the source list the app downloaded),
--journal the review package the app downloaded, --input-dir the city's own GeoJSON layers the check was made from,
--output-dir a folder that does not exist yet, and --city the name of a city file shipped in eval/cities. Exit 0: the
folder was written. Exit 2: something was refused and nothing was written.

What a change may touch comes from the city file (--city), never from the downloaded check: the item, its layer, its id
key and the one yes/no field with its two raw values. The check only says what a reviewer was offered, so a check edited
to offer another field or other values is refused, and so is a package with changes and no --city. A package of
observations and holds alone needs no city file. The city file is read from the repository by name; a journal or a check
cannot bring its own.

Every change is checked before anything is written: the journal against the check (eval.ledger_review), the city file,
then each change against the layer file itself: a safe layer name found once inside the input folder, the record id found exactly
once, all of its original properties hashing to what the export saw, and its current value being the one the export
recorded. The copy is built in a staging folder next to the output, verified, and only then renamed into place. The
input folder is never written; an existing output is never overwritten. A layer with a change is written again as compact
JSON (every record, key order, number and character kept; only the white space differs), so compare layers by record, as
audit.json lists them, not by line.

The output holds the *.geojson layers (untouched ones byte for byte) and `_ledger_review/` with audit.json, the changes
and observations CSV files and the journal. Nothing here writes to a city's system: a person at the city decides what
becomes of the copy, and the reviewer names in the audit are the reviewers' own statements.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from eval import ledger_review
from eval.city_check import load_city
from eval.ledger_review import LedgerError

REVIEW_DIR = "_ledger_review"
CITY_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")  # the name of a file of eval/cities, nothing that is a path
NOTICE = ("Prepared copy only. No city system was written and the original folder is unchanged. Reviewer names are "
          "self-asserted, not authenticated municipal approvals.")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def layer_paths(town: dict) -> dict[str, str | None]:
    """A city file's layers by file name, with the path each has under the city's folder. A name two items share is
    None: it names no one file, so a change to it is refused."""
    out: dict[str, str | None] = {}
    for cfg in town["items"].values():
        rel = cfg.get("layer")
        name = ledger_review.layer_basename(rel)
        if name:
            out[name] = None if name in out and out[name] != rel else rel
    return out


def _layer_files(root: Path, paths: dict | None) -> list[Path]:
    """The layers that are copied: every *.geojson of the folder, or only the ones a city file names."""
    if paths is None:
        return sorted(p for p in root.rglob("*.geojson") if p.is_file())
    return sorted(root / rel for rel in {r for r in paths.values() if r} if (root / rel).is_file())


def _find_layer(root: Path, name: str, paths: dict | None, problems: list[str]) -> Path | None:
    if ledger_review.layer_basename(name) != name:
        problems.append(f"unsafe layer name {name!r}: a layer is a plain *.geojson file name")
        return None
    if paths is not None:
        rel = paths.get(name)
        if name not in paths:
            problems.append(f"layer {name} is not one of the city's layers")
        elif rel is None:
            problems.append(f"layer {name} is named by more than one item of the city file")
        elif PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts or not (root / rel).is_file()                 or not _inside((root / rel).resolve(), root):
            problems.append(f"layer {name}: {rel!r} is not a file inside the input folder")
        else:
            return root / rel
        return None
    found = [p for p in root.rglob("*") if p.name == name and (p.is_file() or p.is_symlink())]
    if not found:
        problems.append(f"no layer file named {name} in the input folder")
        return None
    if len(found) > 1:
        problems.append(f"more than one layer file named {name} in the input folder")
        return None
    if not _inside(found[0].resolve(), root):
        problems.append(f"layer file {name} resolves outside the input folder")
        return None
    return found[0]


def _read_layer(path: Path, name: str, problems: list[str]) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_no_constant)
    except (ValueError, OSError) as err:
        problems.append(f"layer {name} is not valid JSON ({err})")
        return None
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection" or not isinstance(doc.get("features"), list):
        problems.append(f"layer {name} is not a FeatureCollection")
        return None
    return doc


def _locate(doc: dict, item: dict, name: str, problems: list[str]) -> int | None:
    """The index of the one feature whose record id is the decision's, with the properties the export saw."""
    key, wanted = item["record_key"], item["record_id"]
    hits = [n for n, f in enumerate(doc["features"])
            if isinstance(f, dict) and isinstance(f.get("properties"), dict) and f["properties"].get(key) is not None
            and ledger_review.record_text(f["properties"][key]) == wanted]
    if len(hits) != 1:
        problems.append(f"record {wanted} ({key}) matches {len(hits)} records of {name} (exactly 1 is required)")
        return None
    feature = doc["features"][hits[0]]
    if ledger_review.properties_sha256(feature["properties"]) != item["properties_sha256"]:
        problems.append(f"the properties changed since the export: record {wanted} of {name}")
        return None
    if item.get("position_sha256") and ledger_review.position_sha256(feature.get("geometry")) != item["position_sha256"]:
        problems.append(f"the location changed since the export: record {wanted} of {name}")
        return None
    return hits[0]


def _plan(reviewed: dict, root: Path, paths: dict | None, problems: list[str]) -> tuple[dict, list[dict]]:
    """Check every change and observation against the layer files, in memory. Returns the layers to rewrite
    ({relative path: new document}) and the changes with where they landed."""
    docs: dict[str, tuple[Path, dict]] = {}
    rewritten: dict[str, dict] = {}
    landed = []
    for item in [*reviewed["changes"], *reviewed["observations"]]:
        name = item["layer"]
        if name not in docs:
            path = _find_layer(root, name, paths, problems)
            doc = _read_layer(path, name, problems) if path else None
            if doc is None:
                docs[name] = None
                continue
            docs[name] = (path, doc)
        if docs[name] is None:
            continue
        path, doc = docs[name]
        index = _locate(doc, item, name, problems)
        if index is None or item["decision"] != "change":
            continue
        props = doc["features"][index]["properties"]
        field = item["field"]
        if field not in props or ledger_review.canonical_json(props[field]) != ledger_review.canonical_json(item["before"]):
            problems.append(f"the value before the change differs from the export: {field} of record "
                            f"{item['record_id']} in {name} is now {props.get(field)!r}, the export saw {item['before']!r}")
            continue
        landed.append({**item, "feature_index": index, "output_file": path.relative_to(root).as_posix()})
    if problems:
        return {}, []
    for change in landed:
        path, doc = docs[change["layer"]]
        rel = change["output_file"]
        new_doc = rewritten.setdefault(rel, json.loads(json.dumps(doc)))
        new_doc["features"][change["feature_index"]]["properties"][change["field"]] = change["after"]
    return rewritten, landed


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def apply(check: dict, journal: dict, input_dir, output_dir, now: datetime | None = None, town: dict | None = None) -> dict:
    """Validate everything, then write the revised copy. Returns the audit. Raises LedgerError (nothing written).
    town: the trusted city file, as eval.city_check.load_city returns it for a file of eval/cities. A package with changes
    needs it; its layers also tell same-named layers of different folders apart and are the only ones copied. Without
    it (observations and holds alone) every *.geojson under the folder is a candidate. This function cannot tell
    whether the caller's mapping is the repository's: only the CLI loads it by name."""
    root, out = Path(input_dir).resolve(), Path(output_dir).resolve()
    problems: list[str] = []
    if not root.is_dir():
        problems.append("the input folder does not exist")
    if os.path.lexists(out):
        problems.append(f"the output {out.name!r} already exists and is never overwritten")
    if _inside(out, root) or _inside(root, out):
        problems.append("the output must be a new folder apart from the input folder, not inside or around it")
    if isinstance(journal, dict) and journal.get("decisions") == []:
        problems.append("nothing to apply: the journal has no decisions")
    try:
        reviewed = ledger_review.review(check, journal)
    except LedgerError as err:
        raise LedgerError(problems + err.problems) from None
    if problems:
        raise LedgerError(problems)
    items = [*reviewed["changes"], *reviewed["observations"]]
    if town is not None:
        problems += ledger_review.city_problems(items, town)
    elif reviewed["changes"]:
        problems.append("a package with changes needs the city file (--city NAME, a file of eval/cities): the downloaded "
                        "check's own list of editable fields is not trusted")
    paths = layer_paths(town) if isinstance(town, dict) and isinstance(town.get("items"), dict) else None
    rewritten, landed = _plan(reviewed, root, paths, problems)
    if problems:
        raise LedgerError(problems)

    inputs = {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in _layer_files(root, paths)
              if _inside(p.resolve(), root)}
    stage = out.parent / f".{out.name}.staging-{uuid.uuid4().hex[:8]}"
    try:
        stage.mkdir(parents=True)
        files = {}
        for rel, digest in inputs.items():
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel in rewritten:
                data = _dump(rewritten[rel])
                target.write_bytes(data)
                files[rel] = {"input_sha256": digest, "output_sha256": _sha(data), "changed": True}
            else:
                shutil.copyfile(root / rel, target)
                files[rel] = {"input_sha256": digest, "output_sha256": digest, "changed": False}
        when = (now or datetime.now(timezone.utc)).isoformat()
        audit = {"schema_version": ledger_review.SCHEMA_VERSION, "tool": "eval.ledger_apply", "applied_at": when,
                 "scan_id": journal["scan_id"], "journal_sha256": ledger_review.sha256_of(journal),
                 "applied": len(landed), "observed": len(reviewed["observations"]), "held": len(reviewed["holds"]),
                 "changes": landed, "observations": reviewed["observations"], "holds": reviewed["holds"],
                 "whitelist": {"checked_against_city_file": town is not None, "city": (town or {}).get("city")},
                 "files": files, "notice": NOTICE}
        review_dir = stage / REVIEW_DIR
        review_dir.mkdir()
        (review_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (review_dir / "journal.json").write_text(json.dumps(journal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for name, columns, rows in (("changes.csv", ledger_review.CHANGE_COLUMNS, reviewed["changes"]),
                                    ("observations.csv", ledger_review.OBSERVATION_COLUMNS, reviewed["observations"])):
            (review_dir / name).write_bytes(ledger_review.to_csv(columns, rows).encode("utf-8"))  # as the app's files: no BOM
        _verify(stage, root, inputs, rewritten)
        out.parent.mkdir(parents=True, exist_ok=True)
        os.rename(stage, out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return audit


def _verify(stage: Path, root: Path, inputs: dict, rewritten: dict) -> None:
    """The staged copy is what was planned, and the input folder is as it was."""
    for rel, digest in inputs.items():
        if _sha((root / rel).read_bytes()) != digest:
            raise LedgerError([f"the input layer {rel} changed while the copy was made; nothing was written"])
        got = json.loads((stage / rel).read_text(encoding="utf-8"))
        if rel in rewritten:
            if got != rewritten[rel]:
                raise LedgerError([f"the staged layer {rel} does not match the planned change; nothing was written"])
        elif (stage / rel).read_bytes() != (root / rel).read_bytes():
            raise LedgerError([f"the staged layer {rel} is not a byte-for-byte copy; nothing was written"])


def _load(path: str, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as err:
        raise LedgerError([f"cannot read the {what} {path!r}: {err}"]) from None


def trusted_city(name: str) -> dict:
    """The shipped city file of that name (eval/cities/<name>.toml), or LedgerError."""
    if not CITY_NAME.fullmatch(name):
        raise LedgerError([f"--city takes the name of a city file shipped in eval/cities, such as seattle-ballard, "
                           f"not {name!r}"])
    try:
        return load_city(name)
    except (OSError, ValueError) as err:
        raise LedgerError([f"the city file {name!r} could not be loaded from eval/cities ({err})"]) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", required=True, help="the survey's Asset Check JSON")
    parser.add_argument("--journal", required=True, help="the review package JSON the app downloaded")
    parser.add_argument("--input-dir", required=True, help="the city's GeoJSON folder, left untouched")
    parser.add_argument("--output-dir", required=True, help="a new folder for the revised copy")
    parser.add_argument("--city", help="the trusted city file, a name in eval/cities without .toml (for example "
                                       "seattle-ballard). Required when the package has changes: it, not the check, says "
                                       "which field and values may change. Its layer paths tell same-named layers apart "
                                       "and only its layers are copied")
    args = parser.parse_args(argv)
    try:
        town = trusted_city(args.city) if args.city is not None else None
        audit = apply(_load(args.check, "check"), _load(args.journal, "journal"), args.input_dir, args.output_dir, town=town)
    except LedgerError as err:
        print("REFUSED, nothing was written:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    n = audit["applied"]
    print(f"{n} change{'s' if n != 1 else ''} applied, {audit['observed']} observations, {audit['held']} on hold; "
          f"wrote {args.output_dir} (audit: {REVIEW_DIR}/audit.json). {NOTICE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
