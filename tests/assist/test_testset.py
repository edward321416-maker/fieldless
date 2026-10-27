import json

import pytest

from assist import testset


def write(tmp_path, photos):
    data = {"name": "t", "target": "curb ramp", "questions": {"warning_present": "has domes"}, "photos": photos}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")
    return testset.load("t", tmp_path)


def p(i, visible, answer=None):
    return {"id": i, "target_visible": visible, "answers": {} if answer is None else {"warning_present": answer}}


def test_find_score_counts_found_visible_photos_and_false_finds(tmp_path):
    ts = write(tmp_path, [p("a", "yes"), p("b", "yes"), p("c", "no"), p("d", "unclear")])
    s = testset.score_find(ts, {"a": True, "b": False, "c": True, "d": True})
    assert (s["found_visible"], s["total_visible"], s["false_on_negatives"], s["total_negatives"]) == (1, 2, 1, 1)
    assert s["missed"] == ["b"]


def test_answer_score_uses_decidable_visible_photos_and_polarity(tmp_path):
    ts = write(tmp_path, [p("a", "yes", "yes"), p("b", "yes", "no"), p("c", "yes", "unclear"), p("d", "no"),
                          p("e", "yes", "yes")])
    s = testset.score_answers(ts, "warning_present", {"a": "yes", "b": "yes", "c": "no"})
    assert (s["right"], s["decidable"], s["unclear"], s["unchecked"]) == (1, 3, 0, 1)
    assert s["wrong"] == [{"photo_id": "b", "truth": "no", "answer": "yes"}]
    flipped = testset.score_answers(ts, "warning_present", {"a": "no", "b": "yes", "e": "unclear"},
                                    polarity="opposite")
    assert (flipped["right"], flipped["unclear"], flipped["unchecked"]) == (2, 1, 0)


def test_bad_values_and_unknown_keys_are_refused(tmp_path):
    with pytest.raises(ValueError, match="yes, no or unclear"):
        write(tmp_path, [p("a", "maybe")])
    with pytest.raises(ValueError, match="unknown question keys"):
        write(tmp_path, [{"id": "a", "target_visible": "yes", "answers": {"colour": "yes"}}])
    ts = write(tmp_path, [p("a", "yes", "yes")])
    with pytest.raises(ValueError, match="unknown question key"):
        testset.score_answers(ts, "colour", {})


def test_the_seattle_set_loads():
    ts = testset.load("seattle_ramps")
    assert ts.target == "curb ramp" and {"warning_present", "warning_contrast"} <= set(ts.questions)
    assert len(ts.visible()) >= 10 and len(ts.negatives()) >= 4
