import json
import sqlite3

import pytest

from eval import manifest, store
from pipeline import config, db


def make_run(run="r1"):
    path = config.run_dir(run) / "threshold.db"
    conn = db.connect(path)
    scan_id = db.create_scan(conn, "test", [127.0, 37.0, 127.1, 37.1])
    db.add_frames(conn, scan_id, [
        {"idx": i, "clip": 0, "image_id": f"img{i}", "sequence_id": "s", "lat": 37.5 + i * 1e-5, "lng": 127.0,
         "compass": 0.0, "captured_at": "2023-02-08T00:00:00Z", "creator": "u", "width": 2048, "height": 1536}
        for i in (2, 0, 1)])
    conn.close()
    return path, scan_id


def test_open_ro_reads_and_refuses_writes():
    path, scan_id = make_run()
    conn = store.open_ro(path)
    data = store.load_scan(conn, scan_id)
    assert [f["idx"] for f in data["frames"]] == [0, 1, 2] and data["issues"] == []
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM frame")


def test_open_ro_on_a_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.open_ro(tmp_path / "nope.db")


def test_manifest_cli_writes_frames_in_idx_order():
    _, scan_id = make_run("r2")
    out = manifest.main(["--run", "r2", "--scan", str(scan_id)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert out == config.data_dir() / "truth" / f"manifest_r2_scan{scan_id}.json"
    assert data["format"] == "threshold-survey-manifest/1" and data["run"] == "r2" and data["scan_id"] == scan_id
    assert [f["image_id"] for f in data["frames"]] == ["img0", "img1", "img2"]
