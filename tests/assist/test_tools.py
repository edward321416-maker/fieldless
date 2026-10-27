import json

import pytest

from assist import testset
from assist.tools import Bench
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo

ITEMS = [{"id": "warning_present", "section": "705.1", "target": "curb ramp"},
         {"id": "ramp_projection", "section": "406.5", "target": "curb ramp"}]


def make(tmp_path, spark_answer):
    data = {"name": "t", "target": "curb ramp",
            "questions": {"warning_present": {"means": "has domes", "sections": ["705.1"], "defect_when": "no"}},
            "photos": [{"id": "a", "target_visible": "yes", "answers": {"warning_present": "yes"}},
                       {"id": "b", "target_visible": "yes", "answers": {"warning_present": "no"}},
                       {"id": "n", "target_visible": "no", "answers": {}}]}
    (tmp_path / "t.json").write_text(json.dumps(data), encoding="utf-8")
    ts = testset.load("t", tmp_path)
    for seed, name in enumerate("abn"):
        synthetic_photo(tmp_path / f"{name}.jpg", seed=seed)

    def sam(prompt, media):  # finds the ramp in a, wrongly fires on n, misses b
        if prompt != "curb cut" or media.stem == "b":
            return ""
        return sam_text.text([sam_text.line(0, [sam_text.record("0", 10, 10, 50, 40, 160, 120, 0.9)])])

    api = FakeApi(spark=lambda name, parts: spark_answer, sam=sam)
    return api, Bench(api, ts, items=ITEMS, photo=lambda i: tmp_path / f"{i}.jpg", workers=1)


def test_find_scores_the_phrase_and_caches_sam_calls(tmp_path):
    api, bench = make(tmp_path, {})
    r = bench.find("curb cut")
    assert (r["found_visible"], r["total_visible"], r["false_on_negatives"], r["missed"]) == (1, 2, 1, ["b"])
    bench.find(" curb cut ")
    assert sum(1 for c in api.calls if c[0] == "sam_image") == 3  # each photo once


def test_ask_scores_an_item_against_the_question_for_its_own_section(tmp_path):
    answer = {"visible": True, "answer": "yes", "box": [], "confidence": 0.9, "note": "yellow domes"}
    api, bench = make(tmp_path, answer)
    r = bench.ask("warning_present", "curb cut", "Does it have domes?")
    assert r["key"] == "warning_present" and (r["right"], r["decidable"], r["unchecked"]) == (1, 2, 1)
    assert [c for c in api.calls if c[0] == "spark"] == [("spark", "survey_check")]
    assert "whether the curb ramp is visible" in bench.prompts[0]  # Spark is asked about the target, not "curb cut"
    assert bench.last("score_ask", item_id="warning_present", find="curb cut")["right"] == 1
    assert bench.last("score_ask", item_id="ramp_projection") is None


def test_an_item_without_a_labelled_question_cannot_be_scored(tmp_path):
    api, bench = make(tmp_path, {})
    with pytest.raises(ValueError, match="no labelled question covers section 406.5"):
        bench.ask("ramp_projection", "curb cut", "Does it have domes?")
    with pytest.raises(ValueError, match="unknown item"):
        bench.ask("nope", "curb cut", "?")
    assert not [c for c in api.calls if c[0] == "spark"]
