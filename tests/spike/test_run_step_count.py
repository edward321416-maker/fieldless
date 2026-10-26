import json
from pathlib import Path

import pytest

from spike import config, mapillary, video
from spike import meta_client as mc
from spike import run_step_count as rsc
from spike.truth import ClipTruth, Entrance, truth_sha256


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
    (clips / "truth.json").write_text('{"clips": {}}', encoding="utf-8")  # only hashed; load_truth is faked
    monkeypatch.setattr(config, "OUT", out)
    monkeypatch.setattr(config, "CLIPS", clips)
    state = {"frame_t": [], "original": [], "spark_calls": 0, "spark_steps": {}, "fail_spark_on": set(),
             "entrances": list(DEFAULT_ENTRANCES)}

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


def _write_sam(rows, best_phrase, truth_hash=None):
    (config.OUT / "sam_stairs.json").write_text(json.dumps({
        "rows": rows, "rates": {}, "counts": {}, "best_phrase": best_phrase,
        "truth_sha256": truth_hash or truth_sha256(),  # made from the current truth.json unless a test says not
    }), encoding="utf-8")


DEFAULT_ENTRANCES = [(10.0, 2), (20.0, 3), (30.0, 1)]  # the fixture's truth; tests only ever shorten it


def _row(index, phrases=None, **extra):
    # A sam_stairs row carries its entrance's truth t and step_count; run_step_count checks them against truth.json.
    t, steps = DEFAULT_ENTRANCES[index]
    row = {"clip": "a.mp4", "index": index, "t": t, "window_start": max(0.0, t - 2), "step_count": steps,
           "phrases": phrases if phrases is not None else {}}
    return {**row, **extra}


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
        if "pred" in args:  # the scored row's line
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


def _saved() -> dict:
    return json.loads((config.OUT / "step_count.json").read_text(encoding="utf-8"))


def test_output_records_the_truth_hash(io):
    _write_sam([_row(0)], best_phrase=None)
    io["entrances"] = [(10.0, 2)]
    rsc.main()
    assert _saved()["truth_sha256"] == truth_sha256()


def test_stale_row_is_an_error_and_never_sent_to_spark(io):
    # truth.json was edited after run_sam_stairs: entrance 1 moved, entrance 2 changed its step count
    _write_sam([_row(0), _row(1, t=25.0), _row(2, step_count=4)], best_phrase=None)
    io["spark_steps"] = {1: 2}

    rsc.main()

    rows = _saved()["rows"]
    assert "error" not in rows[0] and rows[0]["correct"] is True
    for row in rows[1:]:
        assert row["error"].startswith("truth mismatch: sam_stairs.json is stale")
        assert row["step_count"] is None and row["correct"] is None
    assert io["spark_calls"] == 1  # only the row that still matches was paid for
    assert _saved()["scored"] == 1 and _saved()["failed"] == 2


def test_row_missing_from_truth_is_an_error_not_a_crash(io):
    _write_sam([_row(0), {**_row(0), "index": 7}, {**_row(0), "clip": "gone.mp4"}], best_phrase=None)
    io["spark_steps"] = {1: 2}

    rsc.main()

    rows = _saved()["rows"]
    assert "error" not in rows[0]
    for row in rows[1:]:
        assert row["error"].startswith("truth mismatch: sam_stairs.json is stale")
        assert row["truth_steps"] is None
    assert io["spark_calls"] == 1


def test_warns_when_sam_stairs_was_made_from_another_truth(io, capsys):
    _write_sam([_row(0)], best_phrase=None, truth_hash="0" * 64)
    io["entrances"] = [(10.0, 2)]
    rsc.main()
    assert "different truth.json" in capsys.readouterr().out
    assert "error" not in _saved()["rows"][0]  # the row itself still matches, so it is scored
