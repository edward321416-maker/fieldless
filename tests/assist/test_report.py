import json
import re
from datetime import datetime, timezone

import pytest

from assist import export, report
from pipeline import config
from server import views
from tests.assist import survey_scan
from tests.assist.survey_scan import STANDARD
from tests.pipeline.fakes import FakeApi

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)
GOOD = {"summary": "Most curb ramps found have a detectable warning; one without it and a few findings still await "
                   "review.",
        "items": [{"item": "curb_ramp_warning", "paragraph": "Most ramps showed a warning surface."},
                  {"item": "streetlight_pole", "paragraph": "One pole was unclear from its photo."}],
        "priorities": [{"item": "curb_ramp_warning", "reason": "A possible missing warning awaits review."},
                       {"item": "streetlight_pole", "reason": "One pole was unclear from the photo."}]}
NOTES = {"summary": GOOD["summary"],
         "items": {"curb_ramp_warning": "Most ramps showed a warning surface.",
                   "streetlight_pole": "One pole was unclear from its photo."},
         "priorities": GOOD["priorities"]}


def made(area="Capitol Hill"):
    data = survey_scan.load()
    return data, report.summary(data, STANDARD, area=area, now=NOW)


def test_summary_counts_every_item_the_same_way():
    data, s = made()

    items = {i["item"]: i for i in s["items"]}

    assert list(items) == ["curb_ramp_warning", "streetlight_pole", "litter_can", "obstruction", "entrance"]
    assert items["curb_ramp_warning"] == {
        "item": "curb_ramp_warning", "title": "curb ramp", "kind": "check",
        "question": "Does it have a detectable warning?", "found": 3, "photos": 3,
        "by_answer": {"no": 2, "yes": 1},
        "by_label": {"Curb ramp without detectable warning": 2, "Curb ramp with detectable warning": 1},
        "by_review": {"verified": 1, "auto": 1, "needs_review": 1, "rejected": 1},
        "defects_confirmed": 1, "defects_unconfirmed": 1, "dropped": 0, "city": None}
    pole, can = items["streetlight_pole"], items["litter_can"]
    assert (pole["kind"], pole["found"], pole["by_answer"], pole["defects_unconfirmed"]) == (
        "inventory", 2, {"yes": 1, "unclear": 1}, 0)
    assert (can["found"], can["by_answer"], can["by_review"]) == (0, {}, {})  # in the standard, not found
    assert (items["obstruction"]["kind"], items["obstruction"]["by_answer"]) == ("other", {"traffic cone": 1})
    assert (items["entrance"]["kind"], items["entrance"]["by_answer"]) == ("entrance", {"2": 1})
    assert s["totals"] == {"found": 7, "defects_confirmed": 1, "defects_unconfirmed": 1, "needs_review": 2,
                           "field_check": 0, "unclear": 1, "rejected": 1, "dropped": 0, "dropped_audited": 0}


def test_summary_takes_km_and_cost_by_the_apps_rules_and_dates_from_the_photos():
    data, s = made()

    km = views.surveyed_km(data["frames"])

    assert s["km"] == km and km > 0 and s["usd_per_km"] == round(0.4567 / km, 2) and s["cost_usd"] == 0.46
    assert (s["processing_seconds"], s["review_seconds"], s["photos"]) == (95, 30, 6)
    assert s["photo_dates"] == {"from": "2021-03-01", "to": "2025-06-05"} and s["old_photos"] == 1
    assert [(d["item"], d["photo_id"], d["title"]) for d in s["defects"]] == [("curb_ramp_warning", "101", "curb ramp")]
    assert (s["area"], s["standard"]) == ("Capitol Hill", "US")


def test_markdown_lists_confirmed_defects_and_attributes_every_photo():
    _, s = made()

    text = report.to_markdown(s)

    assert text.startswith("# Street survey — Capitol Hill\n") and "SAM 3.1" in text and "Muse Spark" in text
    assert ("| curb ramp (curb_ramp_warning) | check | 3 | 2 × Curb ramp without detectable warning; "
            "1 × Curb ramp with detectable warning | 1 / 1 / 0 / 1 | 1 / 1 |") in text
    assert "| streetlight pole (streetlight_pole) | inventory | 2 |" in text
    assert "| litter can (litter_can) | inventory | 0 | - | 0 / 0 / 0 / 0 | - |" in text
    assert "| shop entrance (entrance) | entrance | 1 | 1 × 2 steps | 1 / 0 / 0 / 0 | - |" in text
    photo_lines = [line for line in text.splitlines() if "pKey=" in line]
    assert len(photo_lines) == 1 and "pKey=101" in photo_lines[0]  # the confirmed defect only
    for line in photo_lines:
        assert "by alice" in line and "CC BY-SA 4.0" in line
    assert "Possible defects awaiting a person's confirmation: 1." in text
    assert "Older than 3 years: 1 of 6" in text and "Summary (written by Muse Spark" not in text


def test_notes_appear_as_muse_spark_paragraph_in_the_markdown():
    _, s = made()

    text = report.to_markdown(s, NOTES)

    assert "## Summary (written by Muse Spark from the counts below)" in text and NOTES["summary"] in text
    assert "- curb_ramp_warning: A possible missing warning awaits review." in text


def test_narrate_keeps_a_paragraph_written_from_the_summary():
    _, s = made()
    api = FakeApi(spark=lambda name, parts: GOOD)

    notes = report.narrate(api, s)

    assert notes == NOTES and api.calls == [("spark", "survey_report_text")]


@pytest.mark.parametrize("answer, kept, problem", [
    ({**GOOD, "priorities": [{"item": "bus_shelter", "reason": "Shelters need checking."}]},
     lambda n: n["priorities"] == [] and n["summary"] == GOOD["summary"], "unknown item 'bus_shelter'"),
    ({**GOOD, "summary": "41 curb ramps lack a detectable warning."},
     lambda n: n["summary"] == "" and len(n["items"]) == 2, "summary: a number"),
    ({**GOOD, "items": [{"item": "curb_ramp_warning", "paragraph": "Fix 9 ramps first."}]},
     lambda n: n["items"] == {}, "curb_ramp_warning: a number"),
    ({**GOOD, "summary": "Two ramps are a violation of the rules."},
     lambda n: n["summary"] == "", "summary: the word 'violation'"),
])
def test_narrate_drops_only_the_paragraph_with_an_unknown_item_a_number_or_a_banned_word(answer, kept, problem):
    _, s = made()

    notes = report.narrate(FakeApi(spark=lambda name, parts: answer), s)

    assert kept(notes) and problem in " ".join(notes["dropped"])


def test_narrate_sends_no_photo_ids_names_or_area():
    _, s = made(area="Capitol Hill")
    seen = []

    def spark(name, parts):
        seen.append(parts[0]["text"])
        return GOOD

    report.narrate(FakeApi(spark=spark), s)

    assert "pKey" not in seen[0] and "alice" not in seen[0] and "Capitol Hill" not in seen[0]
    assert "never write a digit" in seen[0] and "compares" in seen[0]


def test_cli_writes_the_report_and_export_files(capsys):
    scan_id = survey_scan.build()

    paths = report.main(["--run", "r", "--scan", str(scan_id), "--standard", "US"])

    out = config.run_dir("r") / f"report_scan{scan_id}"
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(["report.md", "report.html", f"survey_scan{scan_id}.geojson",
                            *(f"survey_scan{scan_id}_{item}.csv"
                              for item in ("curb_ramp_warning", "streetlight_pole", "obstruction", "entrance"))])
    assert paths["md"] == out / "report.md"
    assert (out / "report.md").read_text(encoding="utf-8").startswith(f"# Street survey — scan {scan_id}\n")
    assert len(json.loads((out / f"survey_scan{scan_id}.geojson").read_text(encoding="utf-8"))["features"]) == 7
    printed = capsys.readouterr().out
    assert f"md: {out / 'report.md'}" in printed and f"csv entrance: {out}" in printed


def test_cli_narrate_and_out(tmp_path, monkeypatch, capsys):
    scan_id = survey_scan.build()
    made_with = []

    def fake_meta_api(log_path, meter):
        made_with.append(meter.cap_usd)
        return FakeApi(spark=lambda name, parts: GOOD)

    monkeypatch.setattr(report, "MetaApi", fake_meta_api)

    report.main(["--run", "r", "--scan", str(scan_id), "--narrate", "--area", "Capitol Hill",
                 "--out", str(tmp_path / "hand-over")])

    text = (tmp_path / "hand-over" / "report.md").read_text(encoding="utf-8")
    assert made_with == [report.NARRATE_CAP_USD] and GOOD["summary"] in text
    assert text.startswith("# Street survey — Capitol Hill\n") and "model spend: $0.00" in capsys.readouterr().out


def test_a_row_a_person_could_not_tell_stays_and_is_listed_for_a_site_visit():
    data = survey_scan.load()
    ramp = next(r for r in data["issues"] if r["review_status"] == "needs_review")
    ramp["review_status"] = "field_check"

    s = report.summary(data, STANDARD, area="Capitol Hill", now=NOW)
    text = report.to_markdown(s)

    assert s["totals"]["field_check"] == 1 and s["totals"]["needs_review"] == 1
    assert "A person could not tell from the photo either: 1. These need a site visit" in text
    assert "Confirmed / to review / check on site / rejected" in text


def test_finds_the_models_set_aside_are_counted_and_listed_with_their_spot_check():
    data = survey_scan.load()
    data["dropped"] = [
        {"issue_type": "curb_ramp_warning", "frame_idx": 0, "lat": 47.6, "lng": -122.3,
         "captured_at": "2025-06-01T10:00:00Z", "audited": 1,
         "model_outputs": {"check": {"visible": False, "answer": "unclear", "note": "a driveway"}}},
        {"issue_type": "curb_ramp_warning", "frame_idx": 5, "lat": 47.6005, "lng": -122.3,
         "captured_at": "2021-03-01T10:00:00Z", "audited": 0, "model_outputs": {"check": {"visible": False}}}]

    s = report.summary(data, STANDARD, area="Capitol Hill", now=NOW)
    text = report.to_markdown(s)

    ramp = next(i for i in s["items"] if i["item"] == "curb_ramp_warning")
    assert ramp["dropped"] == 2 and (s["totals"]["dropped"], s["totals"]["dropped_audited"]) == (2, 1)
    assert "Set aside by the models as not the target: 2" in text and "1 of them went to a person" in text


CITY = {"scan_id": 1, "city": "Seattle", "measured_at": "2026-10-28", "reviewed": True, "method": "m",
        "method_url": "https://example.org/city.py",
        "items": {"streetlight_pole": {"layer": "City poles", "found": 1, "covered": 3, "matched_ids": [5],
                                       "rejected_at_city_point": []}}}


def test_a_city_record_check_adds_its_numbers_to_the_items_it_covers():
    data = survey_scan.load()
    data["city_check"] = CITY

    s = report.summary(data, STANDARD, area="Capitol Hill", now=NOW)
    text = report.to_markdown(s)

    items = {i["item"]: i for i in s["items"]}
    assert items["streetlight_pole"]["city"] == {"found": 1, "covered": 3, "not_seen": 2, "not_in_city": 1}
    assert items["curb_ramp_warning"]["city"] is None
    assert "| City record |" in text and "1 of 3 (Seattle)" in text and "Measured 2026-10-28" in text
    assert "City record" not in report.to_markdown(report.summary(survey_scan.load(), STANDARD, now=NOW))


def test_summary_lists_every_finding_in_the_ledger_with_its_box_note_and_photo():
    data, s = made()

    ledger = s["ledger"]

    assert len(ledger) == s["totals"]["found"] == 7  # every finding a person did not reject
    first = ledger[0]
    assert (first["item"], first["evidence"], first["creator"], first["license"]) == (
        "curb_ramp_warning", f"scan{data['scan']['id']}_issue1.jpg", "alice", "CC BY-SA 4.0")
    assert [e["item"] for e in ledger][-1] == "entrance"  # items in the summary's order
    assert all(e["lat"] is not None for e in ledger)


def test_summary_sorts_actions_into_repairs_site_visits_and_city_updates():
    data = survey_scan.load()
    data["city_check"] = CITY
    s = report.summary(data, STANDARD, area="Capitol Hill", now=NOW)

    repairs, visits, city = s["actions"]["repairs"], s["actions"]["site_visits"], s["actions"]["city"]

    assert [(e["item"], e["review_status"]) for e in repairs] == [("curb_ramp_warning", "verified"),
                                                                  ("curb_ramp_warning", "needs_review")]
    assert [(e["item"], e["answer"]) for e in visits] == [("streetlight_pole", "unclear")]
    assert city == [{"item": "streetlight_pole", "title": "streetlight pole", "found": 1, "covered": 3,
                     "not_seen": 2, "not_in_city": 1}]


def test_summary_keeps_each_walk_as_a_line_and_dates_the_report():
    _, s = made()

    assert len(s["walks"]) == 1 and len(s["walks"][0]) == 6 and s["made"] == "2026-10-28"


def test_make_text_saves_spark_sentences_for_the_scan():
    from pipeline import db
    scan_id = survey_scan.build()
    conn = db.connect(config.run_dir("r") / "threshold.db")

    notes = report.make_text(conn, config.run_dir("r"), scan_id, FakeApi(spark=lambda name, parts: GOOD))

    assert notes == NOTES and db.report_text(conn, scan_id) == NOTES


def test_cli_save_keeps_the_sentences_and_adds_the_spend_to_the_scan(monkeypatch, capsys):
    from pipeline import db
    scan_id = survey_scan.build()

    class Spending(FakeApi):
        def spark_json(self, parts, schema_name, schema, estimate_usd=0.0):
            self.meter.spent_usd += 0.03
            return super().spark_json(parts, schema_name, schema)

    monkeypatch.setattr(report, "MetaApi", lambda log_path, meter: Spending(spark=lambda name, parts: GOOD))

    report.main(["--run", "r", "--scan", str(scan_id), "--narrate", "--save"])

    conn = db.connect(config.run_dir("r") / "threshold.db")
    assert db.report_text(conn, scan_id) == NOTES
    assert db.get_scan(conn, scan_id)["cost_usd"] == pytest.approx(0.4567 + 0.03)
    page = (config.run_dir("r") / f"report_scan{scan_id}" / "report.html").read_text(encoding="utf-8")
    assert NOTES["summary"] in page


def test_cli_without_narrate_uses_the_saved_sentences():
    from pipeline import db
    scan_id = survey_scan.build()
    conn = db.connect(config.run_dir("r") / "threshold.db")
    db.set_report_text(conn, scan_id, NOTES, "muse-spark-1.3")
    conn.close()

    report.main(["--run", "r", "--scan", str(scan_id)])

    page = (config.run_dir("r") / f"report_scan{scan_id}" / "report.html").read_text(encoding="utf-8")
    assert NOTES["summary"] in page


def test_save_needs_narrate():
    with pytest.raises(SystemExit):
        report.main(["--run", "r", "--scan", "1", "--save"])
