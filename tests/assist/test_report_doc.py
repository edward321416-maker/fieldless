import base64
import html
import re
from datetime import datetime, timezone

import pytest

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
    assert "<h3>4.1 Possible defects</h3>" in actions and "Repairs" not in actions
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
    assert "1 found by the models, 1 models unsure, 1 confirmed; 1 more rejected by a person (not counted)" in page
    text = html.escape(report_doc.summary_text(s))
    assert ("against the city&#x27;s lists, 1 find the lists do not have and 2 listed points the survey did not "
            "find") in text and "may need updating" not in text and "4.3 City records to check" in page
    assert "the photos did not confirm" not in text


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
    # records are in the photos' view by position; the models never saw them, so none is "shown" or "not seen"
    assert "city records in the photos' view that the survey did not find</p>" in actions
    assert "showed" not in actions and "not seen" not in actions
    assert "SCLNR" not in str(report.facts(s))  # Spark reads counts, never the city's record ids


@pytest.fixture
def logo_dirs(tmp_path, monkeypatch):
    """A web/ folder of the test's own for the report to look in, with the logo read afresh on each side."""
    monkeypatch.setattr(report_doc, "WEB_DIR", tmp_path)
    report_doc._logo_uri.cache_clear()
    yield tmp_path
    report_doc._logo_uri.cache_clear()


def test_every_photo_entry_shows_the_mapillary_logo():
    _, s = made()

    page = report_doc.document(s)

    entries = re.findall(r'<figure class="entry".*?</figure>', page, re.S)
    credited = [e for e in entries if "Photo by" in e]
    assert credited and all('<span class="mly" role="img" aria-label="Mapillary"></span></a>' in e for e in credited)
    assert page.count('class="mly"') >= len(credited)
    assert page.count("data:image/png;base64,") == 1  # the logo once, in the style, not in each of the entries
    assert ".mly {" in page and "print-color-adjust: exact" in page and 'src="data:' not in page


def test_the_logo_is_read_from_the_built_app_in_the_image_and_from_its_source_in_the_repo(logo_dirs):
    (logo_dirs / "public").mkdir()
    (logo_dirs / "public" / "mapillary-logo.png").write_bytes(b"source")
    assert report_doc._logo_uri() == "data:image/png;base64," + base64.b64encode(b"source").decode("ascii")

    report_doc._logo_uri.cache_clear()
    (logo_dirs / "dist").mkdir()
    (logo_dirs / "dist" / "mapillary-logo.png").write_bytes(b"built")
    assert report_doc._logo_uri() == "data:image/png;base64," + base64.b64encode(b"built").decode("ascii")


def test_the_real_logo_file_is_found_where_the_report_looks():
    report_doc._logo_uri.cache_clear()

    uri = report_doc._logo_uri()

    assert uri is not None and uri.startswith("data:image/png;base64,") and len(uri) > 1000


def test_without_the_logo_file_the_credit_names_mapillary_in_words(logo_dirs):
    _, s = made()

    page = report_doc.document(s)

    assert report_doc._logo_uri() is None
    assert 'class="mly"' not in page and ".mly {" not in page and "data:image" not in page
    credited = [e for e in re.findall(r'<figure class="entry".*?</figure>', page, re.S) if "Photo by" in e]
    assert credited and all(re.search(r'Photo by [^<]+, <a href="https://www.mapillary.com/[^"]+">Mapillary</a>, '
                                      r"CC BY-SA 4.0", e) for e in credited)


def test_second_opinion_is_named_only_when_the_survey_holds_one():
    assert "judged again by Muse Spark Open" in report_doc.how_made(True)
    assert "Muse Spark Open" not in report_doc.how_made(False)


def with_second_opinion(**opinion) -> dict:
    """The fixture scan with Muse Spark Open's record on its first issue row (what second_opinion.apply writes)."""
    data = survey_scan.load()
    row = data["issues"][0]
    row["model_outputs"] = {**(row.get("model_outputs") or {}), "second_opinion": opinion}
    return data


def test_the_scope_section_names_the_second_opinion_only_when_a_row_really_has_one():
    """BASETEN_API_KEY is optional, so a US survey may have run on Muse Spark alone; the rows tell, not the standard."""
    answered = {"model": "meta-models/muse-spark-open-1026", "answer": "no", "agree": True}
    alone = report.summary(survey_scan.load(), STANDARD, area="Pike & Pine", now=NOW)
    failed = report.summary(with_second_opinion(model="m", error="timeout"), STANDARD, now=NOW)  # a call that failed
    asked = report.summary(with_second_opinion(**answered), STANDARD, now=NOW)

    assert (alone["standard"], alone["second_opinions"], failed["second_opinions"], asked["second_opinions"]) == (
        "US", 0, 0, 1)
    for no_second in (alone, failed):
        page = report_doc.document(no_second)
        assert "Muse Spark Open" not in page and "SAM 3.1 found each survey item" in page
    assert "Defect findings were judged again by Muse Spark Open." in report_doc.document(asked)


def test_a_kr_survey_never_claims_a_second_opinion():
    _, s = made()

    kr = report_doc.document({**s, "standard": "KR"})

    assert "Muse Spark Open" not in kr and "SAM 3.1 found each survey item" in kr


def test_a_counts_only_city_list_is_credited_and_never_called_a_list_the_findings_are_missing_from():
    from tests.assist.test_report import SEOUL
    data = survey_scan.load()
    data["city_check"] = SEOUL
    s = report.summary(data, STANDARD, area="Seongsu", now=NOW)

    page = report_doc.document(s)
    summary = html.unescape(report_doc.summary_text(s))

    actions = page.split('id="actions"')[1].split('<section class="sec"')[0]
    assert "KOGL Type 4" in page and "1 of 3 in the city" in page
    assert "the lists do not have" not in summary and "2 listed points the survey did not find" in summary
    assert "not in the city" not in actions
    assert "<th scope=\"col\">In the city&#x27;s list, not found by this survey</th></tr>" in actions
    assert "<tr><td>Streetlight pole</td><td>2</td></tr>" in actions  # the one count it has: 3 listed, 1 found


def test_the_page_carries_a_citys_credit_for_its_records():
    data = survey_scan.load()
    data["city_check"] = {**CITY, "credit": "City records: Testville Open Data & co."}
    s = report.summary(data, STANDARD, area="Pike & Pine", now=NOW)
    assert "City records: Testville Open Data &amp; co." in report_doc.document(s)
    assert "City records: Testville" not in report_doc.document(made(city=True)[1])


def test_the_page_and_the_text_say_the_numbers_are_the_surveys_own():
    data = survey_scan.load()
    data["asset_check"] = {"totals": {"in_view": 94, "seen": 53, "not_determined": 41}}
    s = report.summary(data, STANDARD, area="Pike & Pine", now=NOW)
    assert "These are the survey&#x27;s own numbers." in report_doc.document(s) or "These are the survey's own numbers." in report_doc.document(s)
    assert "These are the survey's own numbers." in report.to_markdown(s)
    plain = report.summary(survey_scan.load(), STANDARD, area="Pike & Pine", now=NOW)
    assert "own numbers" not in report_doc.document(plain) and "own numbers" not in report.to_markdown(plain)
