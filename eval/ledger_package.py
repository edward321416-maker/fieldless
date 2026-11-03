"""The portable ledger review package: one UTF-8 JSON file holding what a reviewer prepared in the app.

    {"schema_version": 1, "kind": "fieldless-ledger-review", "scan_id": N,
     "source": <the whole Asset Check>, "journal": <the LedgerJournal>,
     "manifest": {"notice": ..., "reviewer_assertion": ...},
     "files": {"changes_csv": <text>, "observations_csv": <text>}}

The source and the journal are the only inputs. The two CSV strings are a convenience for a person who opens the file; they
are rebuilt from the source and journal and must be exactly what is rebuilt, and the manifest must say exactly what it says
here. A package that differs is refused, never trusted and never repaired. Nothing in a package is signed: it is not proof
of who made it, and the reviewer names in it are self-asserted, not an authenticated municipal approval.

    python -m eval.ledger_apply --package FILE --input-dir DIR --output-dir NEW_DIR --city NAME [--dry-run]

web/src/ledgerPackage.ts writes and reads the same format (tests/fixtures/ledger_interop/review-package.json is read by both).
"""
import copy
import json
from pathlib import Path

from eval import ledger_review
from eval.ledger_review import LedgerError

KIND = "fieldless-ledger-review"
SCHEMA_VERSION = 1
MANIFEST = {"notice": "Prepared only; not applied to an official ledger.",
            "reviewer_assertion": "Self-asserted; not authenticated municipal approval."}
FILE_KEYS = ("changes_csv", "observations_csv")
REQUIRED = ("schema_version", "kind", "scan_id", "source", "journal")
OPTIONAL = ("manifest", "files")
MAX_BYTES = 32 * 1024 * 1024  # a package larger than this is not a review of one scan


def _csv_files(check: dict, journal: dict) -> dict[str, str]:
    return {"changes_csv": ledger_review.changes_csv(check, journal),
            "observations_csv": ledger_review.observations_csv(check, journal)}


def build_package(check: dict, journal: dict) -> dict:
    """The package of a check and a journal, or LedgerError when the journal is not valid for the check."""
    ledger_review.review(check, journal)
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "scan_id": check["scan_id"],
            "source": copy.deepcopy(check), "journal": copy.deepcopy(journal),
            "manifest": dict(MANIFEST), "files": _csv_files(check, journal)}


def package_text(package: dict) -> str:
    """The package as the file a person keeps: UTF-8 JSON (no byte-order mark), two-space indent, a final newline."""
    return json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _whole_number(value) -> bool:
    return type(value) is int


def read_package(value) -> tuple[dict, dict]:
    """(check, journal) of a parsed package, or LedgerError listing every problem found. The journal is checked against the
    source by eval.ledger_review (which also recomputes every review key of the source), and the manifest and CSV files,
    when present, must be what is rebuilt from them."""
    if not isinstance(value, dict):
        raise LedgerError(["package is not an object"])
    problems = [f"package: unknown key {k!r}" for k in sorted(set(value) - set(REQUIRED) - set(OPTIONAL), key=str)]
    problems += [f"package: missing key {k!r}" for k in REQUIRED if k not in value]
    if "kind" in value and value["kind"] != KIND:
        problems.append(f"package: kind must be {KIND!r}")
    if "schema_version" in value and (not _whole_number(value["schema_version"]) or value["schema_version"] != SCHEMA_VERSION):
        problems.append(f"package: schema_version must be {SCHEMA_VERSION}")
    source, journal = value.get("source"), value.get("journal")
    if "source" in value and (not isinstance(source, dict) or not isinstance(source.get("rows"), list)):
        problems.append("package: source is not an Asset Check with rows")
    if "journal" in value and not isinstance(journal, dict):
        problems.append("package: journal is not an object")
    if "scan_id" in value:
        scan = value["scan_id"]
        if not _whole_number(scan):
            problems.append("package: scan_id must be a whole number")
        else:
            for name, part in (("source", source), ("journal", journal)):
                if isinstance(part, dict) and part.get("scan_id") != scan:
                    problems.append(f"package: scan_id {scan} is not the {name}'s scan_id {part.get('scan_id')!r}")
    if "manifest" in value and value["manifest"] != MANIFEST:
        problems.append("package: manifest is not the one this format states "
                        "(a manifest cannot say the review is approved or applied)")
    files = value.get("files")
    if "files" in value:
        if not isinstance(files, dict):
            problems.append("package: files must be an object with changes_csv and observations_csv")
        else:
            problems += [f"package: files has unknown key {k!r}" for k in sorted(set(files) - set(FILE_KEYS), key=str)]
            for name in FILE_KEYS:
                if not isinstance(files.get(name), str):
                    problems.append(f"package: files.{name} must be text")
    if problems:
        raise LedgerError(problems)
    ledger_review.review(source, journal)  # raises LedgerError listing the journal and source problems
    if isinstance(files, dict):
        rebuilt = _csv_files(source, journal)
        problems += [f"package: files.{name} is not the CSV rebuilt from the source and journal (it was edited or made elsewhere); "
                     f"the file is not trusted" for name in FILE_KEYS if files[name] != rebuilt[name]]
    if problems:
        raise LedgerError(problems)
    return source, journal


def _no_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError(f"duplicate key {dup[0]!r}")
    return dict(pairs)


def load_package(path) -> tuple[dict, dict]:
    """Read a package file and return (check, journal), or raise LedgerError. Nothing is written."""
    target = Path(path)
    try:
        size = target.stat().st_size
        if size > MAX_BYTES:
            raise LedgerError([f"the package {str(path)!r} is too large ({size} bytes, at most {MAX_BYTES})"])
        raw = target.read_bytes()
    except OSError as err:
        raise LedgerError([f"cannot read the package {str(path)!r}: {err}"]) from None
    try:
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=_no_constant, object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as err:
        raise LedgerError([f"the package {str(path)!r} is not valid JSON ({err})"]) from None
    return read_package(value)
