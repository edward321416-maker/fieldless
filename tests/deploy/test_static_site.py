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
