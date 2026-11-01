import dataclasses
import random

import pytest

from eval import second_look
from pipeline import geo


def frame(idx, metres, compass=0.0, bearing=180.0):
    """A photo metres from the point (47.6, -122.3), bearing degrees from it, facing compass."""
    lat, lng = geo.offset_point(47.6, -122.3, bearing, metres)
    return {"idx": idx, "lat": lat, "lng": lng, "compass": compass, "image_id": str(idx), "captured_at": "2022-08-30T00:00:00Z"}


POINT = {"lat": 47.6, "lng": -122.3}


def test_the_angle_to_a_point_is_measured_from_the_middle_of_the_photo():
    assert second_look.off_centre(frame(1, 10.0), POINT) == pytest.approx(0, abs=0.1)  # south of it, facing north
    assert second_look.off_centre(frame(1, 10.0, compass=20.0), POINT) == pytest.approx(-20, abs=0.1)
    assert second_look.off_centre(frame(1, 10.0, compass=340.0), POINT) == pytest.approx(20, abs=0.1)


def test_a_view_says_how_far_across_the_photo_the_point_should_be():
    middle = second_look.view(frame(1, 10.0), POINT)
    left = second_look.view(frame(1, 10.0, compass=20.0), POINT)
    assert middle["pct"] == pytest.approx(50, abs=0.2) and left["pct"] < 40 and middle["dist_m"] == pytest.approx(10, abs=0.2)


@dataclasses.dataclass
class Item:
    target: str = "city pole"
    ask: str = ""


def test_the_question_points_to_the_side_the_record_is_on():
    assert "toward the left" in second_look.area_check(Item(), {"pct": 20, "dist_m": 12.0}).ask
    assert "toward the right" in second_look.area_check(Item(), {"pct": 80, "dist_m": 12.0}).ask
    assert "toward the middle" in second_look.area_check(Item(), {"pct": 50, "dist_m": 12.0}).ask


def test_far_views_stay_between_the_near_and_far_limits_and_a_few_photos_apart():
    frames = [frame(1, 2.0), frame(2, 12.0), frame(3, 12.5), frame(8, 16.0), frame(20, 60.0)]
    assert [v["frame_idx"] for v in second_look.far_views(POINT, frames)] == [2, 8]


def test_a_photo_looking_away_is_not_a_far_view():
    assert second_look.far_views(POINT, [frame(1, 12.0, compass=120.0)]) == []


def test_a_box_must_lie_toward_the_record():
    middle = {"box": [450, 100, 550, 900]}
    far_side = {"box": [950, 100, 1000, 900]}
    assert second_look.looks_at(middle, frame(1, 10.0), POINT)
    assert not second_look.looks_at(far_side, frame(1, 10.0), POINT)
    assert not second_look.looks_at({"box": []}, frame(1, 10.0), POINT)


def test_seen_takes_a_visible_yes_from_both_models():
    yes = {"answer": "yes", "visible": True}
    assert second_look.is_seen({"spark": yes, "open": yes})
    assert not second_look.is_seen({"spark": yes, "open": {"answer": "no", "visible": True}})
    assert not second_look.is_seen({"spark": {"answer": "yes", "visible": False}, "open": yes})
    assert not second_look.is_seen({"spark": yes})


def test_a_control_spot_keeps_clear_of_the_records_and_of_the_surveys_findings():
    frames = [frame(i, 40.0 + i * 20, compass=90.0) for i in range(12)]
    rows = [{"frame_idx": 3, "model_outputs": {"find": {"group_idxs": [7]}}}]
    records = [{"lat": frames[9]["lat"], "lng": frames[9]["lng"]}]
    spots = second_look.control_spots(frames, records, rows, 3, random.Random(5))
    assert len(spots) == 3
    for spot in spots:
        assert all(geo.haversine_m(spot["lat"], spot["lng"], r["lat"], r["lng"]) >= second_look.CLEAR_M for r in records)
    assert second_look.found_frames(rows) == {2, 3, 4, 6, 7, 8}


def test_the_summary_keeps_records_and_control_spots_apart():
    yes = {"answer": "yes", "visible": True}
    no = {"answer": "no", "visible": True}
    jobs = [{"item": "pole", "kind": "record", "spark": yes, "open": yes}, {"item": "pole", "kind": "record", "spark": no},
            {"item": "pole", "kind": "control", "spark": yes, "open": no}, {"item": "pole", "kind": "control", "spark": no}]
    out = second_look.summary(jobs, {"pole": []}, {})
    assert (out["records"], out["seen_by_both"], out["controls"], out["controls_spark_yes"], out["controls_both_yes"]) == (2, 1, 2, 1, 0)
    assert out["control_false_yes_rate"] == 0.0 and out["items"]["pole"]["still_to_visit"] == 1
