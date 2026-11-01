import json

from deploy import static_site
from pipeline import config, db
from tests.assist import survey_scan


def test_the_static_site_exports_each_surveys_report_page(tmp_path):
    scan_id = survey_scan.build("demo-export")
    conn = db.connect(config.run_dir("demo-export") / "threshold.db")
    db.update_scan(conn, scan_id, status="done")  # a demo survey is finished
    conn.close()
    evidence = config.run_dir("demo-export") / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"scan{scan_id}_issue1.jpg").write_bytes(b"jpg")  # the fixture's one evidence photo
    out = tmp_path / "site"

    written = static_site.export_api("demo-export", [(scan_id, "Pike & Pine")], out)

    page = out / "api" / "scans" / str(scan_id) / "report.html"
    assert f"/api/scans/{scan_id}/report" in written and page.is_file()
    assert "Street Survey Report" in page.read_text(encoding="utf-8")
    site_config = json.loads((out / "api" / "config.json").read_text(encoding="utf-8"))
    assert site_config["report"] is True and site_config["coverage_tiles"] is False
    assert f"/api/scans/{scan_id}/calls" not in written  # no call record was made for this survey


def test_the_static_site_exports_an_asset_check_with_its_photos(tmp_path):
    scan_id = survey_scan.build("demo-asset")
    conn = db.connect(config.run_dir("demo-asset") / "threshold.db")
    db.update_scan(conn, scan_id, status="done")
    conn.close()
    evidence = config.run_dir("demo-asset") / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"scan{scan_id}_issue1.jpg").write_bytes(b"jpg")
    (evidence / f"scan{scan_id}_look0_7.jpg").write_bytes(b"look")
    photo = {"image": f"scan{scan_id}_look0_7.jpg", "image_id": "7", "creator": "kev", "captured_at": "2025-07-11T00:00:00Z",
             "link": "https://www.mapillary.com/app/?pKey=7", "license": "CC BY-SA 4.0", "blurred_regions": 1}
    totals = {"in_view": 1, "seen": 1, "seen_by": {}, "not_determined": 0, "not_listed": 0, "not_listed_unsure": 0, "seen_unsure": 0, "newer": 0}
    row = {"item": "pole", "asset": "City pole", "city_list": "City poles", "city_record": "P1", "city_location": "",
           "status": "Seen in photo", "how_seen": "Second look", "models_unsure": "", "city_date": "",
           "seen_in_photo_on": "2025-07-11", "photo_newer_than_city_date": "", "city_condition": "", "lat": 1.0, "lng": 2.0,
           "position_source": "City record", "position_accuracy": "City record", "box": None, "evidence": photo,
           "action": "No change", "city_says": "", "photo_shows": ""}
    actions = {"No change": 1}
    check = {"scan_id": scan_id, "city": "Testville", "totals": totals, "rows": [row], "actions": actions, "no_visit": 1,
             "left_out": {"repeats": 0, "wrong": 0},
             "items": [{"item": "pole", "asset": "City pole", "city_list": "City poles", **totals, "actions": actions}],
             "trust": {"photo_months": ["2025-07"], "controls": [], "looked_at": 0, "sent_back": 0, "outside_view": 0}}
    (config.run_dir("demo-asset") / f"asset_check_scan{scan_id}.json").write_text(json.dumps(check), encoding="utf-8")
    site = tmp_path / "site"

    static_site.export_api("demo-asset", [(scan_id, "Pike & Pine")], site)

    folder = site / "api" / "scans" / str(scan_id)
    assert json.loads((folder / "asset-check.json").read_text())["rows"][0]["city_record"] == "P1"
    assert "Pike &amp; Pine Asset Check" in (folder / "asset-check" / "report.html").read_text(encoding="utf-8")
    assert (folder / "asset-check" / "table.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads((folder / "asset-check" / "table.geojson").read_text())["features"]
    assert (site / "api" / "evidence" / f"scan{scan_id}_look0_7.jpg").read_bytes() == b"look"


def test_the_static_site_exports_a_surveys_call_record_when_one_was_made(tmp_path):
    scan_id = survey_scan.build("demo-calls")
    conn = db.connect(config.run_dir("demo-calls") / "threshold.db")
    db.update_scan(conn, scan_id, status="done")
    conn.close()
    evidence = config.run_dir("demo-calls") / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"scan{scan_id}_issue1.jpg").write_bytes(b"jpg")
    record = {"scan_id": scan_id, "stages": [], "total": {"calls": 0}}
    (config.run_dir("demo-calls") / f"call_stats_scan{scan_id}.json").write_text(json.dumps(record), encoding="utf-8")

    static_site.export_api("demo-calls", [(scan_id, "Pike & Pine")], tmp_path / "site")

    assert json.loads((tmp_path / "site" / "api" / "scans" / str(scan_id) / "calls.json").read_text()) == record



def test_the_static_site_exports_the_agents_finished_items_with_their_photos(tmp_path):
    scan_id = survey_scan.build("demo-items")
    conn = db.connect(config.run_dir("demo-items") / "threshold.db")
    db.update_scan(conn, scan_id, status="done")
    run_id = db.create_item_run(conn, scan_id, "bike rack")
    name = f"scan{scan_id}_item{run_id}-1_5.jpg"
    row = {"id": f"item{run_id}-1", "scan_id": scan_id, "item_run": run_id, "issue_type": "agent_bike_rack",
           "object_label": "yes", "frame_idx": 0, "lat": 47.61, "lng": -122.32, "review_status": "auto",
           "review_reasons": [], "model_outputs": {"check": {"answer": "yes"}},
           "evidence": {"image": name, "image_id": "5", "creator": "a", "link": "l", "license": "CC BY-SA 4.0"}}
    check = {"id": "agent_bike_rack", "find": "bike rack", "target": "bike rack", "ask": "A bike rack?",
             "report_when": "yes", "label_yes": "Bike rack", "label_no": "No bike rack",
             "label_unclear": "Bike rack unclear", "inventory": True}
    db.update_item_run(conn, run_id, status="done", check_def=check, rows=[row])
    db.update_item_run(conn, db.create_item_run(conn, scan_id, "bench"), status="failed")
    conn.close()
    evidence = config.run_dir("demo-items") / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"scan{scan_id}_issue1.jpg").write_bytes(b"jpg")
    (evidence / name).write_bytes(b"rack")
    out = tmp_path / "site"

    written = static_site.export_api("demo-items", [(scan_id, "Pike & Pine")], out)

    listed = json.loads((out / "api" / "scans" / str(scan_id) / "items.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in listed] == [run_id] and f"/api/items/{run_id}" in written
    item = json.loads((out / "api" / "items" / f"{run_id}.json").read_text(encoding="utf-8"))
    assert item["item"]["added_by"] == "agent" and item["issues"][0]["id"] == f"item{run_id}-1"
    assert (out / "api" / "evidence" / name).read_bytes() == b"rack"
    site_config = json.loads((out / "api" / "config.json").read_text(encoding="utf-8"))
    assert site_config["items"] is True and site_config["make_items"] is False
