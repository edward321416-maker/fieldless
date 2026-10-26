import json
from types import SimpleNamespace

import pytest

from spike import config, preflight, video


@pytest.fixture
def clips(monkeypatch, tmp_path):
    """A temp clips dir with two 60-photo clips; video.probe is faked from a per-file duration table."""
    folder = tmp_path / "clips"
    folder.mkdir()
    durations = {"street_a.mp4": 60.0, "street_b.mp4": 60.0}
    for name in durations:
        (folder / name).write_bytes(b"mp4")
    monkeypatch.setattr(config, "CLIPS", folder)
    monkeypatch.setattr(video, "probe", lambda path: SimpleNamespace(duration_s=durations[path.name]))
    return folder


def entrance(t=10, steps=1, door="swing", **extra):
    return {"t": t, "name": "Café", "step_count": steps, "has_ramp": False, "door_type": door, **extra}


def obstacle(t_start=5, t_end=7, label="bicycle", **extra):
    return {"t_start": t_start, "t_end": t_end, "label": label, "on_tactile": True, **extra}


def truth(**clips):
    return {"clips": {name: {"entrances": [], "tactile_ranges": [], "obstacles": [], "labeling_seconds": 0, **c}
                      for name, c in clips.items()}}


def test_valid_truth_has_no_errors_and_counts_samples(clips):
    raw = truth(
        **{"street_a.mp4": {"entrances": [entrance(t=3, steps=0), entrance(t=10, steps=1), entrance(t=59, steps=3)],
                            "labeling_seconds": 120},
           "street_b.mp4": {"tactile_ranges": [[0, 9], [20, 20]], "obstacles": [obstacle(), obstacle(8, 8)],
                            "labeling_seconds": 30}})
    errors, counts = preflight.check_truth(raw, clips)
    assert errors == []
    assert counts["clips"]["street_a.mp4"] == {"photos": 60, "entrances": 3, "stepped": 2, "steps_2plus": 1,
                                               "tactile_photos": 0, "obstacle_events": 0, "labeling_seconds": 120}
    assert counts["clips"]["street_b.mp4"]["tactile_photos"] == 11  # 10 + 1 photos, ends inclusive
    assert counts["total"] == {"photos": 120, "entrances": 3, "stepped": 2, "steps_2plus": 1, "tactile_photos": 11,
                               "obstacle_events": 2, "labeling_seconds": 150}


@pytest.mark.parametrize("clip, expected", [
    ({"entrances": [entrance(t=60)]}, "entrance 0: t 60 is outside the clip (photos 0..59)"),
    ({"entrances": [entrance(t=-1)]}, "entrance 0: t -1 is outside the clip"),
    ({"entrances": [entrance(t=10.5)]}, "entrance 0: t 10.5 is not a whole photo number"),
    ({"entrances": [entrance(steps=-1)]}, "entrance 0: step_count -1 is not a whole number >= 0"),
    ({"entrances": [entrance(steps=1.5)]}, "entrance 0: step_count 1.5 is not a whole number >= 0"),
    ({"entrances": [entrance(steps=True)]}, "entrance 0: step_count True is not a whole number >= 0"),
    ({"entrances": [entrance(door="glass")]}, "entrance 0: door_type 'glass' is not one of"),
    ({"entrances": [entrance(note="x")]}, "entrance 0: unknown keys ['note']"),
    ({"entrances": [{"t": 3}]}, "entrance 0: missing ['door_type', 'has_ramp', 'name', 'step_count']"),
    ({"tactile_ranges": [[9, 3]]}, "tactile range 0: start 9 is after end 3"),
    ({"tactile_ranges": [[3]]}, "tactile range 0: [3] is not a pair of numbers"),
    ({"tactile_ranges": [[50, 70]]}, "tactile range 0: 70 is outside the clip"),
    ({"obstacles": [obstacle(t_start=9, t_end=4)]}, "obstacle 0: t_start 9 is after t_end 4"),
    ({"obstacles": [obstacle(t_end=60)]}, "obstacle 0: t_end 60 is outside the clip"),
    ({"obstacles": [obstacle(t_start=2.5)]}, "obstacle 0: t_start 2.5 is not a whole photo number"),
    ({"obstacles": [obstacle(label="  ")]}, "obstacle 0: label '  ' is empty"),
    ({"entrances": {"t": 3}}, "entrances is not a list"),
])
def test_each_rule_reports_the_clip_and_the_item(clips, clip, expected):
    errors, _ = preflight.check_truth(truth(**{"street_a.mp4": clip}), clips)
    assert len(errors) == 1, errors
    assert errors[0].startswith("street_a.mp4: " + expected)


def test_missing_clip_file_is_an_error_and_its_length_unknown(clips):
    errors, counts = preflight.check_truth(truth(**{"nowhere.mp4": {"entrances": [entrance(t=500)]}}), clips)
    assert errors == [f"nowhere.mp4: clip file not found: {clips / 'nowhere.mp4'}"]  # t not range-checked
    assert counts["clips"]["nowhere.mp4"]["photos"] is None


def test_empty_truth_is_an_error(clips):
    assert preflight.check_truth({"clips": {}}, clips)[0] == ["truth.json has no clips"]
    assert preflight.check_truth([], clips)[0] == ["truth.json has no clips"]


def test_warnings_for_too_few_samples():
    low = {"stepped": 9, "obstacle_events": 9, "tactile_photos": 29}
    warnings = preflight.sample_warnings(low)
    assert len(warnings) == 3 and all(w.startswith("WARNING: ") for w in warnings)
    assert preflight.sample_warnings({"stepped": 10, "obstacle_events": 10, "tactile_photos": 30}) == []


def write_truth(clips, raw):
    path = clips / "truth.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_main_exits_0_on_valid_truth_and_prints_counts_and_warnings(clips, capsys):
    write_truth(clips, truth(**{"street_a.mp4": {"entrances": [entrance()], "labeling_seconds": 42}}))
    assert preflight.main([]) == 0
    printed = capsys.readouterr().out
    assert "street_a.mp4" in printed and "TOTAL" in printed and "42" in printed
    assert "WARNING: only 1 stepped entrances" in printed
    assert "OK: " in printed


def test_main_exits_1_on_validation_errors(clips, capsys):
    path = write_truth(clips, truth(**{"street_a.mp4": {"entrances": [entrance(door="glass")]}}))
    assert preflight.main(["--truth", str(path)]) == 1
    printed = capsys.readouterr().out
    assert "ERROR: street_a.mp4: entrance 0: door_type 'glass'" in printed
    assert "1 validation error(s)" in printed


def test_main_exits_1_when_truth_is_unreadable(clips, capsys):
    (clips / "truth.json").write_text("{not json", encoding="utf-8")
    assert preflight.main([]) == 1
    assert "ERROR: cannot read" in capsys.readouterr().out
