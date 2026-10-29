import html
import re
from datetime import datetime, timezone

from assist import report, report_doc, style
from server import views
from tests.assist import survey_scan
from tests.assist.survey_scan import STANDARD
from tests.assist.test_report import CITY, NOTES

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def made(city=False):
    data = survey_scan.load()
    if city:
        data["city_check"] = CITY
    return data, report.summary(data, STANDARD, area="Pike & Pine", now=NOW)


def sections(page: str) -> list[str]:
    return re.findall(r'<section class="sec" id="([a-z-]+)"', page)


def test_the_document_has_a_cover_contents_six_sections_and_an_appendix_in_order():
    _, s = made()

    page = report_doc.document(s)

    assert page.startswith("<!doctype html>") and "<script" not in page and "<link" not in page
    assert '<section class="cover"' in page and "Street Survey Report" in page and "Pike &amp; Pine" in page
    assert sections(page) == ["summary", "scope", "findings", "actions", "ledger", "limits", "appendix"]
    assert "@page" in page and "break-before: page" in page and "break-inside: avoid" in page


def test_no_savings_comparison_and_no_person_confirms_wording():
    _, s = made()

    page = report_doc.document(s, NOTES).lower()

    for word in ("cheaper", "saves", "saving", "compared with", "per mile", "$106", "a person confirms"):
        assert word not in page


def test_every_item_gets_the_same_findings_block_and_its_question():
    _, s = made(city=True)

    page = report_doc.document(s)

    blocks = re.findall(r'<article class="item" id="item-([a-z_]+)"', page)
    assert blocks == [i["item"] for i in s["items"]]  # every item, found or not, in the summary's order
    assert "Does it have a detectable warning?" in page and "1 of 3 in the city&#x27;s list" in page


def test_spark_text_is_used_where_kept_and_code_sentences_fill_in_the_rest():
    _, s = made()

    with_notes, without = report_doc.document(s, NOTES), report_doc.document(s, None)

    assert NOTES["summary"] in with_notes and "Written by Muse Spark" in with_notes
    assert NOTES["items"]["curb_ramp_warning"] in with_notes
    assert html.escape(report_doc.summary_text(s)) in without and "Written by Muse Spark" not in without
    litter = next(i for i in s["items"] if i["item"] == "litter_can")
    assert html.escape(report_doc.item_text(litter)) in with_notes  # Spark wrote nothing for it
    assert "A possible missing warning awaits review." in with_notes  # a priority


def test_actions_list_repairs_site_visits_and_city_updates():
    _, s = made(city=True)

    page = report_doc.document(s)

    actions = page.split('id="actions"')[1].split('<section class="sec"')[0]
    assert actions.count('class="repair"') == 2 and actions.count('class="visit"') == 1
    assert "In the photos, not in the city&#x27;s list" in actions and "<td>2</td>" in actions


def test_the_photo_ledger_shows_each_photo_with_its_box_note_and_attribution():
    data, s = made()

    web, zipped = report_doc.document(s, photo_base="../../evidence/"), report_doc.document(s)

    name = f"scan{data['scan']['id']}_issue1.jpg"
    assert f'src="../../evidence/{name}"' in web and f'src="evidence/{name}"' in zipped
    entries = re.findall(r'<figure class="entry".*?</figure>', web, re.S)
    assert len(entries) == len(s["ledger"])
    for entry in entries:
        assert "CC BY-SA 4.0" in entry and ("by " in entry or "no photo" in entry)
    assert 'loading="lazy"' in web


def test_the_map_draws_the_walks_and_one_dot_per_finding_in_the_items_colour():
    _, s = made()

    svg = report_doc.svg_map(s["walks"], s["ledger"], s["styles"])

    assert svg.startswith("<svg") and svg.count("<polyline") == 1 and svg.count("<circle") == len(s["ledger"])
    first = style.item_styles(views.items_json(STANDARD))["curb_ramp_warning"]["color"]
    assert first in svg and "m</text>" in svg  # the scale bar


def test_report_document_builds_the_page_from_the_scan_data():
    data, _ = made()

    page = report.document(data, STANDARD, area="Pike & Pine", notes=NOTES, now=NOW)

    assert NOTES["summary"] in page and "Pike &amp; Pine" in page


def test_names_are_capitalised_every_item_has_a_question_and_rejections_are_said_apart():
    _, s = made(city=True)

    page = report_doc.document(s)

    assert "Curb ramp</h3>" in page and "<b>Shop entrance</b>: How many steps are at the shop entrance?" in page
    assert "1 found by the models, 1 awaiting review, 1 confirmed; 1 more rejected by a person (not counted)" in page
    text = html.escape(report_doc.summary_text(s))
    assert ("against the city&#x27;s lists, 1 find the lists do not have and 2 listed points the photos did not "
            "confirm") in text and "may need updating" not in text and "4.3 City records to check" in page


def test_city_records_the_survey_did_not_find_are_listed_by_the_citys_own_ids():
    data = survey_scan.load()
    city = {**CITY, "items": {"streetlight_pole": {
        **CITY["items"]["streetlight_pole"], "id_fields": ["SCLNR", "ASSET_ID"],
        "missed": [{"lat": 47.6, "lng": -122.3, "city": {"SCLNR": f"00{n}NE", "ASSET_ID": str(1314000 + n)}}
                   for n in range(12)]}}}
    data["city_check"] = city
    s = report.summary(data, STANDARD, area="Pike & Pine", now=NOW)

    page = report_doc.document(s)

    actions = page.split('id="actions"')[1].split('<section class="sec"')[0]
    assert "<th scope=\"col\">SCLNR</th>" in actions and "<td>000NE</td>" in actions and "<td>1314009</td>" in actions
    assert "<td>1314010</td>" not in actions  # the first ten only
    assert f"2 more in survey_scan{s['scan_id']}_streetlight_pole_city_records_to_check.csv" in actions
    assert "SCLNR" not in str(report.facts(s))  # Spark reads counts, never the city's record ids
