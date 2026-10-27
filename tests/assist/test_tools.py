import json

from assist import testset
from assist.tools import Bench
from tests.pipeline import sam_text
from tests.pipeline.fakes import FakeApi, synthetic_photo


def make(tmp_path, spark_answer):
    data = {"name": "t", "target": "curb ramp", "questions": {"warning_present": "has domes"},
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
    return api, Bench(api, ts, photo=lambda i: tmp_path / f"{i}.jpg", workers=1)


def test_find_scores_the_phrase_and_caches_sam_calls(tmp_path):
    api, bench = make(tmp_path, {})
    r = bench.find("curb cut")
    assert (r["found_visible"], r["total_visible"], r["false_on_negatives"], r["missed"]) == (1, 2, 1, ["b"])
    bench.find(" curb cut ")
    assert sum(1 for c in api.calls if c[0] == "sam_image") == 3  # each photo once


def test_ask_only_where_sam_found_the_target_and_score_it(tmp_path):
    answer = {"visible": True, "answer": "yes", "box": [], "confidence": 0.9, "note": "yellow domes"}
    api, bench = make(tmp_path, answer)
    r = bench.ask("curb cut", "Does it have domes?", "warning_present")
    assert (r["right"], r["decidable"], r["unchecked"]) == (1, 2, 1)
    assert [c for c in api.calls if c[0] == "spark"] == [("spark", "survey_check")]
    assert bench.last("score_ask", find="curb cut", ask="Does it have domes?")["right"] == 1
    assert bench.last("score_ask", find="curb ramp") is None
