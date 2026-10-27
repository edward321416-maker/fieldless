import pytest

from pipeline import entrance_detect as ed
from tests.pipeline.fakes import FakeApi, synthetic_photo
from tests.pipeline.sam_text import line, record, text

FRAMES = {i: {"lat": 37.5 + i * 1e-5, "lng": 127.0} for i in range(40)}  # photos about 1.1 m apart


def cand(idx, clip=0, side="left", name="Cafe"):
    return {"frame_idx": idx, "clip": clip, "side": side, "name": name, "category": "cafe",
            "has_visible_steps": False}


def test_name_key_ignores_case_spaces_and_punctuation():
    assert ed.name_key("Calvin Klein!") == ed.name_key("calvinklein") == "calvinklein"
    assert ed.name_key("밀링 가공") == "밀링가공"


def test_same_name_same_side_within_two_photos_merges():
    kept = ed.merge_candidates([cand(12), cand(10)], FRAMES)
    assert [(k.frame_idx, k.merged) for k in kept] == [(10, [12])]


def test_neighbouring_shops_with_different_names_stay_apart():
    assert len(ed.merge_candidates([cand(10, name="Cafe"), cand(11, name="Bakery")], FRAMES)) == 2


def test_the_other_side_of_the_street_stays_apart():
    assert len(ed.merge_candidates([cand(10, side="left"), cand(10, side="right")], FRAMES)) == 2


def test_an_unnamed_neighbour_merges():
    assert len(ed.merge_candidates([cand(10, name="Cafe"), cand(11, name="")], FRAMES)) == 1


def test_same_name_in_another_clip_within_10_m_merges_whatever_the_side():
    kept = ed.merge_candidates([cand(10, clip=0, side="left"), cand(15, clip=1, side="right")], FRAMES)
    assert [(k.frame_idx, k.merged) for k in kept] == [(10, [15])]


def test_same_name_far_away_or_unnamed_in_another_clip_stays_apart():
    assert len(ed.merge_candidates([cand(0, clip=0), cand(30, clip=1)], FRAMES)) == 2
    assert len(ed.merge_candidates([cand(10, clip=0, name=""), cand(11, clip=1, name="")], FRAMES)) == 2


def test_window_idxs_clamps_at_the_clip_ends():
    clip = list(range(20, 30))
    assert ed.window_idxs(20, clip) == [20, 21, 22]
    assert ed.window_idxs(25, clip) == [23, 24, 25, 26, 27]
    assert ed.window_idxs(29, clip) == [27, 28, 29]


def window(tmp_path):
    idxs = [0, 1, 2, 3, 4]
    photos = {i: synthetic_photo(tmp_path / f"{i}.jpg", seed=i) for i in idxs}
    sizes = {i: (160, 120) for i in idxs}
    return idxs, photos, sizes


def test_detect_stairs_takes_the_largest_mask_and_maps_it_to_the_photo(tmp_path):
    answer = text([line(1, [record("0", 80, 160, 160, 240, 1280, 960, 0.6)]),
                   line(3, [record("0", 40, 80, 200, 240, 1280, 960, 0.8)])])
    api = FakeApi(sam=lambda prompt, media: answer if prompt == "stairs" else "")
    idxs, photos, sizes = window(tmp_path)
    stairs = ed.detect_stairs(api, idxs, photos, sizes, tmp_path)
    assert stairs == ed.Stairs(found=True, conf=0.8, best_idx=3, box=(5, 10, 25, 30))
    assert ("sam_video", "stairs", "file:stairs_0000_0004.mp4", 5) in api.calls
    assert api.deleted == ["file:stairs_0000_0004.mp4"]


def test_detect_stairs_with_nothing_found(tmp_path):
    idxs, photos, sizes = window(tmp_path)
    assert ed.detect_stairs(FakeApi(), idxs, photos, sizes, tmp_path) == ed.Stairs(False, None, None, None)


def test_a_frame_past_the_window_is_an_error(tmp_path):
    answer = text([line(5, [record("0", 0, 0, 8, 8, 1280, 960, 0.9)])])
    idxs, photos, sizes = window(tmp_path)
    with pytest.raises(ValueError, match="out of range"):
        ed.detect_stairs(FakeApi(sam=lambda p, m: answer), idxs, photos, sizes, tmp_path)
