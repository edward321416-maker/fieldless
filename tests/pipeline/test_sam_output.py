import sys
from pathlib import Path

import numpy as np
import pytest

from pipeline.sam_output import MAX_BOXES, Seg, box_prompt, check_frames, confident, parse, to_pixels
from tests.pipeline.sam_text import line, record, text

FIXTURES = Path(__file__).parent / "fixtures"


def test_an_empty_answer_has_no_segments():
    assert parse("", video=False) == []
    assert parse("\n", video=True) == []


def test_real_video_answer():
    (seg,) = parse((FIXTURES / "sam_video_stairs.txt").read_text(encoding="utf-8"), video=True)
    assert (seg.frame, seg.obj, seg.box) == (2, "0", (255, 324, 746, 713))
    assert seg.conf == pytest.approx(0.77305, abs=1e-4)
    assert seg.mask.shape == (389, 491)
    assert seg.area == 74493


def test_real_image_answer_scales_reduced_masks_up_to_their_box():
    segs = parse((FIXTURES / "sam_image_boxes.txt").read_text(encoding="utf-8"), video=False)
    assert [s.obj for s in segs] == ["1", "2", "3"]
    first = segs[0]
    assert first.box == (581, 866, 1804, 1536)
    assert first.conf == pytest.approx(0.8828125)
    assert first.mask.shape == (670, 1223)  # sent as 1024 x 561
    assert first.area == 137597
    assert segs[1].mask.shape == (162, 518)


def test_a_synthetic_record_round_trips():
    (seg,) = parse(text([line(0, [record("0", 10, 20, 30, 60, 100, 100, 0.7)])]), video=False)
    assert seg.box == (10, 20, 30, 60)
    assert seg.conf == pytest.approx(0.7)
    assert seg.mask.shape == (40, 20) and seg.mask.all()


def test_video_records_keep_their_frames_and_objects_sorted():
    answer = text([line(1, [record("1", 0, 0, 4, 4, 10, 10, 0.9), record("0", 5, 5, 9, 9, 10, 10, 0.6)]),
                   line(0, [record("0", 0, 0, 2, 2, 10, 10, 0.8)])])
    assert [(s.frame, s.obj) for s in parse(answer, video=True)] == [(0, "0"), (1, "0"), (1, "1")]


def test_a_malformed_answer_raises():
    with pytest.raises(ValueError, match="malformed"):
        parse("<0f>0<|box;x1=1;y1=2|>garbage", video=False)


def test_confident_and_check_frames():
    segs = [Seg(0, "0", (0, 0, 1, 1), 0.4), Seg(3, "1", (0, 0, 1, 1), 0.5), Seg(1, "2", (0, 0, 1, 1), None)]
    assert [s.obj for s in confident(segs)] == ["1", "2"]
    check_frames(segs, 4)
    with pytest.raises(ValueError, match="frame index 3"):
        check_frames(segs, 3)


def test_seg_area_without_a_mask_is_the_box_area():
    assert Seg(0, "0", (10, 10, 20, 15), 0.9).area == 50
    assert Seg(0, "0", (0, 0, 3, 3), 0.9, np.eye(3, dtype=bool)).area == 3


def test_to_pixels_scales_a_0_to_1000_box_to_the_image():
    assert to_pixels({"x1": 20, "y1": 780, "x2": 220, "y2": 998}, 2048, 1536) == (40, 1198, 450, 1532)


def test_to_pixels_clamps_and_rejects_empty_boxes():
    assert to_pixels({"x1": 0, "y1": 0, "x2": 1000, "y2": 1200}, 2048, 1536) == (0, 0, 2047, 1535)
    assert to_pixels({"x1": 0, "y1": 0, "x2": 0, "y2": 0}, 2048, 1536) is None
    assert to_pixels({"x1": 500, "y1": 10, "x2": 400, "y2": 90}, 2048, 1536) is None


def test_box_prompt_numbers_objects_from_one_and_keeps_at_most_16():
    assert box_prompt([(1, 2, 3, 4), (5, 6, 7, 8)], 2048, 1536) == (
        "<0f>1<|box;x1=1;y1=2;x2=3;y2=4;w=2048;h=1536|>,2<|box;x1=5;y1=6;x2=7;y2=8;w=2048;h=1536|>")
    assert box_prompt([(0, 0, 9, 9)] * 20, 100, 100).count("<|box") == MAX_BOXES == 16


def test_parse_can_keep_only_the_boxes():
    answer = text([line(0, [record("0", 10, 20, 50, 60, 100, 100, 0.9)])])
    (seg,) = parse(answer, video=True, masks=False)
    assert seg.mask is None and seg.box == (10, 20, 50, 60) and seg.area == 40 * 40


def test_boxes_read_fast_match_the_parser():
    answer = text([line(0, [record("1", 10, 20, 50, 60, 100, 100, 0.9), record("12", 0, 0, 5, 5, 100, 100, 0.3)]),
                   line(3, [record("2", 1, 2, 3, 4, 100, 100, 0.7)])])
    image = text([line(0, [record("1", 10, 20, 50, 60, 100, 100, 0.9)])])
    for answer, video in ((answer, True), (image, False)):
        slow = [(s.frame, s.obj, s.box, round(s.conf, 4)) for s in parse(answer, video=video)]
        fast = [(s.frame, s.obj, s.box, round(s.conf, 4)) for s in parse(answer, video=video, masks=False)]
        assert fast == slow
    with pytest.raises(ValueError, match="malformed box"):
        parse("<0f>1<|box;x1=1;y1=2;w=9;h=9|><|mask;x=0;y=0|>\n", video=False, masks=False)


def test_a_large_answer_is_decoded_in_another_process_with_the_same_result(monkeypatch):
    from pipeline import sam_output
    monkeypatch.setattr(sam_output, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(sam_output, "POOL_MIN_CHARS", 0)
    answer = text([line(0, [record("1", 10, 20, 50, 60, 100, 100, 0.9)])])
    (away,) = sam_output.parse_away(answer, video=False)
    (here,) = parse(answer, video=False)
    assert away.box == here.box and away.conf == here.conf and (away.mask == here.mask).all()
    with pytest.raises(ValueError):
        sam_output.parse_away("<0f>1<|box;x1=1|><|mask;x=0;y=0;data=1,1,!!|>\n", video=False)


def test_masks_are_decoded_in_place_when_the_program_was_started_as_a_script(monkeypatch):
    import types
    from pipeline import sam_output
    monkeypatch.setattr(sam_output, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(sam_output, "POOL_MIN_CHARS", 0)
    script = types.ModuleType("__main__")
    script.__spec__ = None  # python script.py: a worker would run the script again
    monkeypatch.setitem(sys.modules, "__main__", script)

    def no_pool(*a, **k):
        raise AssertionError("no worker process may start")

    monkeypatch.setattr(sam_output, "ProcessPoolExecutor", no_pool)
    monkeypatch.setattr(sam_output, "_pool", None)
    answer = text([line(0, [record("1", 10, 20, 50, 60, 100, 100, 0.9)])])
    (seg,) = sam_output.parse_away(answer, video=False)
    assert seg.box == (10, 20, 50, 60) and seg.mask is not None
