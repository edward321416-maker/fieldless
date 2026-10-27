import json

from eval import run_eval
from eval.metrics import evaluate
from eval.truth import FORMAT
from pipeline import config, db


def test_run_eval_writes_json_and_prints_markdown(capsys):
    conn = db.connect(config.run_dir("r") / "threshold.db")
    scan_id = db.create_scan(conn, "test", None)
    db.add_frames(conn, scan_id, [
        {"idx": i, "clip": 0, "image_id": f"img{i}", "sequence_id": "s", "lat": 37.5 + i * 1e-4, "lng": 127.0,
         "compass": 0.0, "captured_at": "2023-02-08T00:00:00Z", "creator": "u", "width": 2048, "height": 1536}
        for i in range(5)])
    db.add_issue(conn, scan_id, {"issue_type": "cover_cut", "frame_start": 2, "frame_end": 2, "frame_idx": 2,
                                 "review_status": "needs_review", "review_reasons": ["possible_defect"]})
    db.update_scan(conn, scan_id, processing_seconds=50.4321, review_seconds=6.0, cost_usd=0.2049)
    conn.close()
    truth_path = config.data_dir() / "truth" / "t.json"
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(json.dumps({"format": FORMAT, "run": "r", "scan_id": scan_id, "labeling_seconds": 300,
                                      "photos": {"img2": {"idx": 2, "defects": [{"type": "cover_cut"}]}}}),
                          encoding="utf-8")

    out = run_eval.main(["--run", "r", "--scan", str(scan_id), "--truth", str(truth_path)])

    result = json.loads(out.read_text(encoding="utf-8"))
    assert out.name == f"eval_scan{scan_id}.json" and result["defects"]["cover_cut"]["found"] == 1
    printed = capsys.readouterr().out
    assert "| cover_cut | 1 | 1 | 1 | 1 |" in printed and "Person labeling" in printed
    assert "50 s processing + 6 s review" in printed and "$0.20 (" in printed


def test_markdown_names_fieldless_and_skips_an_untimed_person_line():
    # Since 2026-10-27 the truth is made without timing (labeling_seconds 0); a "0 s" person line would mislead.
    frames = [{"idx": i, "clip": 0, "image_id": f"img{i}", "lat": 37.5 + i * 1e-4, "lng": 127.0} for i in range(3)]
    data = {"scan": {"id": 1, "processing_seconds": 60.0, "review_seconds": 0.0, "cost_usd": 0.1},
            "frames": frames, "issues": [], "entrances": []}
    truth = {"run": "r", "scan_id": 1, "labeled_by": "Claude", "labeling_seconds": 0, "photos": {}}

    text = run_eval.to_markdown(evaluate(data, truth))

    assert "Person labeling" not in text and "- Fieldless: 60 s processing" in text
