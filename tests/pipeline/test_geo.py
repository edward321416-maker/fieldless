import pytest

from pipeline import geo


def test_haversine_one_thousandth_degree_of_latitude():
    assert geo.haversine_m(37.0, 127.0, 37.001, 127.0) == pytest.approx(111.2, abs=0.2)


def test_offset_point_moves_the_given_distance_along_the_bearing():
    lat, lng = geo.offset_point(37.5, 127.0, 0.0, 4.0)
    assert lat > 37.5 and lng == pytest.approx(127.0)
    assert geo.haversine_m(37.5, 127.0, lat, lng) == pytest.approx(4.0, abs=0.01)
    lat, lng = geo.offset_point(37.5, 127.0, 90.0, 4.0)
    assert lng > 127.0 and lat == pytest.approx(37.5)


def test_entrance_on_the_left_of_a_camera_facing_east_is_north_of_it():
    lat, lng, source = geo.entrance_location({"lat": 37.5, "lng": 127.0, "compass": 90.0}, "left")
    assert source == "image_offset"
    assert lat > 37.5 and lng == pytest.approx(127.0, abs=1e-7)
    assert geo.haversine_m(37.5, 127.0, lat, lng) == pytest.approx(geo.ENTRANCE_OFFSET_M, abs=0.01)


def test_entrance_without_a_compass_stays_at_the_photo():
    assert geo.entrance_location({"lat": 37.5, "lng": 127.0, "compass": None}, "right") == (
        37.5, 127.0, "image_position")


def test_issue_sits_at_the_middle_photo_of_its_run():
    frames = {i: {"lat": 37.0 + i, "lng": 127.0} for i in range(10)}
    assert geo.issue_location(frames, 3, 6) == (41.0, 127.0)  # photo 4
