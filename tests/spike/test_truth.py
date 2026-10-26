import json

from spike.truth import in_ranges, load_truth, match_times


def test_match_times_counts_hits_misses_and_false_positives():
    assert match_times([1.0, 5.2, 9.0, 20.0], [1.5, 5.0, 12.0], tol=2.0) == (2, 1, 2)


def test_match_times_is_one_to_one():
    assert match_times([5.0], [4.5, 5.5], tol=2.0) == (1, 1, 0)


def test_in_ranges():
    assert in_ranges(3.0, [(1.0, 4.0)]) and not in_ranges(5.0, [(1.0, 4.0)])


def test_load_truth(tmp_path):
    p = tmp_path / "truth.json"
    p.write_text(json.dumps({"clips": {"a.mp4": {
        "entrances": [{"t": 3.0, "name": "x", "step_count": 2, "has_ramp": False, "door_type": "swing"}],
        "tactile_ranges": [[1, 4]],
        "obstacles": [{"t_start": 2.0, "t_end": 3.0, "label": "bicycle", "on_tactile": True}],
    }}}), encoding="utf-8")
    clips = load_truth(p)
    assert clips[0].file == "a.mp4"
    assert clips[0].entrances[0].step_count == 2
    assert clips[0].tactile_ranges == [(1.0, 4.0)]
    assert clips[0].obstacles[0].label == "bicycle"
