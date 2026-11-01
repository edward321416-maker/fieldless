import csv
import io
import json

from assist import asset_check

EV = {"image": "scan1_issue1_9.jpg", "creator": "bea", "captured_at": "2022-08-30T00:00:00Z",
      "link": "https://www.mapillary.com/app/?pKey=9", "license": "CC BY-SA 4.0", "blurred_regions": 2}


def row(**over):
    return {"item": "pole", "asset": "City pole", "city_list": "City poles", "city_record": "P1", "city_location": "1 Main St",
            "status": "Seen in photo", "how_seen": "Survey", "models_unsure": "", "city_date": "2020-05",
            "seen_in_photo_on": "2022-08-30", "photo_newer_than_city_date": "yes", "city_condition": "GOOD", "lat": 1.5,
            "lng": 2.5, "position_source": "City record", "position_accuracy": "City record", "box": [100, 200, 300, 400],
            "evidence": EV, **over}


ROWS = [row(), row(asset="Public can", how_seen="Second look, panorama", city_record="C1", city_date="",
               photo_newer_than_city_date="", evidence={**EV, "image": "scan1_look0_7.jpg"}),
        row(asset="Street tree", status="Not determined", how_seen="", city_record="T1", seen_in_photo_on="",
            photo_newer_than_city_date="", evidence=None, box=None),
        row(asset="Street tree", status="Not on the city list", how_seen="", city_list="", city_record="", models_unsure="yes",
            city_date="", photo_newer_than_city_date="", position_accuracy="Unknown (within 50 ft)",
            evidence={**EV, "image": "scan1_issue3_9.jpg"})]
TOTALS = {"in_view": 3, "seen": 2, "seen_by": {"Survey": 1}, "not_determined": 1, "not_listed": 1, "not_listed_unsure": 1,
          "seen_unsure": 1, "newer": 1}
CHECK = {"scan_id": 1, "city": "Testville", "rows": ROWS, "totals": TOTALS,
         "items": [{"item": "pole", "asset": "City pole", "city_list": "City poles", **TOTALS}],
         "trust": {"photo_months": ["2022-08", "2025-07"], "controls": [{"photos": "Panoramas", "asked": 10, "both_yes": 1, "note": "It was a real pole."}],
                   "looked_at": 25, "sent_back": 3, "outside_view": 6, "findings": {"n": 88, "yes": 73, "no": 4, "unclear": 11}}}


def lines(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_the_table_has_one_line_per_row_with_the_photos_attribution():
    table = lines(asset_check.table_csv(CHECK))
    assert len(table) == 4 and list(table[0]) == [k for k, _ in asset_check.COLUMNS]
    assert table[0]["photo_url"] == EV["link"] and table[0]["photo_by"] == "bea" and table[0]["license"] == EV["license"]
    assert table[0]["evidence_file"] == "evidence/scan1_issue1_9.jpg"


def test_a_row_without_a_photo_leaves_the_photo_cells_empty():
    third = lines(asset_check.table_csv(CHECK))[2]
    assert third["status"] == "Not determined" and third["photo_url"] == "" and third["evidence_file"] == ""


def test_text_that_starts_like_a_formula_stays_text():
    check = {**CHECK, "rows": [row(city_location="=HYPERLINK(1)")]}
    assert lines(asset_check.table_csv(check))[0]["city_location"] == "'=HYPERLINK(1)"


def test_the_geojson_puts_each_row_at_its_position():
    features = json.loads(asset_check.table_geojson(CHECK))["features"]
    assert len(features) == 4 and features[0]["geometry"] == {"type": "Point", "coordinates": [2.5, 1.5]}
    assert features[3]["properties"]["position_accuracy"] == "Unknown (within 50 ft)"


def test_the_app_gets_the_counts_apart_from_the_rows():
    assert set(asset_check.summary(CHECK)) == {"city", "items", "totals", "trust"}


def test_the_app_gets_each_photo_at_the_servers_address():
    rows = asset_check.for_app(CHECK)["rows"]
    assert rows[0]["evidence"]["url"] == "/api/evidence/scan1_issue1_9.jpg" and rows[2]["evidence"] is None


def test_the_photos_are_named_once_each():
    assert asset_check.photos(CHECK) == {"scan1_issue1_9.jpg", "scan1_look0_7.jpg", "scan1_issue3_9.jpg"}


def test_the_page_opens_with_the_counts_and_is_labelled_as_not_the_citys():
    page = asset_check.document(CHECK, area="Capitol Hill", km=0.94, photo_count=181)
    assert "<title>Capitol Hill Asset Check</title>" in page
    assert "It is not a Testville city document, and the city has not reviewed it." in page
    assert "Of 3 city records in view of the photos, 2 were seen and 1 could not be settled" in page
    assert "the models were unsure of 1 of those" in page and "0.94 km of walked streets" in page


def test_every_photo_on_the_page_has_its_credit():
    page = asset_check.document(CHECK)
    shown = page.count("<figure>")
    assert shown == 2 and page.count("View on Mapillary") == shown and page.count("Photo by bea") == shown
    assert page.count(", CC BY-SA 4.0. Edited: ") == shown


def test_a_view_cut_from_a_panorama_says_so():
    page = asset_check.document(CHECK)
    assert "Edited: cut from a 360-degree photo, people and plates blurred, resized." in page
    assert "Edited: people and plates blurred, resized." in page  # the survey's own photo was not cut


def test_a_photo_with_nothing_to_blur_does_not_say_it_was_blurred():
    check = {**CHECK, "rows": [row(evidence={**EV, "blurred_regions": 0})]}
    assert "Edited: resized." in asset_check.document(check) and "plates blurred" not in asset_check.document(check)


def test_photos_come_from_where_the_page_is_told():
    assert 'src="../../../evidence/scan1_issue1_9.jpg"' in asset_check.document(CHECK, photo_base="../../../evidence/")


def test_the_examples_are_of_different_assets():
    shown = asset_check.examples(CHECK)
    assert len({r["asset"] for r in shown}) == len(shown) and len(shown) >= 2


def test_an_example_is_never_one_the_models_were_unsure_of():
    assert all(r["models_unsure"] != "yes" for r in asset_check.examples(CHECK))


def test_the_page_says_how_the_seen_records_came_to_be_seen():
    totals = {**TOTALS, "seen_by": {"Survey": 45, "Second look": 9, "Second look, panorama": 16}}
    assert asset_check.seen_how(totals) == "45 by the survey, 9 on a second look, 16 on a second look in panoramas"
    assert asset_check.seen_how({**TOTALS, "seen_by": {"Survey": 2}}) == "2 by the survey"


def test_the_page_says_how_far_to_trust_it():
    page = asset_check.document(CHECK)
    assert "An AI assistant checked 88 findings against their photos: 73 right, 4 wrong and 11 could not be told." in page
    assert "10 spots with no city record were asked the same way. Two models agreed on an object at 1 of them. It was a real pole." in page
    assert "looked at the 25 records two models had settled and sent 3 back to Not determined" in page
    assert "6 findings paired with a city record outside the photo's view are counted on neither side." in page
    assert "August 2022 and July 2025" in page


def test_the_page_never_gets_ahead_of_the_citys_decision():
    page = asset_check.document(CHECK).lower()
    for word in ("add to db", "add_to_city_db", "verified", "confirmed", "missing record", "patch", "approved", "passed"):
        assert word not in page


def test_the_page_says_how_rows_were_paired_and_where_the_models_were_unsure():
    page = asset_check.document(CHECK)
    assert "by the photo's position and viewing direction, not by a model comparing the two" in page
    assert "The models were unsure of 1 of the 2 seen records." in page


def test_a_check_without_later_looks_still_makes_a_page():
    check = {**CHECK, "trust": {"photo_months": ["2022-08"], "controls": [], "looked_at": 0, "sent_back": 0, "outside_view": 0}}
    page = asset_check.document(check)
    assert "Control spots" not in page and "Second looks." not in page and "Left out." not in page


def test_write_makes_the_three_files(tmp_path):
    asset_check.write(CHECK, tmp_path, area="Capitol Hill")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["asset_check.csv", "asset_check.geojson", "asset_check.html"]
    assert 'src="evidence/scan1_issue1_9.jpg"' in (tmp_path / "asset_check.html").read_text(encoding="utf-8")
