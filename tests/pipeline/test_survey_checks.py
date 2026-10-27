import pytest

from pipeline import survey_checks, survey_items
from pipeline.cost import CostCapReached
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text

CHECK = survey_items.load("US").check("curb_ramp_warning")
NO = {"visible": True, "answer": "no", "box": [400, 600, 600, 900], "confidence": 0.8, "note": "no domes"}
YES = {"visible": True, "answer": "yes", "box": [100, 600, 300, 900], "confidence": 0.9, "note": "yellow domes"}


def frames(n, step_m=5.0):
    """n photos of one walk going north, step_m apart."""
    return {i: {"idx": i, "lat": 47.6130 + i * step_m / 111_320, "lng": -122.32, "captured_at": f"2022-08-30T10:00:{i:02d}Z"}
            for i in range(n)}


def test_groups_are_runs_of_sampled_photos_that_show_the_object():
    areas = {0: 0, 2: 500, 4: 900, 6: 0, 8: 300, 10: None, 12: 200}
    assert survey_checks.groups(areas, list(range(13))) == [[2, 4], [8], [12]]
    assert survey_checks.best([2, 4], areas) == 4
    assert survey_checks.best([8, 12], {8: 300, 12: 300}) == 8  # a tie keeps the earlier photo


def test_merge_near_keeps_the_larger_of_two_close_sightings():
    by_idx = {**frames(2, step_m=3.0), **{i + 10: {**f, "idx": i + 10} for i, f in frames(2, step_m=30.0).items()}}
    picks = [{"idx": 0, "area": 100, "group": [0]}, {"idx": 1, "area": 400, "group": [1]},  # 3 m apart
             {"idx": 11, "area": 50, "group": [11]}]  # 30 m from photo 10, which is at photo 0's spot
    kept = survey_checks.merge_near(picks, by_idx)
    assert [p["idx"] for p in kept] == [1, 11]


@pytest.mark.parametrize("answer, expected", [
    (NO, ("needs_review", ["possible_defect"], "no")),
    (YES, ("auto", [], "yes")),
    ({**NO, "answer": "unclear"}, ("needs_review", ["unclear"], "unclear")),
    ({**YES, "visible": False}, ("needs_review", ["unclear"], "unclear")),
])
def test_status_follows_report_when(answer, expected):
    assert survey_checks.status(CHECK, answer) == expected


def test_the_prompt_carries_the_check_sentences():
    p = survey_checks.prompt(CHECK)
    assert CHECK.ask in p and "whether the curb ramp is visible" in p and "0-1000" in p


def test_run_check_finds_groups_asks_and_builds_contract_rows(tmp_path):
    by_idx = frames(9)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    areas = {"2.jpg": (40, 40), "4.jpg": (80, 80), "8.jpg": (60, 60)}  # photo 6 shows nothing: 2-4 and 8 are two ramps

    def sam(prompt, media):
        assert prompt == "curb cut"
        if media.name == "0.jpg":
            raise RuntimeError("stream failed")
        if media.name in areas:
            w, h = areas[media.name]
            return text([line(0, [record("0", 0, 0, w, h, 160, 120, 0.9)])])
        return ""

    answers = iter([NO, YES])  # questions run in pick order (photo 4, then photo 8) with workers=1

    def spark(schema_name, parts):
        assert schema_name == "survey_check"
        return next(answers)

    api = FakeApi(spark=spark, sam=sam)
    rows, missing = survey_checks.run_check(api, CHECK, {0: list(range(9))}, photos, by_idx, workers=1)
    assert missing == {"find": [0], "ask": []}
    assert [(r["frame_idx"], r["frame_start"], r["frame_end"]) for r in rows] == [(4, 2, 4), (8, 8, 8)]
    first = rows[0]
    assert first["issue_type"] == "curb_ramp_warning" and first["object_label"] == "no"
    assert (first["review_status"], first["review_reasons"]) == ("needs_review", ["possible_defect"])
    assert first["model_outputs"]["check"] == NO
    assert first["model_outputs"]["find"] == {"phrase": "curb cut", "mask_area": 6400, "group_idxs": [2, 4]}
    assert (first["lat"], first["lng"], first["captured_at"]) == (by_idx[4]["lat"], -122.32, by_idx[4]["captured_at"])
    assert rows[1]["object_label"] == "yes" and rows[1]["review_status"] == "auto"


def test_a_wrong_shape_answer_marks_only_its_object(tmp_path):
    by_idx = frames(9)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    areas = {"2.jpg": (40, 40), "4.jpg": (80, 80), "8.jpg": (60, 60)}  # two ramps: photos 2-4 and 8

    def sam(prompt, media):
        if media.name in areas:
            w, h = areas[media.name]
            return text([line(0, [record("0", 0, 0, w, h, 160, 120, 0.9)])])
        return ""

    answers = iter([{}, YES])  # the first object's answer is empty: reported, the second object is still made
    api = FakeApi(spark=lambda schema_name, parts: next(answers), sam=sam)
    rows, missing = survey_checks.run_check(api, CHECK, {0: list(range(9))}, photos, by_idx, workers=1)
    assert missing == {"find": [], "ask": [4]}
    assert [(r["frame_idx"], r["object_label"]) for r in rows] == [(8, "yes")]



def test_an_answer_without_confidence_still_makes_its_row(tmp_path):
    by_idx = frames(3)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    api = FakeApi(spark=lambda schema_name, parts: {"visible": True, "answer": "no"}, sam=lambda p, m: found)
    (row,), missing = survey_checks.run_check(api, CHECK, {0: [0, 1, 2]}, photos, by_idx, workers=1)
    assert row["object_label"] == "no" and row["confidence"] is None and missing["ask"] == []

def test_a_failed_question_is_reported_and_the_cost_cap_stops_the_run(tmp_path):
    by_idx = frames(3)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def broken(schema_name, parts):
        raise RuntimeError("bad json")

    rows, missing = survey_checks.run_check(FakeApi(spark=broken, sam=lambda p, m: found), CHECK, {0: [0, 1, 2]},
                                            photos, by_idx, workers=1)
    assert rows == [] and missing == {"find": [], "ask": [0]}

    def capped(prompt, media):
        raise CostCapReached("cap")

    with pytest.raises(CostCapReached):
        survey_checks.run_check(FakeApi(sam=capped), CHECK, {0: [0, 1, 2]}, photos, by_idx, workers=1)
