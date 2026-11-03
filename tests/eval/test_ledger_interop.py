"""The two CSV files the app writes and the ones Python writes are the same bytes: tests/fixtures/ledger_interop holds the app's
JSON form of a check, a journal and the CSV bytes; web/src/ledgerInterop.test.ts must reproduce the same bytes in TypeScript."""
import json
from pathlib import Path

from eval import ledger_review

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ledger_interop"


def load(name):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def test_python_writes_the_fixture_csv_bytes():
    check, journal = load("check.json"), load("journal.json")
    assert ledger_review.changes_csv(check, journal).encode("utf-8") == (FIXTURE / "changes.csv").read_bytes()
    assert ledger_review.observations_csv(check, journal).encode("utf-8") == (FIXTURE / "observations.csv").read_bytes()


def test_the_fixture_files_are_not_empty_shells():
    for name in ("changes.csv", "observations.csv"):
        data = (FIXTURE / name).read_bytes()
        assert data.startswith(b"\xef\xbb\xbf") and not data.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
        assert data.count(b"\r\n") >= 2  # the header and at least one row
    assert "'=김검토".encode("utf-8") in (FIXTURE / "changes.csv").read_bytes()
    assert "'@이름".encode("utf-8") in (FIXTURE / "observations.csv").read_bytes()
    journal = load("journal.json")
    assert [d["decision"] for d in journal["decisions"]] == ["change", "observe", "observe", "hold"]
    assert journal["decisions"][0]["reviewer"] == '=김검토, "Zoë"'  # the raw journal keeps what was typed
