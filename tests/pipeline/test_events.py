import numpy as np
import pytest

from pipeline import events
from pipeline.sam_output import Seg
from pipeline.scene import Chunk
from tests.pipeline.fakes import FakeApi, synthetic_photo


def seg(box, frame=0):
    left, top, right, bottom = box
    return Seg(frame, "0", box, 0.9, np.ones((bottom - top, right - left), bool))


def test_footprint_is_the_bottom_fifth_of_the_mask():
    foot = events.footprint(seg((0, 0, 10, 10)))
    assert foot.sum() == 20 and foot[8:].all() and not foot[:8].any()


def test_obstacle_standing_on_the_paving():
    assert events.overlap_ratio(seg((0, 0, 10, 10)), [seg((0, 9, 10, 20))]) == 1.0


def test_paving_beside_the_obstacle_counts_within_the_grown_margin():
    # paving starts 2 px right of the obstacle; grown by 8 px it covers columns 4..9 of the footprint
    assert events.overlap_ratio(seg((0, 0, 10, 10)), [seg((12, 0, 20, 10))]) == pytest.approx(0.6)


def test_paving_far_away_or_absent_does_not_count():
    assert events.overlap_ratio(seg((0, 0, 10, 10)), [seg((0, 50, 10, 60))]) == 0.0
    assert events.overlap_ratio(seg((0, 0, 10, 10)), []) == 0.0


def test_tactile_for_uses_the_nearest_sampled_photo_earlier_first():
    tactile = {0: ["a"], 2: ["b"], 4: None}
    clip = [0, 1, 2, 3, 4, 5]
    assert events.tactile_for(1, tactile, clip) == ["a"]
    assert events.tactile_for(3, tactile, clip) == ["b"]
    assert events.tactile_for(4, tactile, clip) is None  # sampled but failed: unavailable
    assert events.tactile_for(5, {}, clip) is None


def test_chunk_ratios_maps_video_segments_to_photo_pixels():
    chunk = Chunk(0, [0, 1], "v.mp4", "s.mp4", 1280, 960)
    frames = {0: {"width": 160, "height": 120}, 1: {"width": 160, "height": 120}}
    obstacles = {"cone": {0: [seg((0, 800, 160, 960))]}, "bicycle": None}
    tactile = {0: [seg((0, 100, 160, 120))], 1: None}
    ratios = events.chunk_ratios(obstacles, chunk, tactile, frames, [0, 1])
    assert ratios == {"cone": {0: 1.0}}


def test_find_events_needs_two_photos_in_a_row():
    ratios = {"cone": {1: 0.5, 2: 0.9, 3: 0.3, 5: 0.6}, "bicycle": {5: 0.4, 6: 0.4}}
    found = events.find_events(ratios, clip=0, clip_idxs=list(range(7)))
    assert found == [events.Event("cone", 0, 1, 3, 2, 0.9), events.Event("bicycle", 0, 5, 6, 6, 0.4)]


@pytest.mark.parametrize("check, expected", [
    ({"blocked": True, "confidence": 0.9}, ("auto", [])),
    ({"blocked": True, "confidence": 0.5}, ("needs_review", ["low_confidence"])),
    ({"blocked": False, "confidence": 0.9}, (None, [])),
    ({"blocked": False, "confidence": 0.4}, ("needs_review", ["spark_unsure"])),
])
def test_review(check, expected):
    assert events.review(check) == expected


def test_check_event_sends_the_photo(tmp_path):
    answer = {"blocked": True, "object": "cone", "damage_visible": False, "confidence": 0.8, "notes": ""}
    api = FakeApi(spark=lambda name, parts: answer if name == "event_check" else {})
    assert events.check_event(api, synthetic_photo(tmp_path / "p.jpg")) == answer
