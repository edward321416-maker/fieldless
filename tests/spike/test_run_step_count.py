import json
from pathlib import Path

import pytest

from spike import config, mapillary, video
from spike import meta_client as mc
from spike import run_step_count as rsc
from spike.truth import ClipTruth, Entrance


def _truth(entrances):
    return [ClipTruth(
        file="a.mp4",
        entrances=[Entrance(t=t, name=f"door{i}", step_count=s, has_ramp=False, door_type="swing")
                   for i, (t, s) in enumerate(entrances)],
        tactile_ranges=[],
        obstacles=[],
    )]


def _spark_result(step_count):
    return {"step_count": step_count, "has_ramp": False, "door_type": "swing", "confidence": 0.9, "notes": "n"}


@pytest.fixture
def io(monkeypatch, tmp_path):
    """Offline stand-ins: temp OUT/CLIPS, a fake frame grab, a fake original download and a fake Spark call."""
    out, clips = tmp_path / "out", tmp_path / "clips"
    out.mkdir()
    (clips / "a").mkdir(parents=True)
    monkeypatch.setattr(config, "OUT", out)
    monkeypatch.setattr(config, "CLIPS", clips)
    state = {"frame_t": [], "original": [], "spark_calls": 0, "spark_steps": {}, "fail_spark_on": set(),
             "entrances": [(10.0, 2), (20.0, 3), (30.0, 1)]}

    def frame_at(src, t, dst):
        state["frame_t"].append(t)
        Path(dst).write_bytes(b"jpeg")
        return Path(dst)

    def original_image(image_id, dst, max_side=2560):
        state["original"].append(image_id)
        Path(dst).write_bytes(b"jpeg")
        return Path(dst)

    def spark_json(parts, schema_name, schema, model=None):
        state["spark_calls"] += 1
        n = state["spark_calls"]
        if n in state["fail_spark_on"]:
            raise RuntimeError("400 bad request")
        return _spark_result(state["spark_steps"].get(n, 0))

    monkeypatch.setattr(video, "frame_at", frame_at)
    monkeypatch.setattr(mapillary, "original_image", original_image)
    monkeypatch.setattr(mc, "spark_json", spark_json)
    monkeypatch.setattr(rsc, "load_truth", lambda: _truth(state["entrances"]))
    return state


def _write_sam(rows, best_phrase):
    (config.OUT / "sam_stairs.json").write_text(json.dumps({
        "rows": rows, "rates": {}, "counts": {}, "best_phrase": best_phrase,
    }), encoding="utf-8")


def _row(index, phrases=None, **extra):
    return {"clip": "a.mp4", "index": index, "t": 0.0, "window_start": 0.0, "step_count": 2,
            "phrases": phrases if phrases is not None else {}, **extra}


def test_errored_sam_phrase_falls_back_to_truth_t(io):
    _write_sam([
        _row(0, {"stairs": {"detected": True, "max_c": 0.9, "best_frame_t": 9.0}}),
        _row(1, {"stairs": {"error": "RuntimeError: 500"}}),  # per-phrase error: no best_frame_t
        _row(2, {}, error="RuntimeError: window cut failed"),  # row-level error: no phrases at all
    ], best_phrase="stairs")
    io["spark_steps"] = {1: 2, 2: 3, 3: 1}

    rsc.main()

    saved = json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))
    # row 0 uses SAM's best_frame_t; rows 1 and 2 (errored SAM) fall back to their truth t
    assert io["frame_t"] == [9.0, 20.0, 30.0]
    assert len(saved["rows"]) == 3
    assert [r["step_count"] for r in saved["rows"]] == [2, 3, 1]
    assert all("error" not in r for r in saved["rows"])
    assert saved["scored"] == 3 and saved["failed"] == 0


def test_errored_phrase_on_second_entrance_uses_its_truth_t(io):
    _write_sam([_row(1, {"stairs": {"error": "boom"}})], best_phrase="stairs")
    io["entrances"] = [(10.0, 2), (20.0, 3)]
    io["spark_steps"] = {1: 2}

    rsc.main()

    assert io["frame_t"] == [20.0]  # second entrance (index 1) at its truth t


def test_spark_failure_yields_error_row_and_accuracy_over_others(io):
    _write_sam([_row(0), _row(1), _row(2)], best_phrase=None)
    io["fail_spark_on"] = {2}
    io["spark_steps"] = {1: 2, 3: 0}  # truth is 2, 3, 1 -> first correct, third wrong

    rsc.main()

    saved = json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))
    failed = saved["rows"][1]
    assert failed["error"].startswith("RuntimeError: 400 bad request")
    assert failed["correct"] is None and failed["step_count"] is None
    assert saved["scored"] == 2 and saved["failed"] == 1
    assert saved["accuracy"] == pytest.approx(0.5)  # 1 of 2 non-error rows


@pytest.mark.parametrize("predicted, expected", [(2, True), (3, False), (1, False)])
def test_correct_is_exact_match_with_truth(io, predicted, expected):
    _write_sam([_row(0)], best_phrase=None)
    io["entrances"] = [(10.0, 2)]
    io["spark_steps"] = {1: predicted}

    rsc.main()

    saved = json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))
    assert saved["rows"][0]["correct"] is expected
    assert saved["rows"][0]["truth_steps"] == 2


def test_print_failure_cannot_turn_a_scored_row_into_an_error_row(io, monkeypatch):
    _write_sam([_row(0)], best_phrase=None)
    io["entrances"] = [(10.0, 2)]
    io["spark_steps"] = {1: 2}
    monkeypatch.setattr(rsc, "SAVE_EVERY", 1)

    def broken_print(*args, **kwargs):
        raise UnicodeEncodeError("cp949", "Café", 3, 4, "illegal multibyte sequence")

    monkeypatch.setattr("builtins.print", broken_print)
    with pytest.raises(UnicodeEncodeError):
        rsc.main()  # the console failure surfaces instead of being recorded as the row's error

    saved = json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))
    assert "error" not in saved["rows"][0]
    assert saved["rows"][0]["correct"] is True and saved["scored"] == 1


def test_frames_json_selects_photo_by_t(io, monkeypatch):
    frames = {"frames": [{"id": f"img{i}"} for i in range(40)]}
    (config.CLIPS / "a" / "frames.json").write_text(json.dumps(frames), encoding="utf-8")
    _write_sam([_row(0, {"stairs": {"detected": True, "max_c": 0.9, "best_frame_t": 12.4}})], best_phrase="stairs")
    io["entrances"] = [(10.0, 2)]
    io["spark_steps"] = {1: 2}

    rsc.main()

    assert io["original"] == ["img12"]  # round(12.4 * 1 fps) == photo 12
    assert io["frame_t"] == []
    saved = json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))
    assert saved["rows"][0]["image_id"] == "img12"
