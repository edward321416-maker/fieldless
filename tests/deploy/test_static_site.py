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
