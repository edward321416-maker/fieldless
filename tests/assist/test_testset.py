import json

import pytest

from assist import testset

QUESTIONS = {"warning_present": {"means": "has domes", "sections": ["705.1"], "defect_when": "no"},
             "warning_contrast": {"means": "domes contrast", "sections": ["705.1.3"], "defect_when": "no"}}


def write(tmp_path, photos, questions=QUESTIONS):
    data = {"name": "t", "target": "curb ramp", "questions": questions, "photos": photos}
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


def test_confirm_score_wants_yes_on_the_target_and_no_on_false_finds(tmp_path):
    ts = write(tmp_path, [p("a", "yes"), p("b", "yes"), p("c", "yes"), p("d", "yes"), p("n", "no"), p("m", "no"),
                          p("k", "no"), p("u", "unclear")], questions={})
    s = testset.score_confirm(ts, {"a": "yes", "b": "no", "c": "unclear", "n": "no", "m": "yes", "k": "unclear",
                                   "u": "yes"})  # d: SAM missed it; u: the label is unclear, so it does not count
    assert (s["total_visible"], s["found_visible"], s["confirmed"], s["rejected_wrongly"], s["unclear"]) == \
        (4, 3, 1, 1, 1)
    assert (s["total_negatives"], s["false_finds"], s["false_finds_rejected"], s["false_finds_unclear"]) == \
        (3, 3, 1, 1)
    assert (s["kept_true"], s["kept_false"]) == (1, 1)
    assert s["wrong_visible"] == [{"photo_id": "b", "answer": "no"}, {"photo_id": "c", "answer": "unclear"}]
    assert s["wrong_negatives"] == [{"photo_id": "m", "answer": "yes"}, {"photo_id": "k", "answer": "unclear"}]


def test_a_labelled_question_covers_only_the_sections_it_lists(tmp_path):
    # An item may be scored only against the question for its own requirement (2026-10-28 run: the agent rewrote a
    # 406.5 "projects into traffic" item into a detectable-warning question to fit the labels).
    ts = write(tmp_path, [p("a", "yes", "yes")])
    assert ts.key_for("705.1.3") == "warning_contrast" and ts.key_for("705.1") == "warning_present"
    assert ts.key_for("406.5") is None and ts.key_for("705.1.1") is None
    assert ts.questions["warning_contrast"].defect_when == "no"


def test_bad_values_and_unknown_keys_are_refused(tmp_path):
    with pytest.raises(ValueError, match="yes, no or unclear"):
        write(tmp_path, [p("a", "maybe")])
    with pytest.raises(ValueError, match="unknown question keys"):
        write(tmp_path, [{"id": "a", "target_visible": "yes", "answers": {"colour": "yes"}}])
    with pytest.raises(ValueError, match="defect_when"):
        write(tmp_path, [], {"q": {"means": "x", "sections": ["1"], "defect_when": "maybe"}})
    ts = write(tmp_path, [p("a", "yes", "yes")])
    with pytest.raises(ValueError, match="unknown question key"):
        testset.score_answers(ts, "colour", {})


def test_the_seattle_set_loads():
    ts = testset.load("seattle_ramps")
    assert ts.target == "curb ramp" and {"warning_present", "warning_contrast"} <= set(ts.questions)
    assert ts.key_for("705.1.3") == "warning_contrast"
    assert len(ts.visible()) >= 10 and len(ts.negatives()) >= 4
