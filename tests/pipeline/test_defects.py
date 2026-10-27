import pytest

from pipeline import defects
from pipeline.cost import CostCapReached
from tests.pipeline.fakes import FakeApi, synthetic_photo

CLIP = list(range(20))
FRAMES = {i: {"lat": 37.5 + i * 1e-5, "lng": 127.0, "captured_at": f"2025-09-24T10:00:{i:02d}Z"} for i in CLIP}
FLAGS = {0: False, 2: True, 4: True, 6: True, 8: True, 10: True, 12: True, 14: False, 16: None, 18: True}


def answer(line_end="none", damage="none", visible=True, conf=0.8, box=()):
    return {"tactile_visible": visible, "line_end": line_end, "damage": damage, "box": list(box),
            "confidence": conf, "note": ""}


def test_candidates_are_run_edges_next_to_known_empty_photos_and_samples_inside_runs():
    assert defects.candidates(FLAGS, CLIP) == [(2, "edge"), (6, "sample"), (12, "edge")]


def test_a_walk_end_or_a_failed_request_is_not_an_edge():
    assert defects.candidates({0: True, 2: True}, [0, 1, 2]) == []
    assert defects.candidates({0: None, 2: True, 4: None}, [0, 1, 2, 3, 4]) == []


def test_a_single_tactile_photo_between_empty_ones_is_one_edge():
    assert defects.candidates({0: False, 2: True, 4: False}, [0, 1, 2, 3, 4]) == [(2, "edge")]


@pytest.mark.parametrize("ans, found", [
    (answer(damage="missing"), "missing_blocks"),
    (answer(damage="cut_by_cover"), "cover_cut"),
    (answer(damage="covered"), None),  # a mat or debris: not a durable defect
    (answer(damage="broken"), None),
    (answer(damage="worn"), None),
    (answer(line_end="abrupt"), None),
    (answer(line_end="unclear", damage="unclear"), None),
    (answer(damage="missing", visible=False), None),
])
def test_defect_of_keeps_only_the_two_classes_a_surveyor_confirms_from_a_photo(ans, found):
    assert defects.defect_of(ans) == found


def test_the_schema_separates_covers_from_mats_and_debris():
    damage = defects.SCHEMA["properties"]["damage"]["enum"]
    assert "cut_by_cover" in damage and "covered" in damage
    assert '"cut_by_cover" (a manhole, utility cover or metal plate' in defects.PROMPT


def test_check_photo_sends_the_photo_then_the_prompt(tmp_path):
    seen = {}

    def spark(name, parts):
        seen["name"], seen["parts"] = name, parts
        return answer()

    defects.check_photo(FakeApi(spark=spark), synthetic_photo(tmp_path / "p.jpg"))
    assert seen["name"] == "tactile_condition"
    assert [p["type"] for p in seen["parts"]] == ["input_image", "input_text"]
    assert seen["parts"][1]["text"] == defects.PROMPT


def test_find_defects_makes_review_rows_merges_repeats_and_reports_failures(tmp_path, monkeypatch):
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in CLIP}
    answers = {2: answer(damage="missing", box=(375, 670, 575, 825)), 4: answer(damage="missing"),
               6: answer(damage="worn"), 12: answer(damage="cut_by_cover", conf=0.7)}

    def fake_check(api, photo):
        idx = int(photo.stem)
        if idx == 14:
            raise RuntimeError("stream cut")
        return answers[idx]

    monkeypatch.setattr(defects, "check_photo", fake_check)
    cands = [(2, "edge"), (4, "sample"), (6, "sample"), (12, "edge"), (14, "edge")]
    rows, failed, checks = defects.find_defects(FakeApi(), cands, photos, FRAMES, CLIP, workers=2)
    assert failed == [14]
    assert [(c["frame_idx"], c["candidate"]) for c in checks] == [(2, "edge"), (4, "sample"), (6, "sample"), (12, "edge")]
    assert checks[2]["check"]["damage"] == "worn"
    assert [(r["issue_type"], r["object_label"], r["frame_idx"]) for r in rows] == [("missing_blocks", None, 2),
                                                                                  ("cover_cut", None, 12)]
    assert rows[0]["model_outputs"] == {"check": answers[2], "candidate": "edge"}
    row = rows[1]
    assert (row["frame_start"], row["frame_end"], row["review_status"], row["review_reasons"]) == (
        12, 12, "needs_review", ["possible_defect"])
    assert (row["lat"], row["lng"], row["confidence"]) == (FRAMES[12]["lat"], 127.0, 0.7)
    assert row["captured_at"] == FRAMES[12]["captured_at"]


def test_the_cost_cap_stops_the_checks(tmp_path, monkeypatch):
    def capped(api, photo):
        raise CostCapReached("cost cap $3.00")

    monkeypatch.setattr(defects, "check_photo", capped)
    photos = {2: synthetic_photo(tmp_path / "2.jpg")}
    with pytest.raises(CostCapReached):
        defects.find_defects(FakeApi(), [(2, "edge")], photos, FRAMES, CLIP, workers=1)
