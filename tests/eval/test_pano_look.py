import random

import pytest

from eval import pano_look
from pipeline import geo


def pano(id_, metres, month="2025-07", sequence=None, bearing=0.0):
    lat, lng = geo.offset_point(47.6, -122.3, bearing, metres)
    return {"id": id_, "lat": lat, "lng": lng, "sequence": sequence or id_, "captured_at": f"{month}-11T21:00:00Z",
            "creator": "kev", "compass": 0.0}


POINT = {"lat": 47.6, "lng": -122.3}


def ids(chosen):
    return [p["id"] for p in chosen]


def test_a_panorama_too_close_or_too_far_is_not_chosen():
    panos = [pano("near", 1.0), pano("good", 8.0), pano("far", 30.0)]
    assert ids(pano_look.choose(POINT, panos)) == ["good"]


def test_the_newest_month_comes_first_even_when_an_older_photo_is_closer_to_the_best_distance():
    panos = [pano("older", 8.0, month="2024-08"), pano("newer", 15.0)]
    assert ids(pano_look.choose(POINT, panos)) == ["newer", "older"]


def test_within_a_month_the_photo_nearest_the_best_distance_comes_first():
    panos = [pano("edge", 18.0), pano("best", 8.5)]
    assert ids(pano_look.choose(POINT, panos)) == ["best", "edge"]


def test_two_views_never_come_from_one_walk():
    panos = [pano("a", 8.0, sequence="s"), pano("b", 9.0, sequence="s"), pano("c", 12.0, sequence="t")]
    assert ids(pano_look.choose(POINT, panos)) == ["a", "c"]


@pytest.mark.parametrize("compass, expected", [(170.0, 10.0), (190.0, -10.0), (90.0, 90.0), (-90.0, -90.0)])
def test_a_view_turns_from_the_cameras_heading_toward_the_point(compass, expected):
    camera = {**pano("a", 10.0), "compass": compass}  # north of the point, so the point lies due south
    assert pano_look.view_of(camera, POINT)["yaw"] == pytest.approx(expected, abs=0.2)


def test_a_view_says_which_photo_it_is_and_how_far_the_point_is():
    view = pano_look.view_of(pano("a", 10.0), POINT)
    assert view["image_id"] == "a" and view["creator"] == "kev" and view["dist_m"] == pytest.approx(10, abs=0.2)


def test_a_control_spot_keeps_clear_of_every_record():
    panos = [pano(str(n), 10.0 + n, bearing=n * 40.0) for n in range(6)]
    records = [{"lat": 47.6, "lng": -122.3}]
    spots = pano_look.control_spots(panos, records, 3, random.Random(1))
    assert len(spots) == 3
    assert all(geo.haversine_m(s["lat"], s["lng"], 47.6, -122.3) >= pano_look.CLEAR_M for s in spots)


def job(kind="record", found=False, item="pole", second="yes"):
    return {"kind": kind, "found": found, "item": item, "answers": [{"open": {"answer": second}}]}


def test_only_a_yes_from_the_second_model_counts_as_seen():
    assert pano_look.seen(job())
    assert not pano_look.seen(job(second="no"))
    assert not pano_look.seen({"answers": [{"spark": {"answer": "yes"}}]})


def test_the_summary_keeps_records_and_control_spots_apart():
    out = pano_look.summary([job(), job(second="no"), job(found=True), job("control"), job("control", second="no")])
    assert (out["records"], out["seen"], out["newly_seen"], out["controls"], out["controls_seen"]) == (3, 2, 1, 2, 1)
    assert out["items"]["pole"] == {"asked": 3, "seen": 2, "seen_again": 1, "newly_seen": 1}
