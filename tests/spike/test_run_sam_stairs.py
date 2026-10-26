import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spike import config, run_sam_stairs as rss, video
from spike import meta_client as mc
from spike.truth import ClipTruth, Entrance

HIT = "<0f>0<|box;x1=10;y1=20;x2=110;y2=220;w=320;h=334;c=0.91|><|mask;x=0;y=0;c=0.88;data=8,8,~zz|>"


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    """Offline stand-ins for ffmpeg, the Meta upload/SAM/delete calls and the truth file."""
    monkeypatch.setattr(config, "OUT", tmp_path)
    state = {"uploads": [], "deleted": [], "fail_sam_for": set(), "fail_cut_for": set(), "sam_calls": []}

    def cut(src, dst, start, end):
        index = int(Path(dst).stem.rsplit("_e", 1)[1])
        if index in state["fail_cut_for"]:
            raise RuntimeError("ffmpeg failed (exit 1)")
        return Path(dst)

    def upload(path):
        state["uploads"].append(Path(path).name)
        return Path(path).name

    def sam(concept, media, billed_frames):
        state["sam_calls"].append((concept, media["file_id"], billed_frames))
        if media["file_id"] in state["fail_sam_for"]:
            raise RuntimeError("400 bad request")
        return HIT if concept in ("stairs", "steps") else ""

    monkeypatch.setattr(video, "cut", cut)
    monkeypatch.setattr(video, "probe", lambda path: SimpleNamespace(duration_s=5.0))
    monkeypatch.setattr(mc, "upload", upload)
    monkeypatch.setattr(mc, "sam", sam)
    monkeypatch.setattr(mc, "delete_file", lambda file_id: state["deleted"].append(file_id))
    monkeypatch.setattr(rss, "load_truth", lambda: [
        ClipTruth(
            file="a.mp4",
            entrances=[Entrance(t=t, name=f"door{i}", step_count=2, has_ramp=False, door_type="glass")
                       for i, t in enumerate([10.0, 20.0, 30.0])],
            tactile_ranges=[],
            obstacles=[],
        )
    ])
    return state


def test_failing_entrance_keeps_other_results_and_records_error(fake_io):
    fake_io["fail_sam_for"] = {"a_e1.mp4"}

    rss.main()

    out = json.loads((config.OUT / "sam_stairs.json").read_text(encoding="utf-8"))
    rows = out["rows"]
    assert [r["index"] for r in rows] == [0, 1, 2]
    assert rows[0]["phrases"]["stairs"]["detected"] is True
    assert rows[2]["phrases"]["steps"]["detected"] is True
    assert "error" not in rows[0] and "error" not in rows[2]
    for phrase in rss.PHRASES:
        assert rows[1]["phrases"][phrase] == {"error": "RuntimeError: 400 bad request"}
    # rates use only the entrances whose requests succeeded
    assert out["rates"]["stairs"] == 1.0
    assert out["rates"]["steps"] == 1.0
    assert out["rates"]["staircase"] == 0.0
    assert out["best_phrase"] in ("stairs", "steps")
    # the window upload is deleted even when its requests fail
    assert sorted(fake_io["deleted"]) == sorted(fake_io["uploads"]) == ["a_e0.mp4", "a_e1.mp4", "a_e2.mp4"]
    assert all(billed == 50 for _, _, billed in fake_io["sam_calls"])


def test_failed_window_cut_is_recorded_and_others_continue(fake_io):
    fake_io["fail_cut_for"] = {0}

    rss.main()

    out = json.loads((config.OUT / "sam_stairs.json").read_text(encoding="utf-8"))
    rows = out["rows"]
    assert rows[0]["phrases"] == {}
    assert rows[0]["error"] == "RuntimeError: ffmpeg failed (exit 1)"
    assert rows[1]["phrases"]["stairs"]["detected"] is True
    assert rows[2]["phrases"]["stairs"]["detected"] is True
    assert out["rates"]["stairs"] == 1.0
    assert sorted(fake_io["deleted"]) == sorted(fake_io["uploads"]) == ["a_e1.mp4", "a_e2.mp4"]
