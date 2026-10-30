import json

import pytest

from pipeline import call_stats, db

BBOX = [-122.33, 47.61, -122.32, 47.62]


def log(path, calls):
    path.write_text("\n".join(json.dumps(c) for c in calls) + "\nnot json\n", encoding="utf-8")


def test_a_surveys_calls_are_counted_by_stage_and_model_within_its_windows(tmp_path):
    conn = db.connect(tmp_path / "threshold.db")
    scan_id = db.create_scan(conn, "mapillary", BBOX, "US")
    db.update_scan(conn, scan_id, cost_usd=0.2, processing_seconds=60.0)
    t0 = call_stats._ts("2026-10-29T01:14:00Z")
    log(tmp_path / "calls.jsonl", [
        {"kind": "upload", "ts": t0 + 1, "seconds": 3.0, "cost_usd": 0.0, "path": "chunk_0000_spark.mp4"},
        {"kind": "spark", "ts": t0 + 5, "seconds": 40.0, "cost_usd": 0.05, "schema": "street_scan"},
        {"kind": "sam", "ts": t0 + 6, "seconds": 2.0, "cost_usd": 0.0025, "prompt": "curb cut", "frames": 60},
        {"kind": "sam", "ts": t0 + 7, "seconds": 2.0, "cost_usd": 0.0025, "prompt": "sidewalk", "images": 1},
        {"kind": "sam", "ts": t0 + 8, "seconds": 2.0, "cost_usd": 0.0025, "prompt": "person", "images": 1},
        {"kind": "spark", "ts": t0 + 9, "seconds": 10.0, "cost_usd": 0.012, "schema": "survey_check"},
        {"kind": "spark", "ts": t0 + 10, "seconds": 10.0, "cost_usd": 0.012, "schema": "survey_check"},
        {"kind": "upload_failed", "ts": t0 + 11, "seconds": 60.0, "cost_usd": 0.0},
        {"kind": "spark", "ts": t0 + 900, "seconds": 9.0, "cost_usd": 0.3, "schema": "survey_check"},  # another survey
        {"kind": "spark", "ts": t0 + 3600, "seconds": 30.0, "cost_usd": 0.04, "schema": "survey_report_text"},
    ])
    log(tmp_path / "calls_open.jsonl", [{"kind": "spark_open", "ts": t0 + 12, "seconds": 11.0, "check": "curb_ramp_warning"}])

    record = call_stats.build(conn, tmp_path, scan_id, [("calls.jsonl", "2026-10-29T01:14:00Z", "2026-10-29T01:15:00Z"),
                                                        ("calls.jsonl", "2026-10-29T02:13:00Z", "2026-10-29T02:15:00Z")])

    rows = [(s["stage"], s["model"], s["calls"], s["cost_usd"]) for s in record["stages"]]
    assert rows == [("scene", "Muse Spark", 1, 0.05), ("scene", "File upload (for SAM 3.1 video)", 1, 0.0),
                    ("sidewalk", "SAM 3.1", 1, 0.0025), ("items", "SAM 3.1", 1, 0.0025),
                    ("items", "Muse Spark", 2, 0.024), ("second_opinion", "Muse Spark Open", 1, None),
                    ("evidence", "SAM 3.1", 1, 0.0025), ("report", "Muse Spark", 1, 0.04)]
    total = record["total"]
    assert (total["calls"], total["cost_usd"], total["scan_cost_usd"], total["processing_seconds"]) == (9, 0.1215, 0.2, 60.0)
    assert record["windows"][1] == {"log": "calls.jsonl", "start": "2026-10-29T02:13:00Z", "end": "2026-10-29T02:15:00Z"}
    assert "longer than the processing time" in record["note"]


def test_the_cli_writes_the_record_beside_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("THRESHOLD_DATA", str(tmp_path))
    from pipeline import config
    run = config.run_dir("r")
    conn = db.connect(run / "threshold.db")
    scan_id = db.create_scan(conn, "mapillary", BBOX, "US")
    conn.close()
    log(run / "calls.jsonl", [{"kind": "spark", "ts": call_stats._ts("2026-10-29T01:14:30Z"), "seconds": 1.0,
                               "cost_usd": 0.01, "schema": "survey_check"}])
    out = call_stats.main(["--run", "r", "--scan", str(scan_id), "--window", "calls.jsonl", "2026-10-29T01:14:00Z",
                           "2026-10-29T01:15:00Z"])
    assert out == run / f"call_stats_scan{scan_id}.json" and json.loads(out.read_text())["total"]["calls"] == 1
    with pytest.raises(SystemExit):
        call_stats.main(["--run", "r", "--scan", str(scan_id)])  # a window is required
