import pytest

from spike import mapillary as mly

IMAGES = [
    {"id": "1", "sequence": "A", "captured_at": 1700000000000, "is_pano": False, "on_foot": True},
    {"id": "2", "sequence": "A", "captured_at": 1700000001000, "is_pano": False, "on_foot": True},
    {"id": "3", "sequence": "B", "captured_at": 1500000000000, "is_pano": False, "on_foot": True},
    {"id": "4", "sequence": "C", "captured_at": 1700000000000, "is_pano": True, "on_foot": True},
    {"id": "5", "sequence": "D", "captured_at": 1700000000000, "is_pano": False, "on_foot": None},
    {"id": "6", "sequence": "E", "captured_at": 1700000000000, "is_pano": False, "on_foot": False},
]
SINCE = mly.ms("2020-01-01")


def test_bbox_area_and_limit():
    assert abs(mly.bbox_area((127.0, 37.0, 127.01, 37.008)) - 0.00008) < 1e-12
    mly.check_bbox((127.0, 37.0, 127.01, 37.008))
    with pytest.raises(ValueError):
        mly.check_bbox((127.0, 37.0, 127.2, 37.1))


def test_summarize_counts_recent_pedestrian_flat_photos():
    s = mly.summarize(IMAGES, SINCE)
    assert s["total"] == 6 and s["recent"] == 5 and s["capped"] is False
    assert s["recent_on_foot_flat"] == 2
    assert s["recent_unclassified_flat"] == 1
    assert abs(s["pano_ratio"] - 1 / 6) < 1e-9
    assert s["by_year"] == {"2017": 1, "2023": 5}


def test_rank_sequences_prefers_recent_pedestrian_flat():
    ranked = mly.rank_sequences(IMAGES, SINCE)
    assert [r["sequence"] for r in ranked] == ["A", "D"]
    assert ranked[0]["recent_on_foot_flat"] == 2 and ranked[0]["latest"] == "2023-11-14"


def test_frame_record_prefers_computed_values():
    img = {"id": "9", "sequence": "A", "captured_at": 1700000000000,
           "geometry": {"type": "Point", "coordinates": [127.0, 37.0]},
           "computed_geometry": {"type": "Point", "coordinates": [127.0001, 37.0002]},
           "compass_angle": 10.0, "computed_compass_angle": 12.5,
           "creator": {"username": "walker", "id": "u1"}}
    assert mly.frame_record(img) == {"id": "9", "sequence": "A", "lat": 37.0002, "lng": 127.0001,
                                     "compass": 12.5, "captured_at": "2023-11-14T22:13:20Z", "creator": "walker"}


def test_frame_record_tolerates_missing_fields():
    rec = mly.frame_record({"id": "9", "sequence": "A", "captured_at": 1700000000000})
    assert rec["lat"] is None and rec["compass"] is None and rec["creator"] is None


def test_track_km_skips_missing_positions():
    frames = [{"lat": 37.0, "lng": 127.0}, {"lat": 37.001, "lng": 127.0},
              {"lat": None, "lng": None}, {"lat": 37.002, "lng": 127.0}]
    assert abs(mly.track_km(frames) - 0.2224) < 0.002
