import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spike import config, run_spark_entrances as rse, video
from spike import meta_client as mc
from spike.truth import ClipTruth, Entrance


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    """Replace probe/cut/burn/hold/upload/spark/delete with offline fakes and record the calls."""
    monkeypatch.setattr(config, "OUT", tmp_path)
    state = {"duration": 130.0, "spark_calls": 0, "uploads": [], "deleted": [], "fail_on_call": set(),
             "entrances_for_call": {}}

    monkeypatch.setattr(video, "probe", lambda path: SimpleNamespace(duration_s=state["duration"]))
    monkeypatch.setattr(video, "cut", lambda src, dst, s, e: Path(dst))
    monkeypatch.setattr(video, "burn_timecode", lambda src, dst, offset_s=0.0: Path(dst))
    monkeypatch.setattr(video, "hold_frames", lambda src, dst, repeat: Path(dst))

    def upload(path):
        file_id = f"file{len(state['uploads'])}"
        state["uploads"].append(file_id)
        return file_id

    def spark_json(parts, schema_name, schema):
        state["spark_calls"] += 1
        n = state["spark_calls"]
        if n in state["fail_on_call"]:
            raise RuntimeError("400 bad request")
        return {"entrances": state["entrances_for_call"].get(n, []), "obstacle_types": []}

    monkeypatch.setattr(mc, "upload", upload)
    monkeypatch.setattr(mc, "spark_json", spark_json)
    monkeypatch.setattr(mc, "delete_file", lambda file_id: state["deleted"].append(file_id))
    return state


def test_failed_chunk_keeps_other_chunks_and_records_error(fake_io):
    # 130 photos -> chunks [0,60), [60,120), [120,130)
    fake_io["fail_on_call"] = {2}
    fake_io["entrances_for_call"] = {
        1: [{"t": 10.0, "side": "left", "name": "a", "category": "c", "has_visible_steps": False}],
        3: [{"t": 125.0, "side": "right", "name": "b", "category": "c", "has_visible_steps": True}],
    }
    result = rse.run_clip(Path("clip.mp4"))

    assert [e["t"] for e in result["entrances"]] == [10.0, 125.0]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["start"] == 60
    assert result["errors"][0]["end"] == 120
    assert result["errors"][0]["error"].startswith("RuntimeError: 400 bad request")
    # every upload, including the failed chunk's, was deleted
    assert sorted(fake_io["deleted"]) == sorted(fake_io["uploads"])
    assert len(fake_io["uploads"]) == 3


def test_entrance_outside_its_chunk_counts_out_of_range(fake_io):
    fake_io["duration"] = 30.0
    fake_io["entrances_for_call"] = {
        1: [
            {"t": 5.0, "side": "left", "name": "in", "category": "c", "has_visible_steps": False},
            {"t": 70.0, "side": "left", "name": "out", "category": "c", "has_visible_steps": False},
        ],
    }
    result = rse.run_clip(Path("clip.mp4"))

    assert result["out_of_range"] == 1
    assert [e["t"] for e in result["entrances"]] == [5.0, 70.0]  # kept in the list
    assert result["errors"] == []


def test_main_writes_report_after_each_clip(fake_io, monkeypatch):
    clips = [
        ClipTruth(file="a.mp4", entrances=[Entrance(t=1.0, name="x", step_count=0, has_ramp=False, door_type="d")],
                  tactile_ranges=[], obstacles=[]),
        ClipTruth(file="b.mp4", entrances=[], tactile_ranges=[], obstacles=[]),
    ]
    monkeypatch.setattr(rse, "load_truth", lambda: clips)
    calls = {"n": 0}

    def fake_run_clip(path):
        calls["n"] += 1
        if calls["n"] == 2:
            # by the time the second clip runs, the first clip's results must already be on disk
            saved = json.loads((config.OUT / "spark_entrances.json").read_text(encoding="utf-8"))
            assert list(saved) == ["a.mp4"]
            raise KeyboardInterrupt  # stop the run here; the first report must survive
        return {"duration_s": 10.0, "entrances": [{"t": 1.0}], "obstacle_types": [],
                "errors": [], "out_of_range": 0}

    monkeypatch.setattr(rse, "run_clip", fake_run_clip)
    with pytest.raises(KeyboardInterrupt):
        rse.main()
    saved = json.loads((config.OUT / "spark_entrances.json").read_text(encoding="utf-8"))
    assert list(saved) == ["a.mp4"]
    assert saved["a.mp4"]["hits"] == 1
