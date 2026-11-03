"""The ledger source survives the app's JSON, so a journal made from what the app showed can be checked later."""
import json

from assist import asset_check as page
from eval import ledger_review
from tests.eval import ledger_fixture as fx


def test_the_app_form_keeps_every_ledger_source_untouched_and_names_its_scan():
    check = fx.make_check()
    app = json.loads(json.dumps(page.for_app(check)))
    assert app["scan_id"] == fx.SCAN
    before = {r["city_record"]: r["ledger_source"] for r in check["rows"] if "ledger_source" in r}
    after = {r["city_record"]: r["ledger_source"] for r in app["rows"] if "ledger_source" in r}
    assert before and before == after


def test_a_journal_made_from_the_app_form_checks_against_the_saved_file_and_the_app_form_alike():
    check = fx.make_check()
    app = json.loads(json.dumps(page.for_app(check)))
    journal = fx.journal(fx.decision(app, "P1"))
    assert ledger_review.validate_journal(check, journal) == ledger_review.validate_journal(app, journal)


def test_the_summary_for_the_scan_list_does_not_carry_the_rows_or_their_sources():
    assert "rows" not in page.summary(fx.make_check())
