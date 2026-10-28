import dataclasses

import pytest

from pipeline import geo, survey_checks, survey_items
from pipeline.cost import CostCapReached
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text
from tests.pipeline.test_survey_items import INVENTORY as INVENTORY_TOML

# the curb ramp check as found by photos: most tests here cover that path; the video tests are at the end
CHECK = dataclasses.replace(survey_items.load("US").check("curb_ramp_warning"), find_by="photos")
LIGHT = survey_items.parse(INVENTORY_TOML, "us.toml").checks[0]
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
    ({**YES, "visible": False}, None),  # no curb ramp in the photo (a driveway, a plain curb): dropped, see run_check
])
def test_status_follows_report_when(answer, expected):
    assert survey_checks.status(CHECK, answer) == expected


@pytest.mark.parametrize("answer, expected", [
    ({**YES}, ("auto", [], "yes")),
    ({**NO}, None),
    ({**NO, "answer": "unclear"}, ("needs_review", ["unclear"], "unclear")),
    ({**YES, "visible": False}, None),  # the target is not there: SAM found a look-alike, so the pick is dropped
])
def test_status_of_an_inventory_check(answer, expected):
    assert survey_checks.status(LIGHT, answer) == expected


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
    assert missing == {"find": [0], "ask": [], "not_target": []}
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
    assert missing == {"find": [], "ask": [4], "not_target": []}
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
    assert rows == [] and missing == {"find": [], "ask": [0], "not_target": []}

    def capped(prompt, media):
        raise CostCapReached("cap")

    with pytest.raises(CostCapReached):
        survey_checks.run_check(FakeApi(sam=capped), CHECK, {0: [0, 1, 2]}, photos, by_idx, workers=1)


def test_run_check_drops_inventory_picks_that_are_not_the_target(tmp_path):
    by_idx = frames(9)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam(prompt, media):
        return found if media.name in ("2.jpg", "8.jpg") else ""

    answers = iter([YES, NO])  # photo 2 is a streetlight pole, photo 8 is not

    def spark(schema_name, parts):
        return next(answers)

    rows, missing = survey_checks.run_check(FakeApi(spark=spark, sam=sam), LIGHT, {0: list(range(9))}, photos, by_idx, 1)
    assert [(r["frame_idx"], r["object_label"], r["review_status"]) for r in rows] == [(2, "yes", "auto")]
    assert missing == {"find": [], "ask": [], "not_target": [8]}
    assert rows[0]["issue_type"] == "streetlight" and set(rows[0]["model_outputs"]) == {"check", "find"}


def test_a_defect_check_drops_what_is_not_there_keeps_a_record_and_sends_a_seeded_tenth_to_a_person(tmp_path):
    by_idx = frames(48)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    shown = {f"{i}.jpg" for i in range(0, 48, 4)}  # 12 separate sightings, 20 m apart

    def sam(prompt, media):
        return found if media.name in shown else ""

    def spark(schema_name, parts):
        return {**YES, "visible": False}  # a driveway every time

    def run(seed):
        dropped = []
        rows, missing = survey_checks.run_check(FakeApi(spark=spark, sam=sam), CHECK, {0: list(range(48))}, photos,
                                                by_idx, 1, audit_seed=seed, dropped=dropped)
        return rows, missing, dropped

    rows, missing, dropped = run(4)
    assert len(missing["not_target"]) == 12 and len(dropped) == 12
    assert [d["issue_type"] for d in dropped] == ["curb_ramp_warning"] * 12
    assert dropped[0]["model_outputs"]["check"]["visible"] is False and "mask_area" in dropped[0]["model_outputs"]["find"]
    audited = [d["frame_idx"] for d in dropped if d["audited"]]
    assert len(audited) == 2  # ceil(10% of 12)
    assert [(r["frame_idx"], r["review_status"], r["review_reasons"], r["object_label"]) for r in rows] == [
        (i, "needs_review", ["dropped_audit"], "unclear") for i in audited]
    assert [d["frame_idx"] for d in run(4)[2] if d["audited"]] == audited  # the scan id fixes the sample
    assert run(None)[0] and len(run(None)[0]) == 2


def test_an_inventory_check_drops_without_an_audit(tmp_path):
    by_idx = frames(9)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    dropped = []
    rows, missing = survey_checks.run_check(
        FakeApi(spark=lambda schema_name, parts: NO, sam=lambda p, m: found if m.name in ("2.jpg", "8.jpg") else ""),
        LIGHT, {0: list(range(9))}, photos, by_idx, 1, audit_seed=3, dropped=dropped)
    assert rows == [] and [(d["frame_idx"], d["audited"]) for d in dropped] == [(2, False), (8, False)]


def walk_m(piece, by_idx):
    return sum(geo.haversine_m(by_idx[a]["lat"], by_idx[a]["lng"], by_idx[b]["lat"], by_idx[b]["lng"])
               for a, b in zip(piece, piece[1:]))


def test_split_by_distance_cuts_a_run_into_pieces_of_at_most_that_many_metres():
    by_idx = frames(9, step_m=5.0)
    # photos [0, 2, 4, 6, 8] are 10 m apart: 0->2 walks 10, 2->4 would make 20 > 15, so 4 starts a piece and the
    # walk starts again from it (4->6 is 10, 6->8 would make 20 > 15).
    pieces = survey_checks.split_by_distance([0, 2, 4, 6, 8], by_idx, 15.0)
    assert pieces == [[0, 2], [4, 6], [8]]
    assert [i for piece in pieces for i in piece] == [0, 2, 4, 6, 8]  # nothing lost, in order
    for meters in (4.0, 12.0, 15.0, 21.0, 40.0, 100.0):
        for piece in survey_checks.split_by_distance(list(range(9)), by_idx, meters):
            assert len(piece) == 1 or walk_m(piece, by_idx) <= meters
    assert survey_checks.split_by_distance([0, 2, 4, 6, 8], by_idx, 4.0) == [[0], [2], [4], [6], [8]]  # gaps > meters
    assert survey_checks.split_by_distance([0, 2, 4, 6, 8], by_idx, 500.0) == [[0, 2, 4, 6, 8]]
    assert survey_checks.split_by_distance([3], by_idx, 15.0) == [[3]]


def run_light(tmp_path, check, by_idx, idxs):
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    api = FakeApi(spark=lambda schema_name, parts: YES, sam=lambda p, m: found)
    rows, _ = survey_checks.run_check(api, check, {0: idxs}, photos, by_idx, workers=1)
    return rows


def test_group_m_gives_one_long_run_of_poles_more_than_one_row(tmp_path):
    by_idx = frames(17, step_m=5.0)  # one SAM run over photos 0-16, sampled every 2: 10 m between sampled photos
    plain = run_light(tmp_path, LIGHT, by_idx, list(range(17)))
    assert len(plain) == 1 and plain[0]["model_outputs"]["find"]["group_idxs"] == list(range(0, 17, 2))
    rows = run_light(tmp_path, dataclasses.replace(LIGHT, group_m=15.0), by_idx, list(range(17)))
    assert [r["model_outputs"]["find"]["group_idxs"] for r in rows] == [[0, 2], [4, 6], [8, 10], [12, 14], [16]]
    assert [r["frame_idx"] for r in rows] == [0, 4, 8, 12, 16]  # the earlier photo on equal areas
    assert [(r["frame_start"], r["frame_end"]) for r in rows] == [(0, 2), (4, 6), (8, 10), (12, 14), (16, 16)]
    assert {r["issue_type"] for r in rows} == {"streetlight"}


def test_group_m_pieces_whose_best_photos_are_near_each_other_still_merge(tmp_path):
    by_idx = frames(9, step_m=3.0)  # sampled photos 6 m apart; group_m 10 cuts after every second one
    check = dataclasses.replace(LIGHT, group_m=10.0)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    sizes = {"2.jpg": 60, "4.jpg": 40, "6.jpg": 20, "8.jpg": 10, "0.jpg": 10}

    def sam(prompt, media):
        return text([line(0, [record("0", 0, 0, sizes[media.name], sizes[media.name], 160, 120, 0.9)])])

    rows, _ = survey_checks.run_check(FakeApi(spark=lambda s, p: YES, sam=sam), check, {0: list(range(9))}, photos,
                                      by_idx, workers=1)
    # pieces [0,2] [4,6] [8]: best photos 2, 4 (6 m apart, so 4 is the same pole as 2) and 8 (18 m from photo 2)
    assert [r["frame_idx"] for r in rows] == [2, 8]


def test_known_areas_are_not_asked_again_and_the_new_ones_are_added(tmp_path):
    by_idx = frames(9)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])

    def sam(prompt, media):
        if media.name == "6.jpg":
            raise RuntimeError("timeout")  # this one fails: not saved, asked again next time
        return found if media.name in ("2.jpg", "4.jpg") else ""

    api = FakeApi(spark=lambda s, p: YES, sam=sam)
    known = {0: 0, 2: 1600}  # photo 2 was measured before; the new ones must not replace it
    rows, missing = survey_checks.run_check(api, LIGHT, {0: list(range(9))}, photos, by_idx, 1, known=known)
    asked = [c[2] for c in api.calls if c[0] == "sam_image"]
    assert asked == ["4.jpg", "6.jpg", "8.jpg"]  # not the sampled photos 0 and 2, which were known
    assert known == {0: 0, 2: 1600, 4: 1600, 8: 0}  # 6 failed: not saved
    assert missing["find"] == [6] and [r["frame_idx"] for r in rows] == [2]  # photos 2 and 4 make one run, 2 is earlier
    assert survey_checks.run_check(FakeApi(spark=lambda s, p: YES, sam=sam), LIGHT, {0: list(range(9))}, photos,
                                   by_idx, 1)[1]["find"] == [6]  # without `known`: everything is asked as before



GLIMMER = "meta-models/Muse-Glimmer-30B"


class FakeJudge:
    """An OpenModel stand-in: answers by photo name; an Exception value is raised."""

    def __init__(self, answers):
        self.answers = answers
        self.model = GLIMMER
        self.asked: list[str] = []
        self.woken = 0

    def wake(self):
        self.woken += 1

    def answer(self, check, photo, box=None):
        self.asked.append(photo.name)
        result = self.answers[photo.name]
        if isinstance(result, Exception):
            raise result
        return {**result, "model": self.model}


def judged(tmp_path, judge, spark_answers, share, seed=7):
    """Five picks (photos 0, 4, 8, 12, 16) of one run of poles with group_m 15; Spark answers by photo."""
    by_idx = frames(17, step_m=5.0)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    found = text([line(0, [record("0", 0, 0, 40, 40, 160, 120, 0.9)])])
    by_image = {survey_checks.image_part(photos[i])["image_url"]: f"{i}.jpg" for i in photos}
    spark_asked: list[str] = []

    def spark(schema_name, parts):
        name = by_image[parts[0]["image_url"]]
        spark_asked.append(name)
        result = spark_answers[name]
        if isinstance(result, Exception):
            raise result
        return result

    api = FakeApi(spark=spark, sam=lambda p, m: found)
    rows, missing = survey_checks.run_check(api, dataclasses.replace(LIGHT, group_m=15.0), {0: list(range(17))},
                                            photos, by_idx, workers=1, judge=judge, audit_seed=seed,
                                            audit_share=share)
    return {r["frame_idx"]: r for r in rows}, missing, spark_asked


def test_the_judge_answers_first_and_spark_takes_what_it_cannot(tmp_path):
    judge = FakeJudge({"0.jpg": YES, "4.jpg": YES, "8.jpg": NO, "12.jpg": {**YES, "answer": "unclear"},
                       "16.jpg": TimeoutError("cold start")})
    rows, missing, spark_asked = judged(tmp_path, judge, {"12.jpg": YES, "16.jpg": YES}, share=0.0)
    assert judge.woken == 1 and judge.asked == ["0.jpg", "4.jpg", "8.jpg", "12.jpg", "16.jpg"]
    assert sorted(spark_asked) == ["12.jpg", "16.jpg"]  # only what the judge could not decide
    assert sorted(rows) == [0, 4, 12, 16] and missing["not_target"] == [8]
    assert rows[0]["model_outputs"]["check"]["model"] == GLIMMER and rows[0]["review_status"] == "auto"
    assert rows[12]["model_outputs"]["check"]["model"] == survey_checks.config.SPARK_MODEL
    assert rows[12]["model_outputs"]["judge"] == {"model": GLIMMER, "answer": "unclear"}
    assert "cold start" in rows[16]["model_outputs"]["judge"]["error"]
    assert missing["judge"] == {"model": GLIMMER, "answered": 3, "fallback": 2, "audit": {"asked": 0, "agree": 0}}


def test_an_audit_keeps_the_judges_row_and_flags_a_disagreement_either_way(tmp_path):
    judge = FakeJudge({"0.jpg": YES, "4.jpg": YES, "8.jpg": NO, "12.jpg": NO, "16.jpg": YES})
    spark = {"0.jpg": YES, "4.jpg": NO, "8.jpg": YES, "12.jpg": NO, "16.jpg": RuntimeError("503")}
    rows, missing, spark_asked = judged(tmp_path, judge, spark, share=1.0)
    assert sorted(spark_asked) == ["0.jpg", "12.jpg", "16.jpg", "4.jpg", "8.jpg"]
    assert (rows[0]["review_status"], rows[0]["model_outputs"]["audit"]["agree"]) == ("auto", True)
    # the judge kept 4, Spark says no: the judge's row stays, first in line for a person
    assert rows[4]["model_outputs"]["check"]["model"] == GLIMMER and rows[4]["object_label"] == "yes"
    assert (rows[4]["review_status"], rows[4]["review_reasons"]) == ("needs_review", ["models_disagree"])
    assert rows[4]["model_outputs"]["audit"] == {"model": survey_checks.config.SPARK_MODEL, "answer": "no",
                                                 "agree": False, "confidence": 0.8, "note": "no domes",
                                                 "box": NO["box"]}
    # the judge dropped 8, Spark says yes: a row is made from Spark's answer, for a person to settle
    assert rows[8]["model_outputs"]["check"]["model"] == survey_checks.config.SPARK_MODEL
    assert (rows[8]["review_status"], rows[8]["review_reasons"]) == ("needs_review", ["models_disagree"])
    assert rows[8]["model_outputs"]["audit"]["model"] == GLIMMER and rows[8]["model_outputs"]["audit"]["agree"] is False
    assert 12 not in rows and missing["not_target"] == [12]  # both said no
    assert rows[16]["review_status"] == "auto" and "503" in rows[16]["model_outputs"]["audit"]["error"]
    assert missing["judge"]["audit"] == {"asked": 4, "agree": 2}


def test_the_audit_picks_a_tenth_of_the_judges_answers_the_same_way_for_the_same_scan(tmp_path):
    answers = {f"{i}.jpg": YES for i in (0, 4, 8, 12, 16)}
    first = judged(tmp_path / "a", FakeJudge(answers), answers, share=0.1, seed=4)[2]
    again = judged(tmp_path / "b", FakeJudge(answers), answers, share=0.1, seed=4)[2]
    assert len(first) == 1 and first == again  # ceil(10% of 5) = 1, chosen by the scan id



def test_after_three_failures_in_a_row_spark_answers_the_rest_without_waiting_on_the_judge(tmp_path):
    judge = FakeJudge({f"{i}.jpg": TimeoutError("hung") for i in (0, 4, 8, 12, 16)})
    rows, missing, spark_asked = judged(tmp_path, judge, {f"{i}.jpg": YES for i in (0, 4, 8, 12, 16)}, share=0.1)
    assert judge.asked == ["0.jpg", "4.jpg", "8.jpg"]  # workers=1: the fourth pick no longer asks the judge
    assert len(spark_asked) == 5 and len(rows) == 5
    assert "gave up" in rows[16]["model_outputs"]["judge"]["error"]
    assert missing["judge"]["fallback"] == 5


# Found by video: SAM follows each object through the walk's photos joined as a video.
RAMP_V = survey_items.load("US").check("curb_ramp_warning")  # find_by = "video"


def walk(n, first=0, clip=0, step_m=2.0, compass=0.0):
    """n photos of one walk going north, 160 x 120, every photo looking `compass`."""
    return {i: {"idx": i, "clip": clip, "lat": 47.6130 + (i - first) * step_m / 111_320, "lng": -122.32,
                "compass": compass, "width": 160, "height": 120, "captured_at": f"2022-08-30T10:00:{i:02d}Z"}
            for i in range(first, first + n)}


def vrec(obj, x1, x2, conf=0.9, y1=40, y2=200):
    """An object in a 320 x 240 video frame (twice the photos' size)."""
    return record(obj, x1, y1, x2, y2, 320, 240, conf)


def test_tracks_are_the_objects_sam_followed_with_photo_boxes_and_areas():
    video = survey_checks.Video((0, 1, 2), (320, 240), 0)
    answer = text([line(0, [vrec("1", 32, 64)]), line(1, [vrec("1", 64, 128), vrec("2", 160, 192)]),
                   line(2, [vrec("1", 96, 160, conf=0.2)])])  # below the confidence cut
    first, second = survey_checks.tracks(answer, video, walk(3))
    assert [d["idx"] for d in first] == [0, 1] and [d["idx"] for d in second] == [1]
    assert first[1]["box"] == [200, 167, 400, 833]  # 0-1000 of the photo
    assert first[1]["area"] == 64 * 160 // 4  # the mask's area in photo pixels (the video is twice the size)
    with pytest.raises(ValueError):
        survey_checks.tracks(text([line(5, [vrec("1", 0, 10)])]), video, walk(3))  # frame 5 of a 3-frame video


def test_a_track_is_judged_on_its_largest_view_that_the_photo_edge_does_not_cut():
    track = [{"idx": 3, "area": 900, "box": [0, 100, 300, 900]},  # largest, but cut off on the left
             {"idx": 1, "area": 400, "box": [400, 100, 600, 900]}, {"idx": 2, "area": 500, "box": [500, 100, 700, 995]}]
    assert survey_checks.track_pick(track) == {"idx": 1, "area": 400, "group": [1, 2, 3], "box": [400, 100, 600, 900]}
    cut = [{"idx": 5, "area": 900, "box": [0, 0, 300, 999]}, {"idx": 6, "area": 100, "box": [995, 0, 1000, 10]}]
    assert survey_checks.track_pick(cut)["idx"] == 5  # every view is cut: the largest


def test_an_object_sam_lost_for_a_photo_or_that_crosses_into_the_next_video_is_one_object():
    by_idx = walk(10)
    a, b = survey_checks.Video((0, 1, 2, 3, 4), (320, 240), 0), survey_checks.Video((5, 6, 7, 8, 9), (320, 240), 0)
    seen = lambda idx, x: {"idx": idx, "area": 100, "box": [x - 20, 100, x + 20, 900]}  # noqa: E731
    pole = [seen(3, 500), seen(4, 500)]  # reaches the end of video a
    pole_on = [seen(5, 560)]  # the next photo, 4.2 degrees off: the same pole
    tree = [seen(5, 900)]  # 28 degrees off: another object
    early = [seen(0, 300)]  # SAM loses it after photo 0 ...
    early_again = [seen(2, 310)]  # ... and finds it again two photos on, 0.7 degrees off
    too_late = [seen(5, 300)]  # three photos after it was last seen (photo 2): another object
    joined = survey_checks.join([(b, [pole_on, tree, too_late]), (a, [early, pole, early_again])], by_idx)
    assert sorted([d["idx"] for d in t] for t in joined) == [[0, 2], [3, 4, 5], [5], [5]]
    other_walk = survey_checks.Video((5, 6, 7, 8, 9), (320, 240), 1)
    assert len(survey_checks.join([(a, [pole]), (other_walk, [pole_on])], by_idx)) == 2  # another walk: not joined


def test_parts_of_one_thing_seen_together_are_one_object_but_a_thing_behind_another_is_not():
    by_idx = walk(3)
    video = survey_checks.Video((0, 1, 2), (320, 240), 0)
    crown = [{"idx": i, "area": 900, "box": [300, 0, 700, 900]} for i in (0, 1, 2)]
    trunk = [{"idx": i, "area": 100, "box": [480, 500, 520, 900]} for i in (0, 1)]  # inside the crown in both photos
    behind = [{"idx": 0, "area": 50, "box": [400, 100, 450, 300]}, {"idx": 1, "area": 50, "box": [650, 100, 760, 300]}]
    parts = survey_checks.join([(video, [crown, trunk, behind])], by_idx)
    assert sorted(len(t) for t in parts) == [2, 5]  # crown and trunk are one; the far thing shifted out of the crown


def test_two_walks_that_saw_one_object_from_the_same_place_give_one_row():
    by_idx = {**walk(2, clip=0), **walk(2, first=10, clip=1)}  # photo 10 stands where photo 0 does
    pick = lambda idx, area, x=500: {"idx": idx, "area": area, "group": [idx], "box": [x - 20, 0, x + 20, 900]}  # noqa: E731
    kept = survey_checks.merge_walks([pick(0, 100), pick(10, 300), pick(1, 50)], by_idx)
    assert [p["idx"] for p in kept] == [1, 10]  # 0 and 10 are one object (the larger kept); 1 is its walk's own
    apart = survey_checks.merge_walks([pick(0, 100), pick(10, 300, x=900)], by_idx)  # 28 degrees apart
    assert [p["idx"] for p in apart] == [0, 10]


def test_the_prompt_names_the_box_sam_marked():
    p = survey_checks.prompt(RAMP_V, [1, 2, 3, 4])
    assert "marked a possible curb ramp at [1, 2, 3, 4]" in p and RAMP_V.ask in p
    assert "marked" not in survey_checks.prompt(RAMP_V)


def test_run_check_by_video_makes_one_row_per_followed_object_and_asks_about_its_box(tmp_path):
    by_idx = walk(8)
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in by_idx}
    videos = {0: survey_checks.Video((0, 1, 2, 3), (320, 240), 0), 4: survey_checks.Video((4, 5, 6, 7), (320, 240), 0)}
    texts = {0: text([line(0, [vrec("1", 100, 140)]), line(1, [vrec("1", 120, 180)])]), 4: None}  # video 4 failed
    asked = []

    def spark(schema_name, parts):
        asked.append(parts[1]["text"])
        return NO

    api = FakeApi(spark=spark, sam=lambda prompt, media: pytest.fail("SAM was asked; video answers come from the stage"))
    rows, missing = survey_checks.run_check(api, RAMP_V, {0: list(range(8))}, photos, by_idx, workers=1,
                                            video=(texts, videos))
    assert missing == {"find": [4], "ask": [], "not_target": []}
    (row,) = rows
    assert (row["frame_idx"], row["frame_start"], row["frame_end"], row["object_label"]) == (1, 0, 1, "no")
    assert row["model_outputs"]["find"] == {"phrase": "curb cut", "mask_area": 60 * 160 // 4, "group_idxs": [0, 1],
                                            "box": [375, 167, 562, 833]}
    assert "at [375, 167, 562, 833]" in asked[0]
    with pytest.raises(ValueError, match="found by video"):
        survey_checks.run_check(api, RAMP_V, {0: list(range(8))}, photos, by_idx, workers=1)
