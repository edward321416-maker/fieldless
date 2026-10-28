import json
from pathlib import Path

import pytest

from assist import testset
from assist.tools import Bench
from pipeline.meta_api import image_part
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


POLE = {"id": "streetlight_pole", "inventory": True, "target": "streetlight pole", "find": "utility pole"}


def answer(visible, said, note=""):
    return {"visible": visible, "answer": said, "box": [], "confidence": 0.8, "note": note}


ANSWERS = {"a": answer(True, "yes"), "b": answer(False, "yes", "no lamp head in view"),
           "d": answer(True, "no", "a plain wooden pole"), "c": answer(True, "yes"),
           "n": answer(True, "no"), "m": answer(True, "yes", "looks like a lamp"),
           "k": answer(False, "no", "a sign post"), "u": answer(True, "yes")}


def make_inventory(tmp_path):
    """Photos a, b, c, d show a streetlight pole and n, m, k do not; u is labelled unclear. "utility pole" finds
    everything but c; "pole" finds everything."""
    labels = {"a": "yes", "b": "yes", "c": "yes", "d": "yes", "n": "no", "m": "no", "k": "no", "u": "unclear"}
    data = {"name": "poles", "target": "streetlight pole", "questions": {},
            "photos": [{"id": i, "target_visible": v} for i, v in labels.items()]}
    (tmp_path / "poles.json").write_text(json.dumps(data), encoding="utf-8")
    ts = testset.load("poles", tmp_path)
    paths = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=n + 1) for n, i in enumerate(labels)}
    by_image = {image_part(path)["image_url"]: i for i, path in paths.items()}

    def sam(prompt, media):
        if prompt == "utility pole" and media.stem == "c":
            return ""
        return sam_text.text([sam_text.line(0, [sam_text.record("0", 10, 10, 50, 40, 160, 120, 0.9)])])

    api = FakeApi(spark=lambda name, parts: ANSWERS[by_image[parts[0]["image_url"]]], sam=sam)
    return api, Bench(api, ts, items=[POLE], photo=lambda i: paths[i], workers=1)


def test_confirm_wants_yes_on_the_target_and_no_where_sam_fired_without_it(tmp_path):
    api, bench = make_inventory(tmp_path)
    r = bench.confirm("streetlight_pole", " utility pole ", "Is this a streetlight pole? ")
    assert (r["found_visible"], r["confirmed"], r["rejected_wrongly"], r["unclear"]) == (3, 1, 2, 0)
    assert (r["false_finds"], r["false_finds_rejected"], r["false_finds_unclear"]) == (3, 2, 0)
    assert (r["kept_true"], r["kept_false"], r["total_visible"], r["total_negatives"]) == (1, 1, 4, 3)
    # b, k: visible=false drops the pick like a "no", as the pipeline does for inventory checks (585d1de);
    # u: the label is unclear, so Spark is not asked
    assert r["wrong_visible"] == [{"photo_id": "b", "answer": "no", "note": "no lamp head in view"},
                                  {"photo_id": "d", "answer": "no", "note": "a plain wooden pole"}]
    assert [(w["photo_id"], w["answer"]) for w in r["wrong_negatives"]] == [("m", "yes")]
    assert sum(1 for c in api.calls if c[0] == "spark") == 6
    assert "whether the streetlight pole is visible" in bench.prompts[0]  # Spark is asked about the target
    assert bench.last("score_confirm", find="utility pole", ask="Is this a streetlight pole?")["kept_true"] == 1
    assert bench.last("score_find", phrase="utility pole")["found_visible"] == 3


def test_confirm_reuses_answers_when_only_the_phrase_changes(tmp_path):
    api, bench = make_inventory(tmp_path)
    bench.confirm("streetlight_pole", "utility pole", "Is this a streetlight pole?")
    r = bench.confirm("streetlight_pole", "pole", "Is this a streetlight pole?")
    assert (r["found_visible"], r["kept_true"]) == (4, 2)
    assert sum(1 for c in api.calls if c[0] == "spark") == 7  # only photo c is new
    with pytest.raises(ValueError, match="unknown item"):
        bench.confirm("nope", "pole", "?")


def test_confirm_asks_the_injected_judge_and_never_spark(tmp_path):
    api, bench = make_inventory(tmp_path)
    asked = []

    class Judge:
        def answer(self, check, photo):
            asked.append((check.id, check.ask, check.target, Path(photo).stem))
            return {**ANSWERS[Path(photo).stem], "model": "glimmer"}

    bench = Bench(api, bench.ts, items=[POLE], photo=bench.photo, workers=1, judge=Judge())
    r = bench.confirm("streetlight_pole", "utility pole", "Is this a streetlight pole?")
    assert not [c for c in api.calls if c[0] == "spark"]
    assert sorted(a[3] for a in asked) == ["a", "b", "d", "k", "m", "n"]  # SAM's hits; u is labelled unclear
    assert asked[0][:3] == ("streetlight_pole", "Is this a streetlight pole?", "streetlight pole")
    assert (r["kept_true"], r["kept_false"]) == (1, 1)  # the same answers Spark gave in the test above


def test_a_check_answer_that_does_not_see_the_target_is_a_miss_as_in_a_scan(tmp_path):
    # A scan drops a defect-check pick whose answer says the target is not in the photo (survey_checks.status), so
    # on a labelled photo that shows it, the item lost it: scored wrong, not unclear.
    answer = {"visible": False, "answer": "yes", "box": [], "confidence": 0.9, "note": "no ramp here"}
    _, bench = make(tmp_path, answer)
    r = bench.ask("warning_present", "curb cut", "Does it have domes?")
    assert (r["right"], r["unclear"]) == (0, 0)
    assert r["wrong"] == [{"photo_id": "a", "truth": "yes", "answer": "dropped", "note": "no ramp here"}]
