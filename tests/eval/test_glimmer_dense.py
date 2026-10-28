import json

import pytest

from eval import glimmer_dense as dense
from pipeline import survey_checks
from pipeline.survey_items import Check

M_LAT = 1 / 111_320  # degrees of latitude in one metre


def walk(n=40, step_m=2.0, clips=(0,)):
    """n photos per clip, step_m apart going north; each clip starts 200 m further east."""
    frames = []
    for c in clips:
        for k in range(n):
            frames.append({"idx": c * 1000 + k, "clip": c, "image_id": f"img{c}_{k}", "lat": 47.6 + k * step_m * M_LAT,
                           "lng": -122.3 + c * 0.003, "compass": 0.0, "captured_at": "2022-08-30T00:00:00Z"})
    return frames


def pole(group_m=15):
    return Check(id="pole", find="street light pole", target="pole", ask="Is it a pole?", report_when="",
                 label_yes="Pole", label_no="", label_unclear="Pole: unclear", inventory=True, group_m=group_m)


def areas_for(frames, gap=(14,)):
    # SAM finds the object on every sampled photo but those in `gap`; the area peaks every 5 photos
    return {f["idx"]: (0 if f["idx"] % 1000 in gap else 100 + (f["idx"] % 5) * 10) for f in frames}


@pytest.mark.parametrize("group_m", [15, 8, None])
def test_picks_match_what_run_check_picks_from_the_same_saved_areas(monkeypatch, group_m):
    frames = walk(clips=(0, 1))
    areas = areas_for(frames)
    by_idx = {f["idx"]: f for f in frames}
    clips = {}
    for f in frames:
        clips.setdefault(f["clip"], []).append(f["idx"])

    class Api:
        def sam_image(self, *a):
            raise AssertionError("every sampled photo has a saved area")

        def spark_json(self, *a):
            return {"visible": True, "answer": "yes", "box": [], "confidence": 0.9, "note": ""}

    monkeypatch.setattr(survey_checks, "image_part", lambda path: {})
    rows, _ = survey_checks.run_check(Api(), pole(group_m), clips, {i: None for i in by_idx}, by_idx, workers=2,
                                      known=dict(areas))
    assert sorted(p["idx"] for p in dense.picks_at(frames, areas, group_m)) == sorted(r["frame_idx"] for r in rows)


def test_a_shorter_spacing_never_makes_fewer_picks_and_a_missing_area_ends_a_run():
    frames = walk()
    areas = areas_for(frames)
    assert len(dense.picks_at(frames, areas, 8)) >= len(dense.picks_at(frames, areas, 15)) >= 2
    one_run = dense.picks_at(frames, {i: a or 100 for i, a in areas.items()}, None)
    two_runs = dense.picks_at(frames, {i: a for i, a in areas.items() if i != 14}, None)
    assert len(one_run) == 1 and len(two_runs) == 2


def test_rows_drop_no_keep_yes_and_keep_unclear_or_failed_for_a_person():
    picks = [{"idx": i, "area": 1, "group": [i]} for i in (1, 2, 3, 4, 5)]
    answers = {1: {"visible": True, "answer": "yes", "box": [1, 2, 3, 4]},
               2: {"visible": True, "answer": "no", "box": []},
               3: {"visible": False, "answer": "yes", "box": []},
               4: {"visible": True, "answer": "unclear", "box": []},
               5: {"error": "timeout"}}
    rows = {r["frame_idx"]: r for r in dense.rows_from(picks, answers, "pole")}
    assert sorted(rows) == [1, 4, 5]
    assert rows[1]["object_label"] == "yes" and rows[1]["review_status"] == "auto"
    assert rows[1]["model_outputs"]["check"]["box"] == [1, 2, 3, 4]
    assert rows[4]["review_status"] == rows[5]["review_status"] == "needs_review"
    assert all(r["id"] < 0 and r["issue_type"] == "pole" for r in rows.values())


def test_saved_areas_for_another_phrase_are_refused(tmp_path):
    (tmp_path / "find_pole.json").write_text(json.dumps({"phrase": "pole", "areas": {"0": 5}}), encoding="utf-8")
    with pytest.raises(ValueError):
        dense.cached_areas(tmp_path, pole())
    (tmp_path / "find_pole.json").write_text(json.dumps({"phrase": "street light pole", "areas": {"0": 5}}),
                                             encoding="utf-8")
    assert dense.cached_areas(tmp_path, pole()) == {0: 5}


def test_scan_picks_are_rows_dropped_and_failed_photos():
    data = {"issues": [{"issue_type": "pole", "frame_idx": 3}, {"issue_type": "tree", "frame_idx": 9}]}
    assert dense.scan_picks(data, {"pole": {"not_target": [5], "ask": [7]}}, "pole") == {3, 5, 7}


def test_the_spark_judge_asks_the_scans_question_with_the_scans_schema(monkeypatch):
    sent = {}

    class Api:
        def spark_json(self, parts, name, schema):
            sent.update(parts=parts, name=name, schema=schema)
            return {"visible": True, "answer": "yes", "box": [], "confidence": 1.0, "note": ""}

    monkeypatch.setattr(dense, "image_part", lambda path: {"image": str(path)})
    assert dense.SparkJudge(Api()).answer(pole(), "p.jpg")["answer"] == "yes"
    assert sent["name"] == "survey_check" and sent["schema"] is survey_checks.SCHEMA
    assert sent["parts"][0] == {"image": "p.jpg"} and "Is it a pole?" in str(sent["parts"][1])
